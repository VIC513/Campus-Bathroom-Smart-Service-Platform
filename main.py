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
import json
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

SECRET_KEY = "campus_bathroom_secret_key_2026"
ALGORITHM = "HS256"
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
r = redis.Redis(host="localhost", port=6379, decode_responses=True)
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
    "水温异常", "门锁损坏", "花洒堵塞", "水压不足",
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
@app.post("/auth/registert")
def register(data: AuthModel):
    if r.exists(f"user:{data.username}"): raise HTTPException(400, "用户名已存在")
    r.set(f"user:{data.username}", json.dumps({"username": data.username, "password": get_hash(data.password), "role": data.role}))
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
    cleaning = count_by_status("cleaning")
    fault = count_by_status("fault")

    return {
        "total_stalls": total,
        "idle_stalls": idle,
        "occupied_stalls": occupied,
        "cleaning_stalls": cleaning,
        "fault_stalls": fault,
        "queue_length": get_queue_length(),
        "usage_rate": round(occupied / total, 2) if total > 0 else 0
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
    """
    查询当前登录用户自己的排队/使用状态
    """
    username = user.get("sub") # 从 Token 里获取当前登录的用户名
    
    # 1. 查排队队列（Redis List）
    # 取出当前队列里的所有票号
    queue_list = r.lrange(QUEUE_KEY, 0, -1) 
    
    # 2. 查已经分配了隔间的映射表（Redis Hash/String）
    ticket_map = get_ticket_map()
    
    # 3. 因为票号里没有直接存用户名，我们需要一个小机制：
    # 我们可以在取票时，把 user_id 和 ticket_id 绑定一下存起来。
    # 为了简单，我们查一下队列里有没有前缀是这个用户的票。
    # （为了严谨，最好在 /queue 取票时，把 ticket_id 存进 Redis 的 user:{username}:ticket 里）
    
    # 我们新增一个查询：这个用户有没有票
    my_ticket_id = r.get(f"user:{username}:ticket")
    
    if not my_ticket_id:
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
            "status": "in_use",
            "ticket_id": my_ticket_id,
            "stall_id": stall_id,
            "message": f"您已分配到 {stall_id} 隔间，请前往使用"
        }
    else:
        return {"status": "no_ticket", "message": "您的票已失效或使用完毕"}

@app.get("/admin/feedback")
def admin_get_feedback(type: Optional[str] = None, status: Optional[str] = None):
    result = get_feedbacks()
    if type:
        result = [f for f in result if f["type"] == type]
    if status:
        result = [f for f in result if f["status"] == status]
    return result

@app.get("/admin/pending")
def admin_pending():
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
    counter = get_counter()
    counter["female"] += 1
    ticket_id = f"F{counter['female']:04d}"
    save_counter(counter)
    # 把这行加上，绑定用户和票号（有效期一天）
    username = user.get("sub")
    r.set(f"user:{req.user_id}:ticket", ticket_id, ex=86400) 
    stalls = get_stalls()
    idle_stalls = [s for s in stalls if s["status"] == "idle"]
    if idle_stalls:
        stall = idle_stalls[0]
        stall["status"] = "occupied"
        stall["used_seconds"] = 0
        save_stalls(stalls)

        ticket_map = get_ticket_map()
        ticket_map[ticket_id] = stall["stall_id"]
        save_ticket_map(ticket_map)

        return QueueResponse(
            ticket_id=ticket_id,
            position=0,
            estimated_wait=0,
            stall_id=stall["stall_id"]
        )

    # 改造点：入队用 Redis List
    r.rpush(QUEUE_KEY, ticket_id)
    position = r.llen(QUEUE_KEY)
    return QueueResponse(
        ticket_id=ticket_id,
        position=position,
        estimated_wait=position * 1080,
        stall_id=None
    )

@app.post("/queue/cancel")
def cancel_queue(ticket_id: str,user: dict = Depends(require_user)):
    # 改造点：从 Redis List 移除
    removed = r.lrem(QUEUE_KEY, 1, ticket_id)
    if removed > 0:
        return {"message": "已取消排队", "ticket_id": ticket_id}

    ticket_map = get_ticket_map()
    if ticket_id in ticket_map:
        stall_id = ticket_map.pop(ticket_id)
        save_ticket_map(ticket_map)
        stalls = get_stalls()
        for s in stalls:
            if s["stall_id"] == stall_id and s["status"] == "occupied":
                s["status"] = "idle"
                s["used_seconds"] = 0
        save_stalls(stalls)
        return {"message": "已取消，隔间已释放", "ticket_id": ticket_id, "released_stall": stall_id}

    raise HTTPException(status_code=404, detail="票号不存在")

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
def sim_reset( admin: dict = Depends(require_admin)):
    save_stalls(init_stalls())
    r.delete(QUEUE_KEY)  # 改造点：清空 Redis List 队列
    save_counter({"female": 0})
    save_feedbacks([])
    save_ticket_map({})
    return {"message": "已重置"}