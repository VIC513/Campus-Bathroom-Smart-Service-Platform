# 浴室智洗 BathSight · 高校浴室智能排队系统

利用低成本、无侵入的物联网硬件（门磁/水流传感器）+ 数据持久化 + 预测算法，
实现"数字孪生式"的浴室实时状态感知与智能排队预测。**全程无图像/摄像等侵入手段**，保护隐私。

## 团队分工（4 人精简）

| 角色 | 核心职责 | 产出物 |
|---|---|---|
| 后端 Lead & DevOps | FastAPI 核心 API、Redis 排队逻辑、WebSocket 实时推送、Docker 部署 | 后端中台、Swagger 文档、Docker 包 |
| 数据库与算法工程师 | PostgreSQL 表设计、历史数据生成、等待时间预测、跨区分流推荐 | SQL 脚本、Jupyter 报告、LightGBM 模型 |
| 主力前端工程师（本模块） | C 端用户界面：拓扑图实时变色、排队取号、分流推荐 | `frontend/`（本目录） |
| 全栈工程师 | 管理端 Admin Dashboard（Vue3）+ 数据仿真器 Simulator | 运营大盘、报修管理、仿真控制台 |

## 前端模块 `frontend/`

C 端 H5 移动端 Web，**深色科技风**视觉，优先保证答辩演示与视觉冲击力。

### 技术栈
- Vue 3 + Vite + TypeScript + Pinia + Vue Router
- Vant 4（移动端组件，按需导入）
- ECharts 5（未来 1 小时等待预测曲线，按需引入）
- Axios（REST）+ 原生 WebSocket（实时推送，含自动重连封装）

### 页面
| 路由 | 页面 | 说明 |
|---|---|---|
| `/login` | 登录 / 注册 | **学号或手机号 + 密码登录**；第一次使用可切换「注册」tab（账号格式校验：9 位学号或 11 位手机号，密码 ≥6 位，可填昵称，注册成功自动登录）。账号数据存本机 localStorage（密码哈希存储），后端不提供认证接口 |
| `/` | 楼栋列表首页 | **31 栋宿舍楼的浴室卡片**：楼名 + 区域 + 空闲/使用中/维修三态数量（绿/橙/红），点击进入该楼；数据**持续自动仿真**（每 2.5s 随机有人进/出/故障，无提示条/开关），1 号楼后端在线时以 WebSocket 推送为准 |
| `/bathhouse/:id` | 浴室详情 | **按分区的隔间网格**，每格用**文字**标明「空闲/使用中/维修」；点「空闲」格 → 确认预约 → 该格联动变「使用中」并跳转排队页；右上角「报修」弹窗支持**自定义问题**（选隔间 + 故障类型 + 自定义描述，提交后该格变「维修」）；底部嵌入**本楼数据大盘**（用水高峰曲线 + 各分区使用率 + 翻台率） |
| `/queue` | 我的排队 | 票根卡片：号码、前面人数、预计等待、叫号状态 |
| `/dashboard` | 数据大盘看板 | **每日用水高峰曲线**（手写 SVG 面积图，24 小时峰谷）+ **各区域浴室使用率/翻台率**榜单（按使用率降序；1 号楼为真实后端实时数据，其余 30 栋为模拟，均标注 实时/模拟 徽标） |
| `/profile` | 个人中心 | 用户卡片（显示登录账号）、今日统计（含「常去浴室」动态统计 + 点击弹窗）、功能入口（含「数据大盘看板」入口）、**退出登录** |
| 全部页面 | 登录守卫 | 路由级 `beforeEach` 守卫：未登录访问任意页面强制跳 `/login`（带回跳地址），已登录访问 `/login` 自动回首页；登录用户 `user_id`（学号/手机号）自动注入取号/报修接口 |

### 核心机制：无侵入感知与异常场景处理
项目全程**无摄像头/图像手段**，靠 门磁 + 水流传感器 组合判定：

| 场景 | 检测机制 | 系统处理 |
|---|---|---|
| 线下有人直接进浴室 | 门磁闭合 + 水流持续 >30s | 自动标记该隔间「使用中」，**无需取号**，状态实时推送（`/ws/stalls`） |
| 取号了但没去（放鸽子） | 排队号 Redis TTL（如 5 分钟）超时未核销 | **自动释放该号**，空位流转给下一位；门磁闭合 = 到场核销 |
| 没取号但想洗 | 有空位 → 直接进（传感器照样捕捉占用）；没空位 → 无排队身份 | 系统推荐去其他有空位的浴室（分流推荐） |
| 取号者未到、他人先用 | 传感器检测到隔间已被占用 | 该号**自动作废**，队列不卡死（传感器数据优先于号） |

一句话：**传感器自动感知是"地面真相"，取号只是预约加速器**——系统永远以传感器数据为准。

### 目录结构
```
frontend/
├── src/
│   ├── api/          # Axios 实例 + 接口封装（统一 REST 契约）
│   ├── mock/         # Mock 数据 + 数据仿真（后端就绪前驱动全 UI 闭环）
│   ├── socket/       # WebSocket 实时通道封装（含 MOCK 模拟推送）
│   ├── stores/       # Pinia 全局状态（浴室列表 + 我的排队 + 登录用户）
│   ├── components/   # BathhouseCard（浴室卡片）/ StallMap（拓扑图）
│   ├── views/        # 6 个核心页面（登录 + 首页 + 详情 + 排队 + 大盘 + 我的）
│   ├── types/        # 与后端共享的 TS 类型契约
│   ├── router/       # 路由
│   └── style.css     # 深色科技风全局样式
└── vite.config.ts    # 含 /api /ws 代理（指向 FastAPI）
```

### 快速开始
```bash
cd frontend
npm install
npm run dev        # http://localhost:5173（局域网手机可访问）
```

### 与后端对接（接口 v2.0）
前端已实现**「真实请求优先、失败自动回退 Mock」**策略（`src/api/index.ts` 的 `safeFetch`）：
- 后端（FastAPI at `http://localhost:8000`）启动后，前端**自动**请求真实接口；
- 后端未启动 / 网络异常时，自动 `console.warn` 并回退到 `src/mock/data.ts` 的模拟数据，UI 不白屏。

对接的接口清单（与后端 v2.0 文档一致）：
| 前端函数 | 后端接口 | 说明 |
|---|---|---|
| `getBathhouses` / `getBathhouse` | `GET /stalls` | 返回全部隔间，前端组装成"单浴室看板"（`stall_id/status/gender/used_seconds`） |
| `getWaitForecast` | `GET /predict` | 由 `wait_minutes` 生成未来 1 小时等待曲线 |
| `getRecommend` | `GET /recommend` | 分流推荐 |
| `takeTicket` | `POST /queue` | body `{user_id}`，返回 `ticket_id/position/estimated_wait` |
| `cancelTicket` | `POST /queue/cancel?ticket_id=` | 取消排队 |
| `reportFault` | `POST /feedback/fault` | body `{stall_id, reason, detail, user_id}`，reason 用**后端中文枚举**（映射见 `src/types` 的 `FAULT_REASON_MAP`）|
| `getWaterUsage` | —（文档未提供）| 仍走本地 Mock，待后端补充 `/water-usage` 类接口 |

关键约定：
- 隔间状态枚举**与后端对齐**：`idle / occupied / cleaning / fault`（见 `src/types/index.ts` 的 `StallStatus`）。
- 后端无"楼栋/分区"概念：前端把全部隔间视为当前这一个浴室（分区由 `stall_id` 首字母聚类，如 `F01 → F区`）。
- 代理已配好：`/api`（含 `rewrite` 剥离前缀）与 `/ws` 转发到 `http://localhost:8000`（FastAPI），见 `vite.config.ts`。
- 实时推送：后端 `ws://localhost:8000/ws/stalls`。前端 `src/socket/index.ts` 已对接**整包快照**格式 `{"type":"stalls_update","data":[...stalls...]}`，按 `stall_id` 就地同步隔间状态（兼容旧单条/数组格式）；后端未启动时静默失败，断开后每 3 秒自动重连。
- 首页保持**楼栋卡片列表**（`HomeView.vue`）：31 栋中「1 号楼浴室」对接真实后端（实时状态/预约/排队/报修），其余 30 栋沿用本地模拟数据；点击卡片进入 `BathhouseView` 详情页，详情页订阅实时推送。后端未启动时整体回退为 31 栋纯模拟。分区统一为字母分区（A区/B区/C区），不使用楼层命名。

### 开发规划（后续）
1. ❌ 说明：后端在线文档（Swagger）在 `http://127.0.0.1:8000/docs`，后端代码托管于 `github.com/VIC513/Campus-Bathroom-Smart-Service-Platform`（需仓库读权限）。
2. ~~真实 WebSocket 联调~~：淋浴位实时推送已接入（见上），待后端启动后联调验证。
3. 真机调试：`npm run dev -- --host` 后用手机访问局域网地址。
