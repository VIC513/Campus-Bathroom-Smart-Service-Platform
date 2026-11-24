# -*- coding: utf-8 -*-
# 模块归属: 算法 / 模型训练流水线  |  版本: v3.0.0  |  日期: 2026-11-24
"""
训练流水线（v3.0.0，独立测试集 + 消融 + 新旧对照 + 交付清单）

数据来源（CSV 优先，PostgreSQL 仅作为可选后端）:
  训练集: sim_base_logs.csv（日常）+ sim_peak_logs.csv（75% 到访集中 21-23 点）
  测试集: sim_test_logs.csv（独立场景，peak_share=0.6、seed=20261125，不参与训练）
  两者均由 simulator.py --mode export 生成；设置 BATH_USE_PG=1 时才尝试读取
  PostgreSQL shower_logs 表。

划分方式:
  训练集在每个场景内按时间顺序 8:2 切分（前 80% 训练、后 20% 验证，用于
  统计层拟合与默认层选择）；最终性能一律汇报在“独立测试场景”上，避免用
  参与模型选择的验证集自证。

产出:
  wait_time_model_v1.2.0.pkl       版本化三层模型（7 特征）
  wait_time_model.pkl              canonical 副本（内容一致，供既有适配层加载）
  anomaly_model_v1.2.0.pkl         版本化异常检测模型
  anomaly_model.pkl                canonical 副本
  model_comparison_v4.csv          独立测试集上 规则/统计/ML/predict() 分负载指标
  feature_ablation.csv             特征消融实验（同划分、同测试集）
  legacy_comparison.csv            新旧模型在同一测试集上的对比（含泄漏版分析）
  inference_time.csv               各层单条/批量推理耗时
  high_load_report_v3.csv          队列覆盖统计 + 高负载/外推预测网格
  censoring_report.csv             放弃排队（右删失）分布
  anomaly_report.json              异常检测训练环境/数据/指标/标准样例
  model_manifest.json              版本、SHA-256、序列化、类路径、命令、依赖
  training_meta.json               训练元信息（含 split/gain 重要性、测试指标）
"""

import hashlib
import json
import os
import platform
import shutil
import sys
import time
from datetime import datetime

import numpy as np
import pandas as pd
import joblib

from wait_time_predictor import (
    WaitTimePredictor, VERSION, FEATURE_COLS, DEFAULT_AVG_DURATION_MIN,
)
from anomaly_detector import AnomalyDetector

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_CONFIG = {
    "dbname":   "bathroom_female_db",
    "user":     "postgres",
    "password": os.environ.get("BATH_DB_PASSWORD", ""),
    "host":     "localhost",
    "port":     "5432",
    "connect_timeout": 3,
}

MODEL_WAIT = f"wait_time_model_{VERSION}.pkl"
MODEL_WAIT_ALIAS = "wait_time_model.pkl"
MODEL_ANOMALY = f"anomaly_model_{VERSION}.pkl"
MODEL_ANOMALY_ALIAS = "anomaly_model.pkl"

# 正式 7 特征与消融候选
BASE7 = list(FEATURE_COLS)
FEATURE_SETS = {
    'A_完整9特征(temperature+avg_duration)': BASE7 + ['temperature', 'avg_duration'],
    'B_删除temperature(8特征)': BASE7 + ['avg_duration'],
    'C_删除avg_duration(8特征)': BASE7 + ['temperature'],
    'D_删除两者(7特征,正式)': BASE7,
    'E_再删除gender_F(6特征)': [c for c in BASE7 if c != 'gender_F'],
    'F_加入is_fault_area(8特征,仅泄漏分析)': BASE7 + ['is_fault_area'],
}


# ---------- 1. 数据加载 ----------
def _normalize_bools(df: pd.DataFrame) -> pd.DataFrame:
    """CSV 导出时布尔列可能是 t/f 字符串，统一转 bool"""
    for col in df.columns:
        s = df[col].astype(str).str.lower()
        if s.isin(['t', 'f', 'true', 'false']).all() and s.nunique() <= 2:
            df[col] = s.isin(['t', 'true'])
    return df


def _read_pg() -> pd.DataFrame:
    import psycopg2
    pg = psycopg2.connect(**DB_CONFIG)
    df = pd.read_sql("SELECT * FROM shower_logs ORDER BY start_time", pg)
    pg.close()
    df['start_time'] = pd.to_datetime(df['start_time'])
    df['scenario'] = 'db'
    return df


def _read_csv(prefix: str, scenario: str) -> pd.DataFrame:
    path = os.path.join(BASE_DIR, f"{prefix}_logs.csv")
    if not os.path.exists(path):
        return None
    df = _normalize_bools(pd.read_csv(path))
    df['scenario'] = scenario
    df['start_time'] = pd.to_datetime(df['start_time'], utc=True) \
        .dt.tz_convert('Asia/Shanghai')
    print(f"[Load] {os.path.basename(path)} = {len(df)} 条")
    return df


def load_dataframe() -> pd.DataFrame:
    """训练数据：CSV 优先（base + peak）；BATH_USE_PG=1 时才尝试 PostgreSQL"""
    if os.environ.get('BATH_USE_PG') == '1':
        try:
            df = _read_pg()
            print(f"[Load] 来源 PostgreSQL | shower_logs = {len(df)} 条")
            return df.sort_values('start_time').reset_index(drop=True)
        except Exception as e:
            print(f"[Load] PostgreSQL 不可用 ({type(e).__name__})，改用 CSV")

    frames = [f for f in (_read_csv('sim_base', 'base'),
                          _read_csv('sim_peak', 'peak')) if f is not None]
    if not frames:
        old = os.path.join(BASE_DIR, 'shower_logs_new.csv')
        df = _normalize_bools(pd.read_csv(old))
        df['scenario'] = 'base'
        df['start_time'] = pd.to_datetime(df['start_time'], utc=True) \
            .dt.tz_convert('Asia/Shanghai')
        print(f"[Load] 回退旧数据 {os.path.basename(old)} = {len(df)} 条")
        return df.sort_values(['scenario', 'start_time']).reset_index(drop=True)

    df = pd.concat(frames, ignore_index=True)
    return df.sort_values(['scenario', 'start_time']).reset_index(drop=True)


def load_test_dataframe():
    """独立测试场景：仅用于最终评价，不参与训练/调参"""
    return _read_csv('sim_test', 'test')


# ---------- 2. 构造数据集（含消融所需的历史特征） ----------
def build_wait_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """
    只使用取号时刻可获得的字段作为正式特征：
      queue_length_at_start / idle_stalls_at_start / hour_of_day / is_weekend / gender
    标签 = wait_seconds（用户真实等待到分配隔间的时长，由 DES 涌现，未截断）。

    额外保留两个“历史/环境”列供消融与旧版对照使用：
      temperature   仿真室外气温（线上无数据源，仅对比实验）
      avg_duration  同场景同性别最近 100 条完成会话的滚动平均时长（shift(1)，
                    避免使用当前会话自身时长造成泄漏；线上暂无此数据源）
      is_fault      该会话结束时被用户反馈标记为故障（后验信息，仅泄漏分析）
    """
    d = df.sort_values(['scenario', 'start_time']).reset_index(drop=True)
    dur_min = d['duration_seconds'].astype(float) / 60.0
    hist_avg = (dur_min.groupby([d['scenario'], d['gender']])
                .transform(lambda s: s.shift(1).rolling(100, min_periods=1).mean()))
    hist_avg = hist_avg.fillna(dur_min.median())

    out = pd.DataFrame({
        'queue_len':    d['queue_length_at_start'].astype(float),
        'free_stalls':  d['idle_stalls_at_start'].astype(float),
        'hour':         d['hour_of_day'].astype(int),
        'is_weekend':   d['is_weekend'].astype(int),
        'gender':       d['gender'],
        'wait_time':    d['wait_seconds'].astype(float) / 60.0,
        'temperature':  d['temperature'].astype(float),
        'avg_duration': hist_avg.astype(float),
        'is_fault':     d['is_fault'].astype(int),
        'scenario':     d['scenario'].astype(str),
        '_time':        d['start_time'],
    })
    out = out.dropna(subset=['queue_len', 'free_stalls', 'wait_time'])
    return out.sort_values(['scenario', '_time']).reset_index(drop=True)


def _time_split(ds: pd.DataFrame, group_col='scenario', ratio=0.8):
    """返回 (train, val)：每个场景内按时间顺序切分"""
    tr, va = [], []
    for _, sub in ds.groupby(group_col, sort=False):
        sub = sub.reset_index(drop=True)
        n = int(len(sub) * ratio)
        tr.append(sub.iloc[:n])
        va.append(sub.iloc[n:])
    return (pd.concat(tr, ignore_index=True), pd.concat(va, ignore_index=True))


def build_feature_frame(ds: pd.DataFrame) -> pd.DataFrame:
    """构造全部候选特征（正式 7 个 + 消融用 3 个），列名与 FEATURE_SETS 对齐"""
    f = pd.DataFrame(index=ds.index)
    f['queue_len'] = ds['queue_len'].astype(float)
    f['free_stalls'] = ds['free_stalls'].astype(float)
    f['load_per_stall'] = f['queue_len'] / (f['free_stalls'] + 1.0)
    f['hour_seg'] = ds['hour'].apply(WaitTimePredictor.encode_hour).astype(float)
    f['is_peak'] = ds['hour'].apply(lambda h: 1.0 if 21 <= h < 23 else 0.0)
    f['weekend'] = ds['is_weekend'].astype(float)
    f['gender_F'] = ds['gender'].map({'M': 0.0, 'F': 1.0}).fillna(1.0).astype(float)
    f['temperature'] = ds['temperature'].astype(float)
    f['avg_duration'] = ds['avg_duration'].astype(float)
    f['is_fault_area'] = ds['is_fault'].astype(float)
    return f


# ---------- 3. 指标 ----------
def _metrics(y, p) -> dict:
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return {
        'n': int(len(y)),
        'mae_min': round(float(np.mean(np.abs(p - y))), 4),
        'rmse_min': round(float(np.sqrt(np.mean((p - y) ** 2))), 4),
        'bias_min': round(float(np.mean(p - y)), 4),
    }


def _new_lgbm():
    try:
        from lightgbm import LGBMRegressor
        return LGBMRegressor(
            n_estimators=400, learning_rate=0.03, max_depth=7,
            num_leaves=31, min_child_samples=20,
            subsample=0.9, colsample_bytree=0.9,
            random_state=42, verbose=-1,
        ), 'LightGBM'
    except ImportError:
        from sklearn.ensemble import GradientBoostingRegressor
        return GradientBoostingRegressor(
            n_estimators=300, learning_rate=0.03, max_depth=5,
            min_samples_leaf=20, min_samples_split=40, random_state=42,
        ), 'sklearn-GBDT'


# ---------- 4. 训练正式模型 ----------
def train_wait_model(ds: pd.DataFrame):
    print(f"\n[Train] 训练集: {len(ds)} 条 | "
          f"场景分布 {ds['scenario'].value_counts().to_dict()}")
    print(f"[Train]   wait_time 0~{ds['wait_time'].max():.2f} 分钟，"
          f"queue_len 0~{ds['queue_len'].max():.0f}")
    predictor = WaitTimePredictor()
    meta = predictor.train(ds, group_col='scenario')

    path_v = os.path.join(BASE_DIR, MODEL_WAIT)
    path_a = os.path.join(BASE_DIR, MODEL_WAIT_ALIAS)
    predictor.save(path_v)
    shutil.copyfile(path_v, path_a)

    print(f"[Train] 三层模型已保存 -> {MODEL_WAIT}（并同步 canonical {MODEL_WAIT_ALIAS}）")
    print(f"[Train]   特征 {len(predictor.feature_cols)} 个: {predictor.feature_cols}")
    print(f"[Train]   验证集 MAE: 规则 {meta['mae_rule']:.3f} / "
          f"统计 {meta['mae_stat']:.3f} / LightGBM {meta['mae_ml']:.3f} 分钟")
    print(f"[Train]   默认层 = {meta['default_layer']}，"
          f"ML 增益 {meta['ml_gain_vs_stat_min']:+.3f} 分钟")
    return predictor, meta


# ---------- 5. 独立测试集评价 ----------
def evaluate_independent_test(predictor: WaitTimePredictor,
                              test_ds: pd.DataFrame) -> list:
    if test_ds is None or len(test_ds) == 0:
        print("\n[Test] 未找到独立测试集 sim_test_logs.csv，跳过")
        return []

    X = predictor.build_features(test_ds)
    y = test_ds['wait_time'].values.astype(float)
    q = test_ds['queue_len'].values.astype(float)
    free = test_ds['free_stalls'].values.astype(float)
    hour = test_ds['hour'].values.astype(int)

    preds = {
        'rule': np.asarray(predictor.baseline.predict(X), float),
        'statistical': np.asarray(predictor.calibrator.predict(X), float),
        'lightgbm': np.asarray(predictor.ml.predict(X), float),
        'predict()': np.asarray(predictor.predict(df=test_ds), float).ravel(),
    }
    scopes = {
        'overall': np.ones(len(y), dtype=bool),
        'queue_0': q == 0,
        'queue_1_5': (q >= 1) & (q <= 5),
        'queue_6_10': (q >= 6) & (q <= 10),
        'queue_11_15': (q >= 11) & (q <= 15),
        'queue_gt15': q > 15,
        'free_0': free == 0,
        'peak_hours_21_23': (hour >= 21) & (hour < 23),
    }

    rows = []
    print("\n=== 独立测试集评价（sim_test，未参与训练）===")
    print(f"  n={len(y)} | 队列 {q.min():.0f}~{q.max():.0f} | "
          f"wait 0~{y.max():.2f} 分钟")
    for scope, mask in scopes.items():
        if not mask.any():
            continue
        print(f"  <{scope}> n={int(mask.sum())}")
        for layer, p in preds.items():
            m = _metrics(y[mask], p[mask])
            rows.append({'dataset': 'independent_test', 'scenario': 'test',
                         'scope': scope, 'layer': layer, **m})
            print(f"    {layer:12s} MAE={m['mae_min']:.3f} "
                  f"RMSE={m['rmse_min']:.3f} 偏差={m['bias_min']:+.3f}")

    path = os.path.join(BASE_DIR, 'model_comparison_v4.csv')
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    print(f"  -> 已写出 {path}")
    return rows


# ---------- 6. 特征消融 ----------
def run_feature_ablation(ds: pd.DataFrame, test_ds: pd.DataFrame) -> list:
    if test_ds is None or len(test_ds) == 0:
        return []
    tr, _ = _time_split(ds, 'scenario', 0.8)
    F_tr = build_feature_frame(tr)
    F_te = build_feature_frame(test_ds)
    y_tr = tr['wait_time'].values.astype(float)
    y_te = test_ds['wait_time'].values.astype(float)
    q_te = test_ds['queue_len'].values.astype(float)

    rows = []
    print("\n=== 特征消融（相同训练集划分 + 相同独立测试集）===")
    for name, cols in FEATURE_SETS.items():
        model, backend = _new_lgbm()
        model.fit(F_tr[cols].values, y_tr)
        p = np.clip(model.predict(F_te[cols].values), 0, None)
        m = _metrics(y_te, p)
        hi = q_te >= 10
        m_hi = _metrics(y_te[hi], p[hi]) if hi.any() else None
        rows.append({
            'candidate': name, 'backend': backend, 'n_features': len(cols),
            'features': '|'.join(cols), 'n_test': m['n'],
            'mae_min': m['mae_min'], 'rmse_min': m['rmse_min'],
            'bias_min': m['bias_min'],
            'high_load_mae_min': m_hi['mae_min'] if m_hi else None,
        })
        print(f"  {name:<36s} MAE={m['mae_min']:.3f} "
              f"RMSE={m['rmse_min']:.3f} 偏差={m['bias_min']:+.3f}"
              + (f" | q>=10 MAE={m_hi['mae_min']:.3f}" if m_hi else ""))

    path = os.path.join(BASE_DIR, 'feature_ablation.csv')
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    print(f"  -> 已写出 {path}")
    return rows


# ---------- 7. 新旧模型同测试集对比 ----------
def run_legacy_comparison(ds: pd.DataFrame, test_ds: pd.DataFrame) -> list:
    if test_ds is None or len(test_ds) == 0:
        return []
    tr, _ = _time_split(ds, 'scenario', 0.8)
    F_tr = build_feature_frame(tr)
    F_te = build_feature_frame(test_ds)
    y_tr = tr['wait_time'].values.astype(float)
    y_te = test_ds['wait_time'].values.astype(float)
    q_te = test_ds['queue_len'].values.astype(float)

    candidates = {
        '旧版v1.1_9特征(temperature+avg_duration)': BASE7 + ['temperature', 'avg_duration'],
        '旧版v1.1_10特征(+is_fault_area,泄漏)': BASE7 + ['temperature', 'avg_duration', 'is_fault_area'],
        '新版v1.2_7特征(正式)': BASE7,
    }
    rows = []
    print("\n=== 新旧模型同测试集对比 ===")
    for name, cols in candidates.items():
        model, backend = _new_lgbm()
        model.fit(F_tr[cols].values, y_tr)
        p = np.clip(model.predict(F_te[cols].values), 0, None)
        m = _metrics(y_te, p)
        hi = q_te >= 10
        m_hi = _metrics(y_te[hi], p[hi]) if hi.any() else None
        rows.append({
            'model': name, 'backend': backend, 'n_features': len(cols),
            'features': '|'.join(cols), 'n_test': m['n'],
            'mae_min': m['mae_min'], 'rmse_min': m['rmse_min'],
            'bias_min': m['bias_min'],
            'high_load_mae_min': m_hi['mae_min'] if m_hi else None,
            'note': '旧版模型包已被覆盖，此处按 v1.1 特征口径在同一数据上复现',
        })
        print(f"  {name:<44s} MAE={m['mae_min']:.3f} "
              f"RMSE={m['rmse_min']:.3f} 偏差={m['bias_min']:+.3f}"
              + (f" | q>=10 MAE={m_hi['mae_min']:.3f}" if m_hi else ""))

    path = os.path.join(BASE_DIR, 'legacy_comparison.csv')
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    print(f"  -> 已写出 {path}")
    return rows


# ---------- 8. 推理耗时 ----------
def measure_inference_time(predictor: WaitTimePredictor,
                           test_ds: pd.DataFrame) -> list:
    if test_ds is None or len(test_ds) == 0:
        return []
    X = predictor.build_features(test_ds)
    x1 = X[:1]

    layers = {
        'rule': lambda: predictor.baseline.predict(x1),
        'statistical': lambda: predictor.calibrator.predict(x1),
        'lightgbm': lambda: predictor.ml.predict(x1),
        'predict()': lambda: predictor.predict(
            queue_len=float(test_ds.iloc[0]['queue_len']),
            hour=int(test_ds.iloc[0]['hour']),
            free_stalls=float(test_ds.iloc[0]['free_stalls']),
            gender=str(test_ds.iloc[0]['gender'])),
    }
    rows = []
    n_single = 200
    print("\n=== 推理耗时 ===")
    for name, fn in layers.items():
        fn()  # 预热
        t0 = time.perf_counter()
        for _ in range(n_single):
            fn()
        dt = (time.perf_counter() - t0) * 1000
        rows.append({'layer': name, 'mode': 'single', 'n_calls': n_single,
                     'total_ms': round(dt, 3),
                     'ms_per_call': round(dt / n_single, 4),
                     'rows_per_sec': round(n_single / (dt / 1000), 1)})
        print(f"  {name:12s} 单条 {dt / n_single:.4f} ms/次")

    batch_fns = {
        'rule': lambda: predictor.baseline.predict(X),
        'statistical': lambda: predictor.calibrator.predict(X),
        'lightgbm': lambda: predictor.ml.predict(X),
        'predict()': lambda: predictor.predict(df=test_ds),
    }
    for name, fn in batch_fns.items():
        fn()
        t0 = time.perf_counter()
        fn()
        dt = (time.perf_counter() - t0) * 1000
        rows.append({'layer': name, 'mode': 'batch', 'n_calls': len(X),
                     'total_ms': round(dt, 3),
                     'ms_per_call': round(dt / len(X), 6),
                     'rows_per_sec': round(len(X) / (dt / 1000), 1)})
        print(f"  {name:12s} 批量 {len(X)} 行 {dt:.1f} ms "
              f"({len(X) / (dt / 1000):.0f} 行/秒)")

    path = os.path.join(BASE_DIR, 'inference_time.csv')
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    print(f"  -> 已写出 {path}")
    return rows


# ---------- 9. 高负载覆盖 + 外推网格 ----------
def high_load_report(predictor: WaitTimePredictor, datasets: dict) -> list:
    rows = []
    bins = [
        ('queue_0', 0, 0), ('queue_1_5', 1, 5), ('queue_6_10', 6, 10),
        ('queue_11_15', 11, 15), ('queue_16_20', 16, 20),
        ('queue_21_30', 21, 30), ('queue_gt_30', 31, 999),
    ]
    for name, ds in datasets.items():
        if ds is None or len(ds) == 0:
            continue
        q, y, free = (ds['queue_len'].values, ds['wait_time'].values,
                      ds['free_stalls'].values)
        for seg, lo, hi in bins:
            mask = (q >= lo) & (q <= hi)
            if not mask.any():
                continue
            yv = y[mask]
            rows.append({
                'kind': 'coverage', 'scenario': name, 'segment': seg,
                'n': int(mask.sum()),
                'mean_wait_min': round(float(yv.mean()), 3),
                'median_wait_min': round(float(np.median(yv)), 3),
                'max_wait_min': round(float(yv.max()), 3),
                'share_zero_pct': round(float((yv == 0).mean() * 100), 2),
                'share_gt_6min_pct': round(float((yv > 6).mean() * 100), 2),
                'share_gt_15min_pct': round(float((yv > 15).mean() * 100), 2),
                'share_free_0_pct': round(float((free[mask] == 0).mean() * 100), 2),
                'note': '标签来自 DES 仿真，未截断；放弃者不产生完成标签',
            })

    for qq in (10, 15, 20, 30, 40, 47, 60):
        for ff in (0, 2, 5):
            probe = pd.DataFrame([{
                'queue_len': qq, 'free_stalls': ff, 'hour': 21,
                'gender': 'F', 'is_weekend': 0,
            }])
            Xp = predictor.build_features(probe)
            rows.append({
                'kind': 'grid', 'scenario': '-', 'segment': f'q{qq}_f{ff}',
                'n': None, 'mean_wait_min': None, 'median_wait_min': None,
                'max_wait_min': None, 'share_zero_pct': None,
                'share_gt_6min_pct': None, 'share_gt_15min_pct': None,
                'share_free_0_pct': None,
                'rule_min': round(float(predictor.baseline.predict(Xp)[0]), 3),
                'stat_min': round(float(predictor.calibrator.predict(Xp)[0]), 3),
                'ml_raw_min': round(float(predictor.ml.predict(Xp)[0]), 3),
                'predict_min': round(float(predictor.predict(df=probe)), 3),
                'note': '预测行为展示；分布外部分按排队论斜率外推并封顶',
            })

    path = os.path.join(BASE_DIR, 'high_load_report_v3.csv')
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    print("\n  [队列覆盖]")
    for r in rows:
        if r['kind'] == 'coverage':
            print(f"    {r['scenario']:<7} {r['segment']:<11} n={r['n']:<6} "
                  f"均值 {r['mean_wait_min']:>5.2f} 最大 {r['max_wait_min']:>5.2f} "
                  f"free=0 {r['share_free_0_pct']:>5.1f}%")
    print("  [高负载网格] q×free -> rule / stat / ml_raw / predict()")
    for r in rows:
        if r['kind'] == 'grid':
            print(f"    {r['segment']:<10} {r['rule_min']:>6.2f} / "
                  f"{r['stat_min']:>6.2f} / {r['ml_raw_min']:>6.2f} / "
                  f"{r['predict_min']:>6.2f}")
    print(f"  -> 已写出 {path}")
    return rows


# ---------- 10. 放弃排队（右删失）分析 ----------
def censoring_report():
    frames = []
    for prefix in ('sim_base', 'sim_peak', 'sim_test'):
        p = os.path.join(BASE_DIR, f"{prefix}_queue_events.csv")
        if os.path.exists(p):
            frames.append(pd.read_csv(p))
    if not frames:
        print("\n[Censoring] 未找到排队事件 CSV，跳过")
        return

    ev = pd.concat(frames, ignore_index=True)
    ev['q_bin'] = pd.cut(ev['queue_len_at_arrival'],
                         bins=[-1, 0, 5, 10, 15, 20, 30, 999],
                         labels=['0', '1-5', '6-10', '11-15', '16-20',
                                 '21-30', '>30'])
    rows = []
    for (g, b), sub in ev.groupby(['scenario', 'q_bin'], observed=True):
        n = len(sub)
        ab = int((sub['outcome'] == 'abandoned').sum())
        done = sub[sub['outcome'].isin(['completed', 'served'])]
        abandoned = sub[sub['outcome'] == 'abandoned']
        rows.append({
            'scenario': g, 'queue_bin': str(b), 'n_arrivals': n,
            'n_completed': int(len(done)),
            'n_abandoned': ab,
            'abandon_rate_pct': round(ab / max(n, 1) * 100, 2),
            'mean_tolerance_min': round(float(sub['tolerance_seconds'].mean()) / 60, 2),
            'mean_waited_completed_min': (round(float(done['waited_seconds'].mean()) / 60, 2)
                                          if len(done) else None),
            'mean_waited_abandoned_min': (round(float(abandoned['waited_seconds'].mean()) / 60, 2)
                                          if len(abandoned) else None),
            'max_waited_completed_min': (round(float(done['waited_seconds'].max()) / 60, 2)
                                         if len(done) else None),
        })
    path = os.path.join(BASE_DIR, 'censoring_report.csv')
    pd.DataFrame(rows).to_csv(path, index=False, encoding='utf-8-sig')
    print("\n=== 放弃排队（右删失）分析 ===")
    for r in rows:
        print(f"  {r['scenario']:<5} q={r['queue_bin']:<6} "
              f"到达 {r['n_arrivals']:<6} 放弃率 {r['abandon_rate_pct']:>5.1f}% "
              f"完成标签最大等待 {r['max_waited_completed_min']} 分钟")
    print(f"  -> 已写出 {path}")
    print("  说明：放弃事件的 waited_seconds 是删失观测（只知至少等了这么久），"
          "不进入监督训练标签；长等待样本天然稀疏由用户容忍时长决定。")


# ---------- 11. 异常检测：训练 + 评价 + 报告 ----------
def _duration_only_flag(duration_min, hist):
    """仅时长规则方案（无实时用水量时的独立兜底）"""
    if duration_min > 60:
        return True
    if hist and len(hist) > 10:
        mu, sd = float(np.mean(hist)), float(np.std(hist))
        if sd > 0 and duration_min > mu + 3.0 * sd:
            return True
    return False


def train_anomaly_model(df: pd.DataFrame, test_df) -> dict:
    normal = df[~df['is_fault'].astype(bool)]
    X = np.column_stack([
        normal['duration_seconds'].astype(float) / 60.0,
        normal['water_usage'].astype(float),
        normal['hour_of_day'].astype(int),
    ])
    print(f"\n[Anomaly] 训练集: {len(X)} 条正常样本（异常检测为无监督）")
    detector = AnomalyDetector()
    detector.train(X)

    path_v = os.path.join(BASE_DIR, MODEL_ANOMALY)
    path_a = os.path.join(BASE_DIR, MODEL_ANOMALY_ALIAS)
    joblib.dump(detector, path_v)
    shutil.copyfile(path_v, path_a)
    print(f"[Anomaly] 模型已保存 -> {MODEL_ANOMALY}（并同步 {MODEL_ANOMALY_ALIAS}）")

    # ----- 评价：优先用独立测试集 -----
    ev = test_df if test_df is not None and len(test_df) else df
    ev = ev.sample(n=min(len(ev), 4000), random_state=42) if len(ev) > 4000 else ev
    hist = (normal['duration_seconds'].astype(float) / 60.0).tolist()
    normal_ev = ev[~ev['is_fault'].astype(bool)]
    fault_ev = ev[ev['is_fault'].astype(bool)]

    def _rate(rows, water=True):
        if len(rows) == 0:
            return None
        hits = 0
        for _, r in rows.iterrows():
            res = detector.detect(
                r['duration_seconds'] / 60.0,
                r['water_usage'] if water else None,
                int(r['hour_of_day']), history_durations=hist)
            hits += int(res['is_anomaly'])
        return round(hits / len(rows), 4)

    recall_water = _rate(fault_ev, True)
    recall_no_water = _rate(fault_ev, False)
    fpr_water = _rate(normal_ev, True)
    fpr_no_water = _rate(normal_ev, False)

    dur_hits = sum(_duration_only_flag(r['duration_seconds'] / 60.0, hist)
                   for _, r in fault_ev.iterrows())
    dur_fp = sum(_duration_only_flag(r['duration_seconds'] / 60.0, hist)
                 for _, r in normal_ev.iterrows())
    duration_only = {
        'recall': round(dur_hits / len(fault_ev), 4) if len(fault_ev) else None,
        'false_positive_rate': round(dur_fp / len(normal_ev), 4) if len(normal_ev) else None,
        'rule': 'duration>60min 或 duration>均值+3σ',
    }

    examples_in = [
        {'name': '正常会话', 'duration_min': 15.0, 'water_usage_l': 80.0, 'hour': 20},
        {'name': '超长占用', 'duration_min': 75.0, 'water_usage_l': 90.0, 'hour': 21},
        {'name': '用水量异常偏低', 'duration_min': 20.0, 'water_usage_l': 2.0, 'hour': 20},
        {'name': '用水量异常偏高', 'duration_min': 18.0, 'water_usage_l': 600.0, 'hour': 19},
    ]
    examples = []
    for c in examples_in:
        r = detector.detect(c['duration_min'], c['water_usage_l'], c['hour'], hist)
        examples.append({**c, **{k: r[k] for k in
                                 ('is_anomaly', 'reason', 'score', 'degraded')}})

    report = {
        'algorithm_version': VERSION,
        'python': sys.version.split()[0],
        'trained_at': datetime.now().isoformat(timespec='seconds'),
        'model_file': MODEL_ANOMALY,
        'data_source': 'sim_base_logs.csv + sim_peak_logs.csv 中 is_fault=False 的会话',
        'n_train_normal': int(len(X)),
        'n_eval': int(len(ev)),
        'n_eval_fault': int(len(fault_ev)),
        'n_eval_normal': int(len(normal_ev)),
        'features': ['duration_min', 'water_usage_l', 'hour'],
        'contamination': 0.01,
        'metrics': {
            'with_water': {'recall': recall_water, 'false_positive_rate': fpr_water},
            'without_water_degraded': {'recall': recall_no_water,
                                       'false_positive_rate': fpr_no_water},
            'duration_only_rule': duration_only,
        },
        'standard_examples': examples,
        'limitations': [
            '用水量为仿真生成值，线上暂无真实数据源，不能作为正式故障判定依据',
            '水温异常、门锁损坏等用户感知类故障无法由传感器特征识别',
            '线上故障应以用户反馈为唯一判定依据，本模块仅用于离线验证与内部巡检',
        ],
        'integration_policy': '本轮保留模型但不接入正式告警接口；缺少用水量时 '
                              'water_usage_l=None 进入降级模式，或使用仅时长规则方案',
    }
    path = os.path.join(BASE_DIR, 'anomaly_report.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"[Anomaly] 召回率(含用水量)={recall_water} 误报率={fpr_water} | "
          f"仅时长降级召回={recall_no_water} 误报={fpr_no_water}")
    print(f"[Anomaly] 仅时长规则方案 召回={duration_only['recall']} "
          f"误报={duration_only['false_positive_rate']}")
    print(f"  -> 已写出 {path}")
    return report


# ---------- 12. 模型清单（版本 / SHA-256 / 环境 / 命令） ----------
def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def write_model_manifest() -> dict:
    def _entry(name, role):
        path = os.path.join(BASE_DIR, name)
        return {
            'file': name, 'role': role,
            'sha256': _sha256(path),
            'size_bytes': os.path.getsize(path),
        }

    deps = {'numpy': np.__version__, 'pandas': pd.__version__,
            'scikit-learn': __import__('sklearn').__version__,
            'joblib': joblib.__version__}
    try:
        import lightgbm
        deps['lightgbm'] = lightgbm.__version__
    except ImportError:
        deps['lightgbm'] = None

    manifest = {
        'algorithm_version': VERSION,
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'python': sys.version.split()[0],
        'platform': platform.platform(),
        'dependencies': deps,
        'serialization': 'joblib (pickle)，保存完整三层对象（规则/统计/LightGBM）',
        'custom_classes': [
            'wait_time_predictor.WaitTimePredictor',
            'wait_time_predictor.RuleBasedWaitEstimator',
            'wait_time_predictor.StatisticalCalibrator',
            'wait_time_predictor.MLWaitModel',
            'anomaly_detector.AnomalyDetector',
        ],
        'models': [
            _entry(MODEL_WAIT, 'wait_time_predictor'),
            _entry(MODEL_ANOMALY, 'anomaly_detector'),
        ],
        'canonical_alias': {
            MODEL_WAIT_ALIAS: MODEL_WAIT,
            MODEL_ANOMALY_ALIAS: MODEL_ANOMALY,
        },
        'feature_cols': list(BASE7),
        'commands': {
            'generate_train_data': 'python simulator.py --mode export --scenario base '
                                   '--users 300 --days 90 --seed 20261124 --prefix sim_base',
            'generate_peak_data': 'python simulator.py --mode export --scenario peak '
                                  '--users 300 --days 90 --seed 20261124 --prefix sim_peak',
            'generate_test_data': 'python simulator.py --mode export --scenario test '
                                  '--users 300 --days 20 --peak-share 0.6 '
                                  '--seed 20261125 --prefix sim_test',
            'train': 'python train_pipeline.py',
            'inference': "python -c \"from wait_time_predictor import WaitTimePredictor; "
                         "p=WaitTimePredictor().load('wait_time_model.pkl'); "
                         "print(p.predict(queue_len=10, hour=21, free_stalls=0))\"",
        },
        'notes': [
            '模型文件与 Python/依赖版本强绑定，跨版本加载失败时请重训或使用 '
            'wait_time_predictor.rule_fallback_wait() 降级',
            'canonical 文件仅用于兼容既有适配层路径，内容与版本化文件一致',
        ],
    }
    path = os.path.join(BASE_DIR, 'model_manifest.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
    print(f"\n[Manifest] 已写出 {path}")
    for m in manifest['models']:
        print(f"  {m['file']}: {m['size_bytes']} B  sha256={m['sha256'][:16]}...")
    return manifest


# ==================== 主流程 ====================
if __name__ == '__main__':
    df = load_dataframe()
    print(f"       故障 {int(df['is_fault'].sum())} 条 "
          f"({df['is_fault'].astype(float).mean():.2%})")

    ds = build_wait_dataset(df)
    test_df = load_test_dataframe()
    test_ds = build_wait_dataset(test_df) if test_df is not None else None
    if test_ds is not None:
        print(f"[Load] 独立测试集 = {len(test_ds)} 条 | "
              f"queue 0~{test_ds['queue_len'].max():.0f} | "
              f"wait 0~{test_ds['wait_time'].max():.2f} 分钟")

    predictor, meta = train_wait_model(ds)

    split_imp = predictor.feature_importance('split')
    gain_imp = predictor.feature_importance('gain')
    meta['feature_importance_split'] = split_imp
    meta['feature_importance_gain'] = gain_imp
    print("[Train]   特征重要性 split / gain (%):")
    for k in sorted(split_imp, key=lambda x: -split_imp[x]):
        print(f"             {k:16s} {split_imp[k]:6.2f} / {gain_imp.get(k, 0):6.2f}")

    test_rows = evaluate_independent_test(predictor, test_ds)
    if test_rows:
        overall = {r['layer']: r for r in test_rows if r['scope'] == 'overall'}
        meta['metrics_test'] = {k: {kk: v[kk] for kk in
                                    ('n', 'mae_min', 'rmse_min', 'bias_min')}
                                for k, v in overall.items()}
        meta['independent_test'] = {
            'source': 'sim_test_logs.csv',
            'generated_by': 'simulator.py --scenario test --peak-share 0.6 '
                            '--seed 20261125 --users 300 --days 20',
            'n': int(len(test_ds)),
            'note': '未参与训练与模型选择，仅用于最终评价',
        }

    ablation = run_feature_ablation(ds, test_ds)
    legacy = run_legacy_comparison(ds, test_ds)
    infer = measure_inference_time(predictor, test_ds)
    high_load_report(predictor, {'base': ds[ds['scenario'] == 'base'],
                                 'peak': ds[ds['scenario'] == 'peak'],
                                 'test': test_ds})
    censoring_report()
    anomaly = train_anomaly_model(df, test_df)

    meta['version'] = VERSION
    meta['model_file'] = MODEL_WAIT
    meta['canonical_alias'] = MODEL_WAIT_ALIAS
    if ablation:
        meta['ablation_best'] = min(ablation, key=lambda r: r['mae_min'])['candidate']
    with open(os.path.join(BASE_DIR, 'training_meta.json'), 'w',
              encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("\n[Train] 训练元信息 -> training_meta.json")

    write_model_manifest()
    print("\n[Done] 训练、独立测试评价、消融、新旧对照与清单生成完成")
