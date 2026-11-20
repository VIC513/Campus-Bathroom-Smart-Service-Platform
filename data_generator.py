import csv
import random
from datetime import datetime, timedelta
import numpy as np

# ============================================================
# 1. 问卷驱动的参数配置 (基于 66 份真实问卷)
# ============================================================
SURVEY_CONFIG = {
    # 第3题：女生洗澡时长正态分布 (均值 ~18.8分钟, 标准差 ~7.5分钟)
    "female_duration": {"mu": 1130, "sigma": 450, "min": 300, "max": 2700},
    
    # 第2题：24小时到达概率权重 (根据选项占比换算)
    "hourly_weights": [
        0.00, 0.00, 0.00, 0.00, 0.00, 0.00,  # 00:00 - 05:59 (深夜无人)
        0.01, 0.01, 0.01, 0.01, 0.01, 0.01,  # 06:00 - 11:59 (早晨极少)
        0.03, 0.03, 0.02, 0.03, 0.04, 0.08,  # 12:00 - 17:59 (午休+课后)
        0.15, 0.25, 0.25, 0.15, 0.05, 0.01   # 18:00 - 23:59 (19点-22点为绝对高峰)
    ],
    
    # 第4题：气温影响
    "temp_effect": {
        "cold_threshold": 10.0, # 低于10度算冷
        "extra_duration": 180   # 冬季洗澡时长增加180秒(3分钟)
    }
}

def generate_shower_duration(temperature: float) -> int:
    """生成洗澡时长，加入气温影响"""
    cfg = SURVEY_CONFIG["female_duration"]
    duration = int(np.random.normal(cfg["mu"], cfg["sigma"]))
    
    # 问卷第4题：冬季时长增加
    if temperature < SURVEY_CONFIG["temp_effect"]["cold_threshold"]:
        duration += SURVEY_CONFIG["temp_effect"]["extra_duration"]
        
    return max(cfg["min"], min(duration, cfg["max"]))

def generate_history_data(filename="shower_logs.csv", days=7):
    """生成过去 N 天的历史洗浴数据流水"""
    records = []
    base_date = datetime.now() - timedelta(days=days)
    record_id = 1
    total_stalls = 15 # 单楼栋 15 个隔间

    print(f"开始生成过去 {days} 天的历史数据...")

    for day in range(days):
        current_date = base_date + timedelta(days=day)
        
        for hour in range(24):
            weight = SURVEY_CONFIG["hourly_weights"][hour]
            
            # 基础到达人数（用权重乘上一个基准值，外加随机波动）
            num_arrivals = int(total_stalls * weight * random.uniform(1.5, 2.5))
            
            for _ in range(num_arrivals):
                # 随机到达时间
                start_time = current_date.replace(hour=hour, minute=random.randint(0, 59), second=random.randint(0, 59))
                
                # 模拟当时的气温 (5度到30度)
                temperature = round(random.uniform(5.0, 30.0), 1)
                
                # 核心逻辑：根据时段算排队情况
                if 19 <= hour <= 22:
                    idle_stalls = random.randint(0, 2) # 空位极少
                    queue_len = random.randint(3, 10)  # 排队多
                    # 排队时长：按排队人数和15个隔间平摊洗澡时长估算
                    wait_seconds = int((queue_len / total_stalls) * 1130) + random.randint(0, 120)
                elif 12 <= hour <= 17:
                    idle_stalls = random.randint(3, 8)
                    queue_len = random.randint(0, 3)
                    wait_seconds = queue_len * random.randint(30, 90)
                else:
                    idle_stalls = random.randint(10, 15)
                    queue_len = 0
                    wait_seconds = 0
                
                duration = generate_shower_duration(temperature)
                end_time = start_time + timedelta(seconds=duration)
                
                # 模拟异常数据：基于问卷第7题，水温异常占比最高
                is_fault = "f"
                fault_type = ""
                if random.random() < 0.03: # 3% 的概率遇到故障
                    is_fault = "t"
                    fault_type = random.choices(
                        ["水温异常", "门锁损坏", "花洒堵塞", "垃圾未清理"], 
                        weights=[0.7, 0.1, 0.1, 0.1], k=1
                    )[0]
                    # 如果是水温异常，可能洗了一半就出来了（时长减半）
                    if fault_type == "水温异常":
                        duration = int(duration * 0.5)
                        end_time = start_time + timedelta(seconds=duration)

                # 组装数据结构，严格对齐算法同学的表头
                records.append({
                    "id": record_id,
                    "stall_id": f"F{random.randint(1, 15):02d}",
                    "user_id": f"sim_{random.randint(10000, 99999)}",
                    "gender": "F",
                    "start_time": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "end_time": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                    "duration_seconds": duration,
                    "water_usage": round(random.uniform(30.0, 110.0), 1),
                    "hour_of_day": hour,
                    "day_of_week": current_date.weekday(),
                    "is_weekend": "t" if current_date.weekday() >= 5 else "f",
                    "queue_length_at_start": queue_len,
                    "idle_stalls_at_start": idle_stalls,
                    "wait_seconds": wait_seconds,
                    "temperature": temperature,
                    "is_fault": is_fault,
                    "fault_type": fault_type
                })
                record_id += 1

    # 写入 CSV 文件
    with open(filename, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=records[0].keys())
        writer.writeheader()
        writer.writerows(records)
        
    print(f"数据生成完毕！共生成 {len(records)} 条真实仿真记录，已保存至 {filename}")

if __name__ == "__main__":
    generate_history_data()