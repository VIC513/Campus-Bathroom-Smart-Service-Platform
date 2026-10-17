"""
校园浴室智能调度系统 - 后端 API
单楼栋女生版 + 仿真器 + Redis + WebSocket
成员1：后端 Lead & DevOps
"""

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import random
import redis
import json

from simulator import simulator


# ============================================================
# 1. FastAPI 应用
# ============================================================
app = FastAPI(
    title="校园浴室智能调度 API",
    description="单楼栋女生版（Redis + WebSocket 版）",
    version="3.0.0"
)


# ============================================================
# 2. Redis 连接
# ============================================================
r = redis.Redis(host="localhost", port=6379, decode_responses=True)


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
# 6. Redis 数据操作
# ============================================================

def init_stalls() -> List[dict]:
    return [
        {"stall_id": f"F{i:02d}", "status": "idle", "gender": "female", "used_seconds": 0}
        for i in range(1, 16)
    ]


def redis_init():
    if not r.exists("stalls"):
        r.set("stalls", json.dumps(init_stalls()))
    if not r.exists("queues"):
        r.set("queues", json.dumps({"female": []}))
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


def get_queues() -> dict:
    return json.loads(r.get("queues"))


def save_queues(queues: dict):
    r.set("queues", json.dumps(queues))


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


# ============================================================
# 7. 工具函数
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
# 8. 启动事件：绑定 Redis + WebSocket 回调
# ============================================================

@app.on_event("startup")
async def startup_event():
    redis_init()
    simulator.attach_redis(r)

    async def on_update(stalls):
        """仿真器状态变化时，通过 WebSocket 推送"""
        message = json.dumps({"type": "stalls_update", "data": stalls})
        await manager.broadcast(message)

    simulator.set_update_callback(on_update)
    print("[启动] 仿真器已绑定 Redis + WebSocket 回调")


# ============================================================
# 9. WebSocket 端点
# ============================================================

@app.websocket("/ws/stalls")
async def websocket_stalls(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


# ============================================================
# 10. GET 接口
# ============================================================

@app.get("/")
def root():
    return {
        "message": "校园浴室智能调度系统 API（Redis + WebSocket 版）",
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
        "version": "3.0.0"
    }


@app.get("/stats")
def get_stats():
    stalls = get_stalls()
    total = len(stalls)
    idle = count_by_status("idle")
    occupied = count_by_status("occupied")
    cleaning = count_by_status("cleaning")
    fault = count_by_status("fault")
    queues = get_queues()

    return {
        "total_stalls": total,
        "idle_stalls": idle,
        "occupied_stalls": occupied,
        "cleaning_stalls": cleaning,
        "fault_stalls": fault,
        "queue_length": len(queues.get("female", [])),
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
    queues = get_queues()
    queue_len = len(queues.get("female", []))
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
    queues = get_queues()
    idle_count = count_by_status("idle")
    queue_len = len(queues.get("female", []))

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
# 11. POST 接口（用户操作）
# ============================================================

@app.post("/queue", response_model=QueueResponse)
def join_queue(req: QueueRequest):
    counter = get_counter()
    counter["female"] += 1
    ticket_id = f"F{counter['female']:04d}"
    save_counter(counter)

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

    queues = get_queues()
    queues["female"].append(ticket_id)
    save_queues(queues)
    position = len(queues["female"])
    return QueueResponse(
        ticket_id=ticket_id,
        position=position,
        estimated_wait=position * 1080,
        stall_id=None
    )


@app.post("/queue/cancel")
def cancel_queue(ticket_id: str):
    queues = get_queues()
    if ticket_id in queues["female"]:
        queues["female"].remove(ticket_id)
        save_queues(queues)
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
def submit_fault(req: FeedbackRequest):
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
def submit_cleaning(req: FeedbackRequest):
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
# 12. POST 接口（管理员）
# ============================================================

@app.post("/admin/maintenance-all")
def toggle_maintenance_all():
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
def toggle_cleaning_all():
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
def fault_done(stall_id: str):
    stall = find_stall(stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if stall["status"] != "fault":
        raise HTTPException(status_code=400, detail="该隔间当前不是 fault 状态")
    update_stall(stall_id, status="idle", used_seconds=0)
    mark_feedback_done(stall_id)
    return {"stall_id": stall_id, "new_status": "idle", "message": "已取消维修"}


@app.post("/admin/cleaning-done")
def cleaning_done(stall_id: str):
    stall = find_stall(stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if stall["status"] != "cleaning":
        raise HTTPException(status_code=400, detail="该隔间当前不是 cleaning 状态")
    update_stall(stall_id, status="idle", used_seconds=0)
    mark_feedback_done(stall_id)
    return {"stall_id": stall_id, "new_status": "idle", "message": "已取消清理"}
# ============================================================
# 13. 仿真控制接口
# ============================================================

@app.post("/sim/start")
async def sim_start():
    simulator.start()
    return {"message": "仿真器已启动", "status": simulator.status()}


@app.post("/sim/stop")
def sim_stop():
    simulator.stop()
    return {"message": "仿真器已停止", "status": simulator.status()}


@app.get("/sim/status")
def sim_status():
    return simulator.status()


@app.post("/sim/trigger-peak")
def trigger_peak():
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
def sim_reset():
    save_stalls(init_stalls())
    save_queues({"female": []})
    save_counter({"female": 0})
    save_feedbacks([])
    save_ticket_map({})
    return {"message": "已重置"}