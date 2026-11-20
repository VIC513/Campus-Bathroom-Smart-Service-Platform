# -*- coding: utf-8 -*-
# 模块归属: 数据 / 问卷参数配置  |  版本: v1.0.0  |  日期: 2026-11-20
"""
survey_config.py
将女生调研问卷转成仿真器和数据库初始化用的参数
数据来源：女生调研数据.pdf，有效样本 51 份（全部为女生）
问卷共 9 题，本文件按题号组织统计结果，并提供：
  1. 各项统计常量（供建库初始化、报告引用）
  2. export_simulator_config()：把仿真器需要的参数导出为 config.json
"""

import json
import os

# ============================================================
# Q1. 性别分布（单选，n=51）
# ============================================================
SURVEY_META = {
    "total_samples": 51,
    "female_ratio": 1.0,      # 女 51/51 = 100%（女生宿舍楼）
    "male_ratio": 0.0,
}

# ============================================================
# Q2. 洗浴时段分布（多选，比例为选择该选项的人数占比）
# 问卷实际只有 6 个时段选项，没有 14:00-17:00
# ============================================================
SEGMENT_WEIGHTS_RAW = {
    "0-12":  0.0980,   # A. 12:00 之前（早晨/上午）    5人 9.80%
    "12-14": 0.0980,   # B. 12:00-14:00（午休）        5人 9.80%
    "14-17": 0.0,      # 问卷无此选项
    "17-19": 0.2157,   # C. 17:00-19:00（课后）       11人 21.57%
    "19-21": 0.4118,   # D. 19:00-21:00（晚间前段）   21人 41.18%
    "21-23": 0.7059,   # E. 21:00-23:00（晚间高峰）   36人 70.59%
    "23-24": 0.2549,   # F. 23:00 之后（深夜错峰）    13人 25.49%
}

def _normalize(d: dict) -> dict:
    """多选题比例之和 > 1，归一化使总和 = 1.0（用 round 后的残差补到最大项）"""
    total = sum(d.values())
    norm = {k: round(v / total, 4) for k, v in d.items()}
    drift = round(1.0 - sum(norm.values()), 4)
    if drift:
        top = max(norm, key=norm.get)
        norm[top] = round(norm[top] + drift, 4)
    return norm

# 仿真器按“先抽时段、段内均匀抽小时”的方式使用
SEGMENT_WEIGHTS = _normalize(SEGMENT_WEIGHTS_RAW)

# 展开成 24 小时权重（段内均分），仅供报表/可视化使用
def expand_hourly_weights(seg: dict) -> dict:
    hourly = {}
    for s, w in seg.items():
        a, b = s.split("-")
        a, b = int(a), int(b)
        per = w / max(b - a, 1)
        for h in range(a, b):
            hourly[h] = round(per, 4)
    return {h: hourly.get(h, 0.0) for h in range(24)}

HOURLY_WEIGHTS = expand_hourly_weights(SEGMENT_WEIGHTS)

# ============================================================
# Q3. 单次洗澡时长分布（单选，n=51）
# ============================================================
DURATION_DISTRIBUTION = [
    # (最小分钟, 最大分钟, 比例)
    (0,   10,  0.0784),   # A. 10 分钟以内     4人  7.84%
    (10,  15,  0.1961),   # B. 10-15 分钟     10人 19.61%
    (15,  20,  0.2941),   # C. 15-20 分钟     15人 29.41%
    (20,  30,  0.2549),   # D. 20-30 分钟     13人 25.49%
    (30,  40,  0.1765),   # E. 30-40 分钟      9人 17.65%
    (40,  60,  0.0000),   # F. 40 分钟以上      0人
]

def estimate_female_duration_mu() -> int:
    """加权平均估算平均洗澡时长（秒）"""
    return int(sum(((lo + hi) / 2) * r for lo, hi, r in DURATION_DISTRIBUTION) * 60)

def estimate_female_duration_sigma() -> int:
    """加权方差估算时长标准差（秒）"""
    mu = estimate_female_duration_mu() / 60  # 分钟
    var = sum(r * (((lo + hi) / 2) - mu) ** 2 for lo, hi, r in DURATION_DISTRIBUTION)
    return int((var ** 0.5) * 60)

FEMALE_DURATION_MU = estimate_female_duration_mu()        # 约 1232 秒 ≈ 20.5 分钟
FEMALE_DURATION_SIGMA = estimate_female_duration_sigma()  # 约 524 秒 ≈ 8.7 分钟

# ============================================================
# Q4. 气温/季节对洗浴习惯的影响（单选，n=51）
# ============================================================
SEASON_EFFECT = {
    "winter_longer":      0.3529,   # A. 冬季时长明显变长(增加3-5分钟) 18人
    "summer_shorter":     0.0980,   # B. 夏季时长明显变短                5人
    "winter_avoid_peak":  0.0392,   # C. 冬季尽量避开晚高峰              2人
    "no_effect":          0.5098,   # D. 气温几乎不影响                 26人
}
# 冬季 35.29% 的人时长增加 3~5 分钟，在 20.5 分钟上约 +4 分钟 → 1.20
WINTER_PROB = SEASON_EFFECT["winter_longer"]
WINTER_FACTOR = 1.20

# ============================================================
# Q5. 排队容忍度（单选，n=51）
# ============================================================
WAIT_TOLERANCE_DISTRIBUTION = [
    (0,   0,   0.1373),   # A. 0 分钟（直接放弃）      7人 13.73%
    (0,   5,   0.3922),   # B. 5 分钟以内             20人 39.22%
    (5,   10,  0.1765),   # C. 5-10 分钟               9人 17.65%
    (10,  15,  0.0588),   # D. 10-15 分钟              3人  5.88%
    (15,  30,  0.2353),   # E. 15 分钟以上也愿意等     12人 23.53%
]

def estimate_max_wait_tolerance() -> int:
    """加权平均估算用户最大容忍等待时间（秒）"""
    return int(sum(((lo + hi) / 2) * r for lo, hi, r in WAIT_TOLERANCE_DISTRIBUTION) * 60)

MAX_WAIT_TOLERANCE = estimate_max_wait_tolerance()  # 约 500 秒 ≈ 8.3 分钟

# ============================================================
# Q6. 跨区推荐接受度（单选，n=51）
# “邻近楼栋/区域空闲，步行仅需 3 分钟，是否愿意前往？”
# ============================================================
CROSS_AREA_ACCEPT_RATE = {
    "very_willing": 0.0980,   # A. 非常愿意       5人
    "depends":      0.3333,   # B. 视情况而定    17人
    "unwilling":    0.5686,   # C. 不愿意        29人
}
# 有效接受率 = 非常愿意 + 视情况而定的一半
EFFECTIVE_ACCEPT_RATE = round(
    CROSS_AREA_ACCEPT_RATE["very_willing"]
    + CROSS_AREA_ACCEPT_RATE["depends"] * 0.5, 4
)  # 0.2647
# 推荐算法的步行阈值：问卷设定为 3 分钟
RECOMMEND_WALK_THRESHOLD_MIN = 3

# ============================================================
# Q7. 遇到过的浴室故障（多选，n=51）
# 故障定义统一为“用户反馈上报”的口径
# ============================================================
FAULT_REASONS = [
    ("水温异常",             0.7451),   # A. 水不热/忽冷忽热/过烫   38人
    ("门锁损坏",             0.2549),   # B. 门锁/门栓损坏或卡顿    13人
    ("花洒堵塞",             0.3922),   # C. 花洒/龙头堵塞，出水小  20人
    ("水压不足",             0.2941),   # D.                       15人
    ("旋钮损坏",             0.2941),   # E. 水温调节旋钮损坏       15人
    ("储物架损坏",           0.3922),   # F.                       20人
    ("垃圾/积水",            0.7255),   # G. 前人垃圾未清理/积水    37人
    ("没有遇到过问题",       0.0196),   # H.                        1人
]

# ============================================================
# Q8. 故障反馈方式偏好（多选，n=51）
# ============================================================
FEEDBACK_CHANNELS = [
    ("小程序/网页一键报修", 0.5098),   # A. 26人
    ("拨打运维电话",        0.0196),   # B.  1人
    ("向宿管/物业报修",     0.4706),   # C. 24人
    ("宿舍群吐槽不正式报修",0.1373),   # D.  7人
    ("直接忽略换个隔间",    0.4118),   # E. 21人
]

# ============================================================
# Q9. 实时状态查看需求（单选，n=51）
# ============================================================
REALTIME_VIEW_DEMAND = {
    "very_needed": 0.7647,   # A. 非常需要  39人
    "indifferent": 0.1961,   # B. 无所谓    10人
    "not_needed":  0.0392,   # C. 不需要     2人
}

# ============================================================
# 汇总成 system_config 表初始化数据
# ============================================================
SYSTEM_CONFIG_INIT = {
    "total_samples":          str(SURVEY_META["total_samples"]),
    "female_ratio":           str(SURVEY_META["female_ratio"]),
    "female_duration_mu":     str(FEMALE_DURATION_MU),
    "female_duration_sigma":  str(FEMALE_DURATION_SIGMA),
    "max_wait_tolerance":     str(MAX_WAIT_TOLERANCE),
    "cross_area_accept_rate": str(EFFECTIVE_ACCEPT_RATE),
    "recommend_walk_min":     str(RECOMMEND_WALK_THRESHOLD_MIN),
    "peak_hour_start":        "21",
    "peak_hour_end":          "23",
    "total_stalls":           "15",
}

# ============================================================
# 导出仿真器配置 config.json
# ============================================================
def build_simulator_config() -> dict:
    """
    把问卷统计结果映射为 simulator.py 需要的参数结构：
      duration_bins:       分段截断正态（段内 mu=区间中点, sigma=区间宽/4）
      hour_weights:        时段 -> 概率（先抽时段，段内均匀抽小时）
      gender_prob:         性别比例（女生宿舍楼 = 全部女生）
      wait_tolerance_bins: Q5 排队容忍度分档（分钟），用于排队放弃行为
      fault_type_weights:  Q7 故障类型权重（多选比例直接作权重）
      report_prob_per_shower: 单次洗浴触发一条用户报修的概率（工程标定）
      fault_duration_hours:  一次报修后故障从上报到修复的时长范围（小时）
    """
    duration_bins = []
    for lo, hi, ratio in DURATION_DISTRIBUTION:
        if ratio <= 0:
            continue
        duration_bins.append({
            "range": [lo, hi],
            "mu": (lo + hi) / 2,
            "sigma": max((hi - lo) / 4, 1.0),
            "prob": ratio,
        })
    return {
        "n_stalls": 15,
        "gender_prob": {"F": 1.0},
        "hour_weights": SEGMENT_WEIGHTS,
        "duration_bins": duration_bins,
        "winter_prob": WINTER_PROB,
        "winter_factor": WINTER_FACTOR,
        "wait_tolerance_bins": [
            {"range": [lo, hi], "prob": p}
            for lo, hi, p in WAIT_TOLERANCE_DISTRIBUTION
        ],
        "report_prob_per_shower": 0.006,
        "fault_duration_hours": [2, 24],
        "fault_type_weights": {
            "水温异常":   0.7451,
            "门锁损坏":   0.2549,
            "花洒堵塞":   0.3922,
            "水压不足":   0.2941,
            "旋钮损坏":   0.2941,
            "储物架损坏": 0.3922,
            "垃圾/积水":  0.7255,
        },
    }

def export_simulator_config(path=None) -> str:
    """把仿真器配置写入 config.json，默认写到脚本同目录"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config.json")
    path = os.path.abspath(path)
    cfg = build_simulator_config()
    with open(path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    print(f"[survey_config] 已导出仿真器配置 -> {path}")
    return path

# ============================================================
# 自测：直接运行本文件时打印结果并导出 config.json
# ============================================================
if __name__ == "__main__":
    print("=== 女生问卷参数统计结果（n=51）===")
    print(f"平均洗澡时长 μ = {FEMALE_DURATION_MU} 秒 ({FEMALE_DURATION_MU/60:.1f} 分钟)")
    print(f"时长标准差   σ = {FEMALE_DURATION_SIGMA} 秒 ({FEMALE_DURATION_SIGMA/60:.1f} 分钟)")
    print(f"最大容忍等待   = {MAX_WAIT_TOLERANCE} 秒 ({MAX_WAIT_TOLERANCE/60:.1f} 分钟)")
    print(f"跨区有效接受率 = {EFFECTIVE_ACCEPT_RATE}")
    print(f"冬季变长概率   = {WINTER_PROB}，放大系数 = {WINTER_FACTOR}")
    print("\n=== 时段权重（归一化，先抽时段再抽小时）===")
    for seg, w in SEGMENT_WEIGHTS.items():
        print(f"  {seg}: {w:.4f}")
    assert abs(sum(SEGMENT_WEIGHTS.values()) - 1.0) < 1e-9, "时段权重未归一化"
    print("\n=== system_config 初始化 ===")
    for k, v in SYSTEM_CONFIG_INIT.items():
        print(f"  {k} = {v}")
    export_simulator_config()
