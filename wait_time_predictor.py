# -*- coding: utf-8 -*-
# 模块归属: 算法 / 等待时间预测  |  版本: v1.2.0  |  日期: 2026-11-24
"""
三层等待时间预测算法（仿《故障检测与异常预警设计》的三层架构）

第一层 规则基线 RuleBasedWaitEstimator
  wait = (free==0 ? avg/(N+1) : 0) + max(queue-free, 0) / N * avg
  N = total_stalls（默认 15）。free=0 时 15 个隔间都在使用，队首等到“第一个
  释放”的期望时间约为 avg/(N+1)，而不是 avg/2（avg/2 假设只有一个隔间，
  会把短队列也高估数倍）。剩余队伍再按 N 个隔间并行消化。
  avg 使用固定常量 avg_duration_min（默认 20.69 分钟，问卷口径中位数），
  不依赖线上滚动均值，因此规则层在缺少历史时长数据源时也能独立工作。
  优点：可解释、无需训练、不发散（上限 60 分钟）；缺点：未建模放弃排队，
  高负载下仍偏高，这部分由统计层与 ML 层承担。

第二层 统计校正 StatisticalCalibrator
  按 (时段段 × 队列长度段) 的历史均值校正规则基线；未命中的单元格用回归兜底
  优点：响应高峰/低谷的非线性；缺点：冷启动时样本稀疏

第三层 机器学习 LightGBM
  综合全部特征的非线性组合；只在验证集上明显优于统计层时作为默认输出
  优点：在真实数据上 MAE 最低；缺点：需要训练数据、黑盒
  若 queue_len 超出训练上界，predict() 会把超出部分按
  “净排队人数 ÷ 总隔间数 × 固定平均时长”的排队论斜率外推，避免高负载饱和。

特征列表（7 个，训练/预测必须严格一致，顺序固定）:
  queue_len         当前排队人数
  free_stalls       当前空闲隔间数（取号时刻）
  load_per_stall    queue_len / (free_stalls + 1)  — 负载密度
  hour_seg          时段段 0=午休 1=课后 2=晚间 3=晚高峰 4=其他
  is_peak           是否晚高峰 21-22 点
  weekend           是否周末
  gender_F          是否女生（本项目固定 1，但保留接口）

版本沿革:
  v1.1.0 删除 is_fault_area：训练时该字段取自会话自身的 is_fault（后验信息，
         存在目标泄漏），线上取号时刻只能拿到“区域是否有故障隔间”，语义不一致。
  v1.2.0 正式切换为 7 特征轻量模型：同时删除 temperature（线上无数据源）与
         avg_duration（线上无滚动均值数据源）。两者在 v1.1.0 的消融实验中
         删除后整体 MAE 反而下降，故不再作为正式推理特征。
         predict() 保留 temperature / avg_duration / is_fault_area 三个同名参数，
         仅为兼容旧调用，传入值全部忽略。

持久化：
  WaitTimePredictor.save() / load() 把完整 Pipeline（预处理器 + 三层模型）
  序列化为 joblib，避免训练/推理特征不一致。
"""

import json
from datetime import datetime

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error
import joblib


# 模型/代码版本标记：写入模型包与清单，便于新旧版本对照
VERSION = 'v1.2.0'
MODEL_FORMAT_VERSION = 1

try:
    from lightgbm import LGBMRegressor
    _HAS_LGBM = True
except ImportError:
    from sklearn.ensemble import GradientBoostingRegressor
    _HAS_LGBM = False


# 固定平均洗浴时长（分钟）：问卷口径中位数，用于规则层与外推斜率，
# 不依赖线上滚动均值。后端如需调整，可通过构造参数覆盖。
DEFAULT_AVG_DURATION_MIN = 20.69


def rule_fallback_wait(queue_len, free_stalls, total_stalls=15,
                       avg_duration_min=DEFAULT_AVG_DURATION_MIN,
                       max_wait_min=60.0) -> float:
    """
    模型加载失败 / 推理异常时的规则降级入口（无状态、无需训练）。

    与第一层规则基线完全一致的公式：
      q_eff = max(queue_len - free_stalls, 0)
      wait  = (free == 0 ? avg/(N+1) : 0) + q_eff / N * avg
    非法输入按边界截断，保证任何情况下都能返回一个非负分钟数。
    """
    try:
        q = float(queue_len) if queue_len is not None else 0.0
        f = float(free_stalls) if free_stalls is not None else 0.0
    except (TypeError, ValueError):
        q, f = 0.0, 0.0
    n = max(int(total_stalls), 1)
    avg = float(avg_duration_min)
    cap = float(max_wait_min)
    q = min(max(q, 0.0), 1e4)
    f = min(max(f, 0.0), float(n))
    q_eff = max(q - f, 0.0)
    wait = (0.0 if f > 0 else avg / (n + 1.0)) + q_eff / n * avg
    return round(float(min(max(wait, 0.0), cap)), 1)


def _metrics(y, p) -> dict:
    """统一指标：MAE / RMSE / 平均有符号误差（预测-真实，正=高估）"""
    y = np.asarray(y, dtype=float)
    p = np.asarray(p, dtype=float)
    return {
        'n': int(len(y)),
        'mae': round(float(np.mean(np.abs(p - y))), 4),
        'rmse': round(float(np.sqrt(np.mean((p - y) ** 2))), 4),
        'bias': round(float(np.mean(p - y)), 4),
    }


# ============================================================
# 第一层：规则基线
# ============================================================
class RuleBasedWaitEstimator:
    """
    排队等待时间的可解释性基线（v1.2.0）：

      q_eff  = max(queue_len - free_stalls, 0)     # 空闲隔间先吃掉一部分队伍
      wait   = (free == 0 ? avg/(N+1) : 0) + q_eff / N * avg

    · avg 为固定常量 avg_duration_min（默认 20.69 分钟），不读取特征列，
      因此后端无需提供历史平均时长也能得到稳定的规则输出。
    · free=0 时 15 个隔间都在使用，“第一个释放”的期望时间约为 avg/(N+1)；
      旧实现用 avg/2 相当于假设只有 1 个隔间在工作，会把 q=1~5 高估数倍。
    · 剩余队伍按 N 个隔间并行消化（每人平均占用 avg/N 分钟）。
    · free >= queue 时 q_eff=0、无残余等待，等待为 0。
    · max_wait_min 只作为展示上界（默认 60 分钟），保证分布外输入不发散。
    · 未建模“放弃排队”，因此高负载下仍是保守上界；最终输出由 ML 层承担。
    """

    def __init__(self, total_stalls: int = 15,
                 avg_duration_min: float = DEFAULT_AVG_DURATION_MIN,
                 max_wait_min: float = 60.0):
        self.total_stalls = max(int(total_stalls), 1)
        self.avg_duration_min = float(avg_duration_min)
        self.max_wait_min = float(max_wait_min)
        self.feature_cols = None        # 由 WaitTimePredictor.bind 注入

    def bind(self, feature_cols):
        """绑定特征列顺序，predict 时按列名取列，避免索引错位"""
        self.feature_cols = list(feature_cols)
        return self

    def _col(self, X: np.ndarray, name: str) -> np.ndarray:
        cols = self.feature_cols if self.feature_cols else FEATURE_COLS
        return X[:, cols.index(name)]

    def fit(self, df=None):
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        queue_len = self._col(X, 'queue_len')
        free_stalls = np.clip(self._col(X, 'free_stalls'), 0.0, None)
        avg_dur = float(self.avg_duration_min)
        q_eff = np.maximum(queue_len - free_stalls, 0.0)
        # N 个隔间全忙时，队首等到第一个释放的期望时间 ≈ avg/(N+1)
        residual = np.where(free_stalls > 0, 0.0,
                            avg_dur / (float(self.total_stalls) + 1.0))
        wait = residual + q_eff / float(self.total_stalls) * avg_dur
        return np.clip(wait, 0.0, self.max_wait_min)


# ============================================================
# 第二层：统计校正
# ============================================================
class StatisticalCalibrator:
    """
    按 (时段段 × 队列长度段) 查表得到历史平均等待时间，
    若该单元格样本过少 (< min_samples)，回退到全局回归。
    （v1.2.0 起按特征列名取列，不再依赖硬编码索引。）
    """

    def __init__(self, min_samples: int = 10):
        self.min_samples = min_samples
        self.feature_cols = None
        self._table = None        # 行键 = (hour_seg, q_bin)
        self._global_mean = 0.0
        self._coef_q = 0.0
        self._coef_h = 0.0
        self._intercept = 0.0

    def bind(self, feature_cols):
        self.feature_cols = list(feature_cols)
        return self

    def fit(self, df: pd.DataFrame):
        df = df.copy()
        df['q_bin'] = pd.cut(df['queue_len'].astype(int),
                             bins=[-0.5, 0.5, 1.5, 3.5, 5.5, 60.5],
                             labels=[0, 1, 2, 3, 4])
        grouped = (df.groupby(['hour_seg', 'q_bin'], observed=True)['wait_time']
                     .agg(['mean', 'count']))
        self._table = grouped[grouped['count'] >= self.min_samples]['mean'].to_dict()
        self._global_mean = float(df['wait_time'].mean())

        # 冷启动兜底：wait ≈ a * queue_len + b * is_peak + c
        from sklearn.linear_model import LinearRegression
        X_sub = df[['queue_len', 'is_peak']].values
        y_sub = df['wait_time'].values
        reg = LinearRegression().fit(X_sub, y_sub)
        self._coef_q = float(reg.coef_[0])
        self._coef_h = float(reg.coef_[1])
        self._intercept = float(reg.intercept_)
        return self

    def predict_row(self, queue_len, hour_seg, is_peak):
        q_bin = pd.cut([int(queue_len)],
                       bins=[-0.5, 0.5, 1.5, 3.5, 5.5, 60.5],
                       labels=[0, 1, 2, 3, 4])[0]
        key = (int(hour_seg), q_bin)
        if key in self._table:
            return float(self._table[key])
        return max(0.0,
                   self._coef_q * queue_len
                   + self._coef_h * is_peak
                   + self._intercept)

    def predict(self, X: np.ndarray) -> np.ndarray:
        cols = self.feature_cols if self.feature_cols else FEATURE_COLS
        i_q, i_h, i_p = (cols.index('queue_len'),
                         cols.index('hour_seg'),
                         cols.index('is_peak'))
        outs = np.zeros(len(X))
        for i in range(len(X)):
            outs[i] = self.predict_row(X[i, i_q], X[i, i_h], X[i, i_p])
        return outs


# ============================================================
# 第三层：LightGBM / GBDT
# ============================================================
class MLWaitModel:
    def __init__(self):
        if _HAS_LGBM:
            self.model = LGBMRegressor(
                n_estimators=400, learning_rate=0.03, max_depth=7,
                num_leaves=31, min_child_samples=20,
                subsample=0.9, colsample_bytree=0.9,
                random_state=42, verbose=-1,
            )
            self.backend = "LightGBM"
        else:
            self.model = GradientBoostingRegressor(
                n_estimators=300, learning_rate=0.03, max_depth=5,
                min_samples_leaf=20, min_samples_split=40,
                random_state=42,
            )
            self.backend = "sklearn-GBDT"
        self.is_trained = False

    def fit(self, X, y):
        self.model.fit(X, y)
        self.is_trained = True

    def predict(self, X) -> np.ndarray:
        return np.clip(self.model.predict(X), 0, None)


# ============================================================
# 统一入口：封装三层 + 特征工程 + 序列化
# ============================================================
FEATURE_COLS = [
    'queue_len', 'free_stalls', 'load_per_stall',
    'hour_seg', 'is_peak', 'weekend', 'gender_F',
]

# 特征契约：供后端对齐输入字段（含义 / 单位 / 取值范围 / 默认值）
# required=True 表示后端必须提供；False 表示缺省时由 build_features 自动填充
FEATURE_SPEC = {
    'queue_len': {
        'desc': '当前排队人数（不含正在洗浴者）', 'unit': '人',
        'range': [0, 200], 'train_range': [0, 47],
        'default': 0, 'required': True, 'used_in_model': True,
    },
    'free_stalls': {
        'desc': '取号时刻的空闲隔间数（可用且无人使用）', 'unit': '个',
        'range': [0, 15], 'train_range': [0, 15],
        'default': 0, 'required': True, 'used_in_model': True,
        'note': '训练数据已修正为“到达/取号时刻”的口径，排队用户为 0',
    },
    'load_per_stall': {
        'desc': '派生特征 = queue_len / (free_stalls + 1)，无需后端传入', 'unit': '人/隔间',
        'range': [0, 200], 'train_range': [0, 47],
        'default': None, 'required': False, 'used_in_model': True,
    },
    'hour_seg': {
        'desc': '时段编码：0=午休(12-14) 1=课后(17-19) 2=晚间(19-21) 3=晚高峰(21-23) 4=其他',
        'unit': '枚举', 'range': [0, 4], 'train_range': [0, 4],
        'default': 4, 'required': False, 'used_in_model': True, 'note': '由 hour 派生',
    },
    'is_peak': {
        'desc': '是否晚高峰（21:00-23:00）', 'unit': '0/1',
        'range': [0, 1], 'train_range': [0, 1],
        'default': 0, 'required': False, 'used_in_model': True, 'note': '由 hour 派生',
    },
    'weekend': {
        'desc': '是否周末', 'unit': '0/1',
        'range': [0, 1], 'train_range': [0, 1],
        'default': 0, 'required': False, 'used_in_model': True,
    },
    'gender_F': {
        'desc': '是否女生', 'unit': '0/1',
        'range': [0, 1], 'train_range': [1, 1],
        'default': 1, 'required': False, 'used_in_model': True,
        'note': '本项目为女生宿舍楼，固定为 1',
    },
    # ---------- 以下字段自 v1.2.0 起不参与模型计算，仅保留在契约中供旧调用参考 ----------
    'temperature': {
        'desc': '【v1.2.0 已从特征集删除】原室外气温', 'unit': '℃',
        'range': [-20, 45], 'train_range': [-2.4, 30.9],
        'default': 20.0, 'required': False, 'used_in_model': False,
        'note': '线上无实时数据源；v1.1.0 消融显示删除后整体 MAE 下降 0.011 分钟，'
                '故不再作为正式推理特征。predict() 仍接受同名参数但忽略。',
    },
    'avg_duration': {
        'desc': '【v1.2.0 已从特征集删除】原同性别最近 100 条滚动平均时长', 'unit': '分钟',
        'range': [2, 90], 'train_range': [17.7, 28.1],
        'default': 20.69, 'required': False, 'used_in_model': False,
        'note': '线上只有配置常量、没有滚动均值；规则层改用固定常量 20.69 分钟，'
                'ML 层不再依赖该特征。predict() 仍接受同名参数但忽略。',
    },
    'is_fault_area': {
        'desc': '【v1.1.0 已从特征集删除】原“区域故障标记”', 'unit': '0/1',
        'range': [0, 1], 'train_range': [0, 1],
        'used_in_model': False, 'required': False,
        'note': '训练时该字段取自会话自身的 is_fault（后验信息，存在目标泄漏），'
                '线上取号时刻只能拿到区域级故障状态，语义不一致。'
                'predict() 仍保留同名参数以兼容旧调用，传入值会被忽略。',
    },
}


class WaitTimePredictor:
    """
    三层排队时长预测器：规则 → 统计校正 → ML（7 特征）。
    feature_schema 字段名与顺序固定；训练/推理必须用同一个 build_features。
    """

    def __init__(self, ml_backend: str = 'auto', use_ml: bool = True,
                 total_stalls: int = 15, max_wait_min: float = 60.0,
                 avg_duration_min: float = DEFAULT_AVG_DURATION_MIN,
                 min_ml_gain_min: float = 0.02):
        self.total_stalls = max(int(total_stalls), 1)
        self.max_wait_min = float(max_wait_min)
        self.avg_duration_min = float(avg_duration_min)
        self.min_ml_gain_min = float(min_ml_gain_min)
        self.feature_cols = list(FEATURE_COLS)
        self.baseline = RuleBasedWaitEstimator(
            total_stalls=self.total_stalls,
            avg_duration_min=self.avg_duration_min,
            max_wait_min=self.max_wait_min).bind(self.feature_cols)
        self.calibrator = StatisticalCalibrator(min_samples=10).bind(self.feature_cols)
        self.ml = MLWaitModel() if use_ml else None
        self.use_ml = use_ml
        self.is_trained = False
        self.default_layer = 'statistical'
        self.training_meta = {}

    # ---------- 特征工程 ----------
    @staticmethod
    def encode_hour(hour: int) -> int:
        """时段编码，与问卷 Q2 一致：0 午休 / 1 课后 / 2 晚间 / 3 晚高峰 / 4 其他"""
        if 12 <= hour < 14:   return 0
        if 17 <= hour < 19:   return 1
        if 19 <= hour < 21:   return 2
        if 21 <= hour < 23:   return 3
        return 4

    def build_features(self, df: pd.DataFrame) -> np.ndarray:
        """
        输入 DataFrame 需含列: queue_len / free_stalls / hour / gender
        可选: is_weekend 或 weekend
        temperature / avg_duration / is_fault_area 自 v1.2.0 起不再读取。
        """
        f = pd.DataFrame(index=df.index)
        f['queue_len'] = df['queue_len'].astype(float)
        f['free_stalls'] = df['free_stalls'].astype(float)
        f['load_per_stall'] = (df['queue_len'].astype(float)
                              / (df['free_stalls'].astype(float) + 1.0))
        f['hour_seg'] = df['hour'].apply(self.encode_hour).astype(float)
        f['is_peak'] = df['hour'].apply(lambda h: 1.0 if 21 <= h < 23 else 0.0)
        f['weekend'] = (df['is_weekend'] if 'is_weekend' in df.columns
                        else (df['weekend'] if 'weekend' in df.columns
                              else pd.Series(0.0, index=df.index))).astype(float)
        f['gender_F'] = df['gender'].map({'M': 0.0, 'F': 1.0}).fillna(1.0).astype(float)
        X = f[self.feature_cols].values
        return np.nan_to_num(X, nan=0.0, posinf=1e3, neginf=0.0)

    # ---------- 训练 ----------
    def train(self, df: pd.DataFrame, label_col: str = 'wait_time',
              group_col: str = None) -> dict:
        """
        训练三层模型。

        group_col: 若提供（如 scenario），则在每个分组内按时间顺序做 8:2 切分，
                   再合并训练/验证，保证“普通场景”和“压力场景”都有独立的
                   留出集，评价不会被另一场景稀释。
        """
        df = df.reset_index(drop=True)
        use_group = bool(group_col and group_col in df.columns)

        if use_group:
            tr_parts, va_parts = [], []
            for _, sub in df.groupby(group_col, sort=False):
                sub = sub.reset_index(drop=True)
                n = int(len(sub) * 0.8)
                tr_parts.append(sub.iloc[:n])
                va_parts.append(sub.iloc[n:])
            df_tr = pd.concat(tr_parts, ignore_index=True)
            df_va = pd.concat(va_parts, ignore_index=True)
        else:
            n = int(len(df) * 0.8)
            df_tr = df.iloc[:n].reset_index(drop=True)
            df_va = df.iloc[n:].reset_index(drop=True)

        X_tr = self.build_features(df_tr)
        y_tr = df_tr[label_col].values.astype(float)
        X_va = self.build_features(df_va)
        y_va = df_va[label_col].values.astype(float)
        q_tr = df_tr['queue_len'].values.astype(float)
        q_va = df_va['queue_len'].values.astype(float)

        # 训练集排队长度上界：predict() 用它判断是否需要尾段外推
        queue_train_max = float(np.max(q_tr)) if len(q_tr) else 15.0

        # 层 1：规则基线（无状态）
        self.baseline.fit()

        # 层 2：统计校正
        df_tr_named = pd.DataFrame(X_tr, columns=self.feature_cols)
        df_tr_named['wait_time'] = y_tr
        self.calibrator.fit(df_tr_named)

        # 层 3：LightGBM
        if self.use_ml:
            self.ml.fit(X_tr, y_tr)

        pred_rule = self.baseline.predict(X_va)
        pred_stat = self.calibrator.predict(X_va)
        pred_ml = self.ml.predict(X_va) if self.use_ml else None

        m_rule = _metrics(y_va, pred_rule)
        m_stat = _metrics(y_va, pred_stat)
        m_ml = _metrics(y_va, pred_ml) if self.use_ml else None

        # ML 相较统计层是否“有实际优势”：默认要求至少便宜 min_ml_gain_min 分钟
        ml_gain = (m_stat['mae'] - m_ml['mae']) if self.use_ml else -1.0
        if self.use_ml and ml_gain >= self.min_ml_gain_min:
            self.default_layer = 'ml'
        else:
            self.default_layer = 'statistical'

        # 高负载（queue>=6）单独评价
        high_mask = q_va >= 6

        def _seg(pred):
            if pred is None or not high_mask.any():
                return None
            return _metrics(y_va[high_mask], np.asarray(pred, float)[high_mask])

        # 分场景评价（普通 / 压力场景各自的留出集）
        groups = {}
        if use_group:
            g_va = df_va[group_col].astype(str).values
            for g in sorted(set(g_va)):
                gm = g_va == g
                hm = gm & (q_va >= 6)
                entry = {
                    'n': int(gm.sum()),
                    'rule': _metrics(y_va[gm], pred_rule[gm]),
                    'statistical': _metrics(y_va[gm], pred_stat[gm]),
                    'lightgbm': (_metrics(y_va[gm], pred_ml[gm])
                                 if pred_ml is not None else None),
                }
                if hm.any():
                    entry['high_load_q_ge6'] = {
                        'rule': _metrics(y_va[hm], pred_rule[hm]),
                        'statistical': _metrics(y_va[hm], pred_stat[hm]),
                        'lightgbm': (_metrics(y_va[hm], pred_ml[hm])
                                     if pred_ml is not None else None),
                    }
                groups[g] = entry

        self.training_meta = {
            'n_train': len(y_tr), 'n_val': len(y_va),
            'n_train_groups': ({str(k): int(v) for k, v in
                                df_tr.groupby(group_col).size().items()}
                               if use_group else None),
            'n_val_groups': ({str(k): int(v) for k, v in
                              df_va.groupby(group_col).size().items()}
                             if use_group else None),
            'metrics_val': {
                'rule': m_rule, 'statistical': m_stat, 'lightgbm': m_ml,
            },
            'mae_rule': m_rule['mae'],
            'mae_stat': m_stat['mae'],
            'mae_ml': m_ml['mae'] if m_ml else None,
            'ml_gain_vs_stat_min': round(float(ml_gain), 4),
            'default_layer': self.default_layer,
            'layer_selection_rule':
                f'ML 的 MAE 比统计层低至少 {self.min_ml_gain_min} 分钟时用 ML，否则用统计层',
            'backend': self.ml.backend if self.use_ml else 'none',
            'feature_cols': list(self.feature_cols),
            'avg_duration_min': self.avg_duration_min,
            'total_stalls': self.total_stalls,
            'max_wait_min': self.max_wait_min,
            'queue_train_max': queue_train_max,
            'high_load_q_ge6': {
                'rule': _seg(pred_rule),
                'statistical': _seg(pred_stat),
                'lightgbm': _seg(pred_ml),
            },
            'groups': groups,
        }

        self.is_trained = True
        return self.training_meta

    # ---------- 预测 ----------
    def _validate_single(self, queue_len, hour, free_stalls, gender) -> tuple:
        """
        单条输入校验与缺省填充，返回 (queue_len, hour, free_stalls, gender, warnings)。
          · hour 必填且必须为 0~23 整数，否则抛 ValueError（非法输入）
          · queue_len 缺失按 0、负值截断到 0
          · free_stalls 缺失按 0（保守）、负值截断、超过总隔间数截断
          · gender 非 M/F 时按 F 处理（本项目固定女生楼）
        """
        warnings = []
        if hour is None:
            hour = datetime.now().hour
            warnings.append('hour 缺失，已用当前系统小时替代')
        try:
            hour = int(hour)
        except (TypeError, ValueError):
            raise ValueError(f'hour 必须为 0~23 的整数，收到 {hour!r}')
        if not 0 <= hour <= 23:
            raise ValueError(f'hour 必须为 0~23 的整数，收到 {hour!r}')

        if queue_len is None:
            queue_len = 0
            warnings.append('queue_len 缺失，按 0 处理')
        try:
            queue_len = float(queue_len)
        except (TypeError, ValueError):
            raise ValueError(f'queue_len 必须为数值，收到 {queue_len!r}')
        if queue_len < 0:
            warnings.append(f'queue_len={queue_len} 为负，已截断为 0')
            queue_len = 0.0

        if free_stalls is None:
            free_stalls = 0
            warnings.append('free_stalls 缺失，按 0（保守）处理')
        try:
            free_stalls = float(free_stalls)
        except (TypeError, ValueError):
            raise ValueError(f'free_stalls 必须为数值，收到 {free_stalls!r}')
        if free_stalls < 0:
            warnings.append(f'free_stalls={free_stalls} 为负，已截断为 0')
            free_stalls = 0.0
        if free_stalls > self.total_stalls:
            warnings.append(f'free_stalls={free_stalls} 超过总隔间数 '
                            f'{self.total_stalls}，已截断')
            free_stalls = float(self.total_stalls)

        g = str(gender).strip().upper() if gender is not None else 'F'
        if g not in ('M', 'F'):
            warnings.append(f'gender={gender!r} 非法，已按 F 处理')
            g = 'F'
        return queue_len, hour, free_stalls, g, warnings

    def predict_detail(self, queue_len=None, hour=None, temperature=None,
                       avg_duration=None, free_stalls=None, gender='F',
                       is_weekend=False, is_fault_area=False) -> dict:
        """
        单条预测的详细结果（供契约样例与后端适配层使用）：
          {'wait_min': float, 'layer': 'ml'|'statistical'|'rule_fallback',
           'warnings': [...], 'features': {...}}
        temperature / avg_duration / is_fault_area 仅为兼容旧调用，传入值忽略。
        """
        q, h, f, g, warnings = self._validate_single(
            queue_len, hour, free_stalls, gender)
        if not self.is_trained:
            raise RuntimeError('模型尚未训练或加载失败，请改用 rule_fallback_wait')

        wait_min = self.predict(
            queue_len=q, hour=h, free_stalls=f, gender=g,
            is_weekend=is_weekend)
        layer = ('ml' if (self.default_layer == 'ml' and self.ml is not None)
                 else 'statistical')
        return {
            'wait_min': float(wait_min),
            'layer': layer,
            'warnings': warnings,
            'features': {
                'queue_len': q, 'free_stalls': f, 'hour': h, 'gender': g,
                'is_weekend': int(bool(is_weekend)),
            },
        }

    def safe_predict(self, queue_len=None, hour=None, temperature=None,
                     avg_duration=None, free_stalls=None, gender='F',
                     is_weekend=False, is_fault_area=False) -> dict:
        """
        生产环境推荐入口：模型未加载 / 推理异常时自动降级到规则层，
        保证接口始终返回可用结果，并在 warnings 中标注降级原因。
        """
        try:
            return self.predict_detail(
                queue_len=queue_len, hour=hour, temperature=temperature,
                avg_duration=avg_duration, free_stalls=free_stalls,
                gender=gender, is_weekend=is_weekend,
                is_fault_area=is_fault_area)
        except Exception as e:
            wait_min = rule_fallback_wait(
                queue_len, free_stalls, self.total_stalls,
                self.avg_duration_min, self.max_wait_min)
            return {
                'wait_min': float(wait_min),
                'layer': 'rule_fallback',
                'warnings': [f'预测异常，已降级到规则层：{type(e).__name__}: {e}'],
                'features': {
                    'queue_len': queue_len, 'free_stalls': free_stalls,
                    'hour': hour, 'gender': gender,
                    'is_weekend': int(bool(is_weekend)),
                },
            }

    def predict(self, queue_len=None, hour=None, temperature=None,
                avg_duration=None, free_stalls=None, gender='F',
                is_weekend=False, is_fault_area=False,
                df: pd.DataFrame = None) -> float:
        """
        单条预测（参数版本）或批量预测（df 版本，df 必须含 queue_len/free_stalls/hour/gender）。
        返回预计等待分钟数（>=0，保留 1 位小数）。

        temperature / avg_duration / is_fault_area 仅为兼容旧调用保留，
        v1.2.0 起不参与任何计算，传入任意值结果一致。
        """
        if not self.is_trained:
            raise RuntimeError("模型尚未训练")

        if df is not None:
            X = self.build_features(df)
        else:
            q, h, f, g, _ = self._validate_single(
                queue_len, hour, free_stalls, gender)
            X = self.build_features(pd.DataFrame([{
                'queue_len': q, 'hour': h, 'free_stalls': f,
                'gender': g, 'is_weekend': int(bool(is_weekend)),
            }]))

        if self.default_layer == 'ml' and self.ml is not None:
            base = np.asarray(self.ml.predict(X), dtype=float)
        else:
            base = np.asarray(self.calibrator.predict(X), dtype=float)

        # 尾段外推（所有层统一）：训练集没覆盖到的长队列按排队论斜率增长
        i_q = self.feature_cols.index('queue_len')
        i_f = self.feature_cols.index('free_stalls')
        q = X[:, i_q]
        free = np.clip(X[:, i_f], 0.0, None)
        q_eff = np.maximum(q - free, 0.0)
        train_max = float(self.training_meta.get('queue_train_max', 15.0))
        excess = np.maximum(q_eff - train_max, 0.0)
        if np.any(excess > 0):
            base = base + excess / float(self.total_stalls) * self.avg_duration_min

        # 统一上界，避免分布外输入给出无法运营的数值
        base = np.clip(base, 0.0, float(self.max_wait_min))

        if base.ndim == 0 or base.shape[0] == 1:
            return max(0.0, round(float(np.atleast_1d(base)[0]), 1))
        return np.round(base, 1)

    # ---------- 持久化 ----------
    def save(self, path='wait_time_model.pkl'):
        joblib.dump({
            'version': VERSION,
            'format_version': MODEL_FORMAT_VERSION,
            'baseline': self.baseline,
            'calibrator': self.calibrator,
            'ml': self.ml,
            'use_ml': self.use_ml,
            'is_trained': self.is_trained,
            'feature_cols': self.feature_cols,
            'training_meta': self.training_meta,
            'default_layer': self.default_layer,
            'total_stalls': self.total_stalls,
            'max_wait_min': self.max_wait_min,
            'avg_duration_min': self.avg_duration_min,
            'min_ml_gain_min': self.min_ml_gain_min,
        }, path)

    def load(self, path='wait_time_model.pkl'):
        try:
            bundle = joblib.load(path)
        except FileNotFoundError:
            raise FileNotFoundError(
                f'模型文件不存在: {path}；可改用 rule_fallback_wait() 降级')
        except Exception as e:
            raise RuntimeError(
                f'模型加载失败（{type(e).__name__}: {e}）；'
                f'请检查 Python/依赖版本（见 model_manifest.json），'
                f'或改用 rule_fallback_wait() 降级') from e
        self.model_version = bundle.get('version', 'unknown')
        self.baseline = bundle['baseline']
        self.calibrator = bundle['calibrator']
        self.ml = bundle['ml']
        self.use_ml = bundle['use_ml']
        self.is_trained = bundle['is_trained']
        self.feature_cols = bundle.get('feature_cols', list(FEATURE_COLS))
        self.training_meta = bundle.get('training_meta', {})
        self.default_layer = bundle.get('default_layer', 'statistical')
        self.total_stalls = int(bundle.get('total_stalls', 15))
        self.max_wait_min = float(bundle.get('max_wait_min', 60.0))
        self.avg_duration_min = float(
            bundle.get('avg_duration_min', DEFAULT_AVG_DURATION_MIN))
        self.min_ml_gain_min = float(bundle.get('min_ml_gain_min', 0.02))
        # 兼容旧模型包：补齐属性并按当前特征顺序重新绑定
        if not hasattr(self.baseline, 'total_stalls'):
            self.baseline.total_stalls = self.total_stalls
        if not hasattr(self.baseline, 'max_wait_min'):
            self.baseline.max_wait_min = self.max_wait_min
        if not hasattr(self.baseline, 'avg_duration_min'):
            self.baseline.avg_duration_min = self.avg_duration_min
        self.baseline.bind(self.feature_cols)
        self.calibrator.bind(self.feature_cols)
        return self

    def feature_importance(self, kind: str = 'split') -> dict:
        """
        返回 ML 层特征重要性百分比。
          kind='split'：特征被用于树节点分裂的次数占比（默认，兼容旧调用）
          kind='gain' ：特征带来的总增益占比（更能反映真实预测贡献）

        LightGBM 通过 booster_.feature_importance(importance_type=...) 获取；
        sklearn GBDT 无 gain 接口，gain 回退为 impurity 重要性。
        """
        if not (self.use_ml and self.is_trained):
            return {}
        model = self.ml.model
        if kind == 'gain' and hasattr(model, 'booster_'):
            importances = model.booster_.feature_importance(importance_type='gain')
        elif kind == 'split' and hasattr(model, 'booster_'):
            importances = model.booster_.feature_importance(importance_type='split')
        else:
            importances = model.feature_importances_
        importances = np.asarray(importances, dtype=float)
        total = importances.sum() or 1.0
        return {
            col: round(float(imp / total) * 100, 2)
            for col, imp in zip(self.feature_cols, importances)
        }


# ================= 演示 =================
if __name__ == '__main__':
    np.random.seed(42)
    n = 5000
    data = pd.DataFrame({
        'queue_len': np.random.randint(0, 20, n),
        'hour': np.random.choice(range(24), n),
        'free_stalls': np.random.randint(0, 10, n),
        'gender': 'F',
        'is_weekend': np.random.choice([0, 1], n, p=[0.7, 0.3]),
    })
    data['wait_time'] = (
        np.maximum(data['queue_len'] - data['free_stalls'], 0) / 15.0 * 20.69
        + np.random.normal(0, 1.5, n)
    ).clip(0, 40)

    predictor = WaitTimePredictor()
    meta = predictor.train(data)
    print("\n训练结果:", json.dumps(meta, indent=2, ensure_ascii=False))
    print("\n特征重要性:", json.dumps(predictor.feature_importance(), indent=2, ensure_ascii=False))
    print("\n单条预测示例:", predictor.predict(
        queue_len=18, hour=21, free_stalls=0, gender='F',
        is_weekend=False, temperature=20, avg_duration=18.83, is_fault_area=0
    ), "分钟")
