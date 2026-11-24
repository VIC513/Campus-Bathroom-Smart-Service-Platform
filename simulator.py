# -*- coding: utf-8 -*-
# 模块归属: 数据 / 仿真数据生成  |  版本: v1.1.0  |  日期: 2026-11-24
"""
基于女生问卷统计的校园浴室数据仿真器
依赖: pip install numpy psycopg2-binary redis
配置: 先运行 survey_config.py 生成 config.json

规模口径：一栋女生宿舍楼 300 人共用 15 个隔间，每人每天 1 次到访，默认仿真 90 天。
故障口径：不做任何“根据数据判定故障”，is_fault 完全由用户反馈上报（feedbacks）决定。

功能模式:
  history   批量生成历史数据（票据/日志/传感器/反馈），写入 PostgreSQL
  realtime  实时状态模拟，定时改写 Redis 中各隔间状态
  peak      一键触发晚高峰（80% 隔间占用），用于答辩演示
  export    纯内存仿真并导出 CSV（无需数据库/Redis），供训练流水线使用
  demo      无需数据库/Redis，直接打印 5 条样本日志与状态机示例（自检用）

v1.1.0:
  · 修正取号时刻口径：排队用户的 idle_stalls_at_start 记录为 0（旧版记为 1）
  · 新增排队事件记录（含放弃者的容忍时长与实际等待），支持右删失分析
  · 新增 --scenario peak 压力场景（75% 到访集中 21:00-23:00）
  · 新增 --mode export，直接导出训练用 CSV
"""

import argparse
import heapq
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

import numpy as np

# ---------- 统一的数据库配置（与 create_tables.py 保持一致） ----------
DB_CONFIG = {
    "dbname":   "bathroom_female_db",
    "user":     "postgres",
    "password": os.environ.get("BATH_DB_PASSWORD", ""),
    "host":     "localhost",
    "port":     "5432",
}

# 与 create_tables.py 中 stalls 状态约束一致: idle/occupied/cleaning/fault
STALL_STATES = ["idle", "occupied", "cleaning", "fault"]

# 统一使用东八区时间，保证 TIMESTAMPTZ 语义与 hour_of_day 口径一致
TZ = timezone(timedelta(hours=8))

# 单次洗浴触发一条用户报修的概率（工程标定，使故障日志占比约 5%）
REPORT_PROB_PER_SHOWER = 0.006

# 一次报修后，故障从上报到修复的持续时长（小时）
FAULT_DURATION_RANGE = (2.0, 24.0)


# ==================== 有限状态机（PPT 状态推断矩阵） ====================
def infer_stall_state(door, lock, flow):
    """
    根据门磁 / 门锁 / 水流三路传感器推断隔间实时状态。

    参数:
      door: 'open' / 'closed'
      lock: 'locked' / 'unlocked'
      flow: True（有水流）/ False（无水流）
    返回:
      (state, confidence, note)
    """
    door = str(door).lower()
    lock = str(lock).lower()
    flow = bool(flow)

    if door == "closed" and lock == "locked" and flow:
        return ("occupied", "high", "高置信度使用中")
    if door == "closed" and lock == "locked" and not flow:
        return ("occupied", "medium", "可能正在使用")
    if door == "closed" and lock == "unlocked" and not flow:
        return ("idle", "low", "可能空闲，需结合历史事件")
    if door == "open" and lock == "unlocked" and not flow:
        return ("idle", "medium", "可能刚离开")
    if door == "open" and lock == "unlocked" and flow:
        return ("fault", "high", "疑似未关水或设备异常")
    return ("unknown", "low", "组合未定义，需人工确认")


# ==================== 配置加载 ====================
def load_config(path=None) -> dict:
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "config.json")
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


# ==================== 仿真器 ====================
class BathSimulator:
    def __init__(self, config: dict, pg_conn=None, redis_client=None):
        self.cfg = config
        self.pg = pg_conn
        self.r = redis_client
        # 隔间编号与建表约束 ^F(0[1-9]|1[0-5])$ 对齐: F01..F15
        self.n_stalls = config.get('n_stalls', 15)
        self.stalls = [f"F{i+1:02d}" for i in range(self.n_stalls)]
        # 性别 / 时段权重
        self.genders = list(config['gender_prob'].keys())
        self.gender_p = list(config['gender_prob'].values())
        self.hour_segs = list(config['hour_weights'].keys())
        self.hour_p = np.array(list(config['hour_weights'].values()), dtype=float)
        self.hour_p = self.hour_p / self.hour_p.sum()   # 归一化

        # 故障类型权重（问卷 Q7 多选比例，归一化后抽取）
        self.fault_types = list(config['fault_type_weights'].keys())
        ft_w = np.array(list(config['fault_type_weights'].values()), dtype=float)
        self.fault_p = list(ft_w / ft_w.sum())

        # 排队容忍度分档（问卷 Q5，单位分钟）：(lo, hi, prob)
        # 0 分钟档代表"看到排队直接放弃"
        self.tol_bins = config.get('wait_tolerance_bins') or [
            {"range": [0, 0],   "prob": 0.1667},
            {"range": [0, 5],   "prob": 0.3333},
            {"range": [5, 10],  "prob": 0.2273},
            {"range": [10, 15], "prob": 0.0758},
            {"range": [15, 30], "prob": 0.1970},
        ]
        self._tol_cache = {}            # uid -> 容忍秒数（按人固定，体现个体差异）
        self.report_prob = float(
            config.get('report_prob_per_shower', REPORT_PROB_PER_SHOWER))
        self.fault_hours = tuple(
            config.get('fault_duration_hours', list(FAULT_DURATION_RANGE)))

        # 仿真中间结果（内存态，最后批量写库 / 导出 CSV）
        self._logs = []
        self._tickets = []
        self._queue_events = []         # 每次到达的排队事件（含放弃者）
        self._scenario = 'base'
        self._ticket_seq = 0            # 票据编号自增（含放弃者）
        self._abandoned = 0             # 放弃排队人数
        self._feedbacks = []            # 用户报修记录（故障唯一来源）
        self._anomaly_episodes = []     # 传感器异常片段（仅用于算法预警演示）

    # ---------- 采样: 性别 ----------
    def sample_gender(self) -> str:
        return random.choices(self.genders, weights=self.gender_p, k=1)[0]

    # ---------- 采样: 时长（分钟，分段截断正态） ----------
    def sample_duration(self, is_winter=False) -> float:
        bins = self.cfg['duration_bins']
        b = random.choices(bins, weights=[x['prob'] for x in bins], k=1)[0]
        lo, hi = b['range']
        d = random.gauss(b['mu'], b['sigma'])
        d = max(lo, min(hi, d))
        # Q4: 冬季有 winter_prob 比例的人时长放大 winter_factor 倍
        if is_winter and random.random() < self.cfg['winter_prob']:
            d *= self.cfg['winter_factor']
        # 下限 2 分钟：截断正态在 10 分钟以内这一档会被截到 0，不符合实际
        return round(max(2.0, d), 1)

    # ---------- 采样: 小时（按问卷时段权重抽，段内均匀抽小时） ----------
    def sample_hour(self) -> int:
        seg = random.choices(self.hour_segs, weights=self.hour_p, k=1)[0]
        a, b = seg.split("-")
        return random.randint(int(a), int(b) - 1)

    # ---------- 用户等待容忍度（问卷 Q5，按人固定） ----------
    def tolerance_seconds(self, uid) -> float:
        tol = self._tol_cache.get(uid)
        if tol is None:
            b = random.choices(self.tol_bins,
                               weights=[x['prob'] for x in self.tol_bins], k=1)[0]
            lo, hi = b['range']
            tol = (random.uniform(lo, hi) if hi > lo else float(lo)) * 60.0
            self._tol_cache[uid] = tol
        return tol

    # ---------- 采样: 气温（简易日变化模型，环境气温而非水温） ----------
    def sample_temperature(self, hour: int) -> float:
        base = 15 + 10 * float(np.sin((hour - 6) / 24 * 2 * np.pi))
        return round(base + random.gauss(0, 2), 1)

    # ---------- 高峰系数（仅 demo 自检使用；历史生成不再重复乘高峰） ----------
    @staticmethod
    def _peak_factor(hour: int) -> float:
        if 21 <= hour < 23:
            return 3.0
        if 19 <= hour < 21:
            return 2.0
        if 17 <= hour < 19:
            return 1.5
        return 1.0

    # ---------- 故障判定：只由用户反馈上报确定 ----------
    def _assign_faults(self):
        """
        “故障直接通过用户上传确定”：
          · 按时间顺序遍历每个隔间的洗浴记录；
          · 若该次洗浴处于某条反馈造成的故障窗口内 → is_fault=True；
          · 否则按 report_prob 概率，由该次洗浴的用户提交一条报修（feedbacks），
            报修开出一个故障窗口（2~24 小时），窗口内的后续洗浴也记为故障样本。
        全程不读取任何传感器数据来判定故障。
        """
        by_stall = {}
        for log in self._logs:
            by_stall.setdefault(log['stall_id'], []).append(log)

        for stall, logs in by_stall.items():
            logs.sort(key=lambda x: x['start_time'])
            active = None                    # 当前生效的故障窗口 (type, end)
            for log in logs:
                if active and log['start_time'] > active[1]:
                    active = None
                if active:
                    log['is_fault'] = True
                    log['fault_type'] = active[0]
                    continue
                if random.random() >= self.report_prob:
                    continue

                ftype = random.choices(self.fault_types, weights=self.fault_p, k=1)[0]
                created = log['end_time'] + timedelta(minutes=random.uniform(5, 30))
                roll = random.random()
                if roll < 0.70:                     # 约 70% 已解决
                    status = "resolved"
                    resolved = created + timedelta(
                        hours=random.uniform(*self.fault_hours))
                elif roll < 0.90:                   # 约 20% 处理中
                    status, resolved = "processing", None
                else:                               # 约 10% 待处理
                    status, resolved = "pending", None

                end = resolved or created + timedelta(
                    hours=random.uniform(*self.fault_hours))

                log['is_fault'] = True
                log['fault_type'] = ftype
                active = (ftype, end)

                self._feedbacks.append({
                    'user_id':       log['user_id'],
                    'stall_id':      stall,
                    'type':          ftype,
                    'description':   self._fault_description(stall, ftype),
                    'status':        status,
                    'created_at':    created,
                    'resolved_at':   resolved,
                    'fault_start':   log['start_time'],
                    'fault_end':     end,
                })

    @staticmethod
    def _fault_description(stall_id: str, fault_type: str) -> str:
        """用户反馈的中文描述模板"""
        templates = {
            "水温异常":   "{s}号隔间水温忽冷忽热，洗到一半水就凉了，希望尽快检修。",
            "水压不足":   "{s}号隔间水压明显不足，水流细小，洗澡很不方便。",
            "花洒堵塞":   "{s}号隔间花洒出水孔堵塞，出水很少，请清理或更换。",
            "门锁损坏":   "{s}号隔间门锁坏了，门关不上，存在隐私和安全隐患。",
            "旋钮损坏":   "{s}号隔间水温调节旋钮失灵，调不了冷热，麻烦检修。",
            "储物架损坏": "{s}号隔间储物架松动/断裂，东西没地方放，请维修。",
            "垃圾/积水":  "{s}号隔间地面有积水和前人留下的垃圾，体验很差，需要保洁。",
        }
        return templates.get(fault_type, "{s}号隔间设备疑似异常，麻烦安排师傅巡检。").format(
            s=stall_id.lstrip("F").lstrip("0") or stall_id
        )

    # ---------- 生成单条日志（由 DES 循环调用） ----------
    # 参数说明：
    #   arrival_time:  用户到达时刻
    #   assign_time:   分到隔间的时刻（如果到达时就有空闲 = arrival_time）
    #   stall_id:      分到的隔间
    #   queue_len_at_arrival:  用户到达时看到的排队长度
    #   free_stalls_at_arrival: 用户到达时看到的空闲隔间数
    #   user_id:       真实用户 ID（users 表 1..300）
    def generate_log(self, arrival_time: datetime, assign_time: datetime,
                     stall_id: str, queue_len_at_arrival: int,
                     free_stalls_at_arrival: int, user_id=None,
                     is_winter=False) -> dict:
        gender = self.sample_gender()
        duration_min = self.sample_duration(is_winter)
        # start_time = assign_time（分到隔间那一刻才开始洗）
        start_time = assign_time
        duration_seconds = int(duration_min * 60)
        # 极少数异常：超长占用 70~90 分钟（仅用于异常检测演示，不参与故障判定）
        if random.random() < 0.002:
            duration_seconds = random.randint(70 * 60, 90 * 60)
        end_time = start_time + timedelta(seconds=duration_seconds)
        hour = start_time.hour
        temperature = self.sample_temperature(hour)

        # 水流速率 6~9 L/min（GB 28378-2019 淋浴器三级水效上限 9.0 L/min）
        flow = round(random.uniform(6.0, 9.0), 2)
        # 用水量 = 流量 × 时长 × 0.85~1.0（中间搓洗/打泡沫时会短暂关水）
        water_usage = flow * (duration_seconds / 60.0) * random.uniform(0.85, 1.0)

        wait_seconds = int((assign_time - arrival_time).total_seconds())

        return {
            'stall_id': stall_id,
            'user_id': user_id,
            'gender': gender,
            'start_time': start_time,
            'end_time': end_time,
            'duration_seconds': duration_seconds,
            'water_usage': round(water_usage, 1),
            'hour_of_day': hour,
            'day_of_week': start_time.weekday(),
            'is_weekend': start_time.weekday() >= 5,
            'queue_length_at_start': queue_len_at_arrival,
            'idle_stalls_at_start': free_stalls_at_arrival,
            'wait_seconds': wait_seconds,
            'temperature': temperature,
            'is_fault': False,                 # 由 _assign_faults 依据用户反馈回填
            'fault_type': None,
            'flow_rate': flow,                 # 内部字段，供传感器事件使用
            'ticket_id': None,                 # 由 _emit 分配
            'data_source': 'simulated',
        }

    # ---------- demo 专用：生成一条自洽的日志 ----------
    # 不依赖 DES / PostgreSQL，仅用于自检分布是否合理
    def _demo_generate_log(self, base_time: datetime, is_winter=False,
                           user_id=None) -> dict:
        gender = self.sample_gender()
        duration_min = self.sample_duration(is_winter)
        hour = base_time.hour
        temperature = self.sample_temperature(hour)
        duration_seconds = int(duration_min * 60)
        # 与 generate_log 口径一致：流量 6~9 L/min × 时长 × 0.85~1.0
        flow = round(random.uniform(6.0, 9.0), 2)
        water_usage = flow * (duration_seconds / 60.0) * random.uniform(0.85, 1.0)

        # demo 仅用于打印样本：等待必须与排队状态自洽
        pf = self._peak_factor(hour)
        free_stalls = max(0, int(random.gauss(5 / pf, 1)))
        if free_stalls > 0:
            queue_len = 0
            wait_min = 0.0
        else:
            queue_len = max(1, int(random.gauss(3 * pf, 2)))
            wait_min = min(120.0, max(0.0, random.gauss(queue_len * 4, 6)))

        return {
            'stall_id': random.choice(self.stalls),
            'user_id': user_id,
            'gender': gender,
            'start_time': base_time,
            'end_time': base_time + timedelta(seconds=duration_seconds),
            'duration_seconds': duration_seconds,
            'water_usage': round(water_usage, 1),
            'hour_of_day': hour,
            'day_of_week': base_time.weekday(),
            'is_weekend': base_time.weekday() >= 5,
            'queue_length_at_start': queue_len,
            'idle_stalls_at_start': free_stalls,
            'wait_seconds': int(wait_min * 60),
            'temperature': temperature,
            'is_fault': False,          # 故障只由反馈驱动的故障窗口产生
            'fault_type': None,
            'ticket_id': None,
            'data_source': 'simulated',
        }

    # ---------- 票据相关 ----------
    def _new_ticket_id(self) -> int:
        self._ticket_seq += 1
        return self._ticket_seq

    def _abandon(self, uid, arrival_t, tol_seconds, reason='cancelled'):
        """用户放弃排队：写一条 cancelled 票据，不产生洗浴日志"""
        tid = self._new_ticket_id()
        cancelled_at = arrival_t + timedelta(seconds=tol_seconds)
        self._tickets.append((
            tid, uid, 'A', None, reason,
            arrival_t, None, None, None, None, None, cancelled_at,
        ))
        self._abandoned += 1

    def _pop_servable(self, queue, now):
        """从队首取一个仍在容忍范围内的人；超时者按放弃处理并回填排队事件"""
        while queue:
            person = queue[0]
            waited = (now - person['arrival']).total_seconds()
            if waited > person['tol']:
                queue.pop(0)
                self._abandon(person['uid'], person['arrival'], person['tol'])
                self._finish_event(person['ev'], 'abandoned', waited)
                continue
            queue.pop(0)
            return person
        return None

    def _finish_event(self, ev_idx, outcome, waited_seconds, stall_id=None):
        """回填一条排队事件的最终结果（completed / abandoned / served）"""
        ev = self._queue_events[ev_idx]
        ev['outcome'] = outcome
        ev['waited_seconds'] = round(float(waited_seconds), 1)
        if stall_id is not None:
            ev['stall_id'] = stall_id

    def _assign_queued(self, person, now, idle_stalls, is_winter, finishing):
        """
        给队首用户分配一个刚释放（或当前空闲）的隔间。
        注意：排队用户“取号时刻”的空闲隔间数为 0，不能用分配时刻的 1 代替，
        否则训练数据永远看不到 free_stalls=0 的真实高负载组合。
        """
        uid, arrival_t, q_len, ev_idx = (person['uid'], person['arrival'],
                                         person['q_len'], person['ev'])
        stall = idle_stalls.pop()
        assign_t = max(now, arrival_t)
        log = self._emit(arrival_t, assign_t, stall, q_len, 0, uid, is_winter)
        heapq.heappush(finishing, (log['end_time'], stall, log['duration_seconds']))
        self._finish_event(ev_idx, 'completed',
                           (assign_t - arrival_t).total_seconds(), stall)

    def _make_arrivals(self, day_start, user_count, peak_share=0.0):
        """
        生成当日到访列表。
        peak_share > 0 时为压力场景：该比例的到访集中在 21:00-23:00。
        """
        out = []
        for uid in range(1, user_count + 1):
            if peak_share > 0 and random.random() < peak_share:
                hour = random.choice([21, 22])
            else:
                hour = self.sample_hour()
            out.append((day_start + timedelta(
                hours=hour,
                minutes=random.randint(0, 59),
                seconds=random.randint(0, 59),
            ), uid))
        out.sort(key=lambda x: x[0])
        return out

    # ---------- 记录一条日志及其排队票据（内存态） ----------
    def _emit(self, arrival_t, assign_t, stall, q_len, free_n, uid, is_winter):
        tid = self._new_ticket_id()
        log = self.generate_log(
            arrival_time=arrival_t, assign_time=assign_t,
            stall_id=stall,
            queue_len_at_arrival=q_len,
            free_stalls_at_arrival=free_n,
            user_id=uid, is_winter=is_winter,
        )
        log['ticket_id'] = tid
        self._logs.append(log)

        # 已完成票据：joined_at = 到达(取号)时刻 = start_time - wait_seconds
        joined_at = assign_t - timedelta(seconds=log['wait_seconds'])
        self._tickets.append((
            tid, uid, 'A', stall, 'completed',
            joined_at, assign_t, assign_t + timedelta(seconds=180),
            assign_t, log['end_time'], 'scan', None,
        ))
        return log

    # ---------- 批量生成历史数据（DES 事件驱动，纯内存） ----------
    def simulate(self, user_count=300, days=90, scenario='base', seed=20261124,
                 peak_share=None):
        """
        事件驱动仿真 (Discrete-Event Simulation)，结果只保存在内存：
          · 按天循环，每天从 00:00 → 24:00 按分钟推进
          · 每天为这 300 人各生成 1 次到访，到达小时由女生问卷时段权重抽样
            （权重本身已含高峰信息，不再重复乘高峰系数）
          · 隔间按 sample_duration 完成后释放 → 队首自动分配
          · wait_seconds 由 (assign_time - arrival_time) 涌现，不是公式
          · 每次到达都记录一条排队事件（含放弃者），用于分析右删失
          · 故障不参与生成过程，全部在生成完成后由用户反馈（feedbacks）决定

        scenario:
          base  日常场景，按问卷时段权重到达
          peak  压力场景，peak_share（默认 75%）的到访集中在 21:00-23:00
          test  独立测试场景，仅用于最终评价，不参与训练
        peak_share:
          None 时按场景取默认值（peak=0.75，其余=0）；显式传入可覆盖，
          用于生成与训练集分布不同的独立测试场景。
        seed:
          随机种子；测试场景应使用与训练不同的种子，保证不重叠。
        返回: (logs, queue_events, tickets)
        """
        random.seed(seed)
        np.random.seed(seed)

        self._logs = []
        self._tickets = []
        self._queue_events = []
        self._ticket_seq = 0
        self._abandoned = 0
        self._feedbacks = []
        self._anomaly_episodes = []
        self._tol_cache = {}
        self._scenario = scenario
        if peak_share is None:
            peak_share = 0.75 if scenario == 'peak' else 0.0
        peak_share = float(np.clip(peak_share, 0.0, 1.0))

        # 从今天 00:00 往前推 days 天
        today = datetime.now(TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        mean_dur = self._mean_duration_min()
        print(f"[Simulator] 场景={scenario} 规模: {user_count} 人/天 × {days} 天 = "
              f"{user_count * days} 人次 | 15 隔间 | 平均时长 {mean_dur:.1f}min | "
              f"集中高峰占比 {peak_share:.0%}")

        for day_offset in range(days):
            day_start = today - timedelta(days=day_offset)
            day_end = day_start + timedelta(days=1)
            is_winter = day_start.month in (11, 12, 1, 2, 3)

            arrivals = self._make_arrivals(day_start, user_count, peak_share)

            # ----- DES 状态 -----
            t = day_start                          # 当前仿真时钟
            idle_stalls = set(self.stalls)          # 空闲隔间集合
            finishing = []                          # 最小堆: (end_time, stall_id, duration_seconds)
            queue = []                              # 等待队列: dict(uid/arrival/q_len/tol/ev)
            ptr = 0                                 # 当日到达列表游标

            while t < day_end:
                # 1) 释放所有 end_time <= t 的隔间，并叫号
                while finishing and finishing[0][0] <= t:
                    end_t, freed_stall, _dur = heapq.heappop(finishing)
                    idle_stalls.add(freed_stall)
                    while queue and idle_stalls:
                        person = self._pop_servable(queue, end_t)
                        if person is None:
                            break
                        self._assign_queued(person, end_t, idle_stalls,
                                            is_winter, finishing)

                # 2) 处理本分钟内的到达
                minute_end = t + timedelta(minutes=1)
                while ptr < len(arrivals) and arrivals[ptr][0] < minute_end:
                    arrival_t, uid = arrivals[ptr]
                    ptr += 1
                    q_len = len(queue)
                    free_n = len(idle_stalls)
                    ev_idx = len(self._queue_events)
                    self._queue_events.append({
                        'arrival_time': arrival_t, 'user_id': uid,
                        'queue_len_at_arrival': q_len,
                        'free_stalls_at_arrival': free_n,
                        'tolerance_seconds': None,
                        'outcome': None, 'waited_seconds': None,
                        'stall_id': None, 'scenario': scenario,
                    })
                    if idle_stalls:
                        # 有空闲隔间：直接入场，零等待
                        stall = idle_stalls.pop()
                        log = self._emit(arrival_t, arrival_t, stall, q_len,
                                         free_n, uid, is_winter)
                        heapq.heappush(finishing, (
                            log['end_time'], stall, log['duration_seconds']))
                        self._queue_events[ev_idx]['tolerance_seconds'] = \
                            round(self.tolerance_seconds(uid), 1)
                        self._finish_event(ev_idx, 'served', 0.0, stall)
                    else:
                        # 无空闲：照常取号排队；Q5 中"零容忍"的人看到满员直接走
                        tol = self.tolerance_seconds(uid)
                        self._queue_events[ev_idx]['tolerance_seconds'] = round(tol, 1)
                        if tol <= 0:
                            self._abandon(uid, arrival_t, 0.0)
                            self._finish_event(ev_idx, 'abandoned', 0.0)
                        else:
                            queue.append({'uid': uid, 'arrival': arrival_t,
                                          'q_len': q_len, 'tol': tol, 'ev': ev_idx})

                # 3) 下一分钟
                t = minute_end

            # ----- 收尾排水：当天结束仍在排队，最多延长 4 小时 -----
            drain_deadline = day_end + timedelta(hours=4)
            while queue and t < drain_deadline:
                if idle_stalls:
                    person = self._pop_servable(queue, t)
                    if person is None:
                        break
                    self._assign_queued(person, t, idle_stalls, is_winter, finishing)
                elif finishing:
                    end_t, freed_stall, _dur = heapq.heappop(finishing)
                    t = max(t, end_t)
                    idle_stalls.add(freed_stall)
                else:
                    break

            # 仍未等到（超过容忍度或营业时间结束）→ 放弃
            while queue:
                person = queue.pop(0)
                waited = (t - person['arrival']).total_seconds()
                self._abandon(person['uid'], person['arrival'], person['tol'])
                self._finish_event(person['ev'], 'abandoned', waited)

            if (day_offset + 1) % 10 == 0 or day_offset == days - 1:
                print(f"[Simulator] 已推进 {day_offset + 1}/{days} 天，"
                      f"累计日志 {len(self._logs)} 条，"
                      f"放弃 {self._abandoned} 人次")

        return self._logs, self._queue_events, self._tickets

    # ---------- 批量生成历史数据并写入数据库 ----------
    def generate_history(self, user_count=300, days=90, scenario='base'):
        if not self.pg:
            raise RuntimeError("需要 PostgreSQL 连接")
        cur = self.pg.cursor()
        self.simulate(user_count=user_count, days=days, scenario=scenario)

        # ----- 故障判定：只由用户反馈上报确定（不读传感器、不做算法判定） -----
        self._assign_faults()

        # ----- 写库（票据 → 日志 → 传感器 → 反馈 → 异常） -----
        self._write_all(cur)
        self.pg.commit()

        print(f"[Simulator] 完成：日志 {len(self._logs)} 条 -> shower_logs")
        self.print_stats()

    # ---------- 导出为 CSV（无需 PostgreSQL / Redis） ----------
    def export_csv(self, out_dir, prefix=None):
        """
        把内存中的仿真结果导出为 CSV，供训练流水线直接读取：
          <prefix>_logs.csv          每条完成的洗浴记录（含取号时刻特征与真实等待标签）
          <prefix>_queue_events.csv  每次到达的排队事件（含放弃者与容忍时长，用于删失分析）
        """
        import pandas as pd

        os.makedirs(out_dir, exist_ok=True)
        prefix = prefix or f"sim_{self._scenario}"
        log_rows = []
        for log in self._logs:
            log_rows.append({
                'stall_id': log['stall_id'], 'user_id': log['user_id'],
                'gender': log['gender'], 'start_time': log['start_time'],
                'end_time': log['end_time'],
                'duration_seconds': log['duration_seconds'],
                'water_usage': log['water_usage'],
                'hour_of_day': log['hour_of_day'],
                'day_of_week': log['day_of_week'],
                'is_weekend': log['is_weekend'],
                'queue_length_at_start': log['queue_length_at_start'],
                'idle_stalls_at_start': log['idle_stalls_at_start'],
                'wait_seconds': log['wait_seconds'],
                'temperature': log['temperature'],
                'is_fault': log['is_fault'], 'fault_type': log['fault_type'],
                'ticket_id': log['ticket_id'],
                'data_source': 'simulated',
                'scenario': self._scenario,
            })
        logs_path = os.path.join(out_dir, f"{prefix}_logs.csv")
        ev_path = os.path.join(out_dir, f"{prefix}_queue_events.csv")
        pd.DataFrame(log_rows).to_csv(logs_path, index=False, encoding='utf-8-sig')
        pd.DataFrame(self._queue_events).to_csv(
            ev_path, index=False, encoding='utf-8-sig')

        n = len(log_rows)
        n_q = sum(1 for l in log_rows if l['queue_length_at_start'] > 0)
        n_f0 = sum(1 for l in log_rows if l['idle_stalls_at_start'] == 0)
        n_ab = sum(1 for e in self._queue_events if e['outcome'] == 'abandoned')
        print(f"[Simulator] 已导出 {n} 条洗浴日志 -> {logs_path}")
        print(f"[Simulator] 已导出 {len(self._queue_events)} 条排队事件 -> {ev_path}")
        print(f"[Simulator]   其中排队取号 {n_q} 条、取号时 free=0 的 {n_f0} 条、"
              f"放弃 {n_ab} 人次（{n_ab / max(len(self._queue_events), 1):.1%}）")
        return logs_path, ev_path

    # ---------- 问卷加权的平均洗澡时长（分钟） ----------
    def _mean_duration_min(self) -> float:
        bins = self.cfg['duration_bins']
        total_p = sum(b['prob'] for b in bins) or 1.0
        return sum(((b['range'][0] + b['range'][1]) / 2.0) * b['prob']
                   for b in bins) / total_p

    # ---------- 写库：票据 / 日志 / 传感器 / 反馈 / 异常 ----------
    def _write_all(self, cur):
        from psycopg2.extras import Json, execute_values

        # 1) 排队票据（显式 ticket_id = 1..N，插入后校正 BIGSERIAL 序列）
        ticket_rows = self._tickets
        execute_values(cur, """
            INSERT INTO queue_tickets
            (ticket_id, user_id, area_id, reserved_stall_id, ticket_status,
             joined_at, called_at, expires_at, checked_in_at, finished_at,
             check_in_method, cancelled_at)
            VALUES %s
        """, ticket_rows, page_size=5000)
        # 显式插入后校正序列：下一次 nextval 应返回 N+1
        cur.execute("SELECT setval('queue_tickets_ticket_id_seq', %s, true)",
                    (len(ticket_rows),))

        # 2) 洗浴日志（关联 ticket_id）
        log_rows = [
            (l['stall_id'], l['user_id'], l['gender'], l['start_time'], l['end_time'],
             l['duration_seconds'], l['water_usage'], l['hour_of_day'],
             l['day_of_week'], l['is_weekend'], l['queue_length_at_start'],
             l['idle_stalls_at_start'], l['wait_seconds'], l['temperature'],
             l['is_fault'], l['fault_type'], l['ticket_id'], l['data_source'])
            for l in self._logs
        ]
        execute_values(cur, """
            INSERT INTO shower_logs
            (stall_id, user_id, gender, start_time, end_time, duration_seconds,
             water_usage, hour_of_day, day_of_week, is_weekend,
             queue_length_at_start, idle_stalls_at_start, wait_seconds,
             temperature, is_fault, fault_type, ticket_id, data_source)
            VALUES %s
        """, log_rows, page_size=5000)
        print(f"[Simulator] 已写入 {len(log_rows)} 条 shower_logs、"
              f"{len(ticket_rows)} 条 queue_tickets")

        # 3) 传感器事件（每次洗浴 9~13 条，批量 5000 写入）
        batch = []
        sensor_total = 0
        for log in self._logs:
            for row in self._sensor_rows(log, Json):
                batch.append(row)
            if len(batch) >= 5000:
                sensor_total += len(batch)
                execute_values(cur, """
                    INSERT INTO sensor_events
                    (stall_id, sensor_type, sensor_value, flow_rate,
                     sensor_timestamp, received_at, data_source, device_id)
                    VALUES %s
                """, batch, page_size=5000)
                batch = []
        if batch:
            sensor_total += len(batch)
            execute_values(cur, """
                INSERT INTO sensor_events
                (stall_id, sensor_type, sensor_value, flow_rate,
                 sensor_timestamp, received_at, data_source, device_id)
                VALUES %s
            """, batch, page_size=5000)
        print(f"[Simulator] 已写入 {sensor_total} 条 sensor_events")

        # 4) 用户反馈（故障唯一来源，anomaly_id 先留空，最后统一回填）
        feedback_rows = [
            (f['user_id'], f['stall_id'], f['type'], f['description'],
             f['status'], f['created_at'], f['resolved_at'], None)
            for f in self._feedbacks
        ]
        if feedback_rows:
            execute_values(cur, """
                INSERT INTO feedbacks
                (user_id, stall_id, fault_type, description, feedback_status,
                 created_at, resolved_at, anomaly_id)
                VALUES %s
            """, feedback_rows, page_size=1000)

        # 5) 算法异常预警（只来自传感器异常片段，不参与故障判定）
        anomaly_rows = self._build_anomaly_rows(cur)
        inserted_anomalies = []
        if anomaly_rows:
            execute_values(cur, """
                INSERT INTO anomaly_events
                (stall_id, event_id, detected_at, detection_method, anomaly_type,
                 anomaly_score, severity, alert_status, confirmed_fault,
                 resolved_at, description)
                VALUES %s
            """, anomaly_rows, page_size=1000)
            # 回读本次写入的预警，建立 (隔间, 检测时间) -> anomaly_id
            cur.execute("SELECT anomaly_id, stall_id, detected_at, anomaly_type "
                        "FROM anomaly_events")
            inserted_anomalies = cur.fetchall()

        # 6) 若某条预警落在用户反馈的故障窗口内，则把 anomaly_id 挂到该反馈上
        linked = 0
        for f in self._feedbacks:
            aid = None
            for anomaly_id, stall_id, detected_at, _atype in inserted_anomalies:
                if (stall_id == f['stall_id']
                        and f['fault_start'] <= detected_at <= f['fault_end']):
                    aid = anomaly_id
                    break
            if aid is None:
                continue
            cur.execute("""
                UPDATE feedbacks SET anomaly_id = %s
                WHERE user_id = %s AND stall_id = %s AND created_at = %s
            """, (aid, f['user_id'], f['stall_id'], f['created_at']))
            linked += 1

        print(f"[Simulator] 已写入 {len(feedback_rows)} 条 feedbacks、"
              f"{len(anomaly_rows)} 条 anomaly_events"
              f"（其中 {linked} 条反馈已关联算法预警）")

    # ---------- 单次洗浴的传感器事件（门磁 / 门锁 / 水流） ----------
    def _sensor_rows(self, log, Json):
        """
        按真实硬件的事件顺序生成一次洗浴的传感器序列：
          门磁 open(开始前12s) → closed(前10s) → 门锁 locked(前9s)
          → 水流 on(开始+5s) → 25%/50%/75% 三个采样点 → off(结束前3s)
          → 门锁 unlocked(结束+1s) → 门磁 open(结束+4s)
        流量取 6~9 L/min（GB 28378-2019《淋浴器水效限定值及水效等级》
        三级水效流量上限 9.0 L/min），家用霍尔式水流传感器精度约 ±10%，
        门磁/门锁事件上报延迟取 0.3~2 秒。
        约 0.1% 的洗浴会模拟“人走了水没关”，用于算法预警演示；
        该异常不参与故障判定，故障仍只由用户反馈决定。
        """
        sid = log['stall_id']
        start, end = log['start_time'], log['end_time']
        dur = log['duration_seconds']
        flow = log['flow_rate']
        total = log['water_usage']

        # 超长占用（>60 分钟）→ 记录一条算法预警片段
        if dur >= 60 * 60:
            self._anomaly_episodes.append({
                'stall_id':   sid,
                'type':       '超长占用',
                'detected_at': start + timedelta(minutes=60),
                'method':     'rule',
                'score':      round(random.uniform(0.70, 0.99), 4),
                'severity':   'high',
            })

        events = [
            ('door',       {'state': 'open'},   None, start - timedelta(seconds=12)),
            ('door',       {'state': 'closed'}, None, start - timedelta(seconds=10)),
            ('lock',       {'state': 'locked'}, None, start - timedelta(seconds=9)),
        ]
        # 水流开启后按 0/25%/50%/75% 四个时间点采样，读数围绕基准流量小幅波动
        for frac in (0.0, 0.25, 0.5, 0.75):
            sample_flow = round(max(4.0, flow + random.uniform(-0.4, 0.4)), 2)
            events.append((
                'water_flow',
                {'state': 'on', 'sampled_liters': round(total * frac, 1)},
                sample_flow,
                start + timedelta(seconds=5 + dur * frac),
            ))

        leak = random.random() < 0.001
        if leak:
            # 人离开但水没关：门开了水流仍在，持续 8~20 分钟
            leak_minutes = random.uniform(8, 20)
            events.append(('lock', {'state': 'unlocked'}, None,
                           end + timedelta(seconds=1)))
            events.append(('door', {'state': 'open'}, None,
                           end + timedelta(seconds=4)))
            t = end + timedelta(seconds=4)
            stop = end + timedelta(minutes=leak_minutes)
            cum = total
            while t < stop:
                t += timedelta(minutes=4)
                cum += flow * 4
                events.append(('water_flow',
                               {'state': 'on', 'sampled_liters': round(cum, 1)},
                               round(max(4.0, flow + random.uniform(-0.4, 0.4)), 2),
                               t))
            events.append(('water_flow',
                           {'state': 'off', 'sampled_liters': round(cum, 1)},
                           0.0, t + timedelta(seconds=3)))
            self._anomaly_episodes.append({
                'stall_id':   sid,
                'type':       '持续出水',
                'detected_at': end + timedelta(minutes=8),
                'method':     'isolation_forest',
                'score':      round(random.uniform(0.75, 0.99), 4),
                'severity':   'high',
            })
        else:
            events.append(('water_flow',
                           {'state': 'off', 'sampled_liters': round(total, 1)},
                           0.0, end - timedelta(seconds=3)))
            events.append(('lock', {'state': 'unlocked'}, None,
                           end + timedelta(seconds=1)))
            events.append(('door', {'state': 'open'}, None,
                           end + timedelta(seconds=4)))

        events.sort(key=lambda e: e[3])
        device_suffix = {'door': 'DOOR', 'lock': 'LOCK', 'water_flow': 'FLOW'}

        rows = []
        for sensor_type, value, flow_rate, timestamp in events:
            # 上报延迟 0.3~2 秒
            received = timestamp + timedelta(seconds=random.uniform(0.3, 2.0))
            rows.append((
                sid, sensor_type, Json(value), flow_rate, timestamp, received,
                'simulated', f"DEV-{sid}-{device_suffix[sensor_type]}",
            ))
        return rows

    # ---------- 构造算法渠道的异常预警（与故障判定无关） ----------
    def _build_anomaly_rows(self, cur):
        """
        只把传感器观测到的异常片段（超长占用 / 持续出水）写成预警记录；
        是否真实故障由用户反馈（feedbacks）决定：反馈窗口覆盖该时刻才记
        confirmed_fault=True，误报记 False，其余留 NULL（待核验）。
        """
        rows = []
        for ep in self._anomaly_episodes:
            status = random.choices(
                ['pending', 'confirmed', 'resolved', 'dismissed'],
                weights=[0.35, 0.30, 0.25, 0.10], k=1)[0]
            covered = any(
                f['stall_id'] == ep['stall_id']
                and f['fault_start'] <= ep['detected_at'] <= f['fault_end']
                for f in self._feedbacks)
            if covered:
                confirmed = True
            elif status == 'dismissed':
                confirmed = False
            else:
                confirmed = None
            resolved = (ep['detected_at'] + timedelta(hours=random.uniform(1, 12))
                        if status == 'resolved' else None)

            # 关联该时刻附近的传感器事件
            cur.execute("""
                SELECT event_id FROM sensor_events
                WHERE stall_id = %s
                  AND sensor_timestamp BETWEEN %s AND %s
                ORDER BY ABS(EXTRACT(EPOCH FROM (sensor_timestamp - %s)))
                LIMIT 1
            """, (ep['stall_id'],
                  ep['detected_at'] - timedelta(minutes=30),
                  ep['detected_at'] + timedelta(minutes=30),
                  ep['detected_at']))
            found = cur.fetchone()
            event_id = found[0] if found else None

            desc = (f"算法检测到 {ep['stall_id']} 隔间{ep['type']}，"
                    f"异常分数 {ep['score']:.2f}，建议现场核查。")
            rows.append((
                ep['stall_id'], event_id, ep['detected_at'], ep['method'],
                ep['type'], ep['score'], ep['severity'], status,
                confirmed, resolved, desc,
            ))
        return rows

    # ---------- 生成结果统计 ----------
    def print_stats(self):
        cur = self.pg.cursor()
        cur.execute("""
            SELECT COUNT(*),
                   COUNT(*) FILTER (WHERE is_fault),
                   AVG(wait_seconds),
                   COUNT(*) FILTER (WHERE wait_seconds = 0),
                   COUNT(*) FILTER (WHERE hour_of_day BETWEEN 21 AND 22)
            FROM shower_logs
        """)
        n_logs, n_fault, avg_wait, n_zero, n_peak = cur.fetchone()
        cur.execute("SELECT ticket_status, COUNT(*) FROM queue_tickets "
                    "GROUP BY ticket_status ORDER BY 2 DESC")
        status_rows = cur.fetchall()
        cur.execute("SELECT COUNT(*) FROM sensor_events")
        n_sensors = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM feedbacks")
        n_feedbacks = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM anomaly_events")
        n_anomalies = cur.fetchone()[0]
        cur.close()

        print("\n=== 生成结果统计 ===")
        print(f"  洗浴日志        : {n_logs}")
        print(f"  故障日志        : {n_fault}（{n_fault / max(n_logs, 1):.2%}）")
        print("  排队票据        : "
              + "，".join(f"{s}={c}" for s, c in status_rows))
        print(f"  放弃排队        : {self._abandoned} 人次")
        print(f"  传感器事件      : {n_sensors}")
        print(f"  用户反馈        : {n_feedbacks}")
        print(f"  算法异常事件    : {n_anomalies}")
        print(f"  平均等待        : {float(avg_wait or 0):.1f} 秒 "
              f"({float(avg_wait or 0) / 60:.1f} 分钟)")
        print(f"  零等待占比      : {n_zero / max(n_logs, 1):.2%}")
        print(f"  高峰(21-23)占比 : {n_peak / max(n_logs, 1):.2%}")

    # ---------- 实时状态模拟（写 Redis） ----------
    def tick(self):
        """随机挑 1~5 个隔间变更状态，并刷新区域空闲计数"""
        if not self.r:
            return
        for stall in random.sample(self.stalls, k=random.randint(1, 5)):
            state = random.choices(
                STALL_STATES, weights=[0.5, 0.4, 0.08, 0.02], k=1
            )[0]
            self.r.hset(f"stall:{stall}", mapping={
                "status": state,
                "updated_at": time.time()
            })
        free = sum(
            1 for s in self.stalls
            if (self.r.hget(f"stall:{s}", "status") or b"idle") == b"idle"
        )
        self.r.set("area:A:free_count", free)

    # ---------- 演示/测试接口 ----------
    def trigger_peak(self):
        """一键晚高峰: 80% 隔间设为占用（对应 PDF 的 trigger-peak）"""
        if not self.r:
            return
        n_busy = int(self.n_stalls * 0.8)
        busy = set(random.sample(self.stalls, k=n_busy))
        for stall in self.stalls:
            state = "occupied" if stall in busy else "idle"
            self.r.hset(f"stall:{stall}", mapping={"status": state})
        self.r.set("area:A:free_count", self.n_stalls - n_busy)
        print(f"[Simulator] 晚高峰已触发: {n_busy}/{self.n_stalls} 占用")

    def toggle_stall(self, stall_id: str, status: str):
        """把指定隔间设为某状态（对应 PDF 的 toggle-stall）"""
        if not self.r:
            return
        if stall_id not in self.stalls:
            raise ValueError(f"未知隔间 {stall_id}，合法范围 {self.stalls[0]}~{self.stalls[-1]}")
        if status not in STALL_STATES:
            raise ValueError(f"状态必须是 {STALL_STATES} 之一")
        self.r.hset(f"stall:{stall_id}", mapping={"status": status})
        print(f"[Simulator] {stall_id} -> {status}")


# ==================== 入口 ====================
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode',
                        choices=['history', 'realtime', 'peak', 'demo', 'export'],
                        default='demo')
    parser.add_argument('--users', type=int, default=300,
                        help='每天到访人数（一栋女生楼规模，默认 300）')
    parser.add_argument('--days', type=int, default=90,
                        help='历史仿真天数（默认 90）')
    parser.add_argument('--scenario', choices=['base', 'peak', 'test'], default='base',
                        help='base=日常到达；peak=75%% 到访集中在 21-23 点的压力场景；'
                             'test=独立测试场景（不参与训练）')
    parser.add_argument('--peak-share', type=float, default=None,
                        help='覆盖集中高峰到访占比（0~1，默认 peak=0.75，其余=0）')
    parser.add_argument('--seed', type=int, default=20261124,
                        help='随机种子（默认 20261124；测试场景建议用不同种子）')
    parser.add_argument('--export-dir', default=None,
                        help='export 模式下 CSV 输出目录（默认项目根目录）')
    parser.add_argument('--prefix', default=None,
                        help='export 模式下输出文件前缀（默认 sim_<scenario>）')
    args = parser.parse_args()

    cfg = load_config()

    if args.mode == 'demo':
        # 无需任何外部服务，直接打印样本验证分布
        sim = BathSimulator(cfg)
        print("=== demo 模式：5 条样本日志（仅验证分布）===")
        for i in range(5):
            day = datetime.now(TZ).replace(hour=sim.sample_hour(),
                                           minute=random.randint(0, 59))
            log = sim._demo_generate_log(day, is_winter=False, user_id=i + 1)
            print({k: (str(v) if isinstance(v, datetime) else v)
                   for k, v in log.items()})

        print("\n=== 有限状态机：门磁 / 门锁 / 水流 → 隔间状态 ===")
        samples = [
            ("closed", "locked", True),
            ("closed", "locked", False),
            ("closed", "unlocked", False),
            ("open", "unlocked", False),
            ("open", "unlocked", True),
        ]
        for door, lock, flow in samples:
            state, conf, note = infer_stall_state(door, lock, flow)
            print(f"  door={door:<8} lock={lock:<9} flow={str(flow):<5} "
                  f"-> {state}/{conf}/{note}")
        raise SystemExit(0)

    if args.mode == 'export':
        # 纯内存仿真 + CSV 导出，无需 PostgreSQL / Redis
        out_dir = args.export_dir or os.path.dirname(os.path.abspath(__file__))
        sim = BathSimulator(cfg)
        sim.simulate(user_count=args.users, days=args.days,
                     scenario=args.scenario, seed=args.seed,
                     peak_share=args.peak_share)
        sim._assign_faults()
        sim.export_csv(out_dir, prefix=args.prefix)
        raise SystemExit(0)

    import psycopg2
    import redis
    pg = psycopg2.connect(**DB_CONFIG)
    r = redis.Redis(host='localhost', port=6379, decode_responses=False)
    sim = BathSimulator(cfg, pg, r)

    if args.mode == 'history':
        sim.generate_history(user_count=args.users, days=args.days,
                             scenario=args.scenario)
    elif args.mode == 'realtime':
        while True:
            sim.tick()
            time.sleep(random.uniform(3, 5))
    elif args.mode == 'peak':
        sim.trigger_peak()
