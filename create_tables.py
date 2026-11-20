# -*- coding: utf-8 -*-
# 模块归属: 数据库 / 建表与初始化  |  版本: v1.0.0  |  日期: 2026-11-20
"""
create_tables.py
女生宿舍楼 15 个隔间建库建表（按《数据库参数设计.pdf》的 8 张表结构）
依赖：pip install psycopg2-binary
用法：先运行 survey_config.py（生成参数），再运行本脚本
"""

import os
import hashlib

import psycopg2
from psycopg2 import sql
from psycopg2.extras import Json

from survey_config import SYSTEM_CONFIG_INIT

# ============================================================
# 1. 数据库连接配置（与 simulator.py / train_pipeline.py 保持一致）
# ============================================================
DB_CONFIG = {
    "dbname":   "bathroom_female_db",
    "user":     "postgres",
    "password": os.environ.get("BATH_DB_PASSWORD", ""),
    "host":     "localhost",
    "port":     "5432",
}

# 模拟规模：一栋女生宿舍楼 300 人共用这 15 个隔间
TOTAL_USERS = 300
SIMULATION_DAYS = 90

# 8 张表（按依赖顺序建表，DROP 时用 CASCADE 处理外键）
EXPECTED_TABLES = [
    "users", "stalls", "queue_tickets", "shower_logs",
    "sensor_events", "anomaly_events", "feedbacks", "system_config",
]

# ============================================================
# 2. 建表 SQL（字段与约束严格按设计 DDL，时间统一用 TIMESTAMPTZ）
# ============================================================
SCHEMA_SQL = """
-- 逆依赖顺序删除（CASCADE 处理外键引用）
DROP TABLE IF EXISTS feedbacks CASCADE;
DROP TABLE IF EXISTS anomaly_events CASCADE;
DROP TABLE IF EXISTS sensor_events CASCADE;
DROP TABLE IF EXISTS shower_logs CASCADE;
DROP TABLE IF EXISTS queue_tickets CASCADE;
DROP TABLE IF EXISTS stalls CASCADE;
DROP TABLE IF EXISTS system_config CASCADE;
DROP TABLE IF EXISTS users CASCADE;

-- 1. 用户表
CREATE TABLE users (
    user_id         BIGSERIAL PRIMARY KEY,
    username        VARCHAR(50) UNIQUE NOT NULL,
    password_hash   TEXT NOT NULL,
    role            VARCHAR(20) NOT NULL DEFAULT 'student',
    account_status  VARCHAR(20) NOT NULL DEFAULT 'active',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_login_at   TIMESTAMPTZ
);

-- 2. 隔间表
CREATE TABLE stalls (
    stall_id          VARCHAR(10) PRIMARY KEY,
    area_id           VARCHAR(20) NOT NULL DEFAULT 'A',
    stall_status      VARCHAR(20) NOT NULL DEFAULT 'idle',
    is_available      BOOLEAN NOT NULL DEFAULT TRUE,
    current_ticket_id BIGINT,
    occupied_since    TIMESTAMPTZ,
    last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    version           INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT chk_stall_status CHECK (stall_status IN ('idle','occupied','cleaning','fault')),
    CONSTRAINT chk_stall_id CHECK (stall_id ~ '^F(0[1-9]|1[0-5])$')
);

-- 3. 排队票据表
CREATE TABLE queue_tickets (
    ticket_id          BIGSERIAL PRIMARY KEY,
    user_id            BIGINT NOT NULL REFERENCES users(user_id),
    area_id            VARCHAR(20) NOT NULL,
    reserved_stall_id  VARCHAR(10) REFERENCES stalls(stall_id),
    ticket_status      VARCHAR(20) NOT NULL,
    joined_at          TIMESTAMPTZ NOT NULL,
    called_at          TIMESTAMPTZ,
    expires_at         TIMESTAMPTZ,
    checked_in_at      TIMESTAMPTZ,
    finished_at        TIMESTAMPTZ,
    cancelled_at       TIMESTAMPTZ,
    check_in_method    VARCHAR(20),
    prefer_stall_ids   TEXT[],                                -- 规则2 心仪隔间偏好
    device_id          VARCHAR(64),                           -- 规则9 同设备限流
    source             VARCHAR(10) NOT NULL DEFAULT 'online',
    is_compensated     BOOLEAN NOT NULL DEFAULT FALSE,        -- 规则8 故障补偿票据
    no_show_count      SMALLINT NOT NULL DEFAULT 0,           -- 爽约次数统计
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT chk_ticket_status CHECK (ticket_status IN ('waiting','called','checked_in','completed','expired','cancelled')),
    CONSTRAINT chk_ticket_source CHECK (source IN ('online','offline'))
);
CREATE UNIQUE INDEX uq_active_ticket ON queue_tickets(user_id, area_id)
    WHERE ticket_status IN ('waiting','called','checked_in');

-- 4. 洗浴流水日志表（供算法训练；与 simulator.py 写入字段一一对应）
CREATE TABLE shower_logs (
    id                      BIGSERIAL PRIMARY KEY,
    stall_id                VARCHAR(10) NOT NULL,
    user_id                 BIGINT,
    gender                  VARCHAR(10),
    start_time              TIMESTAMPTZ NOT NULL,
    end_time                TIMESTAMPTZ NOT NULL,
    duration_seconds        INTEGER NOT NULL,
    water_usage             DOUBLE PRECISION,
    hour_of_day             SMALLINT NOT NULL,
    day_of_week             SMALLINT NOT NULL,
    is_weekend              BOOLEAN NOT NULL DEFAULT FALSE,
    queue_length_at_start   INTEGER NOT NULL DEFAULT 0,
    idle_stalls_at_start    INTEGER NOT NULL DEFAULT 0,
    wait_seconds            INTEGER NOT NULL DEFAULT 0,
    temperature             DOUBLE PRECISION,
    is_fault                BOOLEAN NOT NULL DEFAULT FALSE,
    fault_type              VARCHAR(50),
    ticket_id               BIGINT,
    data_source             VARCHAR(20) NOT NULL DEFAULT 'simulated'
);

-- 5. 传感器事件表（门磁 / 门锁 / 水流）
CREATE TABLE sensor_events (
    event_id         BIGSERIAL PRIMARY KEY,
    stall_id         VARCHAR(10) NOT NULL REFERENCES stalls(stall_id),
    sensor_type      VARCHAR(20) NOT NULL,
    sensor_value     JSONB NOT NULL,
    flow_rate        DOUBLE PRECISION,
    sensor_timestamp TIMESTAMPTZ NOT NULL,
    received_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    data_source      VARCHAR(20) NOT NULL DEFAULT 'simulated',
    device_id        VARCHAR(50),
    CONSTRAINT chk_sensor_type CHECK (sensor_type IN ('door','lock','water_flow'))
);

-- 6. 异常预警表（算法渠道）
CREATE TABLE anomaly_events (
    anomaly_id       BIGSERIAL PRIMARY KEY,
    stall_id         VARCHAR(10) NOT NULL REFERENCES stalls(stall_id),
    event_id         BIGINT REFERENCES sensor_events(event_id),
    detected_at      TIMESTAMPTZ NOT NULL,
    detection_method VARCHAR(30) NOT NULL,
    anomaly_type     VARCHAR(50) NOT NULL,
    anomaly_score    DOUBLE PRECISION,
    severity         VARCHAR(20) NOT NULL,
    alert_status     VARCHAR(20) NOT NULL DEFAULT 'pending',
    confirmed_fault  BOOLEAN,
    resolved_at      TIMESTAMPTZ,
    description      TEXT,
    CONSTRAINT chk_detection_method CHECK (detection_method IN ('rule','three_sigma','isolation_forest')),
    CONSTRAINT chk_severity CHECK (severity IN ('low','medium','high')),
    CONSTRAINT chk_alert_status CHECK (alert_status IN ('pending','confirmed','resolved','dismissed'))
);

-- 7. 用户报修反馈表（用户渠道，与算法渠道相互独立）
CREATE TABLE feedbacks (
    feedback_id     BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES users(user_id),
    stall_id        VARCHAR(10) NOT NULL REFERENCES stalls(stall_id),
    fault_type      VARCHAR(50) NOT NULL,
    description     TEXT,
    feedback_status VARCHAR(20) NOT NULL DEFAULT 'pending',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at     TIMESTAMPTZ,
    anomaly_id      BIGINT REFERENCES anomaly_events(anomaly_id),
    CONSTRAINT chk_feedback_status CHECK (feedback_status IN ('pending','processing','resolved'))
);

-- 8. 系统配置表（JSONB 值）
CREATE TABLE system_config (
    config_key   VARCHAR(100) PRIMARY KEY,
    config_value JSONB NOT NULL,
    description  TEXT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by   BIGINT REFERENCES users(user_id)
);

-- 索引：保留原有日志索引，并补充传感器 / 反馈 / 异常索引
CREATE INDEX idx_logs_start_time ON shower_logs(start_time);
CREATE INDEX idx_logs_hour ON shower_logs(hour_of_day);
CREATE INDEX idx_logs_weekend ON shower_logs(is_weekend);
CREATE INDEX idx_sensor_timestamp ON sensor_events(sensor_timestamp);
CREATE INDEX idx_feedback_stall ON feedbacks(stall_id);
CREATE INDEX idx_anomaly_stall ON anomaly_events(stall_id);
"""

# ============================================================
# 3. 初始化 SQL
# ============================================================
# 15 个隔间 F01..F15，全部位于 A 区，初始空闲可用
INIT_STALLS_SQL = """
INSERT INTO stalls (stall_id, area_id, stall_status, is_available)
SELECT 'F' || lpad(i::text, 2, '0'), 'A', 'idle', TRUE
FROM generate_series(1, 15) AS g(i);
"""

# 300 个模拟用户 sim_u001..sim_u300，演示口令 demo123 的 sha256
INIT_USERS_SQL = """
INSERT INTO users (username, password_hash, role, account_status)
SELECT 'sim_u' || lpad(i::text, 3, '0'), %s, 'student', 'active'
FROM generate_series(1, %s) AS g(i);
"""

# 问卷参数以外的工程/系统配置（PDF 要求的运行参数）
EXTRA_CONFIG = {
    "checkin_timeout_seconds":         180,
    "temp_leave_timeout_seconds":      600,    # 规则10 临时离开上限
    "max_active_tickets_per_device":   2,      # 规则9 同设备有效票据上限
    "fault_compensation_enabled":      True,   # 规则8 故障补偿插队
    "max_shower_duration_seconds":     3600,
    "sensor_offline_seconds":          30,
    "expected_release_window_minutes": 5,
    "simulator_enabled":               True,
    "anomaly_threshold":               None,   # 待标定
    "total_users":                     TOTAL_USERS,
    "simulation_days":                 SIMULATION_DAYS,
}

# ============================================================
# 4. system_config 的说明文字（问卷参数由 survey_config 提供）
# ============================================================
CONFIG_DESCRIPTIONS = {
    "total_samples":          "问卷有效样本数",
    "female_ratio":           "女生占比",
    "female_duration_mu":     "平均洗澡时长（秒）",
    "female_duration_sigma":  "洗澡时长标准差（秒）",
    "max_wait_tolerance":     "最大容忍等待时间（秒）",
    "cross_area_accept_rate": "跨区有效接受率",
    "recommend_walk_min":     "推荐步行阈值（分钟）",
    "peak_hour_start":        "高峰开始小时",
    "peak_hour_end":          "高峰结束小时",
    "total_stalls":           "总隔间数",
    "checkin_timeout_seconds":         "叫号后签到超时时间（秒）",
    "temp_leave_timeout_seconds":      "临时离开最长时间（秒），超时自动释放隔间",
    "max_active_tickets_per_device":   "同一设备允许的有效排队票据数",
    "fault_compensation_enabled":      "洗浴中故障后是否给予优先排队补偿",
    "max_shower_duration_seconds":     "单次洗浴最长时长（秒）",
    "sensor_offline_seconds":          "传感器离线判定阈值（秒）",
    "expected_release_window_minutes": "隔间释放预期窗口（分钟）",
    "simulator_enabled":               "仿真器开关",
    "anomaly_threshold":               "异常检测阈值（待标定）",
    "total_users":                     "模拟用户规模（半栋楼人数）",
    "simulation_days":                 "历史仿真天数",
}


# ============================================================
# 5. 工具函数
# ============================================================
def _to_json_scalar(raw):
    """把 survey_config 里的字符串常量转成 JSON 数值/字符串"""
    if not isinstance(raw, str):
        return raw
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


def _build_config_values() -> dict:
    """合并问卷参数与 PDF 要求的运行参数，得到 system_config 的完整键值"""
    values = {k: _to_json_scalar(v) for k, v in SYSTEM_CONFIG_INIT.items()}
    values.update(EXTRA_CONFIG)
    return values


# ============================================================
# 6. 执行函数
# ============================================================
def create_database_if_not_exists():
    """连接到默认 postgres 库，创建目标数据库（若不存在）"""
    conn = psycopg2.connect(
        dbname="postgres",
        user=DB_CONFIG["user"],
        password=DB_CONFIG["password"],
        host=DB_CONFIG["host"],
        port=DB_CONFIG["port"],
    )
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s",
        (DB_CONFIG["dbname"],)
    )
    exists = cur.fetchone()
    if not exists:
        cur.execute(
            sql.SQL("CREATE DATABASE {}").format(
                sql.Identifier(DB_CONFIG["dbname"])
            )
        )
        print(f"[OK] 数据库 {DB_CONFIG['dbname']} 创建成功")
    else:
        print(f"[INFO] 数据库 {DB_CONFIG['dbname']} 已存在")
    cur.close()
    conn.close()


def create_tables():
    """建表 + 初始化数据"""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # 1. 建表（DROPs 旧表并重建 8 张表）
        cur.execute(SCHEMA_SQL)
        print("[OK] 8 张表创建完成")

        # 2. 初始化 15 个隔间
        cur.execute(INIT_STALLS_SQL)
        print("[OK] 15 个隔间初始化完成")

        # 3. 初始化 300 个模拟用户（口令统一为 demo123 的 sha256）
        pwd_hash = hashlib.sha256(b"demo123").hexdigest()
        cur.execute(INIT_USERS_SQL, (pwd_hash, TOTAL_USERS))
        print(f"[OK] {TOTAL_USERS} 个模拟用户初始化完成")

        # 4. 初始化 system_config（JSONB 值）
        config_values = _build_config_values()
        for key, value in config_values.items():
            cur.execute(
                """
                INSERT INTO system_config (config_key, config_value, description)
                VALUES (%s, %s, %s)
                ON CONFLICT (config_key) DO UPDATE
                SET config_value = EXCLUDED.config_value,
                    description  = EXCLUDED.description,
                    updated_at   = NOW()
                """,
                (key, Json(value), CONFIG_DESCRIPTIONS.get(key, "")),
            )
        print(f"[OK] system_config 初始化完成，共 {len(config_values)} 条")

        conn.commit()
        print("[OK] 全部提交成功")

    except Exception as e:
        conn.rollback()
        print(f"[ERROR] 事务失败，已回滚：{e}")
        raise
    finally:
        cur.close()
        conn.close()


def verify():
    """验证表和初始数据"""
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("\n=== 验证：表列表 ===")
    cur.execute("""
        SELECT table_name FROM information_schema.tables
        WHERE table_schema = 'public'
        ORDER BY table_name
    """)
    tables = [row[0] for row in cur.fetchall()]
    for name in tables:
        print(" -", name)
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    extra = [t for t in tables if t not in EXPECTED_TABLES]
    print(f"  8 张表检查：{'OK' if not missing else '缺少 ' + str(missing)}"
          + (f"，多余：{extra}" if extra else ""))

    print("\n=== 验证：关键行数 ===")
    cur.execute("SELECT COUNT(*) FROM users")
    users = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM stalls")
    stalls = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM system_config")
    configs = cur.fetchone()[0]
    print(f"  users={users}（期望 {TOTAL_USERS}）"
          f"，stalls={stalls}（期望 15）"
          f"，system_config={configs}")

    print("\n=== 验证：stalls 表（前 3 行）===")
    cur.execute("""
        SELECT stall_id, area_id, stall_status, is_available
        FROM stalls ORDER BY stall_id LIMIT 3
    """)
    for row in cur.fetchall():
        print(f"  {row[0]} | area={row[1]} | status={row[2]} | available={row[3]}")

    cur.close()
    conn.close()


def test_constraints():
    """测试约束是否生效（测试事务最终回滚，不污染正式数据）"""
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = False
    cur = conn.cursor()

    print("\n=== 约束测试 ===")

    # 测试 1：非法隔间 ID
    try:
        cur.execute("INSERT INTO stalls (stall_id) VALUES ('F16')")
        conn.commit()
        print("[FAIL] F16 居然插入成功了")
    except Exception as e:
        conn.rollback()
        print(f"[OK] F16 被拒绝：{e.pgcode}")

    # 测试 2：非法隔间状态
    try:
        cur.execute("UPDATE stalls SET stall_status = 'unknown' WHERE stall_id = 'F01'")
        conn.commit()
        print("[FAIL] unknown 状态居然更新成功了")
    except Exception as e:
        conn.rollback()
        print(f"[OK] unknown 状态被拒绝：{e.pgcode}")

    # 测试 3：非法票据状态
    try:
        cur.execute("""
            INSERT INTO queue_tickets (user_id, area_id, reserved_stall_id,
                                       ticket_status, joined_at)
            VALUES (1, 'A', 'F01', 'unknown', NOW())
        """)
        conn.commit()
        print("[FAIL] 非法 ticket_status 居然插入成功了")
    except Exception as e:
        conn.rollback()
        print(f"[OK] 非法 ticket_status 被拒绝：{e.pgcode}")

    # 测试 4：正常插入排队票（插入后回滚，保证票据行数不被污染）
    try:
        cur.execute("""
            INSERT INTO queue_tickets (user_id, area_id, reserved_stall_id,
                                       ticket_status, joined_at)
            VALUES (1, 'A', 'F01', 'waiting', NOW())
        """)
        conn.rollback()
        print("[OK] 正常排队票插入成功（已回滚）")
    except Exception as e:
        conn.rollback()
        print(f"[FAIL] 正常插入失败：{e}")

    cur.close()
    conn.close()


if __name__ == "__main__":
    print("=== 开始初始化数据库和表 ===")
    create_database_if_not_exists()
    create_tables()
    verify()
    test_constraints()
    print("\n=== 全部完成 ===")
