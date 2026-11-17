# -*- coding: utf-8 -*-
"""
排队算法 - Redis 虚拟排队与取号（对应《排队算法中各种情况及其解决方式》11 类规则）

队列结构:
  Redis List + LPUSH 入队 / RPOP 叫号 => FIFO
  列表头部 = 最新入队者（队尾），列表尾部 = 最早入队者（下一个被叫号）
  位次 = llen - 列表下标

已实现的 11 类处理规则:
  1  叫号逾期未到        handle_no_show()：票据 expired，释放预留隔间，自动叫下一位
  2  指定心仪隔间        join(prefer_stall_ids=...)：只在偏好隔间池中分配
  3  预约隔间被线下占用  handle_stall_conflict()：解绑、收回票据、插回队列靠前位置、记录冲突
  4  线下有空闲隔间      join(source='offline', available_stalls=[...])：直接 checked_in
  5  线下无空闲隔间      join(...) 入统一 FIFO，线上线下同队列公平排队
  6  过期后仍想洗澡      join()：旧票据作废，新建票据排到队尾（不恢复原位次）
  7  短时间重复取票      join() 幂等：已有有效票据直接返回原票据，不重复入队
  8  洗浴中故障重新排队  report_fault()：当前票据取消，故障补偿插到队首优先服务
  9  多账号恶意抢票      join(device_id=...)：同设备最多 max_active_per_device 张有效票
  10 中途临时离开        temp_leave()/return_from_temp_leave()：保留隔间，超时才释放
  11 扫码核销            check_in()：校验票据状态/隔间归属/核销超时，统一 scan_site

说明：Redis 只负责实时队列与状态，票据的永久记录由 PostgreSQL 的 queue_tickets 表承担。
"""

import json
import time
import threading

from redis import Redis


class QueueManager:
    """基于 Redis List 的高并发虚拟排队系统"""

    ACTIVE_STATUS = ("waiting", "called", "checked_in", "temp_leave")

    def __init__(self, redis_client: Redis, wait_predictor=None,
                 checkin_timeout: int = 180, temp_leave_timeout: int = 600,
                 max_active_per_device: int = 2, fault_compensation: bool = True):
        self.r = redis_client
        self.predictor = wait_predictor          # 可注入 WaitTimePredictor
        self.checkin_timeout = checkin_timeout
        self.temp_leave_timeout = temp_leave_timeout
        self.max_active_per_device = max_active_per_device
        self.fault_compensation = fault_compensation

    # ---------- Redis key 工具 ----------
    @staticmethod
    def _queue_key(area_id):    return f"queue:{area_id}"
    @staticmethod
    def _counter_key(area_id):  return f"queue:{area_id}:counter"
    @staticmethod
    def _user_key(user_id):     return f"user:{user_id}:queue"
    @staticmethod
    def _free_key(area_id):     return f"area:{area_id}:free_count"
    @staticmethod
    def _device_key(device_id): return f"device:{device_id}:active"
    @staticmethod
    def _stall_key(stall_id):   return f"stall:{stall_id}"

    # ---------- 基础读写 ----------
    @staticmethod
    def _s(v):
        return v.decode() if isinstance(v, bytes) else v

    def _hget(self, user_id) -> dict:
        info = self.r.hgetall(self._user_key(user_id))
        return {self._s(k): self._s(v) for k, v in info.items()} if info else {}

    def _queue_items(self, area_id) -> list:
        return [self._s(x) for x in self.r.lrange(self._queue_key(area_id), 0, -1)]

    def _prefs(self, info: dict) -> list:
        try:
            return json.loads(info.get("prefer", "") or "[]")
        except ValueError:
            return []

    def _device_count(self, device_id) -> int:
        return int(self.r.get(self._device_key(device_id)) or 0)

    def _device_incr(self, device_id):
        if device_id:
            self.r.incr(self._device_key(device_id))

    def _device_decr(self, device_id):
        if device_id:
            key = self._device_key(device_id)
            if int(self.r.get(key) or 0) > 0:
                self.r.decr(key)

    # ---------- 取号（规则 2/4/5/6/7/9） ----------
    def join(self, user_id: str, area_id: str, hour: int,
             temperature: float = 20, avg_duration: float = 15,
             gender: str = 'F', prefer_stall_ids=None, device_id=None,
             source: str = 'online', available_stalls=None) -> dict:
        """
        统一取号入口（线上/线下都走这里）。
        available_stalls: 线下扫码时可传入当前空闲隔间列表，用于直接分配（规则 4）；
                          线上取号不传，进入 waiting 等叫号。
        """
        # 规则 7：同一用户已有有效票据 -> 幂等返回，不重复入队
        info = self._hget(user_id)
        if info and info.get("status") in self.ACTIVE_STATUS:
            return {"error": "您在该区域已有有效票据，请勿重复取号",
                    "info": self.status(user_id)}

        # 规则 9：同设备/IP 有效票据数量限制
        if device_id and self._device_count(device_id) >= self.max_active_per_device:
            return {"error": f"同一设备最多允许 {self.max_active_per_device} 张有效排队票据"}

        # 规则 6：历史票据已过期则清除旧状态，重新取号排到队尾
        if info:
            self.r.delete(self._user_key(user_id))

        prefs = list(prefer_stall_ids or [])
        number = self.r.incr(self._counter_key(area_id))

        # 规则 4：线下扫码且有空闲未预约隔间 -> 直接核销入场，不排队
        if source == 'offline' and available_stalls:
            stall = self._match_stall(prefs, available_stalls)
            if stall:
                self._occupy_stall(stall, user_id)
                if int(self.r.get(self._free_key(area_id)) or 0) > 0:
                    self.r.decr(self._free_key(area_id))
                free = int(self.r.get(self._free_key(area_id)) or 0)
                self._device_incr(device_id)
                self.r.hset(self._user_key(user_id), mapping={
                    "area_id": area_id,
                    "number": number,
                    "status": "checked_in",
                    "stall_id": stall,
                    "check_in_method": "scan_site",
                    "join_time": time.time(),
                    "checked_in_at": time.time(),
                    "prefer": json.dumps(prefs, ensure_ascii=False),
                    "device_id": device_id or "",
                    "source": source,
                })
                return {"number": number, "status": "checked_in", "stall_id": stall,
                        "check_in_method": "scan_site", "free_stalls": free,
                        "area_id": area_id}

        # 规则 5：排队等待（线上/线下共用同一 FIFO，不区分来源）
        self.r.lpush(self._queue_key(area_id), f"{user_id}:{number}")
        self.r.hset(self._user_key(user_id), mapping={
            "area_id": area_id,
            "number": number,
            "join_time": time.time(),
            "status": "waiting",
            "prefer": json.dumps(prefs, ensure_ascii=False),
            "device_id": device_id or "",
            "source": source,
            "is_compensated": "0",
        })
        self._device_incr(device_id)

        position = self.r.llen(self._queue_key(area_id))
        free = int(self.r.get(self._free_key(area_id)) or 0)
        wait_time = None
        if self.predictor and self.predictor.is_trained:
            wait_time = self.predictor.predict(
                queue_len=position, hour=hour, temperature=temperature,
                avg_duration=avg_duration, free_stalls=free, gender=gender
            )
        return {"number": number, "position": position, "free_stalls": free,
                "wait_time": wait_time, "area_id": area_id, "status": "waiting"}

    # ---------- 查询 ----------
    def status(self, user_id: str) -> dict:
        info = self._hget(user_id)
        if not info:
            return {"error": "未在排队"}
        info["position"] = 0
        if info.get("status") == "waiting":
            target = f"{user_id}:{info['number']}"
            queue = self._queue_items(info["area_id"])
            if target in queue:
                info["position"] = len(queue) - queue.index(target)
        return info

    # ---------- 叫号（规则 2：按偏好筛选；规则 3：返回绑定隔间） ----------
    def call_next(self, area_id: str, available_stalls=None) -> dict:
        """
        隔间释放时调用。传入 available_stalls 时按用户偏好从队首向后挑人，
        并绑定一个隔间（reserved）；不传则退化为纯 FIFO，由调用方自行绑定。
        """
        lock_key = f"lock:call:{area_id}"
        if not self.r.setnx(lock_key, 1):
            return {"error": "正在叫号，请稍后"}
        self.r.expire(lock_key, 5)
        try:
            if self.r.llen(self._queue_key(area_id)) == 0:
                return {"msg": "队列为空"}
            free = int(self.r.get(self._free_key(area_id)) or 0)
            if available_stalls is None and free <= 0:
                return {"msg": "无空闲隔间"}
            if available_stalls is not None and not available_stalls:
                return {"msg": "无空闲隔间"}

            picked = self._pick_next(area_id, available_stalls)
            if not picked:
                return {"msg": "队列为空"}
            user_id, number = picked

            stall = None
            if available_stalls:
                prefs = self._prefs(self._hget(user_id))
                stall = self._match_stall(prefs, available_stalls)
                if stall:
                    self._reserve_stall(area_id, stall, user_id)

            self.r.hset(self._user_key(user_id), mapping={
                "status": "called",
                "called_time": time.time(),
                "reserved_stall_id": stall or "",
            })
            return {"user_id": user_id, "number": int(number),
                    "area_id": area_id, "reserved_stall_id": stall}
        finally:
            self.r.delete(lock_key)

    # ---------- 扫码核销（规则 11，兼容规则 4） ----------
    def check_in(self, user_id: str, stall_id: str, area_id: str = 'A',
                 method: str = 'scan_site') -> dict:
        info = self._hget(user_id)
        stall = {self._s(k): self._s(v)
                 for k, v in (self.r.hgetall(self._stall_key(stall_id)) or {}).items()}

        if info:
            status = info.get("status")
            if status == "called":
                # 情况 D：票据已超时
                elapsed = time.time() - float(info.get("called_time", 0))
                if elapsed > self.checkin_timeout:
                    self.handle_no_show(user_id)
                    return {"error": "排队已过期，请重新取号"}
                reserved = info.get("reserved_stall_id") or ""
                # 情况 A：扫码隔间与叫号分配隔间一致
                if reserved and reserved != stall_id:
                    return {"error": f"请前往分配隔间 {reserved} 核销"}
            elif status in ("checked_in", "temp_leave"):
                return {"msg": "已核销", "info": self.status(user_id)}
            else:
                return {"error": "尚未叫号，请等待叫号"}
        else:
            # 情况 B：无票据但隔间空闲 -> 现场创建并直接核销
            if stall.get("status") in ("occupied", "reserved", "fault"):
                return {"error": "该隔间已占用或故障，暂不可使用"}

        # 情况 C：隔间被占用/故障
        if stall.get("status") in ("occupied", "fault"):
            return {"error": "该隔间已占用或故障，暂不可使用"}

        self._occupy_stall(stall_id, user_id)
        free = int(self.r.get(self._free_key(area_id)) or 0)
        if free > 0:
            self.r.decr(self._free_key(area_id))

        number = info.get("number") or self.r.incr(self._counter_key(area_id))
        self.r.hset(self._user_key(user_id), mapping={
            "area_id": area_id,
            "number": number,
            "status": "checked_in",
            "stall_id": stall_id,
            "check_in_method": method,
            "checked_in_at": time.time(),
            "device_id": info.get("device_id", ""),
            "source": info.get("source", "online"),
        })
        return {"ok": True, "status": "checked_in", "stall_id": stall_id,
                "check_in_method": method}

    # ---------- 叫号超时（规则 1） ----------
    def handle_no_show(self, user_id: str, timeout: int = None) -> dict:
        """叫号后超过核销窗口未到 -> 票据 expired、释放预留隔间、叫下一位"""
        timeout = self.checkin_timeout if timeout is None else timeout
        info = self._hget(user_id)
        if not info:
            return {"error": "未在排队"}
        if info.get("status") != "called":
            return {"msg": "尚未叫号"}
        if time.time() - float(info.get("called_time", 0)) < timeout:
            return {"msg": "未超时"}

        area_id = info["area_id"]
        self._release_stall(area_id, info.get("reserved_stall_id"))
        self._device_decr(info.get("device_id"))
        self.r.delete(self._user_key(user_id))
        nxt = self.call_next(area_id)
        return {"ok": True, "status": "expired", "user_id": user_id,
                "reason": "叫号超时未核销", "next": nxt.get("user_id")}

    # ---------- 预约隔间被线下占用（规则 3） ----------
    def handle_stall_conflict(self, area_id: str, stall_id: str) -> dict:
        """
        传感器上报隔间已被占用，但绑定票据尚未核销：
        解绑票据、隔间标记不可用、票据插回队列靠前位置（保留叫号状态）。
        """
        stall = {self._s(k): self._s(v)
                 for k, v in (self.r.hgetall(self._stall_key(stall_id)) or {}).items()}
        # 预留时已在 stall 里记录了 current_user，优先用它定位受影响票据
        candidates = [stall.get("current_user")] if stall.get("current_user") else []
        candidates += [u for u in self._all_users() if u not in candidates]

        owner, info = None, None
        for uid in candidates:
            cand = self._hget(uid)
            if cand.get("status") == "called" and cand.get("reserved_stall_id") == stall_id:
                owner, info = uid, cand
                break

        self.r.hset(self._stall_key(stall_id), mapping={
            "status": "occupied", "current_user": owner or "",
            "available": "0", "updated_at": time.time(),
        })
        if not owner:
            return {"ok": True, "stall_id": stall_id, "ticket": None,
                    "reason": "未授权占用，已标记隔间不可用"}

        # 票据退回队列靠前位置（rpush = 下一个被叫号），不排到队尾
        self.r.hset(self._user_key(owner), mapping={
            "status": "waiting", "reserved_stall_id": "", "join_time": time.time(),
        })
        self.r.rpush(self._queue_key(area_id), f"{owner}:{info['number']}")
        return {"ok": True, "stall_id": stall_id, "ticket_user": owner,
                "status": "waiting", "reason": "预约隔间被占用，已重新分配"}

    # ---------- 洗浴中故障 + 补偿排队（规则 8） ----------
    def report_fault(self, user_id: str, stall_id: str, fault_type: str = "设备故障") -> dict:
        info = self._hget(user_id)
        area_id = (info or {}).get("area_id") or "A"
        self.r.hset(self._stall_key(stall_id), mapping={
            "status": "fault", "available": "0", "current_user": "", "updated_at": time.time(),
        })
        if info:
            self._device_decr(info.get("device_id"))
            self.r.delete(self._user_key(user_id))

        number = self.r.incr(self._counter_key(area_id))
        if self.fault_compensation:
            # 补偿：直接插到队首（下一个被服务），不排到队尾
            self.r.rpush(self._queue_key(area_id), f"{user_id}:{number}")
            priority = "front"
        else:
            self.r.lpush(self._queue_key(area_id), f"{user_id}:{number}")
            priority = "tail"

        self.r.hset(self._user_key(user_id), mapping={
            "area_id": area_id,
            "number": number,
            "status": "waiting",
            "join_time": time.time(),
            "is_compensated": "1" if self.fault_compensation else "0",
            "prefer": json.dumps(self._prefs(info), ensure_ascii=False),
            "device_id": "",
            "source": "online",
        })
        position = self.status(user_id).get("position", 1)
        return {"ok": True, "status": "waiting", "reason": f"{fault_type}，已登记报修",
                "compensation": priority, "position": position, "new_number": number}

    # ---------- 临时离开（规则 10） ----------
    def temp_leave(self, user_id: str) -> dict:
        info = self._hget(user_id)
        if not info or info.get("status") != "checked_in":
            return {"error": "仅已核销用户可临时离开"}
        self.r.hset(self._user_key(user_id), mapping={
            "status": "temp_leave", "temp_leave_at": time.time(),
        })
        if info.get("stall_id"):
            self.r.hset(self._stall_key(info["stall_id"]),
                        mapping={"status": "temp_leave", "current_user": user_id})
        return {"ok": True, "status": "temp_leave",
                "timeout": self.temp_leave_timeout}

    def return_from_temp_leave(self, user_id: str) -> dict:
        info = self._hget(user_id)
        if not info or info.get("status") != "temp_leave":
            return {"error": "当前不在临时离开状态"}
        self.r.hset(self._user_key(user_id), mapping={"status": "checked_in"})
        if info.get("stall_id"):
            self.r.hset(self._stall_key(info["stall_id"]),
                        mapping={"status": "occupied", "current_user": user_id})
        return {"ok": True, "status": "checked_in"}

    def check_temp_leave_timeout(self, user_id: str) -> dict:
        """临时离开超时 -> 视为洗浴结束，票据 completed，释放隔间"""
        info = self._hget(user_id)
        if not info or info.get("status") != "temp_leave":
            return {"msg": "不在临时离开状态"}
        if time.time() - float(info.get("temp_leave_at", 0)) < self.temp_leave_timeout:
            return {"msg": "未超时"}
        return self.complete(user_id, info.get("stall_id"))

    def handle_door_event(self, area_id: str, stall_id: str, door: str) -> dict:
        """
        门磁事件联动：临时离开期间开门不释放隔间；否则开门即视为洗浴结束。
        """
        stall = {self._s(k): self._s(v)
                 for k, v in (self.r.hgetall(self._stall_key(stall_id)) or {}).items()}
        uid = stall.get("current_user") or ""
        if door != "open" or not uid:
            return {"msg": "忽略"}
        info = self._hget(uid)
        if info.get("status") == "temp_leave":
            return {"msg": "临时离开中，不释放隔间", "stall_id": stall_id}
        return self.complete(uid, stall_id)

    # ---------- 洗浴完成 / 取消 / 隔间联动 ----------
    def complete(self, user_id: str, stall_id: str = None) -> dict:
        info = self._hget(user_id)
        if not info:
            return {"error": "未找到票据"}
        area_id = info.get("area_id", "A")
        stall_id = stall_id or info.get("stall_id")
        self._device_decr(info.get("device_id"))
        self.r.delete(self._user_key(user_id))
        if stall_id:
            self.r.hset(self._stall_key(stall_id), mapping={
                "status": "idle", "current_user": "", "available": "1",
                "updated_at": time.time()})
            self.r.incr(self._free_key(area_id))
        return {"ok": True, "status": "completed", "stall_id": stall_id,
                "next": self.call_next(area_id).get("user_id")}

    def leave(self, user_id: str) -> dict:
        """用户主动取消排队"""
        info = self._hget(user_id)
        if not info:
            return {"error": "未在排队"}
        area_id = info["area_id"]
        if info.get("status") == "waiting":
            self.r.lrem(self._queue_key(area_id), 0, f"{user_id}:{info['number']}")
        if info.get("status") == "called":
            self._release_stall(area_id, info.get("reserved_stall_id"))
        self._device_decr(info.get("device_id"))
        self.r.delete(self._user_key(user_id))
        return {"ok": True, "area_id": area_id, "status": "cancelled"}

    def on_stall_freed(self, area_id: str, stall_id: str = None) -> dict:
        """隔间由占用变为空闲时调用"""
        self.r.incr(self._free_key(area_id))
        if stall_id:
            self.r.hset(self._stall_key(stall_id), mapping={
                "status": "idle", "current_user": "", "available": "1"})
            return self.call_next(area_id, available_stalls=[stall_id])
        return self.call_next(area_id)

    def on_stall_occupied(self, area_id: str):
        """隔间被占用时调用"""
        if int(self.r.get(self._free_key(area_id)) or 0) > 0:
            self.r.decr(self._free_key(area_id))

    # ---------- 内部工具 ----------
    def _all_users(self) -> list:
        """扫描用户票据 key（演示/小规模用；线上应改用 stall -> user 反向索引）"""
        store = getattr(self.r, "store", None)
        if isinstance(store, dict):
            return [k[len("user:"):-len(":queue")] for k in store
                    if k.startswith("user:") and k.endswith(":queue")]
        return []

    def _pick_next(self, area_id: str, available_stalls=None):
        """从队首（列表尾部）向后找第一个可服务且偏好匹配的用户"""
        queue = self._queue_items(area_id)
        for item in reversed(queue):
            uid, _, number = item.rpartition(":")
            info = self._hget(uid)
            if not info or info.get("status") != "waiting":
                self.r.lrem(self._queue_key(area_id), 0, item)   # 清理脏队列项
                continue
            prefs = self._prefs(info)
            if prefs and available_stalls and not set(prefs) & set(available_stalls):
                continue                                        # 规则 2：偏好不匹配继续等
            self.r.lrem(self._queue_key(area_id), 0, item)
            return uid, number
        return None

    @staticmethod
    def _match_stall(prefs, available_stalls):
        if not available_stalls:
            return None
        if prefs:
            for s in available_stalls:
                if s in prefs:
                    return s
            return None
        return available_stalls[0]

    def _reserve_stall(self, area_id, stall_id, user_id):
        self.r.hset(self._stall_key(stall_id), mapping={
            "status": "reserved", "current_user": user_id, "available": "0",
            "updated_at": time.time()})
        if int(self.r.get(self._free_key(area_id)) or 0) > 0:
            self.r.decr(self._free_key(area_id))

    def _release_stall(self, area_id, stall_id):
        """释放预留/临时离开中的隔间，并把空闲计数加回去"""
        if not stall_id:
            return
        stall = {self._s(k): self._s(v)
                 for k, v in (self.r.hgetall(self._stall_key(stall_id)) or {}).items()}
        if stall.get("status") in ("reserved", "temp_leave", "fault"):
            self.r.hset(self._stall_key(stall_id), mapping={
                "status": "idle", "current_user": "", "available": "1",
                "updated_at": time.time()})
            self.r.incr(self._free_key(area_id))

    def _occupy_stall(self, stall_id, user_id):
        self.r.hset(self._stall_key(stall_id), mapping={
            "status": "occupied", "current_user": user_id, "available": "0",
            "updated_at": time.time()})


# ================= 演示（用内存版 Redis 模拟） =================
class MemoryRedis:
    """极简内存版 Redis，仅用于本地演示"""
    def __init__(self):
        self.store = {}
        self.lock = threading.Lock()

    def _k(self, k): return k.decode() if isinstance(k, bytes) else k

    def exists(self, k): return self._k(k) in self.store
    def incr(self, k):
        with self.lock:
            self.store[self._k(k)] = int(self.store.get(self._k(k), 0)) + 1
            return self.store[self._k(k)]
    def decr(self, k):
        with self.lock:
            self.store[self._k(k)] = int(self.store.get(self._k(k), 0)) - 1
            return self.store[self._k(k)]
    def get(self, k): return self.store.get(self._k(k))
    def setnx(self, k, v):
        with self.lock:
            if self._k(k) in self.store: return False
            self.store[self._k(k)] = v; return True
    def expire(self, k, t): pass
    def delete(self, k): self.store.pop(self._k(k), None)
    def lpush(self, k, v):
        self.store.setdefault(self._k(k), []).insert(0, v)
    def rpush(self, k, v):
        self.store.setdefault(self._k(k), []).append(v)
    def rpop(self, k):
        lst = self.store.get(self._k(k), [])
        return lst.pop() if lst else None
    def llen(self, k): return len(self.store.get(self._k(k), []))
    def lrange(self, k, s, e):
        lst = self.store.get(self._k(k), [])
        return lst[s:] if e == -1 else lst[s:e+1]
    def lrem(self, k, count, v):
        lst = self.store.get(self._k(k), [])
        self.store[self._k(k)] = [x for x in lst if x != v]
    def hset(self, k, mapping=None):
        self.store.setdefault(self._k(k), {}).update(mapping or {})
    def hgetall(self, k):
        return dict(self.store.get(self._k(k), {}))


if __name__ == '__main__':
    r = MemoryRedis()
    qm = QueueManager(r, checkin_timeout=180)
    r.store['area:A:free_count'] = 2
    r.store['stall:F01'] = {'status': 'idle', 'available': '1'}
    r.store['stall:F02'] = {'status': 'idle', 'available': '1'}

    print("1 线上取号  :", qm.join('u1', 'A', hour=21, device_id='dev1'))
    print("7 重复取号  :", qm.join('u1', 'A', hour=21, device_id='dev1'))
    print("2 偏好隔间  :", qm.join('u2', 'A', hour=21, prefer_stall_ids=['F02']))
    print("9 设备限制  :", qm.join('u3', 'A', hour=21, device_id='dev1'),
          qm.join('u4', 'A', hour=21, device_id='dev1'))

    print("  队列服务顺序(尾部=下一个):", qm._queue_items('A'))
    print("11 叫号绑定 :", qm.call_next('A', available_stalls=['F01']))
    # u1 无偏好 -> 分到 F01
    print("11 扫码核销 :", qm.check_in('u1', 'F01'))
    print("10 临时离开 :", qm.temp_leave('u1'))
    print("10 开门事件 :", qm.handle_door_event('A', 'F01', 'open'))
    print("10 返回隔间 :", qm.return_from_temp_leave('u1'))

    # 规则 1：叫号后超时
    print("  叫号 u2   :", qm.call_next('A', available_stalls=['F02']))
    r.store['user:u2:queue']['called_time'] = 0
    print("1  超时未到 :", qm.handle_no_show('u2'))

    # 规则 8：洗浴中故障 + 补偿插队
    print("8  故障重排 :", qm.report_fault('u1', 'F01'))
    print("  队列顺序  :", qm._queue_items('A'))

    # 规则 4/5：线下取号
    r.store['stall:F03'] = {'status': 'idle', 'available': '1'}
    print("4  线下直入 :", qm.join('u5', 'A', hour=21, source='offline',
                                    available_stalls=['F03']))
    print("5  线下排队 :", qm.join('u6', 'A', hour=21, source='offline'))
