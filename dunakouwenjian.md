校园浴室智能调度系统 - 后端接口清单 (v5.0 终极版)

负责人：成员1
基础地址：http://127.0.0.1:8000

认证说明：

· 🔓 公共接口：无需登录，任何人可查。
· 👤 用户接口：需在 Header 携带 Authorization: Bearer <token>，角色为 user 或 admin。
· 🛡️ 管理员接口：需 Token 且角色必须为 admin。

一、 认证模块 (🔓 公共)

· POST /auth/register：用户注册。JSON 参数：{"username":"...", "password":"...", "role":"user"}
· POST /auth/login：用户登录。JSON 参数：{"username":"...", "password":"..."}。返回 access_token。

二、 核心查询与看板 (🔓 公共)

· GET /stalls：查询隔间状态。支持 ?status=idle 或 ?stall_id=F01 精准过滤。
· GET /stats：数据大盘统计。返回空闲、预约、占用、维修的数量（总和等于 15）。
· GET /predict：预测排队时间。基于当前时段与排队人数计算。
· GET /recommend：获取错峰建议。根据空闲隔间数给出前往建议。
· GET /health：健康检查。检查 Redis 连接状态。

三、 用户排队与操作 (👤 用户)

· POST /queue：线上取号排队。自动分配空闲隔间（状态变 reserved），防重复取号。
· GET /queue/my_ticket：查我的票号状态。返回 queuing（排队中）/ calling（叫号了）/ expired（超时作废）。
· POST /queue/cancel：取消排队/释放隔间。Query 参数：?ticket_id=F0001
· POST /queue/scan_checkin：线下扫码核销。JSON 参数：{"stall_id":"F01"}。防进错门、防抢占。
· POST /feedback/fault：故障报修。JSON 参数：{"stall_id":"...", "reason":"水温异常", "user_id":"..."}
· POST /feedback/cleaning：清理反馈。JSON 参数：{"stall_id":"...", "reason":"地面湿滑", "user_id":"..."}

四、 管理员后台 (🛡️ 管理员)

· GET /admin/feedback：查所有反馈。支持 ?type=fault 或 ?status=pending 过滤。
· GET /admin/pending：查待处理任务。返回待维修、待清理的隔间列表及未处理反馈数。
· POST /admin/maintenance-all：一键全部维修。开关式接口，按一次进维修，再按一次恢复。
· POST /admin/cleaning-all：一键全部清理。开关式接口。
· POST /admin/fault-done：完成单个维修。Query 参数：?stall_id=F01
· POST /admin/cleaning-done：完成单个清理。Query 参数：?stall_id=F05

五、 仿真控制模块 (🛡️ 管理员)

· POST /sim/start：启动仿真器。后台开始自动模拟洗澡与超时释放。
· POST /sim/stop：停止仿真器。冻结状态。
· GET /sim/status：仿真运行状态。查看一共来了多少人、洗完了多少人。
· POST /sim/trigger-peak：一键晚高峰。80% 隔间瞬间变成 occupied。
· POST /sim/reset：一键重置系统。清空队列、票号、反馈，恢复初始状态（联调必备）。

六、 实时通信 (WebSocket)

· WS /ws/stalls：实时状态推送。后端状态一变（如超时释放、有人扫码），自动向前端广播 JSON 数据。