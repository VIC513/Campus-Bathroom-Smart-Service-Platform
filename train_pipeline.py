# -*- coding: utf-8 -*-
# 模块归属: 算法 / 模型训练流水线  |  版本: v1.0.0  |  日期: 2026-11-20
"""
训练流水线：从数据库加载真实数据训练两个模型
  1. 等待时间预测模型（三层架构）-> wait_time_model.pkl
  2. 异常检测模型（IsolationForest）-> anomaly_model.pkl

对比基线：规则公式 / 统计校正 / LightGBM 三层各自的 MAE
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import psycopg2
import joblib

from wait_time_predictor import WaitTimePredictor, RuleBasedWaitEstimator, FEATURE_COLS
from anomaly_detector import AnomalyDetector

DB_CONFIG = {
    "dbname":   "bathroom_female_db",
    "user":     "postgres",
    "password": os.environ.get("BATH_DB_PASSWORD", ""),
    "host":     "localhost",
    "port":     "5432",
}


# ---------- 1. 从数据库加载日志 ----------
def load_logs(pg_conn) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM shower_logs ORDER BY start_time", pg_conn)
    df['start_time'] = pd.to_datetime(df['start_time'])
    return df


# ---------- 2. 构造训练数据集（避免泄漏） ----------
def build_wait_dataset(df: pd.DataFrame) -> pd.DataFrame:
    """
    使用数据库里已有字段 + 必要派生；避免用 end_time / duration_seconds / water_usage
    （这些只在用户已完成洗浴后才知道，线上无法实时获得）。
    """
    df = df.sort_values('start_time').reset_index(drop=True)
    df['duration_min'] = df['duration_seconds'] / 60.0

    # 历史洗澡时长：同性别、滚 100 条、shift(1)
    df['avg_duration'] = (
        df.groupby('gender')['duration_min']
          .apply(lambda x: x.shift(1).rolling(100, min_periods=1).mean())
          .reset_index(level=0, drop=True)
    )
    df['avg_duration'] = df['avg_duration'].fillna(df['duration_min'].mean())

    # 故障区域标记：同一时刻是否有隔间已在故障窗口内
    # 简化口径：用该条日志本身的 is_fault 作为代理（故障窗口内 is_fault=True）
    df['is_fault_area'] = df['is_fault'].astype(int)

    out = pd.DataFrame({
        'queue_len':    df['queue_length_at_start'].astype(float),
        'hour':         df['hour_of_day'].astype(int),
        'temperature':  df['temperature'].astype(float),
        'avg_duration': df['avg_duration'].astype(float),
        'free_stalls':  df['idle_stalls_at_start'].astype(float),
        'gender':       df['gender'],
        'is_weekend':   df['is_weekend'].astype(int),
        'is_fault_area': df['is_fault_area'].astype(int),
        'wait_time':    df['wait_seconds'].astype(float) / 60.0,
    })
    return out.dropna()


# ---------- 3. 训练等待时间模型 ----------
def train_wait_model(df: pd.DataFrame):
    ds = build_wait_dataset(df)
    print(f"[Train] 等待时间训练集: {len(ds)} 条 "
          f"(wait_time>0: {(ds['wait_time'] > 0).sum()}, "
          f"平均 {ds['wait_time'].mean():.2f} min)")

    predictor = WaitTimePredictor()
    meta = predictor.train(ds)

    predictor.save('wait_time_model.pkl')
    print(f"[Train] 三层等待模型已保存 -> wait_time_model.pkl")
    print(f"[Train]  验证集 MAE: 规则 {meta['mae_rule']:.3f} / "
          f"统计 {meta['mae_stat']:.3f} / ML {meta['mae_ml']:.3f} 分钟")
    print(f"[Train] 特征重要性(ML层):")
    for k, v in sorted(predictor.feature_importance().items(),
                       key=lambda x: -x[1]):
        print(f"          {k:20s}: {v:.2f}%")
    return predictor, meta


# ---------- 4. 训练异常检测模型 ----------
def train_anomaly_model(df: pd.DataFrame):
    normal = df[~df['is_fault']]
    X = np.column_stack([
        normal['duration_seconds'] / 60.0,
        normal['water_usage'],
        normal['hour_of_day'],
    ])
    print(f"[Train] 异常检测训练集: {len(X)} 条正常样本")

    detector = AnomalyDetector()
    detector.train(X)

    # 故障样本召回率（规则+3σ+IsolationForest 全部启用）
    faults = df[df['is_fault']]
    if len(faults) > 0:
        hist = (normal['duration_seconds'] / 60.0).tolist()
        detected = sum(
            detector.detect(r['duration_seconds'] / 60.0,
                            r['water_usage'], r['hour_of_day'],
                            history_durations=hist)['is_anomaly']
            for _, r in faults.iterrows()
        )
        recall = detected / len(faults)
        print(f"[Train] 异常检测召回率: {recall:.2%} ({detected}/{len(faults)})")

    joblib.dump(detector, 'anomaly_model.pkl')
    print("[Train] 异常检测模型已保存 -> anomaly_model.pkl")
    return detector


# ---------- 5. 三种基线的对比验证 ----------
def run_baseline_comparison(ds: pd.DataFrame):
    """用时间切分法，三种基线在同一验证集上的 MAE"""
    from sklearn.metrics import mean_absolute_error

    X = WaitTimePredictor().build_features(ds)
    y = ds['wait_time'].values.astype(float)
    n = int(len(X) * 0.8)
    X_va, y_va = X[n:], y[n:]

    # 规则层
    rb = RuleBasedWaitEstimator()
    pred_rule = rb.predict(X_va)

    # 统计层
    predictor = WaitTimePredictor(use_ml=False)
    meta = predictor.train(ds)  # 只训练前 80%，验证也是后 20%
    pred_stat = predictor.calibrator.predict(X_va)

    mae1 = mean_absolute_error(y_va, pred_rule)
    mae2 = mean_absolute_error(y_va, pred_stat)

    # 单独训练 ML 层做对比
    predictor_ml = WaitTimePredictor(use_ml=True)
    meta_ml = predictor_ml.train(ds)
    mae3 = meta_ml['mae_ml']

    best = min([("规则公式", mae1), ("统计校正", mae2), ("LightGBM", mae3)],
               key=lambda x: x[1])
    print(f"\n=== 基线对比（时间切分验证集，{len(y_va)} 条）===")
    print(f"  规则公式    MAE = {mae1:.3f} min")
    print(f"  统计校正    MAE = {mae2:.3f} min")
    print(f"  LightGBM    MAE = {mae3:.3f} min  ← 最优: {best[0]}")


# ==================== 主流程 ====================
if __name__ == '__main__':
    # 优先从 PostgreSQL 读取；连不上则 fallback 到同目录的 CSV
    csv_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "shower_logs_new.csv")
    try:
        pg = psycopg2.connect(**DB_CONFIG)
        df = load_logs(pg)
        pg.close()
        print(f"[Load] 来源 PostgreSQL | shower_logs = {len(df)} 条")
    except Exception as e:
        print(f"[Load] PostgreSQL 不可用 ({type(e).__name__})，"
              f"fallback 读取 CSV: {csv_path}")
        df = pd.read_csv(csv_path)
        # CSV 导出时 PostgreSQL boolean 列会变成 't'/'f' 字符串，统一转 bool
        for col in df.columns:
            s = df[col].astype(str).str.lower()
            if s.isin(['t', 'f', 'true', 'false', '1', '0']).all() \
                    and s.nunique() <= 2:
                df[col] = s.isin(['t', 'true', '1'])
        if 'start_time' in df.columns:
            df['start_time'] = pd.to_datetime(df['start_time'], utc=True) \
                .dt.tz_convert('Asia/Shanghai')
    print(f"       故障 {df['is_fault'].sum()} 条 "
          f"({df['is_fault'].mean():.2%})")

    predictor, meta = train_wait_model(df)

    ds = build_wait_dataset(df)
    run_baseline_comparison(ds)

    train_anomaly_model(df)
