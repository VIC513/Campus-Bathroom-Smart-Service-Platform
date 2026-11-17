# 校园浴室智能调度平台 - 后端中台 (成员1)

本项目为纯软件架构的“数字孪生”后端，基于 FastAPI 构建，实现了从数据仿真、状态缓存、排队调度到实时推送的完整闭环。

## 🚀 核心技术栈
- **Web 框架**：FastAPI (Python 3.10+)
- **数据存储**：Redis (高速缓存与状态持久化)
- **实时通信**：WebSocket (状态秒级推送)
- **仿真引擎**：基于 66 份真实问卷分布的数据仿真器

## 📦 核心功能模块
1. **数据仿真器 (Simulator)**
   - 依据问卷调研（女生均时 18.8 分钟，晚高峰 21:00-23:00），自动模拟隔间状态流转（空闲 -> 占用 -> 空闲）。
   - 支持一键高峰、暂停/恢复。
2. **核心 API (20+ 接口)**
   - 用户端：查询隔间、获取建议、排队取号、取消排队、故障/清理报修。
   - 管理端：反馈列表查询、一键统一维修/清理、单个处理。
3. **Redis 缓存**
   - 隔间状态、排队队列、报修反馈均落盘 Redis，服务器重启数据不丢失。
4. **WebSocket 实时推送**
   - 端点：`ws://127.0.0.1:8000/ws/stalls`，仿真器状态变化每 5 秒主动推送前端。

## 🛠️ 本地运行指南
1. 启动 Redis (Memurai)
2. 安装依赖：`pip install fastapi uvicorn redis websockets`
3. 启动后端：`python -m uvicorn main:app --reload`
4. 访问 Swagger 文档：`http://127.0.0.1:8000/docs`

## 📊 答辩/演示流程
1. 调 `POST /sim/start` 启动仿真，观察隔间状态实时变化。
2. 调 `POST /queue` 模拟用户排队取号。
3. 运行 `test_ws.py` 展示 WebSocket 数据实时推送。
4. 调 `POST /feedback/fault` 和 `GET /admin/feedback` 展示报修处理闭环。

## 👨‍💻 负责人
- 成员1 (后端 Lead & DevOps)

本项目为纯软件架构的“数字孪生”后端，基于 Python FastAPI 构建。在无物理传感器硬件的限制下，通过自研的数据仿真引擎，实现了从状态生成、排队调度到实时推送的完整业务闭环。

## 📂 核心文件结构
- `main.py`：后端核心业务 API、状态机管理、多角色权限路由。
- `simulator.py`：数据仿真引擎，基于真实问卷的正态分布算法。
- `test_ws.py`：WebSocket 实时推送测试客户端。

---

## 🚀 模块一：数据仿真器引擎 (simulator.py)
**解决痛点**：无真实门磁/水流传感器，如何产生逼真数据？
**技术实现**：利用 `asyncio` 开启后台异步协程，每 5 秒执行一次 Tick，遍历所有隔间。
1. **真实参数注入**（基于 66 份校内问卷统计）：
   - 均值时长：女生平均 18.8 分钟（1130 秒）。
   - 波动标准差：450 秒，范围限定在 5 分钟 ~ 45 分钟之间。
   - 时段权重：根据问卷统计的 24 小时到达概率表（19:00-23:00 为绝对高峰，进入概率最大）。
2. **状态流转逻辑**：
   - 若隔间为 `idle`（空闲），根据当前小时权重生成随机数，命中则流转为 `occupied`（占用）。
   - 若隔间为 `occupied`（占用），累加已用时间；当时间超过系统生成的正态分布时长，流转回 `idle`。

---

## 🧠 模块二：核心业务 API 与状态机 (main.py)
系统采用严格的四态机设计：`idle` (空闲) / `occupied` (占用) / `cleaning` (清理中) / `fault` (维修中)。共提供 20+ 个 RESTful 接口。

### 1. 用户端 (C端) 接口
- **查状态与预测**：`GET /stalls` (查询隔间拓扑)、`GET /predict` (根据排队人数及当前时段预测等待时长)、`GET /recommend` (给用户前往建议)。
- **排队取号**：`POST /queue` (有空位直接分配，无空位进入队列)。
- **一键反馈（闭环起点）**：
  - `POST /feedback/fault` (报修)：严格校验原因（水温异常/门锁损坏等）。
  - `POST /feedback/cleaning` (报清理)：严格校验原因（地面湿滑/有垃圾等）。

### 2. 管理端 (B端) 接口
- **工单处理**：`GET /admin/feedback` (查看用户反馈列表)、`GET /admin/pending` (查看待处理清单)。
- **全局开关控制**：
  - `POST /admin/maintenance-all`：一键让所有隔间进入维修，再点一次恢复空闲。
  - `POST /admin/cleaning-all`：一键让所有隔间进入保洁，再点一次恢复空闲。
- **单点闭环（终点）**：`POST /admin/fault-done` 和 `POST /admin/cleaning-done`，处理完成后隔间恢复 `idle`，并同步更新反馈工单状态。

---

## ⚡ 模块三：WebSocket 实时推送 (结合 test_ws.py)
**解决痛点**：传统前端 HTTP 轮询导致高延迟与服务器高压。
**技术实现**：
- 在 FastAPI 中挂载 WebSocket 端点：`ws://127.0.0.1:8000/ws/stalls`。
- 由 `ConnectionManager` 类统一管理所有活跃的前端连接。
- **触发机制**：当 `simulator.py` 每次改变状态并写回数据后，立即触发回调，将最新的完整隔间状态数组通过 WebSocket 广播给所有在线的前端。
- **实测效果**：运行 `test_ws.py`，终端无需刷新，每 5 秒自动打印 `[收到] stalls_update`，证明数据实现了毫秒级主动推送。

---

## 💻 快速启动与测试
1. 启动 Memurai (本地 Redis 服务)。
2. 终端运行：`python -m uvicorn main:app --reload`
3. 浏览器打开 Swagger UI：`http://127.0.0.1:8000/docs`
4. 终端运行：`python test_ws.py` (观察实时推送)。

# 校园浴室智能调度系统 - 后端核心中台 (v5.0 终极闭环版)
**负责人**：成员1 (后端 Lead & DevOps)
**项目架构**：Python FastAPI + Redis + WebSocket + JWT (纯软件数字孪生架构)

本项目为无物理硬件支撑下的“纯软件数字孪生”中台。基于用户线上取号、线下扫码的物理交互逻辑，实现了从数据仿真、状态机流转、Redis 高并发排队、JWT 权限隔离到 WebSocket 秒级推送的完整业务闭环。

---

## 🚀 一、 核心业务闭环 (答辩绝对亮点)

系统彻底打通了“线上排队”与“线下物理隔间”的交互，引入了严格的**状态机与超时核销机制**：
1. **线上取号 (reserved)**：用户线上取号，系统分配隔间，状态变为 `reserved`（已预约/待核销）。
2. **线下扫码 (occupied)**：用户到达浴室扫码。后端校验票号与隔间是否匹配，匹配则变更为 `occupied`（使用中）。
3. **超时作废 (expired)**：若分配后 3 分钟内未扫码，仿真器后台自动触发倒计时，释放隔间变回 `idle`，并作废用户票号，提示重新排队。
4. **线下冲突拦截**：若用户拿着 F01 的票去扫 F05 的门，系统将直接拦截并报错警告，防止资源抢占。

---

## 🔗 二、 前后端联调与状态字典（前端必看）

**1. 鉴权机制**
- 登录接口：`POST /auth/login`（预置测试账号：`admin/admin123`，`user/user123`）
- 前端需在 Header 中携带：`Authorization: Bearer <access_token>`

**2. 隔间状态字典 (Status)**
前端页面（红绿黄大盘）需要根据以下状态渲染颜色：
- `idle`：空闲（绿色，可扫码）
- `reserved`：已预约，等待扫码（黄色，**不可被他人扫码占用**）
- `occupied`：使用中（红色）
- `cleaning` / `fault`：保洁/维修（灰色，不可用）

**3. 用户票号状态字典**
通过 `GET /queue/my_ticket` 获取：
- `no_ticket`：无票（显示去取号）
- `queuing`：排队中（显示前面还有几人）
- `calling`：已叫号分配（显示去对应隔间扫码）
- `expired`：超时作废（弹窗提示“超时未核销，请重新排队”）

---

## 📚 三、 核心 API 模块清单

### 1. 认证模块
- `POST /auth/register` : 注册账号
- `POST /auth/login` : 登录获取 Token

### 2. 用户操作模块 (需 User Token)
- `POST /queue` : 排队取号（自动分配空闲隔间并设为 `reserved`）。
- `GET /queue/my_ticket` : 查询我的票号状态（含排队位次、分配隔间、超时提示）。
- `POST /queue/cancel` : 取消排队或取消已分配隔间。
- **`POST /queue/scan_checkin`** : **扫码核销核心接口**。前端传入扫到的 `stall_id`，后端执行：线下现场取号、进错门拦截、正常核销变 `occupied` 等全量逻辑。

### 3. 管理员模块 (需 Admin Token)
- `GET /admin/feedback` / `GET /admin/pending` : 查工单与待处理。
- `POST /admin/maintenance-all` / `POST /admin/cleaning-all` : 一键全局维修/清理。
- `POST /admin/fault-done` / `POST /admin/cleaning-done` : 单个完结工单。

### 4. 演示与仿真控制 (需 Admin Token)
- `POST /sim/start` / `POST /sim/stop` : 启停后台自动仿真器。
- `POST /sim/trigger-peak` : 一键触发晚高峰。
- `POST /sim/reset` : 清空所有状态，重置系统。

---

## 💻 四、 本地启动指南

1. **前置要求**：安装 Memurai (Redis) 并保持后台运行。
2. **安装依赖**：`pip install fastapi uvicorn redis pyjwt websockets`
3. **启动后端**：`python -m uvicorn main:app --reload`
4. **交互式文档**：浏览器打开 `http://127.0.0.1:8000/docs`。

**👉 测试演示核心流程**：
1. `POST /sim/reset` 重置。
2. 登录获取 Token。
3. `POST /queue` 取票（拿到 F01）。
4. `POST /queue/scan_checkin` 传入 F01，模拟线下扫码。
5. 观察 `GET /stalls` 中 F01 变成红色的 `occupied`。
6. 等待 3 分钟（或修改代码里的超时时间），观察 F01 自动释放回 `idle`。