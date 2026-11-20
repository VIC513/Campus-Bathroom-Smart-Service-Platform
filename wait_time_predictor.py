# -*- coding: utf-8 -*-
# 模块归属: 算法 / 等待时间预测  |  版本: v1.0.0  |  日期: 2026-11-20
"""
三层等待时间预测算法（仿《故障检测与异常预警设计》的三层架构）

第一层 规则基线 RuleBasedWaitEstimator
  排队时长 ≈ queue_len / (free_stalls + 1) * 历史平均时长
  优点：可解释、无需训练；缺点：高估且对高峰/冬季失真严重

第二层 统计校正 StatisticalCalibrator
  按 (时段段 × 队列长度段) 的历史均值校正规则基线；3σ 之外用回归填充
  优点：响应高峰/低谷的非线性；缺点：冷启动时样本稀疏

第三层 机器学习 LightGBM
  综合全部特征的非线性组合；只在前两层不可用时启用
  优点：在真实数据上 MAE 最低；缺点：需要训练数据、黑盒

特征列表（训练/预测必须严格一致，顺序固定）:
  queue_len         当前排队人数
  free_stalls       当前空闲隔间数
  load_per_stall    queue_len / (free_stalls + 1)  — 负载密度
  hour_seg          时段段 0=午休 1=课后 2=晚间 3=晚高峰 4=其他
  temperature       当前气温（仿真时生成）
  avg_duration      同性别历史平均洗澡时长（分钟，避免泄漏）
  is_peak           是否晚高峰 21-22 点
  is_fault_area     此刻是否有隔间处于故障窗口
  weekend           是否周末
  gender_F          是否女生（本项目固定 1，但保留接口）

持久化：
  WaitTimePredictor.save() / load() 把完整 Pipeline（预处理器 + 三层模型）
  序列化为 joblib，避免训练/推理特征不一致。
"""

import json
import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split
from sklearn.metrics import mean_absolute_error
import joblib

try:
    from lightgbm import LGBMRegressor
    _HAS_LGBM = True
except ImportError:
    from sklearn.ensemble import GradientBoostingRegressor
    _HAS_LGBM = False


# ============================================================
# 第一层：规则基线
# ============================================================
class RuleBasedWaitEstimator:
    """
    排队等待时间的可解释性基线：
      wait = queue_len / (free_stalls + 1) * avg_duration_in_minutes
    公式源自排队论中 G/G/c 系统的近似（c 个并行服务台，队列外排队人数 q），
    对本项目数据会系统性高估（因为实际有"放弃排队"行为）。
    """
    def fit(self, df=None):
        return self

    def predict(self, X: np.ndarray) -> np.ndarray:
        queue_len = X[:, 0]
        free_stalls = X[:, 1]
        avg_dur = X[:, 5]      # avg_duration 是第 6 列
        free_safe = np.clip(free_stalls, 0.5, None)
        wait = queue_len / free_safe * avg_dur
        return np.clip(wait, 0, None)


# ============================================================
# 第二层：统计校正
# ============================================================
class StatisticalCalibrator:
    """
    按 (时段段 × 队列长度段) 查表得到历史平均等待时间，
    若该单元格样本过少 (< min_samples)，回退到全局基线。
    同时保留一个 (queue_len, hour_seg) 上的线性回归做冷启动兜底。
    """
    def __init__(self, min_samples: int = 10):
        self.min_samples = min_samples
        self._table = None        # DataFrame 行键 = (hour_seg, q_bin)
        self._global_mean = 0.0
        self._coef_q = 0.0
        self._coef_h = 0.0
        self._intercept = 0.0

    def fit(self, df: pd.DataFrame):
        df = df.copy()
        df['q_bin'] = pd.cut(df['queue_len'].astype(int),
                             bins=[-0.5, 0.5, 1.5, 3.5, 5.5, 20.5],
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
                       bins=[-0.5, 0.5, 1.5, 3.5, 5.5, 20.5],
                       labels=[0, 1, 2, 3, 4])[0]
        key = (int(hour_seg), q_bin)
        if key in self._table:
            return float(self._table[key])
        # 冷启动
        return max(0.0,
                   self._coef_q * queue_len
                   + self._coef_h * is_peak
                   + self._intercept)

    def predict(self, X: np.ndarray) -> np.ndarray:
        outs = np.zeros(len(X))
        for i in range(len(X)):
            outs[i] = self.predict_row(X[i, 0], X[i, 3], X[i, 7])   # queue_len, hour_seg, is_peak
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
    'hour_seg', 'temperature', 'avg_duration',
    'is_peak', 'is_fault_area', 'weekend', 'gender_F',
]


class WaitTimePredictor:
    """
    三层排队时长预测器：规则 → 统计校正 → ML。
    feature_schema 字段名与顺序固定；训练/推理必须用同一个 build_features。
    """

    def __init__(self, ml_backend: str = 'auto', use_ml: bool = True):
        self.baseline = RuleBasedWaitEstimator()
        self.calibrator = StatisticalCalibrator(min_samples=10)
        self.ml = MLWaitModel() if use_ml else None
        self.use_ml = use_ml
        self.is_trained = False
        self.feature_cols = list(FEATURE_COLS)
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
        输入 DataFrame 需含列:
          queue_len / hour / temperature / avg_duration / free_stalls / gender
          以及可选: is_weekend, is_fault_area（没有就用 0 填充）
        """
        f = pd.DataFrame()
        f['queue_len'] = df['queue_len'].astype(float)
        f['free_stalls'] = df['free_stalls'].astype(float)
        f['load_per_stall'] = df['queue_len'].astype(float) / (df['free_stalls'].astype(float) + 1.0)
        f['hour_seg'] = df['hour'].apply(self.encode_hour).astype(float)
        f['temperature'] = df['temperature'].fillna(20.0).astype(float)
        f['avg_duration'] = df['avg_duration'].astype(float)
        f['is_peak'] = df['hour'].apply(lambda h: 1.0 if 21 <= h < 23 else 0.0)
        f['is_fault_area'] = (df['is_fault_area'] if 'is_fault_area' in df.columns
                               else pd.Series(0.0, index=df.index)).astype(float)
        f['weekend'] = (df['is_weekend'] if 'is_weekend' in df.columns
                        else (df['weekend'] if 'weekend' in df.columns
                              else pd.Series(0.0, index=df.index))).astype(float)
        f['gender_F'] = df['gender'].map({'M': 0.0, 'F': 1.0}).fillna(1.0).astype(float)
        X = f[self.feature_cols].values
        # 防 NaN / Inf
        X = np.nan_to_num(X, nan=0.0, posinf=1e3, neginf=0.0)
        return X

    # ---------- 训练 ----------
    def train(self, df: pd.DataFrame, label_col: str = 'wait_time') -> dict:
        X = self.build_features(df)
        y = df[label_col].values.astype(float)
        if np.any(np.isnan(y)):
            mask = ~np.isnan(y)
            X, y = X[mask], y[mask]

        # 时间切分 8:2（前 80% 天训练，后 20% 天验证）
        train_size = int(len(X) * 0.8)
        X_tr, y_tr = X[:train_size], y[:train_size]
        X_va, y_va = X[train_size:], y[train_size:]

        # 层 1：规则基线（无状态）
        self.baseline.fit()

        # 层 2
        df_tr = self._reconstruct_df(X_tr, y_tr)
        self.calibrator.fit(df_tr)

        # 层 3
        if self.use_ml:
            self.ml.fit(X_tr, y_tr)

        # 验证集 MAE 对比
        mae_rule  = mean_absolute_error(y_va, self.baseline.predict(X_va))
        mae_stat  = mean_absolute_error(y_va, self.calibrator.predict(X_va))
        if self.use_ml:
            mae_ml = mean_absolute_error(y_va, self.ml.predict(X_va))
        else:
            mae_ml = float('nan')

        # 自动层选择：验证集上选最优（至少用规则层兜底）
        self.training_meta = {
            'n_train': len(y_tr), 'n_val': len(y_va),
            'mae_rule': round(float(mae_rule), 3),
            'mae_stat': round(float(mae_stat), 3),
            'mae_ml': round(float(mae_ml), 3) if self.use_ml else None,
            'backend': self.ml.backend if self.use_ml else 'none',
        }

        self.is_trained = True
        return self.training_meta

    def _reconstruct_df(self, X, y):
        """把特征矩阵+标签还原成带列名的 DataFrame（只用于 StatisticalCalibrator.fit）"""
        df = pd.DataFrame(X, columns=self.feature_cols)
        df['wait_time'] = y
        return df

    # ---------- 预测 ----------
    def predict(self, queue_len=None, hour=None, temperature=None,
                avg_duration=None, free_stalls=None, gender='F',
                is_weekend=False, is_fault_area=False,
                df: pd.DataFrame = None) -> float:
        """
        单条预测（参数版本）或批量预测（df 版本，df 列必须含 queue_len, hour, ...）
        返回预计等待分钟数（>=0，保留 1 位小数）。
        """
        if not self.is_trained:
            raise RuntimeError("模型尚未训练")

        if df is None:
            # 构造单条 DataFrame
            df = pd.DataFrame([{
                'queue_len': queue_len, 'hour': hour, 'temperature': temperature,
                'avg_duration': avg_duration, 'free_stalls': free_stalls,
                'gender': gender, 'is_weekend': int(bool(is_weekend)),
                'is_fault_area': int(bool(is_fault_area)),
            }])
        X = self.build_features(df)

        # 按验证集最优层选择（优先 ML，其次统计，最后规则）
        use_ml_best = (self.use_ml and self.training_meta.get('mae_ml') is not None
                       and self.training_meta['mae_ml']
                       <= self.training_meta['mae_stat'])
        if use_ml_best:
            wait = self.ml.predict(X)
        else:
            wait = self.calibrator.predict(X)
            # 若统计层表没命中（==全局均值时），回退到规则层
            stat_raw = wait
            rule_raw = self.baseline.predict(X)
            mask_global = np.isclose(stat_raw, self.training_meta.get('mae_stat', 0), atol=0.01)
            wait = np.where(mask_global, rule_raw, stat_raw)

        if wait.ndim == 0 or wait.shape[0] == 1:
            return max(0.0, round(float(np.atleast_1d(wait)[0]), 1))
        return np.round(np.clip(wait, 0, None), 1)

    # ---------- 持久化 ----------
    def save(self, path='wait_time_model.pkl'):
        joblib.dump({
            'baseline': self.baseline,
            'calibrator': self.calibrator,
            'ml': self.ml,
            'use_ml': self.use_ml,
            'is_trained': self.is_trained,
            'feature_cols': self.feature_cols,
            'training_meta': self.training_meta,
        }, path)

    def load(self, path='wait_time_model.pkl'):
        bundle = joblib.load(path)
        self.baseline = bundle['baseline']
        self.calibrator = bundle['calibrator']
        self.ml = bundle['ml']
        self.use_ml = bundle['use_ml']
        self.is_trained = bundle['is_trained']
        self.feature_cols = bundle.get('feature_cols', list(FEATURE_COLS))
        self.training_meta = bundle.get('training_meta', {})
        return self

    def feature_importance(self) -> dict:
        """返回 ML 层的特征重要性百分比（可解释性）"""
        if not (self.use_ml and self.is_trained):
            return {}
        importances = self.ml.model.feature_importances_
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
        'queue_len': np.random.randint(0, 15, n),
        'hour': np.random.choice(range(24), n),
        'temperature': np.random.uniform(5, 35, n),
        'avg_duration': np.random.normal(20, 4, n).clip(5, 35),
        'free_stalls': np.random.randint(1, 10, n),
        'gender': 'F',
        'is_weekend': np.random.choice([0, 1], n, p=[0.7, 0.3]),
        'is_fault_area': np.random.choice([0, 1], n, p=[0.94, 0.06]),
    })
    data['wait_time'] = (
        data['queue_len'] / (data['free_stalls'] + 1)
        * data['avg_duration']
        + np.random.normal(0, 1.5, n)
    ).clip(0, 40)

    predictor = WaitTimePredictor()
    meta = predictor.train(data)
    print("\n训练结果:", json.dumps(meta, indent=2, ensure_ascii=False))
    print("\n特征重要性:", json.dumps(predictor.feature_importance(), indent=2, ensure_ascii=False))
    print("\n单条预测示例:", predictor.predict(
        queue_len=8, hour=21, temperature=18,
        avg_duration=20.5, free_stalls=2, gender='F',
        is_weekend=False, is_fault_area=False
    ), "分钟")
