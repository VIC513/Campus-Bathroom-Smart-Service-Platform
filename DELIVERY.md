# 校园浴室智慧服务平台 — 数据库与算法模块交付说明

| 项目 | 内容 |
|------|------|
| 模块 | ③ 数据库模块、④ 算法模块 |
| 版本 | v1.0.0 |
| 日期 | 2026-11-20 |
| 运行环境 | Anaconda Python 3.14（**禁止 3.8 及以下**，否则模型反序列化失败） |
| 依赖 | 见 `requirements.txt`（全部版本锁定） |

> 本文件为 GitHub 分支交付的入口说明。原始设计文档（Word/PDF）一并放在本目录，作为字段级与接口级权威依据。

---

## 一、目录内容

### 数据库模块

| 文件 | 说明 |
|------|------|
| `create_tables.py` | 建库建表初始化脚本：8 张表 DDL + CHECK 约束 + 外键 + 索引 + 初始化数据（15 个隔间、300 个模拟用户、system_config） |
| `bathroom_female_db.sql` | PostgreSQL 完整导出（表结构 + 数据），可直接 `psql -f` 恢复 |
| `bathroom_female_db.dump` | PostgreSQL 自定义格式备份，`pg_restore` 恢复 |
| `survey_config.py` | 问卷参数统计（51 份有效样本，9 题），导出 `config.json` |
| `config.json` | 仿真器与初始化用参数 |
| `shower_logs_new.csv` | 测试数据（23,002 条洗浴流水，含 1,278 条故障样本） |
| `shower_logs.csv` | 早期版本仿真数据（26,913 条） |

### 算法模块

| 文件 | 模块 / 版本 | 说明 |
|------|-------------|------|
| `wait_time_predictor.py` | 算法 / 等待时间预测 v1.0.0 | 三层架构：规则公式 → 统计校正 → LightGBM |
| `anomaly_detector.py` | 算法 / 异常检测 v1.0.0 | 三层组合：规则 → 3σ 统计离群 → IsolationForest |
| `queue_manager.py` | 算法 / 排队管理 v1.0.0 | 11 类排队业务场景，Redis 数据结构封装 |
| `recommender.py` | 算法 / 分流推荐 v1.0.0 | 跨区域分流推荐 |
| `run_anomaly_detection.py` | 算法 / 异常检测运行入口 v1.0.0 | 读 sensor_events/shower_logs，写 anomaly_events 并置隔间 fault |
| `train_pipeline.py` | 算法 / 模型训练流水线 v1.0.0 | 训练两个模型并输出 MAE / 召回率 / 特征重要性 |
| `simulator.py` | 数据 / 仿真数据生成 v1.0.0 | 按 GB 28378-2019 生成 6~9 L/min 流量数据 |
| `integration_demo.py` | 算法 / 集成演示 v1.0.0 | 内存 Redis + 合成数据，无需部署即可跑通四算法协同 |
| `wait_time_model.pkl` | 模型文件 | LightGBM + 统计层 + 规则层参数 |
| `anomaly_model.pkl` | 模型文件 | IsolationForest 模型 |

---

## 二、数据结构设计（8 张表）

> 完整字段见 `create_tables.py` 的 `SCHEMA_SQL`；字段级设计说明见《数据库参数设计.pdf》。

| 表 | 作用 | 关键字段 / 约束 |
|----|------|----------------|
| `users` | 用户账号 | `user_id`, `username UNIQUE`, `password_hash`, `role`, `account_status` |
| `stalls` | 隔间状态 | `stall_id`（CHECK 正则 `^F(0[1-9]\|1[0-5])$`，共 15 间）, `stall_status IN ('idle','occupied','cleaning','fault')`, `is_available`, `version`（乐观锁） |
| `queue_tickets` | 排队票据 | `ticket_status IN ('waiting','called','checked_in','completed','expired','cancelled')`, `prefer_stall_ids TEXT[]`（心仪隔间）, `device_id`（同设备限流）, `is_compensated`（故障补偿票）, `no_show_count`；**唯一索引** `uq_active_ticket(user_id, area_id) WHERE status IN (waiting,called,checked_in)` 保证一人一区一活跃票 |
| `shower_logs` | 洗浴流水（训练数据源） | `duration_seconds`, `water_usage`, `hour_of_day`, `is_weekend`, `queue_length_at_start`, `idle_stalls_at_start`, `wait_seconds`, `temperature`, `is_fault`, `fault_type`, `data_source` |
| `sensor_events` | 门磁 / 门锁 / 水流事件 | `sensor_type IN ('door','lock','water_flow')`, `sensor_value JSONB`, `flow_rate`, `device_id` |
| `anomaly_events` | 算法侧异常预警 | `detection_method IN ('rule','three_sigma','isolation_forest')`, `severity IN ('low','medium','high')`, `alert_status` |
| `feedbacks` | 用户报修反馈（**唯一故障来源**） | `fault_type`, `feedback_status`, 可关联 `anomaly_id` |
| `system_config` | 系统参数 | `config_key PK`, `config_value JSONB` |

**索引**：`shower_logs(start_time)`、`shower_logs(hour_of_day)`、`shower_logs(is_weekend)`、`sensor_events(sensor_timestamp)`、`feedbacks(stall_id)`、`anomaly_events(stall_id)`。

**关键约束**
- 隔间被判定 `fault` 后，在维修完成（`is_available=true`）前不可投入后续叫号。
- 时间字段统一 `TIMESTAMPTZ`，服务端时区 `Asia/Shanghai`。

---

## 三、Redis Key 说明

Redis 侧状态由 `queue_manager.py` 统一读写，前缀设计如下（`{}` 为变量占位）：

| Key | 类型 | 含义 | 写入方 | 读取方 | 过期策略 |
|-----|------|------|--------|--------|---------|
| `queue:{area_id}` | List（LPUSH/RPOP） | 区域排队队列，元素为用户票据 JSON | `join()` 入队、`call_next()` 出队 | `status()`, `call_next()`, `_queue_items()` | 不设过期，队列清空即自然为空 |
| `queue:{area_id}:counter` | String（INCR） | 区域序号发生器，用于生成取号号码 | `join()` | `status()`, `join()` | 不设过期 |
| `user:{user_id}:queue` | Hash | 用户当前票据快照（ticket_id、区域、状态、取号时间、是否临时离开等） | `join()`, `call_next()`, `check_in()`, `temp_leave()`, `complete()`, `leave()` | `status()` 查位次、`_pick_next()` 判重 | 用户离队/完成后删除，不设 TTL |
| `area:{area_id}:free_count` | String（INCR/DECR） | 区域空闲隔间数，供等待时间预测输入 | `on_stall_freed()`, `on_stall_occupied()`, `_reserve_stall()`, `_release_stall()` | `join()` 取特征、`status()` 展示 | 不设过期，以 DB `stalls` 为准校准 |
| `device:{device_id}:active` | String（INCR/DECR） | 同一设备当前活跃取号数，用于规则 9 同设备限流 | `_device_incr()`, `_device_decr()` | `_device_count()` 限流判断 | 不设过期，随票据结束递减 |
| `stall:{stall_id}` | Hash | 隔间实时占用（当前用户、ticket_id、占用起始时间） | `_occupy_stall()`, `_release_stall()` | `_match_stall()`, `handle_door_event()` | 不设过期，隔间释放即删除 |

**一致性规则**：Redis 为实时读写的快照层，PostgreSQL 为持久化真相层。排队票据结束、故障上报、维修完成等关键节点必须双写（先 DB 后 Redis），服务启动时可用 DB 重建 Redis 快照。

---

## 四、数据读写规则

| 场景 | 写 | 读 |
|------|----|----|
| 用户取号 `join()` | `queue_tickets`（waiting）、`queue:{area}:*`、`user:{uid}:queue`、`device:{id}:active` | `stalls`（空闲数）、`system_config`（排队参数） |
| 叫号 `call_next()` | `queue_tickets`（called + expires_at）、Redis 出队 | `queue:*`、`stalls`、`wait_time_model.pkl` |
| 扫码核销 `check_in()` | `queue_tickets`（checked_in）、`stalls`（occupied）、`stall:{id}` | `queue_tickets`、`stalls` |
| 洗浴结束 `complete()` | `shower_logs`、`queue_tickets`（completed）、`stalls`（idle）、`area:*:free_count` | `user:{uid}:queue` |
| 用户报修 | `feedbacks`（**用户反馈是故障唯一判定来源**） | `stalls`、`anomaly_events`（用于关联，不互相替代） |
| 传感器异常检测 `run_anomaly_detection.py` | `anomaly_events`、`stalls`（fault + is_available=false） | `sensor_events`、`shower_logs` |
| 模型训练 `train_pipeline.py` | `wait_time_model.pkl`、`anomaly_model.pkl`（只读 DB，不写业务表） | `shower_logs` |

---

## 五、算法输入输出规范

### 5.1 等待时间预测 `WaitTimePredictor`

```python
from wait_time_predictor import WaitTimePredictor

p = WaitTimePredictor()
p.load("wait_time_model.pkl")

# 单条预测（参数版）
minutes = p.predict(
    queue_len=6, hour=21, temperature=28.5, avg_duration=16.0,
    free_stalls=2, gender="F", is_weekend=False, is_fault_area=False,
)
# -> float，单位分钟，>=0，保留 1 位小数

# 批量预测（DataFrame 版，需含同名列）
preds = p.predict(df=batch_df)
```

**特征（10 维，`FEATURE_COLS`）**：`queue_len`, `hour_seg`, `temperature`, `avg_duration`, `free_stalls`, `load_per_stall`, `is_peak`, `is_weekend`, `is_fault_area`, `gender_F`。

**三层降级**：训练时按验证集 MAE 自动优选层（ML 优先，其次统计，最后规则）；统计层未命中时回退规则层。

### 5.2 异常检测 `AnomalyDetector`

```python
from anomaly_detector import AnomalyDetector

d = AnomalyDetector(max_duration_min=60, sigma_k=3.0)
d.train(normal_logs)          # np.ndarray, shape=(n, 3): [duration_min, water_l, hour]

res = d.detect(duration_min=65, water_usage_l=3, hour=21,
               history_durations=[12, 15, 18, 14, 16, 13, 17, 15, 14, 16, 15])
# -> {'is_anomaly': bool, 'reason': str, 'score': float, 'timestamp': str}

alerts = d.scan_stalls([
    {'stall_id': 'F03', 'duration_min': 65, 'water_usage_l': 3, 'hour': 21},
], history_durations=hist)
# -> 仅返回异常隔间，字段为原字典 + detect() 结果
```

**三层判定**：规则（超长占用 >60min、用水 <5L、用水 >500L）→ 3σ 统计离群（需 ≥10 条历史）→ IsolationForest（`pred == -1`）。
**注意**：水温异常、门锁损坏等用户可感知类故障**无法仅靠传感器识别**，必须以 `feedbacks` 表作为唯一判定依据。

### 5.3 排队管理 `QueueManager`

```python
from queue_manager import QueueManager
import redis

qm = QueueManager(redis.Redis(host="localhost", port=6379, db=2))
t = qm.join(user_id="1001", area_id="A", hour=21)
qm.status("1001")             # {'position': n, 'ticket_id': ..., ...}
qm.call_next("A")             # 叫号并返回票据
qm.check_in("1001", stall_id="F07", area_id="A")
qm.report_fault("1001", stall_id="F07", fault_type="水压不足")   # 故障重排 + 补偿票
qm.temp_leave("1001"); qm.return_from_temp_leave("1001")
qm.complete("1001")
```

覆盖 11 类场景：叫号逾期、偏好隔间、预约冲突、线下空闲、线下排队、过期重排、重复取票、故障重排、恶意抢票、临时离开、扫码核销。

### 5.4 一键跑通（无需部署）

```bash
python integration_demo.py        # 内存 Redis + 合成数据，四算法协同演示
python train_pipeline.py          # 训练 + 评估（PostgreSQL 不可用时自动 fallback 到 CSV）
python run_anomaly_detection.py   # 需 PostgreSQL
```

---

## 六、效果验证结果（2026-11-20 实测）

数据来源：`shower_logs_new.csv`（23,002 条，故障 1,278 条，占 5.56%）；时间切分验证集 4,601 条。

### 等待时间预测

| 层 | 验证集 MAE |
|----|-----------|
| 规则公式 | 13.531 min |
| 统计校正 | 0.414 min |
| LightGBM | **0.406 min（最优）** |

特征重要性（ML 层）：`avg_duration 35.69%` > `temperature 30.71%` > `queue_len 17.46%` > `free_stalls 4.52%` > `weekend 3.62%`，其余均 <3%。

### 异常检测（IsolationForest，21,724 条正常样本训练）

| 指标 | 数值 |
|------|------|
| 召回率 | **1.56%（20 / 1,278）** |

> 该结果偏低是**已知问题**，见第八节。规则层与 3σ 层对典型故障仍有效，生产调用为三层组合判定。

---

## 七、已完成 / 未完成

### 已完成

- 8 张表 DDL、约束、外键、索引、初始化数据；`psql` 与 `pg_restore` 两种恢复方式
- 三层等待时间预测（含训练、持久化、自动降级、特征重要性输出）
- 三层异常检测（规则 + 3σ + IsolationForest）与批量扫描
- 11 类排队场景、Redis 6 个 Key 的完整读写
- 分流推荐、仿真数据生成、训练流水线、集成演示
- 硬编码密码清除（改为环境变量）、依赖版本锁定、`.gitignore`
- 训练评估结果固化（本文件第六节）

### 未完成

- 等待时间**逐场景**误差拆分（高峰 / 平峰 / 周末分别统计）
- 异常检测模型调优（降低阈值、引入用户反馈标签做半监督）
- 排队模块尚未做压测（高并发下 Redis 与 DB 双写一致性）
- 未接入真实硬件：`sensor_events` 目前为仿真数据（`data_source='simulated'`）

---

## 八、已知问题

1. **异常检测召回率仅 1.56%**：IsolationForest 仅用 3 维特征（时长、用水量、小时），对水压不足 / 堵塞类故障（用水量 24~48L，属正常区间）不敏感。当前生产链路依赖规则 + 3σ 兜底，ML 层需重训。
2. **规则公式 MAE 13.5 min**：原始规则明显高估等待时间，仅在统计 / ML 层缺失时兜底，不可单独使用。
3. **历史文档口径不一致**：旧文档写"shower_logs 5 万条"，实际为 2.3 万；旧版接口文档为 v1.0，以本目录《新版本的接口.docx》为准。
4. **PostgreSQL 未随包运行**：交付包只含 SQL / dump，需要目标机器本地安装并恢复。
5. **模型文件与 Python 版本强绑定**：必须用 Anaconda Python 3.14 加载，换版本需重训。

---

## 九、需要其他模块配合

| 配合方 | 需求 |
|--------|------|
| 后端 | ① 等待时间计算统一调用 `WaitTimePredictor.predict()`，**禁止另写公式**（口径唯一）；② 票据状态变更必须同步调用 `QueueManager`，保证 Redis 与 DB 双写；③ 模型加载在服务启动时完成，勿每请求 `load()` |
| 硬件 / IoT | 提供 `sensor_events` 真实数据接入协议（字段对齐 `sensor_type` / `flow_rate` / `device_id`），替换仿真源 |
| 前端 | 位次、预计等待、隔间状态需从 `status()` 与 `stalls` 读取，不自行推算 |
| 运维 | 部署时设置环境变量 `BATH_DB_PASSWORD`；Redis 建议独立 db（演示用 db=2）；数据库恢复后执行一次 `create_tables.py` 校准 |

---

## 十、部署与运行

```bash
# 1. 安装依赖（必须使用 Anaconda Python 3.14）
pip install -r requirements.txt

# 2. 配置数据库密码（不要写进代码）
# Windows PowerShell:
$env:BATH_DB_PASSWORD = "你的密码"
# Linux/macOS:
export BATH_DB_PASSWORD="你的密码"

# 3. 初始化数据库
python create_tables.py

# 4. 训练模型
python train_pipeline.py

# 5. 无环境快速验证
python integration_demo.py
```

---

## 十一、安全说明

- 交付包内**不含任何真实密码、密钥或 Token**；数据库密码通过 `BATH_DB_PASSWORD` 环境变量注入。
- `.gitignore` 已排除 `.env`、`__pycache__`、虚拟环境、IDE 配置等可再生成文件。
- 仓库中保留 `wait_time_model.pkl` / `anomaly_model.pkl` 与两份 CSV，因为它们是当前交付必需的模型与测试样本；若仓库对体积有要求，可后续迁移至 Git LFS。
