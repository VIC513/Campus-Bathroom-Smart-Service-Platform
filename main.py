"""
校园浴室智能调度系统 - 后端 API
单楼栋女生版（无后台自动仿真）
成员1：后端 Lead & DevOps
"""

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
import random


# ============================================================
# 1. FastAPI 应用
# ============================================================
app = FastAPI(
    title="校园浴室智能调度 API",
    description="单楼栋女生版",
    version="1.1.0"
)


# ============================================================
# 2. 数据模型
# ============================================================

class Stall(BaseModel):
    stall_id: str
    status: str            # idle / occupied / cleaning / fault
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
# 3. 全局数据
# ============================================================

def init_stalls() -> List[Stall]:
    return [
        Stall(stall_id=f"F{i:02d}", status="idle", gender="female")
        for i in range(1, 16)
    ]


STALLS = init_stalls()
QUEUES = {"female": []}
TICKET_COUNTER = {"female": 0}
FEEDBACKS = []       # 反馈列表
TICKET_TO_STALL = {} # 票号 -> 隔间 映射（用于取消已分配的票）


# ============================================================
# 4. 工具函数
# ============================================================

def find_stall(stall_id: str) -> Optional[Stall]:
    for s in STALLS:
        if s.stall_id == stall_id:
            return s
    return None


def count_by_status(status: str) -> int:
    return len([s for s in STALLS if s.status == status])


def mark_feedback_done(stall_id: str):
    """把某个隔间的所有 pending 反馈标记为 done"""
    for f in FEEDBACKS:
        if f["stall_id"] == stall_id and f["status"] == "pending":
            f["status"] = "done"
            f["done_time"] = datetime.now().isoformat()


def mark_all_feedback_done(feedback_type: str):
    """把所有 pending 的某类反馈标记为 done"""
    for f in FEEDBACKS:
        if f["type"] == feedback_type and f["status"] == "pending":
            f["status"] = "done"
            f["done_time"] = datetime.now().isoformat()


# ============================================================
# 5. GET 接口
# ============================================================

@app.get("/")
def root():
    return {
        "message": "校园浴室智能调度系统 API（单楼栋女生版）",
        "docs": "http://127.0.0.1:8000/docs"
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "time": datetime.now().isoformat(),
        "version": "1.1.0"
    }


@app.get("/stats")
def get_stats():
    total = len(STALLS)
    idle = count_by_status("idle")
    occupied = count_by_status("occupied")
    cleaning = count_by_status("cleaning")
    fault = count_by_status("fault")
    queue_len = len(QUEUES["female"])

    return {
        "total_stalls": total,
        "idle_stalls": idle,
        "occupied_stalls": occupied,
        "cleaning_stalls": cleaning,
        "fault_stalls": fault,
        "queue_length": queue_len,
        "usage_rate": round(occupied / total, 2) if total > 0 else 0
    }


@app.get("/stalls", response_model=List[Stall])
def get_stalls(status: Optional[str] = None, stall_id: Optional[str] = None):
    if stall_id:
        stall = find_stall(stall_id)
        if stall is None:
            raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
        return [stall]
    if status:
        return [s for s in STALLS if s.status == status]
    return STALLS


@app.get("/predict")
def predict():
    queue_len = len(QUEUES["female"])
    idle_count = count_by_status("idle")

    if idle_count == 0:
        wait_seconds = queue_len * 1080
    else:
        wait_seconds = int(queue_len / idle_count * 1080)

    hour = datetime.now().hour
    if 19 <= hour <= 22:
        next_hour_level = "busy"
    elif 12 <= hour <= 14:
        next_hour_level = "medium"
    else:
        next_hour_level = "low"

    return {
        "queue_length": queue_len,
        "idle_stalls": idle_count,
        "wait_seconds": wait_seconds,
        "wait_minutes": round(wait_seconds / 60, 1),
        "next_hour_level": next_hour_level
    }


@app.get("/recommend")
def recommend():
    idle_count = count_by_status("idle")
    queue_len = len(QUEUES["female"])

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
    """
    管理员查反馈
    - ?type=fault：只看报修
    - ?type=cleaning：只看报清理
    - ?status=pending：只看待处理
    - ?status=done：只看已完成
    """
    result = FEEDBACKS
    if type:
        result = [f for f in result if f["type"] == type]
    if status:
        result = [f for f in result if f["status"] == status]
    return result


@app.get("/admin/pending")
def admin_pending():
    """
    管理员快速查看待处理任务
    """
    fault_stalls = [s.stall_id for s in STALLS if s.status == "fault"]
    cleaning_stalls = [s.stall_id for s in STALLS if s.status == "cleaning"]
    pending_feedbacks = len([f for f in FEEDBACKS if f["status"] == "pending"])

    return {
        "fault_stalls": fault_stalls,
        "cleaning_stalls": cleaning_stalls,
        "pending_feedbacks": pending_feedbacks
    }


# ============================================================
# 6. POST 接口（用户操作）
# ============================================================

@app.post("/queue", response_model=QueueResponse)
def join_queue(req: QueueRequest):
    TICKET_COUNTER["female"] += 1
    ticket_id = f"F{TICKET_COUNTER['female']:04d}"

    idle_stalls = [s for s in STALLS if s.status == "idle"]

    if idle_stalls:
        stall = idle_stalls[0]
        stall.status = "occupied"
        stall.used_seconds = 0
        TICKET_TO_STALL[ticket_id] = stall.stall_id    # 记录映射
        return QueueResponse(
            ticket_id=ticket_id,
            position=0,
            estimated_wait=0,
            stall_id=stall.stall_id
        )

    QUEUES["female"].append(ticket_id)
    position = len(QUEUES["female"])
    return QueueResponse(
        ticket_id=ticket_id,
        position=position,
        estimated_wait=position * 1080,
        stall_id=None
    )


@app.post("/queue/cancel")
def cancel_queue(ticket_id: str):
    """
    取消取票
    - 如果在排队：从队列删除
    - 如果已分配隔间：释放隔间
    """
    # 情况 1：在排队队列里
    if ticket_id in QUEUES["female"]:
        QUEUES["female"].remove(ticket_id)
        return {"message": "已取消排队", "ticket_id": ticket_id}

    # 情况 2：已分配隔间
    if ticket_id in TICKET_TO_STALL:
        stall_id = TICKET_TO_STALL.pop(ticket_id)
        stall = find_stall(stall_id)
        if stall and stall.status == "occupied":
            stall.status = "idle"
            stall.used_seconds = 0
        return {
            "message": "已取消，隔间已释放",
            "ticket_id": ticket_id,
            "released_stall": stall_id
        }

    raise HTTPException(status_code=404, detail="票号不存在")


@app.post("/feedback/fault")
def submit_fault(req: FeedbackRequest):
    stall = find_stall(req.stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {req.stall_id} 不存在")

    stall.status = "fault"
    FEEDBACKS.append({
        "type": "fault",
        "stall_id": req.stall_id,
        "reason": req.reason,
        "detail": req.detail,
        "user_id": req.user_id,
        "time": datetime.now().isoformat(),
        "status": "pending"
    })

    return {
        "message": "报修已提交",
        "stall_id": req.stall_id,
        "reason": req.reason,
        "new_status": stall.status
    }


@app.post("/feedback/cleaning")
def submit_cleaning(req: FeedbackRequest):
    stall = find_stall(req.stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {req.stall_id} 不存在")

    stall.status = "cleaning"
    FEEDBACKS.append({
        "type": "cleaning",
        "stall_id": req.stall_id,
        "reason": req.reason,
        "detail": req.detail,
        "user_id": req.user_id,
        "time": datetime.now().isoformat(),
        "status": "pending"
    })

    return {
        "message": "报清理已提交",
        "stall_id": req.stall_id,
        "reason": req.reason,
        "new_status": stall.status
    }


# ============================================================
# 7. POST 接口（管理员）
# ============================================================

@app.post("/admin/maintenance-all")
def toggle_maintenance_all():
    """开关：一键统一维修（自动更新反馈状态）"""
    fault_count = count_by_status("fault")

    if fault_count == len(STALLS):
        for s in STALLS:
            s.status = "idle"
            s.used_seconds = 0
        mark_all_feedback_done("fault")   # 标记所有报修反馈为已完成
        return {
            "action": "end",
            "message": "现在已全部完成维修",
            "fault_count": 0
        }
    else:
        for s in STALLS:
            s.status = "fault"
            s.used_seconds = 0
        return {
            "action": "start",
            "message": "现在已全部进入维修状态",
            "fault_count": len(STALLS)
        }


@app.post("/admin/cleaning-all")
def toggle_cleaning_all():
    """开关：一键统一清理（自动更新反馈状态）"""
    cleaning_count = count_by_status("cleaning")

    if cleaning_count == len(STALLS):
        for s in STALLS:
            s.status = "idle"
            s.used_seconds = 0
        mark_all_feedback_done("cleaning")   # 标记所有清理反馈为已完成
        return {
            "action": "end",
            "message": "现在已全部完成清理",
            "cleaning_count": 0
        }
    else:
        for s in STALLS:
            s.status = "cleaning"
            s.used_seconds = 0
        return {
            "action": "start",
            "message": "现在已全部进入清理状态",
            "cleaning_count": len(STALLS)
        }


@app.post("/admin/fault-done")
def fault_done(stall_id: str):
    """管理员：单个取消维修（同步更新反馈）"""
    stall = find_stall(stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if stall.status != "fault":
        raise HTTPException(status_code=400, detail="该隔间当前不是 fault 状态")
    stall.status = "idle"
    stall.used_seconds = 0
    mark_feedback_done(stall_id)   # 标记对应反馈为已完成
    return {"stall_id": stall_id, "new_status": "idle", "message": "已取消维修"}


@app.post("/admin/cleaning-done")
def cleaning_done(stall_id: str):
    """管理员：单个取消清理（同步更新反馈）"""
    stall = find_stall(stall_id)
    if stall is None:
        raise HTTPException(status_code=404, detail=f"隔间 {stall_id} 不存在")
    if stall.status != "cleaning":
        raise HTTPException(status_code=400, detail="该隔间当前不是 cleaning 状态")
    stall.status = "idle"
    stall.used_seconds = 0
    mark_feedback_done(stall_id)   # 标记对应反馈为已完成
    return {"stall_id": stall_id, "new_status": "idle", "message": "已取消清理"}


# ============================================================
# 8. POST 接口（演示用）
# ============================================================

@app.post("/sim/trigger-peak")
def trigger_peak():
    occupied = 0
    for s in STALLS:
        if random.random() < 0.8:
            s.status = "occupied"
            s.used_seconds = 0
            occupied += 1
        else:
            s.status = "idle"
            s.used_seconds = 0
    return {"message": "晚高峰已触发", "occupied": occupied}


@app.post("/sim/reset")
def reset_all():
    for s in STALLS:
        s.status = "idle"
        s.used_seconds = 0
    QUEUES["female"].clear()
    TICKET_COUNTER["female"] = 0
    FEEDBACKS.clear()
    TICKET_TO_STALL.clear()
    return {"message": "已重置"}