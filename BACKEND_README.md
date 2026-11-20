# 校园浴室智能调度系统 - 后端交付说明 (v5.0 联调版)

本模块基于 FastAPI + Redis 实现，包含完整的排队状态机与数据仿真引擎。

## 一、 联调基础环境与账号
1. **启动方法**：
   - 确保本地 Redis (Memurai) 已启动。
   - 安装依赖：`pip install -r requirements.txt` (需包含 fastapi, uvicorn, redis, pyjwt, websockets, numpy)。
   - 启动服务：`uvicorn main:app --reload`。
   - 访问文档：`http://127.0.0.1:8000/docs`
2. **测试账号**：
   - 管理员：`admin` / `admin123`
   - 普通用户：`user` / `user123`
3. **联调神技 (必须看)**：
   - `POST /sim/reset`：清空一切脏数据，恢复 15 个空闲隔间。
   - `POST /sim/trigger-peak`：一键造 80% 占用率，方便前端调红绿黄大盘和排队。
   - `POST /sim/start`：启动后台自动仿真。

## 二、 Redis 数据结构说明 (供后端与数据库同学对齐)
所有 Key 均由后端自动初始化（见 `main.py` 的 `redis_init()`）：
| Key 名称 | 类型 | 说明 | 示例 |
| :--- | :--- | :--- | :--- |
| `stalls` | String (JSON) | 15个隔间的实时状态 | `[{"stall_id":"F01","status":"idle"...}]` |
| `queue:female` | List | 排队队列（先进先出） | `["F0001", "F0002"]` |
| `ticket_to_stall`| String (JSON) | 票号与隔间的映射 | `{"F0001": "F02"}` |
| `user:{name}:ticket`| String | 用户当前持有的票号 | `"F0001"` |
| `feedback` | String (JSON) | 报修/清理记录 | `[{"type":"fault", "status":"pending"}]` |

## 三、 核心业务闭环状态流转
系统包含 5 种状态：`idle`(空闲) -> `reserved`(待扫码) -> `occupied`(使用中) -> `idle`。
- **超时机制**：`reserved` 状态超过 3 分钟未扫码，仿真器自动回收隔间变 `idle`，并作废用户票号。
- **异常场景预留**：支持 `fault`（维修中）和 `cleaning`（清理中）状态，全局一键切换。

## 四、 历史数据生成 (交付给算法同学)
为支持算法同学的 LightGBM (等待预测) 和 Isolation Forest (异常检测) 训练，我们开发了 `data_generator.py`。
- **运行方法**：直接运行 `python data_generator.py`。
- **输出文件**：`shower_logs.csv`（包含过去 7 天的真实时序模拟）。
- **数据字段亮点**：
  - `queue_length_at_start`, `wait_seconds`：基于 66 份问卷的高峰排队逻辑生成（非纯 0 假数据）。
  - `temperature`：引入了冬季气温对洗浴时长的影响。
  - `is_fault`, `fault_type`：按 3% 概率随机注入的故障样本，供异常检测算法学习。

## 五、 目前已知问题 (待后续迭代修复)
1. **并发竞态**：取号/扫码接口直接读写 Redis，高并发下极小概率出现状态覆盖，下阶段计划引入 Redis 事务 (WATCH/MULTI) 或分布式锁。
2. **安全密钥**：`SECRET_KEY` 暂为硬编码，后续需抽离至 `.env` 环境变量。