# -*- coding: utf-8 -*-
# 模块归属: 算法 / 集成演示  |  版本: v1.0.0  |  日期: 2026-11-20
"""
演示四个算法如何协同工作:
  排队取号 -> 预测等待 -> 异常预警 -> 分流推荐
全部在内存中运行（MemoryRedis + 合成训练数据），无需 PostgreSQL / Redis 服务
"""

import sys
import pathlib

import numpy as np
import pandas as pd

from wait_time_predictor import WaitTimePredictor
from anomaly_detector import AnomalyDetector
from queue_manager import QueueManager, MemoryRedis
from recommender import CrossAreaRecommender

# ---- 1. 训练等待时间预测器（合成数据演示；正式环境由 train_pipeline.py 训练） ----
np.random.seed(0)
n = 3000
df = pd.DataFrame({
    'queue_len': np.random.randint(0, 20, n),
    'hour': np.random.choice(range(24), n),
    'temperature': np.random.uniform(5, 35, n),
    'avg_duration': np.random.normal(15, 3, n).clip(5, 30),
    'free_stalls': np.random.randint(0, 10, n),
    'gender': np.random.choice(['M', 'F'], n),
})
df['wait_time'] = (df['queue_len'] / (df['free_stalls'] + 1)
                   * df['avg_duration'] + np.random.normal(0, 2, n)).clip(0, 120)
predictor = WaitTimePredictor()
predictor.train(df)

# ---- 2. 训练异常检测器 ----
normal_logs = np.column_stack([
    np.random.normal(15, 3, 1000).clip(5, 30),
    np.random.normal(80, 20, 1000).clip(20, 200),
    np.random.choice(range(24), 1000)
])
detector = AnomalyDetector()
detector.train(normal_logs)

# ---- 3. 初始化排队系统 ----
r = MemoryRedis()
qm = QueueManager(r, wait_predictor=predictor)
r.store['area:A:free_count'] = 1

# ---- 4. 用户取号（返回位次 + 预计等待） ----
res = qm.join('u1', 'A', hour=21, temperature=18, avg_duration=18.5, gender='F')
print("取号结果:", res)

# ---- 5. 异常检测扫描（发现异常 -> 自动标记维修中） ----
stalls = [
    {'stall_id': 'F01', 'duration_min': 12, 'water_usage_l': 85, 'hour': 21},
    {'stall_id': 'F02', 'duration_min': 75, 'water_usage_l': 90, 'hour': 21},
]
alerts = detector.scan_stalls(stalls)
for a in alerts:
    print(f"[预警] {a['stall_id']}: {a['reason']} -> 自动标记维修中")

# ---- 6. 跨区域分流推荐（A 区拥挤时推荐邻近区域） ----
rec = CrossAreaRecommender(
    {"A": {"walk_min": 0}, "B": {"walk_min": 3}, "C": {"walk_min": 8}})
current_wait = res['wait_time'] or 0
suggestion = rec.recommend(
    "A", current_wait_min=current_wait,
    area_waits={
        "A": current_wait,
        # 用同一预测器估算其他区域的等待（队列短、空闲多）
        "B": predictor.predict(queue_len=1, hour=21, temperature=18,
                               avg_duration=18.5, free_stalls=6, gender='F'),
        "C": predictor.predict(queue_len=2, hour=21, temperature=18,
                               avg_duration=18.5, free_stalls=5, gender='F'),
    })
print("分流推荐:", suggestion)

# ---- 7. 隔间释放叫号 ----
print("叫号:", qm.on_stall_freed('A'))
