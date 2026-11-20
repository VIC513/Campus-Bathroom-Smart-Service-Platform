# -*- coding: utf-8 -*-
# 模块归属: 算法 / 异常检测运行入口  |  版本: v1.0.0  |  日期: 2026-11-20
"""
基于三路传感器规则对历史数据做异常判定（只读 sensor_events / shower_logs / stalls，
只写 anomaly_events 与 stalls）。判定后 stall_status=fault、is_available=false，
维修前不可投入使用。

规则来源: 截图《实时数据获取》中的状态推断矩阵：
  门磁 open + 门锁 unlocked + 水流持续 on (≥3min) → 疑似未关水或设备异常 (故障级)
  water_flow on 与洗浴周期无重叠且持续 ≥3min       → 持续出水 (故障级)

故障级命中: stall 置 fault、is_available=false，并写 anomaly_events；
不删旧记录、不重建表 — 最小影响方案。
"""

import json
import os
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2.extras import Json, execute_values

DB_CONFIG = {
    "dbname": "bathroom_female_db", "user": "postgres",
    "password": os.environ.get("BATH_DB_PASSWORD", ""),
    "host": "localhost", "port": "5432",
}
TZ = timezone(timedelta(hours=8))


def load_events_and_logs(cur):
    """按 stall 聚合传感器事件，并加载洗浴周期"""
    cur.execute("""
        SELECT stall_id, sensor_timestamp, sensor_type,
               COALESCE(sensor_value->>'state','') AS state, flow_rate
        FROM sensor_events ORDER BY stall_id, sensor_timestamp
    """)
    events = {}
    for sid, ts, stype, state, flow in cur.fetchall():
        events.setdefault(sid, []).append((ts, stype, state, flow))

    cur.execute("SELECT stall_id, start_time, end_time FROM shower_logs ORDER BY 1,2")
    cycles = {}
    for sid, st, et in cur.fetchall():
        # 缓冲只给 +30s（结束关门的短暂物理延迟），
        # "人走后 8~20 分钟 flow=on" 的泄漏片段会自然落在周期外
        cycles.setdefault(sid, []).append((st, et + timedelta(seconds=30)))
    for sid in cycles:
        cycles[sid].sort()
    return events, cycles


def overlaps_any_cycle(ts, cycles):
    return any(s <= ts <= e for s, e in cycles)


def detect(cur):
    events, cycles = load_events_and_logs(cur)

    anomalies = []                 # (stall_id, detected_at, type, severity, desc, event_id)
    stalls_to_fault = {}           # stall_id -> 最早命中时刻 (datetime)

    def record(stall_id, detected_at, atype, severity, desc, eid):
        anomalies.append((stall_id, detected_at, 'sensor_fusion', atype,
                          1.0 if severity == 'high' else 0.75,
                          severity, 'confirmed', desc, eid))

    def mark_fault(stall_id, ts):
        prev = stalls_to_fault.get(stall_id)
        if prev is None or ts < prev:
            stalls_to_fault[stall_id] = ts

    def event_id_for(stall_id, sensor_type, ts):
        cur.execute("""
            SELECT event_id FROM sensor_events
            WHERE stall_id=%s AND sensor_type=%s
              AND ABS(EXTRACT(EPOCH FROM (sensor_timestamp - %s))) < 1
            LIMIT 1
        """, (stall_id, sensor_type, ts))
        row = cur.fetchone()
        return row[0] if row else None

    for stall_id, seq in events.items():
        cycle = cycles.get(stall_id, [])

        # === 规则 A: open + unlocked + 后续出现 water_flow on，持续 ≥3min ===
        i = 0
        n = len(seq)
        while i < n:
            ts, stype, state, _flow = seq[i]
            if stype == 'door' and state == 'open':
                # 往后找最近的 lock.unlocked 与 water_flow.on（同一洗浴序列内）
                j = i + 1
                lock_unlocked = None
                flow_on = None
                # 洗浴序列通常在门 open 后 30s 内出现
                while j < n and seq[j][0] - ts < timedelta(seconds=30):
                    if seq[j][1] == 'lock' and seq[j][2] == 'unlocked' and lock_unlocked is None:
                        lock_unlocked = seq[j][0]
                    if seq[j][1] == 'water_flow' and seq[j][2] == 'on' and flow_on is None:
                        flow_on = (seq[j][0], j)
                        break
                    j += 1
                if lock_unlocked and flow_on:
                    # flow_on 之后的 on 采样持续时长
                    base_ts, base_idx = flow_on
                    k = base_idx + 1
                    last_on = base_ts
                    while k < n and seq[k][1] == 'water_flow' and seq[k][2] == 'on':
                        last_on = seq[k][0]
                        k += 1
                    dur = last_on - base_ts
                    # 必须与任何洗浴周期不重叠（人走后水没关）
                    if (dur >= timedelta(minutes=3)
                            and not overlaps_any_cycle(base_ts, cycle)):
                        eid = event_id_for(stall_id, 'water_flow', base_ts)
                        desc = (f"多源融合异常: 门磁 open + 门锁 unlocked + 水流持续 on "
                                f"({dur.total_seconds()/60:.1f}min)，疑似未关水或设备故障")
                        record(stall_id, base_ts, '疑似未关水或设备异常',
                               'high', desc, eid)
                        mark_fault(stall_id, base_ts)
                        i = k
                        continue
            i += 1

        # === 规则 B: water_flow on 段与所有洗浴周期无重叠且持续 ≥3min ===
        water_seq = [(t, s) for t, s2, s, _f in seq if s2 == 'water_flow']
        k = 0
        while k < len(water_seq):
            ts, state = water_seq[k]
            if state != 'on':
                k += 1
                continue
            j = k + 1
            while j < len(water_seq) and water_seq[j][1] == 'on':
                j += 1
            off_ts = water_seq[j][0] if j < len(water_seq) else water_seq[-1][0]
            dur = off_ts - ts
            if not overlaps_any_cycle(ts, cycle) and dur >= timedelta(minutes=3):
                eid = event_id_for(stall_id, 'water_flow', ts)
                desc = (f"持续出水: 洗浴周期外水流未关 "
                        f"({dur.total_seconds()/60:.1f}min)，疑似忘记关水")
                record(stall_id, ts, '持续出水', 'high', desc, eid)
                mark_fault(stall_id, ts)
            k = j + 1

    # ---- 去重后写 anomaly_events ----
    seen = set()
    dedup = []
    for row in anomalies:
        key = (row[0], row[2], row[1].replace(microsecond=0, second=0))
        if key in seen:
            continue
        seen.add(key)
        dedup.append(row)

    if dedup:
        execute_values(cur, """
            INSERT INTO anomaly_events
            (stall_id, event_id, detected_at, detection_method, anomaly_type,
             anomaly_score, severity, alert_status, confirmed_fault, resolved_at, description)
            VALUES %s
        """, [
            (s, eid, dt, dmeth, atype, score, sev, alert, True, None, desc)
            for s, dt, dmeth, atype, score, sev, alert, desc, eid in dedup
        ], page_size=1000)
        print(f"[异常判定] 新增 anomaly_events = {len(dedup)} 条 "
              f"(其中 {len(stalls_to_fault)} 个 stall 需置 fault)")
    else:
        print("[异常判定] 未发现符合规则的故障片段")

    # ---- stall 级 fault 汇总 ----
    # 1) 本次 sensor 规则命中 → stall 置 fault
    # 2) 当前有未解决反馈的 stall（pending/processing 或 resolved_at 还在未来）→ stall 置 fault
    #    说明: 历史 confirmed anomaly_events 只表示曾有过告警，
    #          不能用来推断"当前 stall 坏了"，除非 unresolved feedback 同时存在
    cur.execute("""
        SELECT DISTINCT stall_id FROM feedbacks
        WHERE feedback_status IN ('pending','processing')
           OR (feedback_status='resolved' AND resolved_at > NOW())
    """)
    fb_faults = {sid for (sid,) in cur.fetchall()}
    stalls_to_fault.update({sid: None for sid in fb_faults})

    if stalls_to_fault:
        cur.execute("""
            UPDATE stalls
               SET stall_status = 'fault',
                   is_available = FALSE,
                   last_updated_at = NOW(),
                   version = version + 1
             WHERE stall_id = ANY(%s)
        """, (sorted(stalls_to_fault.keys()),))
        print(f"[异常判定] stalls 置 fault (不可投入使用) = "
              f"{sorted(stalls_to_fault.keys())}")
        print(f"   (本次 sensor 规则命中 {len([k for k in stalls_to_fault if stalls_to_fault[k] is not None])} 个, "
              f"feedback 未解决 {len(fb_faults)} 个)")
    else:
        print("[异常判定] 没有 stall 需要置 fault（全部 stall 当前状态正常）")

    return stalls_to_fault


def main():
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("=== 异常判定开始 ===")
    faults = detect(cur)
    conn.commit()

    print("\n=== stalls 现状 (更新后) ===")
    cur.execute("SELECT stall_id, stall_status, is_available FROM stalls ORDER BY stall_id")
    for row in cur.fetchall():
        print(f"  {row[0]}  status={row[1]:<10s} available={row[2]}")

    print("\n=== 最新 anomaly_events (前 15 条) ===")
    cur.execute("""
        SELECT stall_id, detected_at, anomaly_type, severity,
               alert_status, confirmed_fault
        FROM anomaly_events
        ORDER BY detected_at DESC
        LIMIT 15
    """)
    for row in cur.fetchall():
        print(f"  {row[0]}  {row[1]}  {row[2]:<14s} {row[3]:<8s} {row[4]:<10s} confirmed={row[5]}")

    cur.close()
    conn.close()
    print("\n=== 判定完成 ===")


if __name__ == '__main__':
    main()
