"""
数据仿真器（Simulator）
基于问卷数据，模拟浴室隔间状态变化
使用 Redis 存储隔间状态 + WebSocket 回调推送
成员1：后端 Lead & DevOps
"""

import random
import asyncio
import json
from datetime import datetime


# ============================================================
# 1. 仿真参数（基于问卷统计结果）
# ============================================================

SIM_CONFIG = {
    # 题 3：洗澡时长分布（秒）
    "avg_duration_seconds": 1130,    # 问卷加权平均：18.8 分钟
    "std_seconds": 450,              # 标准差约 7.5 分钟
    "min_duration": 300,             # 最短 5 分钟
    "max_duration": 2700,            # 最长 45 分钟

    # 题 2：24 小时到达概率权重（总和 = 1.0）
    "hourly_weights": [
        0.00, 0.00, 0.00, 0.00, 0.00, 0.00,  # 00-05
        0.01, 0.01, 0.01, 0.01, 0.01, 0.01,  # 06-11
        0.02, 0.03, 0.02, 0.03, 0.03, 0.05,  # 12-17
        0.15, 0.15, 0.21, 0.15, 0.05, 0.05,  # 18-23
    ],

    # 题 5：最大等待容忍（秒）
    "max_wait_seconds": 360,

    # 题 6：跨区推荐接受率（暂不用，多楼栋时再用）
    "accept_recommendation_rate": 0.75,

    # 仿真运行参数
    "tick_seconds": 5,
    "idle_to_occupied_prob": 0.1,
}


# ============================================================
# 2. 仿真器
# ============================================================

class Simulator:
    def __init__(self):
        self.running = False
        self.r = None
        self.task = None
        self.total_arrivals = 0
        self.total_completed = 0
        self.on_update = None        # WebSocket 回调

    def attach_redis(self, redis_client):
        """绑定 Redis 客户端"""
        self.r = redis_client

    def set_update_callback(self, callback):
        """设置状态变化回调（用于 WebSocket 推送）"""
        self.on_update = callback

    def generate_duration(self) -> int:
        """生成一个洗澡时长（秒）"""
        cfg = SIM_CONFIG
        d = int(random.gauss(cfg["avg_duration_seconds"], cfg["std_seconds"]))
        return max(cfg["min_duration"], min(d, cfg["max_duration"]))

    def get_hourly_prob(self) -> float:
        """根据当前小时，返回该小时的到达概率"""
        hour = datetime.now().hour
        weight = SIM_CONFIG["hourly_weights"][hour]
        return weight * SIM_CONFIG["idle_to_occupied_prob"] * 10

    async def run(self):
        """仿真主循环"""
        self.running = True
        print(f"[仿真器] 已启动 {datetime.now()}")

        while self.running:
            if self.r is None:
                await asyncio.sleep(SIM_CONFIG["tick_seconds"])
                continue

            try:
                raw = self.r.get("stalls")
                if raw is None:
                    await asyncio.sleep(SIM_CONFIG["tick_seconds"])
                    continue
                stalls = json.loads(raw)

                prob = self.get_hourly_prob()

                for s in stalls:
                    if s["status"] == "idle":
                        if random.random() < prob:
                            s["status"] = "occupied"
                            s["used_seconds"] = 0
                            self.total_arrivals += 1
                    elif s["status"] == "occupied":
                        s["used_seconds"] += SIM_CONFIG["tick_seconds"]
                        if s["used_seconds"] >= self.generate_duration():
                            s["status"] = "idle"
                            s["used_seconds"] = 0
                            self.total_completed += 1

                # 写回 Redis
                self.r.set("stalls", json.dumps(stalls))

                # 通过 WebSocket 推送给前端
                if self.on_update:
                    await self.on_update(stalls)

            except Exception as e:
                print(f"[仿真器] 出错：{e}")

            await asyncio.sleep(SIM_CONFIG["tick_seconds"])

        print(f"[仿真器] 已停止 {datetime.now()}")

    def start(self):
        if not self.running:
            self.task = asyncio.create_task(self.run())

    def stop(self):
        self.running = False

    def status(self):
        return {
            "running": self.running,
            "total_arrivals": self.total_arrivals,
            "total_completed": self.total_completed,
        }


# 全局单例
simulator = Simulator()