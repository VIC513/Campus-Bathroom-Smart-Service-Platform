"""
数据仿真器（Simulator）
基于问卷数据，模拟浴室隔间状态变化
使用 Redis 存储隔间状态 + WebSocket 回调推送 + 自动补位叫号
成员1：后端 Lead & DevOps
"""

import random
import asyncio
import json
from datetime import datetime


# ============================================================
# 1. 仿真参数（基于问卷统计结果）
# ============================================================
SIM_CONFIG ={ 
    "avg_duration_seconds": 1130,    # 问卷加权平均：18.8 分钟  
    "std_seconds": 450,
    "min_duration": 300,                 
    "max_duration": 2700,                   
     # 【更新】问卷第4题：冬季气温对时长的影响  
    "cold_threshold": 10.0,          # 低于10度算冷    
    "extra_duration": 180,           # 冬季洗澡时长增加180秒(3分钟)   
      # 【更新】问卷第2题：24 小时到达概率权重    
    "hourly_weights": [      
        0.00, 0.00, 0.00, 0.00, 0.00, 0.00,  # 00:00 - 05:59 (深夜无人)        
        0.01, 0.01, 0.01, 0.01, 0.01, 0.01,  # 06:00 - 11:59 (早晨极少)       
        0.03, 0.03, 0.02, 0.03, 0.04, 0.08,  # 12:00 - 17:59 (午休+课后)       
        0.15, 0.25, 0.25, 0.15, 0.05, 0.01   # 18:00 - 23:59 (19点-22点为绝对高峰)  
        ],    
    "max_wait_seconds": 360,    
    "tick_seconds": 5,    
    "idle_to_occupied_prob": 0.1, # 每5秒基础到达概率
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

    def generate_duration(self, temperature: float = 25.0) -> int:
        """生成一个洗澡时长（秒）"""
        cfg = SIM_CONFIG
        d = int(random.gauss(cfg["avg_duration_seconds"], cfg["std_seconds"]))
        if temperature < cfg["cold_threshold"]:
            d += cfg["extra_duration"]
        return max(cfg["min_duration"], min(d, cfg["max_duration"]))

    def get_hourly_prob(self) -> float:
        """根据当前小时，返回该小时的到达概率"""
        hour = datetime.now().hour
        weight = SIM_CONFIG["hourly_weights"][hour]
        return weight * SIM_CONFIG["idle_to_occupied_prob"]

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

                # 1. 随机产生洗澡的人 / 已有的人洗完
                for s in stalls:
                    if s["status"] == "idle":
                        if random.random() < prob:
                            s["status"] = "occupied"
                            s["used_seconds"] = 0
                            self.total_arrivals += 1
                    elif s["status"] == "occupied":
                        s["used_seconds"] += SIM_CONFIG["tick_seconds"]
                        if s["used_seconds"] >= self.generate_duration(temperature=15.0):
                            s["status"] = "idle"
                            s["used_seconds"] = 0
                            self.total_completed += 1

               # ========================================================
                # 2. 【超时释放】检查有没有人预约了(reserved)但3分钟没来扫码
                # ========================================================
                import time
                for s in stalls:
                    if s["status"] == "reserved" and "assign_time" in s:
                        if time.time() - s["assign_time"] > 180: # 180秒 = 3分钟
                            print(f"[超时释放] 隔间 {s['stall_id']} 预约超时，已回收！")
                            s["status"] = "idle"
                            del s["assign_time"]
                            
                            # 清理映射表，并给超时用户留案底
                            ticket_map = json.loads(self.r.get("ticket_to_stall") or "{}")
                            for t_id, st_id in list(ticket_map.items()):
                                if st_id == s["stall_id"]:
                                    del ticket_map[t_id]
                                    self.r.set("ticket_to_stall", json.dumps(ticket_map))
                                    
                                    # 找到该票号对应的用户，删掉他的票，并打上超时标记
                                    keys = self.r.keys("user:*:ticket")
                                    for k in keys:
                                        if self.r.get(k) == t_id:
                                            self.r.delete(k)
                                            username = k.split(":")[1]
                                            self.r.set(f"user:{username}:timeout_flag", "true", ex=300) # 案底保留5分钟
                                    break
                # ========================================================
                # 3. 【自动补位】有空闲隔间且队列有人，自动叫号分配
                # ========================================================
                idle_stalls = [s for s in stalls if s["status"] == "idle"]
                while idle_stalls:
                    ticket_id = self.r.lpop("queue:female")
                    if not ticket_id:
                        break
                    
                    stall = idle_stalls.pop(0)
                    # 这里补位也是分配给用户，状态变成 reserved（等待他来扫码）
                    stall["status"] = "reserved"
                    stall["used_seconds"] = 0
                    stall["assign_time"] = time.time()
                    
                    ticket_map = json.loads(self.r.get("ticket_to_stall") or "{}")
                    ticket_map[ticket_id] = stall["stall_id"]
                    self.r.set("ticket_to_stall", json.dumps(ticket_map))

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