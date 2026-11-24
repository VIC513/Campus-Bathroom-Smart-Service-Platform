# -*- coding: utf-8 -*-
# 模块归属: 算法 / 异常检测  |  版本: v1.0.0  |  日期: 2026-11-20
"""
异常检测与故障预警算法
依赖: pip install scikit-learn numpy

在线口径说明:
  本模块只使用传感器可得的时长 / 用水量 / 时段特征，离线验证对 shower_logs 的
  故障召回仅约 1.6%（水温异常、门锁损坏等用户感知类故障无法由传感器识别）。
  因此线上故障判定必须以用户反馈为唯一依据，本模块仅用于离线验证与内部巡检，
  暂不接入正式告警接口；water_usage_l=None 时进入降级模式（返回 degraded=True）。
"""

import numpy as np
from sklearn.ensemble import IsolationForest
from datetime import datetime


class AnomalyDetector:
    """
    三层组合策略:
      1. 规则基线: 超长占用 / 异常用水
      2. 统计离群: 3σ 原则（需要传入历史时长）
      3. 无监督 ML: Isolation Forest
    """

    def __init__(self, max_duration_min=60, sigma_k=3.0):
        self.max_duration_min = max_duration_min
        self.sigma_k = sigma_k
        self.iso = IsolationForest(contamination=0.01, random_state=42)
        self.is_trained = False
        self._water_median = None   # 训练集中用水量中位数，用于缺失时兜底

    # ---------- 训练 ----------
    def train(self, normal_logs: np.ndarray):
        """
        normal_logs: shape (n, 3)
          每行 = [duration_min, water_usage_l, hour]
        只用“正常”样本训练（无监督）
        """
        self.iso.fit(normal_logs)
        self._water_median = float(np.median(normal_logs[:, 1]))
        self.is_trained = True
        print(f"[AnomalyDetector] IsolationForest 训练完成, 样本数={len(normal_logs)}, "
              f"用水量中位数={self._water_median:.1f}L")

    # ---------- 单条检测 ----------
    def detect(self, duration_min, water_usage_l=None, hour=0,
               history_durations=None) -> dict:
        """
        water_usage_l 传 None 时进入降级模式（无实时用水量数据）：
          - 跳过“用水量偏低/偏高”规则
          - ML 层用训练集中位数代替，结论仅供参考
          - 时长规则与 3σ 离群仍然生效
        返回: {'is_anomaly': bool, 'reason': str, 'score': float,
               'degraded': bool, 'timestamp': str}
        """
        reasons = []
        degraded = water_usage_l is None
        median = getattr(self, '_water_median', None)   # 兼容旧版 pkl 模型
        water_for_ml = median if degraded else water_usage_l
        if degraded and water_for_ml is None:
            water_for_ml = 80.0   # 模型未训练且无中位数时的保守兜底

        # 1. 规则基线
        if duration_min > self.max_duration_min:
            reasons.append(f"超长占用({duration_min:.0f}min > {self.max_duration_min}min)")

        if not degraded:
            if water_usage_l < 5 and duration_min > 5:
                reasons.append("用水量异常偏低，疑似阀门故障")
            if water_usage_l > 500:
                reasons.append("用水量异常偏高，疑似漏水")

        # 2. 统计离群 (3σ)
        if history_durations and len(history_durations) > 10:
            mu = np.mean(history_durations)
            sigma = np.std(history_durations)
            if sigma > 0 and duration_min > mu + self.sigma_k * sigma:
                reasons.append(
                    f"时长离群({duration_min:.0f}min > μ+3σ={mu + 3 * sigma:.1f})"
                )

        # 3. 无监督 ML
        score = 0.0
        if self.is_trained:
            x = np.array([[duration_min, water_for_ml, hour]])
            pred = self.iso.predict(x)[0]
            score = float(self.iso.score_samples(x)[0])
            if pred == -1:
                tag = "用水量缺失，按中位数估算，" if degraded else ""
                reasons.append(f"{tag}IsolationForest 判定异常(score={score:.3f})")

        is_anomaly = bool(reasons)
        return {
            'is_anomaly': is_anomaly,
            'reason': '; '.join(reasons) if reasons else '正常',
            'score': score,
            'degraded': degraded,
            'timestamp': datetime.now().isoformat()
        }

    # ---------- 批量扫描 ----------
    def scan_stalls(self, stalls: list, history_durations=None) -> list:
        """
        stalls: [{'stall_id': 'F01', 'duration_min': 65,
                  'water_usage_l': 3, 'hour': 21}, ...]
        history_durations: 可选，传入后 3σ 规则生效
        返回所有异常隔间列表
        """
        alerts = []
        for s in stalls:
            r = self.detect(
                s['duration_min'], s.get('water_usage_l'), s['hour'],
                history_durations=history_durations,
            )
            if r['is_anomaly']:
                alerts.append({**s, **r})
        return alerts


# ================= 演示 =================
if __name__ == '__main__':
    np.random.seed(42)
    # 正常日志: 时长 ~ N(15,3), 用水 ~ N(80,20)
    normal = np.column_stack([
        np.random.normal(15, 3, 1000).clip(5, 30),
        np.random.normal(80, 20, 1000).clip(20, 200),
        np.random.choice(range(24), 1000)
    ])

    detector = AnomalyDetector()
    detector.train(normal)

    # 测试用例
    cases = [
        {'stall_id': 'F01', 'duration_min': 12, 'water_usage_l': 85, 'hour': 21},
        {'stall_id': 'F02', 'duration_min': 75, 'water_usage_l': 90, 'hour': 21},
        {'stall_id': 'F03', 'duration_min': 20, 'water_usage_l': 2,  'hour': 20},
        {'stall_id': 'F04', 'duration_min': 18, 'water_usage_l': 600, 'hour': 19},
    ]
    alerts = detector.scan_stalls(cases)
    for a in alerts:
        print(f"[预警] {a['stall_id']}: {a['reason']}")
