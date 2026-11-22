"""
校园浴室智能调度系统 - 后端 API
单楼栋女生版 + 仿真器 + Redis List 排队 + WebSocket
成员1：后端 Lead & DevOps
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import random
import redis
import time
import json
import os
import secrets
import jwt
import hashlib
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import Depends   # 如果你原来没导 Depends，记得加上

from simulator import simulator

# ============================================================
# 1. FastAPI 应用
# ============================================================
app = FastAPI(
    title="校园浴室智能调度 API",
    description="单楼栋女生版（Redis List + WebSocket 版）",
    version="4.0.0"
)

# JWT 密钥优先取环境变量；未配置时随机生成（进程重启后旧 Token 失效）
SECRET_KEY = os.getenv("JWT_SECRET") or secrets.token_urlsafe(48)
ALGORITHM = "HS256"

# 管理员初始账号：仅用于本地演示，部署时务必通过环境变量覆盖
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
security = HTTPBearer(auto_error=False)
class AuthModel(BaseModel):
    username: str
    password: str
    role: str = "user"
def get_hash(pwd: str) -> str: return hashlib.sha256(pwd.encode()).hexdigest()

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    if not credentials: return None
    try:
        return jwt.decode(credentials.credentials, SECRET_KEY, algorithms=[ALGORITHM])
    except:
        raise HTTPException(status_code=401, detail="无效或过期的凭证")

def require_user(user: dict = Depends(get_current_user)):
    if user is None: raise HTTPException(status_code=401, detail="请先登录")
    return user

def require_admin(user: dict = Depends(get_current_user)):
    if user is None or user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="权限不足，需要管理员身份")
    return user

# ============================================================
# 2. Redis 连接与 Key 定义
# ============================================================
# 本机 Redis 5 不支持 RESP3 的 HELLO 握手，显式使用 RESP2，避免启动时报 unknown command `HELLO`
r = redis.Redis(host="localhost", port=6379, decode_responses=True, protocol=2)
QUEUE_KEY = "queue:female"  # 使用 Redis List 存排队队列

# ============================================================
# 3. WebSocket 连接管理
# ============================================================
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        print(f"[WebSocket] 新连接，当前 {len(self.active_connections)} 个")

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)
        print(f"[WebSocket] 断开，当前 {len(self.active_connections)} 个")

    async def broadcast(self, message: str):
        for conn in self.active_connections:
            try:
                await conn.send_text(message)
            except Exception:
                pass

manager = ConnectionManager()

# ============================================================
# 4. 预设选项
# ============================================================
FAULT_REASONS = [
    "水温异常", "门锁损坏", "花洒堵塞", "水压不足", "排水堵塞",
    "水温调节旋钮损坏", "无储物架或储物架损坏", "其他"
]
CLEANING_REASONS = [
    "有前人留下的垃圾", "地面严重积水", "有异味", "其他"
]

# ============================================================
# 5. 数据模型
# ============================================================
class Stall(BaseModel):
    stall_id: str
    status: str
    gender: str = "female"
    used_seconds: int = 0

class QueueRequest(BaseModel):
    user_id: str
    # 预约指定隔间时传入（如 F07）；不传则按排队逻辑分配第一个空闲位
    stall_id: Optional[str] = None

class QueueResponse(BaseModel):
    ticket_id: str
    position: int
    estimated_wait: int
    stall_id: Optional[str]

class FeedbackRequest(BaseModel):
    stall_id: str
    reason: str
    detail: Optional[str] = ""
    user_id: str

# ============================================================
# 6. Redis 数据操作工具
# ============================================================
def init_stalls() -> List[dict]:
    return [
        {"stall_id": f"F{i:02d}", "status": "idle", "gender": "female", "used_seconds": 0}
        for i in range(1, 16)
    ]

def redis_init():
    if not r.exists("stalls"):
        r.set("stalls", json.dumps(init_stalls()))
    if not r.exists("feedback"):
        r.set("feedback", json.dumps([]))
    if not r.exists("ticket_counter"):
        r.set("ticket_counter", json.dumps({"female": 0}))
    if not r.exists("ticket_to_stall"):
        r.set("ticket_to_stall", json.dumps({}))
    print("[Redis] 初始化完成")

def ensure_admin():
    """首次启动时预置管理员账号，避免全新 Redis 下管理后台无人可登录"""
    if r.exists(f"user:{ADMIN_USERNAME}"):
        return
    r.set(f"user:{ADMIN_USERNAME}", json.dumps({
        "username": ADMIN_USERNAME,
        "password": get_hash(ADMIN_PASSWORD),
        "role": "admin",
    }))
    print(f"[Redis] 已预置管理员账号 {ADMIN_USERNAME}（可通过 ADMIN_PASSWORD 覆盖）")

def get_stalls() -> List[dict]:
    return json.loads(r.get("stalls"))

def save_stalls(stalls: List[dict]):
    r.set("stalls", json.dumps(stalls))

def get_feedbacks() -> List[dict]:
    return json.loads(r.get("feedback"))

def save_feedbacks(feedbacks: List[dict]):
    r.set("feedback", json.dumps(feedbacks))

def get_counter() -> dict:
    return json.loads(r.get("ticket_counter"))

def save_counter(counter: dict):
    r.set("ticket_counter", json.dumps(counter))

def get_ticket_map() -> dict:
    return json.loads(r.get("ticket_to_stall"))

def save_ticket_map(m):
    r.set("ticket_to_stall", json.dumps(m))

def get_queue_length() -> int:
    # 获取 Redis List 里的排队人数
    return r.llen(QUEUE_KEY)

# ============================================================
# 7. 通用工具
# ============================================================
def find_stall(stall_id: str) -> Optional[dict]:
    for s in get_stalls():
        if s["stall_id"] == stall_id:
            return s
    return None

def update_stall(stall_id: str, **kwargs):
    stalls = get_stalls()
    for s in stalls:
        if s["stall_id"] == stall_id:
            s.update(kwargs)
            break
    save_stalls(stalls)

def count_by_status(status: str) -> int:
    return len([s for s in get_stalls() if s["status"] == status])

def mark_feedback_done(stall_id: str):
    feedbacks = get_feedbacks()
    for f in feedbacks:
        if f["stall_id"] == stall_id and f["status"] == "pending":
            f["status"] = "done"
            f["done_time"] = datetime.now().isoformat()
    save_feedbacks(feedbacks)

def mark_all_feedback_done(feedback_type: str):
    feedbacks = get_feedbacks()
    for f in feedbacks:
        if f["type"] == feedback_type and f["status"] == "pending":
            f["status"] = "done"
            f["done_time"] = datetime.now().isoformat()
    save_feedbacks(feedbacks)

# ============================================================
# 8. 启动事件 & WebSocket 端点
# ============================================================
@app.on_event("startup")
async def startup_event():
    redis_init()
    ensure_admin()
    simulator.attach_redis(r)

    async def on_update(stalls):
        message = json.dumps({"type": "stalls_update", "data": stalls})
        await manager.broadcast(message)

    simulator.set_update_callback(on_update)
    print("[启动] 仿真器已绑定 Redis + WebSocket 回调")

@app.websocket("/ws/stalls")
async def websocket_stalls(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# ============================================================
# 9. GET 接口
# ============================================================
@app.post("/auth/register")
def register(data: AuthModel):
    if r.exists(f"user:{data.username}"): raise HTTPException(400, "用户名已存在")
    # 注册只允许创建普通用户：忽略客户端传入的 role，防止越权注册管理员
    r.set(f"user:{data.username}", json.dumps({"username": data.username, "password": get_hash(data.password), "role": "user"}))
    return {"message": "注册成功"}

@app.post("/auth/login")
def login(data: AuthModel):
    user_raw = r.get(f"user:{data.username}")
    if not user_raw: raise HTTPException(400, "账号或密码错误")
    user_info = json.loads(user_raw)
    if user_info["password"] != get_hash(data.password): raise HTTPException(400, "账号或密码错误")
    
    token = jwt.encode({"sub": data.username, "role": user_info["role"]}, SECRET_KEY, algorithm=ALGORITHM)
    return {"access_token": token, "role": user_info["role"], "username": data.username}


@app.get("/")
def root():
    return {
        "message": "校园浴室智能调度系统 API（Redis List + WebSocket 版）",
        "docs": "http://127.0.0.1:8000/docs",
        "websocket": "ws://127.0.0.1:8000/ws/stalls"
    }

@app.get("/health")
def health():
    try:
        r.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "error"
    return {
        "status": "ok",
        "redis": redis_status,
        "time": datetime.now().isoformat(),
        "version": "4.0.0"
    }


@app.get("/stats")
def get_stats():
    stalls = get_stalls()
    total = len(stalls)
    idle = count_by_status("idle")
    occupied = count_by_status("occupied")
    reserved = count_by_status("reserved")  # 【新增】预约中/待核销
    cleaning = count_by_status("cleaning")
    fault = count_by_status("fault")

    return {
        "total_stalls": total,
        "idle_stalls": idle,
        "reserved_stalls": reserved,      # 【新增】
        "occupied_stalls": occupied,
        "cleaning_stalls": cleaning,
        "fault_stalls": fault,
        "queue_length": get_queue_length(),
        "usage_rate": round((occupied + reserved) / total, 2) if total > 0 else 0 # 使用率包含已经被占用的
    }

@app.get("/stalls")
def get_stalls_api(status: Optional[str] = None, stall_id: Optional[str] = None):
    stalls = get_stalls()
    if stall_id:
        for s in stalls:
            if s["stall_id"] == stall_id:
                return [s]
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if status:
        return [s for s in stalls if s["status"] == status]
    return stalls

@app.get("/predict")
def predict():
    queue_len = get_queue_length()
    idle_count = count_by_status("idle")
    hour = datetime.now().hour

    if 19 <= hour <= 22:
        avg_duration = 1200
    elif 12 <= hour <= 14:
        avg_duration = 1080
    else:
        avg_duration = 900

    if idle_count == 0:
        wait_seconds = queue_len * avg_duration
    else:
        wait_seconds = int(queue_len / idle_count * avg_duration)

    if wait_seconds < 300:
        level = "low"
    elif wait_seconds < 900:
        level = "medium"
    else:
        level = "high"

    return {
        "queue_length": queue_len,
        "idle_stalls": idle_count,
        "avg_duration_used": avg_duration,
        "wait_seconds": wait_seconds,
        "wait_minutes": round(wait_seconds / 60, 1),
        "level": level,
        "current_hour": hour
    }

@app.get("/recommend")
def recommend():
    idle_count = count_by_status("idle")
    queue_len = get_queue_length()

    if idle_count == 0 and queue_len == 0:
        suggestion = "暂时没有空闲隔间，建议稍后再来"
        level = "busy"
    elif idle_count >= 5:
        suggestion = "现在空闲隔间充足，建议立即前往"
        level = "good"
    elif idle_count >= 2:
        suggestion = "现在有空闲隔间，可以前往"
        level = "ok"
    else:
        suggestion = "空闲隔间较少，建议等 5 分钟再来"
        level = "busy"

    return {
        "level": level,
        "suggestion": suggestion,
        "idle_stalls": idle_count,
        "queue_length": queue_len
    }
@app.get("/queue/my_ticket")
def get_my_ticket(user: dict = Depends(require_user)):
    """查询当前登录用户自己的排队/使用状态"""
    username = user.get("sub") 
    
    queue_list = r.lrange(QUEUE_KEY, 0, -1) 
    ticket_map = get_ticket_map()
    my_ticket_id = r.get(f"user:{username}:ticket")
    
    # 【修改点】判断没有票的情况
    if not my_ticket_id:
        # 1. 检查有没有超时被踢的案底
        if r.get(f"user:{username}:timeout_flag"):
            r.delete(f"user:{username}:timeout_flag") # 报错一次后就删掉，防止一直弹
            return {
                "status": "expired", 
                "message": "超时未核销！您的票已作废，请重新取号排队。"
            }
        # 2. 正常的无票状态
        return {"status": "no_ticket", "message": "您当前没有取号排队"}
        
    # 判断这个票是在排队，还是已经分配了
    if my_ticket_id in queue_list:
        position = queue_list.index(my_ticket_id) + 1
        return {
            "status": "queuing",
            "ticket_id": my_ticket_id,
            "position": position,
            "message": f"您正在排队中，前面还有 {position - 1} 人"
        }
    elif my_ticket_id in ticket_map:
        stall_id = ticket_map[my_ticket_id]
        return {
            "status": "calling",
            "ticket_id": my_ticket_id,
            "stall_id": stall_id,
            "message": f"📢 叫号啦！请前往 {stall_id} 号隔间"
        }
    else:
        return {"status": "no_ticket", "message": "您的票已失效或使用完毕"}

@app.get("/admin/feedback")
def admin_get_feedback(
    type: Optional[str] = None,
    status: Optional[str] = None,
    _: dict = Depends(require_admin),
):
    result = get_feedbacks()
    if type:
        result = [f for f in result if f["type"] == type]
    if status:
        result = [f for f in result if f["status"] == status]
    return result

@app.get("/admin/pending")
def admin_pending(_: dict = Depends(require_admin)):
    stalls = get_stalls()
    feedbacks = get_feedbacks()
    return {
        "fault_stalls": [s["stall_id"] for s in stalls if s["status"] == "fault"],
        "cleaning_stalls": [s["stall_id"] for s in stalls if s["status"] == "cleaning"],
        "pending_feedbacks": len([f for f in feedbacks if f["status"] == "pending"])
    }

# ============================================================
# 10. POST 接口（用户操作）
# ============================================================
@app.post("/queue", response_model=QueueResponse)
def join_queue(req: QueueRequest, user: dict = Depends(require_user)):
    username = user.get("sub")
    
    if r.exists(f"user:{username}:ticket"):
        raise HTTPException(400, "您已有一个有效票号，请勿重复取号！")

    counter = get_counter(); counter["female"] += 1
    ticket_id = f"F{counter['female']:04d}"; save_counter(counter)
    r.set(f"user:{username}:ticket", ticket_id, ex=86400) 

    stalls = get_stalls()
    # 指定隔间预约：先校验存在性与空闲状态，避免两个用户抢到同一格
    stall = None
    if req.stall_id:
        stall = next((s for s in stalls if s["stall_id"] == req.stall_id), None)
        if stall is None:
            r.delete(f"user:{username}:ticket")
            raise HTTPException(status_code=404, detail=f"隔间 {req.stall_id} 不存在")
        if stall["status"] != "idle":
            r.delete(f"user:{username}:ticket")
            raise HTTPException(status_code=400, detail=f"隔间 {req.stall_id} 已被占用或预约，请选择其他隔间")
    else:
        idle_stalls = [s for s in stalls if s["status"] == "idle"]
        stall = idle_stalls[0] if idle_stalls else None

    if stall:
        # 【关键修改】变成 reserved（已预约），并记录分配时间
        stall["status"] = "reserved"
        stall["used_seconds"] = 0
        stall["assign_time"] = time.time() # 记录当前时间戳
        save_stalls(stalls)
        
        ticket_map = get_ticket_map(); ticket_map[ticket_id] = stall["stall_id"]; save_ticket_map(ticket_map)
        return QueueResponse(ticket_id=ticket_id, position=0, estimated_wait=0, stall_id=stall["stall_id"])

    r.rpush(QUEUE_KEY, ticket_id)
    return QueueResponse(ticket_id=ticket_id, position=r.llen(QUEUE_KEY), estimated_wait=r.llen(QUEUE_KEY)*1080, stall_id=None)
@app.post("/queue/cancel")
def cancel_queue(ticket_id: str, user: dict = Depends(require_user)):
    username = user.get("sub")
    
    # 1. 先尝试从排队队列中删除
    removed = r.lrem(QUEUE_KEY, 1, ticket_id)
    if removed > 0:
        r.delete(f"user:{username}:ticket") # 别忘了把这把锁删了
        return {"message": "已取消排队", "ticket_id": ticket_id}

    # 2. 如果不在队列，说明已经分配了隔间（reserved 或 occupied）
    ticket_map = get_ticket_map()
    if ticket_id in ticket_map:
        stall_id = ticket_map.pop(ticket_id)
        save_ticket_map(ticket_map)
        
        stalls = get_stalls()
        for s in stalls:
            if s["stall_id"] == stall_id:
                # 【修复点】不管是预约状态还是占用状态，统统变回空闲
                if s["status"] in ["reserved", "occupied"]:
                    s["status"] = "idle"
                    s["used_seconds"] = 0
                    if "assign_time" in s:
                        del s["assign_time"]
                break
        save_stalls(stalls)
        
        # 【核心修复点】必须删掉 Redis 里的这行记录，否则系统认为你一直有票！
        r.delete(f"user:{username}:ticket") 
        
        return {"message": "已取消，隔间已释放", "ticket_id": ticket_id, "released_stall": stall_id}

    # 3. 如果都找不到，说明可能已经洗完了，强行清理一下残留的键值
    r.delete(f"user:{username}:ticket")
    raise HTTPException(status_code=404, detail="票号不存在或已失效")

class ScanRequest(BaseModel):
    stall_id: str
@app.post("/queue/scan_checkin")
def scan_checkin(req: ScanRequest, user: dict = Depends(require_user)):
    """线下扫码核销开关"""
    username = user.get("sub")
    stall = find_stall(req.stall_id)
    if not stall: raise HTTPException(404, "隔间不存在")

    my_ticket = r.get(f"user:{username}:ticket")
    ticket_map = get_ticket_map()
    
    if not my_ticket:
        # 线下未取票扫码
        if stall["status"] != "idle":
            raise HTTPException(400, f"警告：{req.stall_id} 已被占用或预约，请勿强行进入！")
        # 无人且空闲，现场自动分配
        counter = get_counter(); counter["female"] += 1
        new_ticket = f"F{counter['female']:04d}"; save_counter(counter)
        r.set(f"user:{username}:ticket", new_ticket, ex=86400)
        update_stall(req.stall_id, status="occupied", used_seconds=0)
        ticket_map[new_ticket] = req.stall_id; save_ticket_map(ticket_map)
        return {"message": f"欢迎线下用户，已为您开启 {req.stall_id} 隔间", "status": "success"}
    else:
        # 线上取票后的扫码
        assigned_stall = ticket_map.get(my_ticket)
        if assigned_stall and assigned_stall != req.stall_id:
            raise HTTPException(400, f"警告：系统为您分配的是 {assigned_stall}，请离开并前往对应隔间！")
            
        # 核销成功，开始使用
        update_stall(req.stall_id, status="occupied", used_seconds=0)
        return {"message": "扫码成功！检测到您已按时进入，开始计费。", "status": "success"}

@app.post("/feedback/fault")
def submit_fault(req: FeedbackRequest, user: dict = Depends(require_user)):
    if find_stall(req.stall_id) is None:
        raise HTTPException(status_code=404, detail=f"隔间 {req.stall_id} 不存在")

    if req.reason not in FAULT_REASONS:
        raise HTTPException(status_code=400, detail=f"reason 必须是以下之一：{FAULT_REASONS}")

    update_stall(req.stall_id, status="fault")

    feedbacks = get_feedbacks()
    feedbacks.append({
        "type": "fault",
        "stall_id": req.stall_id,
        "reason": req.reason,
        "detail": req.detail,
        "user_id": req.user_id,
        "time": datetime.now().isoformat(),
        "status": "pending"
    })
    save_feedbacks(feedbacks)
    return {"message": "报修已提交", "stall_id": req.stall_id, "reason": req.reason}

@app.post("/feedback/cleaning")
def submit_cleaning(req: FeedbackRequest, user: dict = Depends(require_user)):
    if find_stall(req.stall_id) is None:
        raise HTTPException(status_code=404, detail=f"隔间 {req.stall_id} 不存在")

    if req.reason not in CLEANING_REASONS:
        raise HTTPException(status_code=400, detail=f"reason 必须是以下之一：{CLEANING_REASONS}")

    update_stall(req.stall_id, status="cleaning")

    feedbacks = get_feedbacks()
    feedbacks.append({
        "type": "cleaning",
        "stall_id": req.stall_id,
        "reason": req.reason,
        "detail": req.detail,
        "user_id": req.user_id,
        "time": datetime.now().isoformat(),
        "status": "pending"
    })
    save_feedbacks(feedbacks)
    return {"message": "报清理已提交", "stall_id": req.stall_id, "reason": req.reason}

# ============================================================
# 11. POST 接口（管理员）
# ============================================================
@app.post("/admin/maintenance-all")
def toggle_maintenance_all( admin: dict = Depends(require_admin)):
    stalls = get_stalls()
    fault_count = len([s for s in stalls if s["status"] == "fault"])

    if fault_count == len(stalls):
        for s in stalls:
            s["status"] = "idle"
            s["used_seconds"] = 0
        save_stalls(stalls)
        mark_all_feedback_done("fault")
        return {"action": "end", "message": "现在已全部完成维修"}
    else:
        for s in stalls:
            s["status"] = "fault"
            s["used_seconds"] = 0
        save_stalls(stalls)
        return {"action": "start", "message": "现在已全部进入维修状态"}

@app.post("/admin/cleaning-all")
def toggle_cleaning_all( admin: dict = Depends(require_admin)):
    stalls = get_stalls()
    cleaning_count = len([s for s in stalls if s["status"] == "cleaning"])

    if cleaning_count == len(stalls):
        for s in stalls:
            s["status"] = "idle"
            s["used_seconds"] = 0
        save_stalls(stalls)
        mark_all_feedback_done("cleaning")
        return {"action": "end", "message": "现在已全部完成清理"}
    else:
        for s in stalls:
            s["status"] = "cleaning"
            s["used_seconds"] = 0
        save_stalls(stalls)
        return {"action": "start", "message": "现在已全部进入清理状态"}

@app.post("/admin/fault-done")
def fault_done(stall_id: str, admin: dict = Depends(require_admin)):
    stall = find_stall(stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if stall["status"] != "fault":
        raise HTTPException(status_code=400, detail="该隔间当前不是 fault 状态")
    update_stall(stall_id, status="idle", used_seconds=0)
    mark_feedback_done(stall_id)
    return {"stall_id": stall_id, "new_status": "idle", "message": "已取消维修"}

@app.post("/admin/cleaning-done")
def cleaning_done(stall_id: str, admin: dict = Depends(require_admin)):
    stall = find_stall(stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if stall["status"] != "cleaning":
        raise HTTPException(status_code=400, detail="该隔间当前不是 cleaning 状态")
    update_stall(stall_id, status="idle", used_seconds=0)
    mark_feedback_done(stall_id)
    return {"stall_id": stall_id, "new_status": "idle", "message": "已取消清理"}

# ============================================================
# 12. 仿真控制接口
# ============================================================
@app.post("/sim/start")
async def sim_start( admin: dict = Depends(require_admin)):
    simulator.start()
    return {"message": "仿真器已启动", "status": simulator.status()}

@app.post("/sim/stop")
def sim_stop( admin: dict = Depends(require_admin)):
    simulator.stop()
    return {"message": "仿真器已停止", "status": simulator.status()}

@app.get("/sim/status")
def sim_status( admin: dict = Depends(require_admin)):
    return simulator.status()

@app.post("/sim/trigger-peak")
def trigger_peak( admin: dict = Depends(require_admin)):
    stalls = get_stalls()
    occupied = 0
    for s in stalls:
        if random.random() < 0.8:
            s["status"] = "occupied"
            s["used_seconds"] = 0
            occupied += 1
        else:
            s["status"] = "idle"
            s["used_seconds"] = 0
    save_stalls(stalls)
    return {"message": "晚高峰已触发", "occupied": occupied}

@app.post("/sim/reset")
def sim_reset(admin: dict = Depends(require_admin)):
    save_stalls(init_stalls())
    r.delete(QUEUE_KEY)  
    save_counter({"female": 0})
    save_feedbacks([])
    save_ticket_map({})
    
    # 【新增】清理所有用户的临时票号锁
    keys = r.keys("user:*:ticket")
    if keys:
        r.delete(*keys)
        
    return {"message": "系统已重置"}