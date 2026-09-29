import random
import time
import numpy as np

# -------------------------------------------------------------
# Step 1: 根据问卷统计结果配置仿真参数 (Config from Survey)
# -------------------------------------------------------------
SURVEY_CONFIG = {
    # Q1 & Q4: 性别洗澡时长均值与标准差 (单位: 秒)
    # 假设问卷得出: 男生平均 12 分钟(720s)，女生平均 18 分钟(1080s)
    "duration": {
        "male": {"mu": 720, "sigma": 180},    # 男: 均值12分, 标准差3分
        "female": {"mu": 1080, "sigma": 300}  # 女: 均值18分, 标准差5分
    },
    
    # Q3: 24小时到达概率权重表 ( Sum = 1.0 )
    # 根据问卷 B、C、D 选项占比分配 17:00-23:00 的高权重
    "hourly_weights": [
        0.00, 0.00, 0.00, 0.00, 0.00, 0.00,  # 00:00 - 05:59 (无人)
        0.01, 0.02, 0.02, 0.02, 0.03, 0.05,  # 06:00 - 11:59 (早晨微量)
        0.08, 0.04, 0.03, 0.03, 0.04, 0.10,  # 12:00 - 17:59 (午休+课后)
        0.15, 0.20, 0.12, 0.05, 0.01, 0.00   # 18:00 - 23:59 (19-22点为绝对高峰)
    ],

    # Q6: 平均容忍等待上限 (秒)
    "max_wait_tolerance": 600,  # 假设问卷中 80% 的人最多等 10 分钟

    # Q7: 跨区推荐接受率
    "accept_recommendation_rate": 0.75  # 75% 的用户愿意多走3分钟
}

# -------------------------------------------------------------
# Step 2: 仿真器核心生成函数 (Simulator Core Logic)
# -------------------------------------------------------------

def generate_shower_duration(gender: str) -> int:
    """根据问卷拟合的正态分布，生成一个合法的洗澡时长（秒）"""
    cfg = SURVEY_CONFIG["duration"][gender]
    # 使用 Gaussian 正态分布随机采样
    duration = int(np.random.normal(cfg["mu"], cfg["sigma"]))
    
    # 限制合理上下限：男最少5分钟，女最少8分钟；最多不超过45分钟
    min_limit = 300 if gender == "male" else 480
    return max(min_limit, min(duration, 2700))

def simulate_user_arrival(current_hour: int, total_stalls: int) -> int:
    """根据当前时段权重，计算当前 1 分钟内会有多少人产生洗澡需求"""
    weight = SURVEY_CONFIG["hourly_weights"][current_hour]
    # 基准到达人数计算
    base_arrivals = int(total_stalls * weight * random.uniform(0.8, 1.2))
    return base_arrivals

# -------------------------------------------------------------
# Step 3: 模拟运行示例 (测试生成结果)
# -------------------------------------------------------------
if __name__ == "__main__":
    print("--- 1. 根据问卷参数生成 5 位男生的洗澡时长 ---")
    for i in range(5):
        d = generate_shower_duration("male")
        print(f"虚拟男生 {i+1} 洗澡时长: {d//60} 分 {d%60} 秒")

    print("\n--- 2. 模拟 21:00 (高峰期) 某个 20 隔间浴室的到达人数 ---")
    arrivals = simulate_user_arrival(current_hour=19, total_stalls=20)
    print(f"21:00 该分钟内预计有 {arrivals} 人尝试排队/洗澡")