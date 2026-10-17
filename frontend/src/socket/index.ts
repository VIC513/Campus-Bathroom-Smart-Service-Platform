import { ref, onBeforeUnmount } from 'vue'
import type { Stall, StallStatus } from '@/types'

/**
 * WebSocket 实时通道封装（对接后端 FastAPI /ws/stalls，协议 v3.0）。
 * 连接地址：ws://localhost:8000/ws/stalls（开发环境经 vite /ws 代理转发）。
 * 后端推送格式（整包快照）：
 *   {"type":"stalls_update","data":[{"stall_id":"F01","status":"occupied","gender":"female","used_seconds":0}, …]}
 * 兼容旧格式：单条 {"stall_id":"F01","status":"occupied"} 或纯数组。
 * 后端未启动时静默失败（不打扰页面），连接断开后每 3 秒自动重连，后端上线即自动恢复。
 */

const WS_URL = `ws://${location.host}/ws/stalls`

/** 解析后端推送：优先整包快照，兼容单条 / 纯数组两种旧格式 */
function parseUpdates(raw: unknown): Array<{ stall_id: string; status: StallStatus }> {
  // 整包快照：{"type":"stalls_update","data":[...stalls...]}
  if (raw && typeof raw === 'object' && !Array.isArray(raw)) {
    const o = raw as { type?: string; data?: unknown }
    if (o.type === 'stalls_update' && Array.isArray(o.data)) {
      return o.data as Array<{ stall_id: string; status: StallStatus }>
    }
    // 旧格式：单条 {"stall_id":"F01","status":"occupied"}
    const single = raw as { stall_id?: string; status?: StallStatus }
    if (single.stall_id && single.status) return [{ stall_id: single.stall_id, status: single.status }]
    return []
  }
  // 旧格式：纯数组
  if (Array.isArray(raw)) return raw as Array<{ stall_id: string; status: StallStatus }>
  return []
}

/**
 * 订阅实时状态：stalls 必须是响应式对象（由调用方持有），
 * 每次推送会就地修改淋浴位状态并触发视图更新。
 * 返回 stop 函数，组件卸载时调用。
 */
export function useBathhouseChannel(stalls: Stall[]) {
  let ws: WebSocket | null = null
  let closed = false
  let timer: ReturnType<typeof setInterval> | null = null

  const start = () => {
    try {
      ws = new WebSocket(WS_URL)
    } catch {
      // 连接失败（如后端未启动），静默忽略，不干扰页面
      return () => {}
    }

    ws.onmessage = (e) => {
      let data: unknown
      try {
        data = JSON.parse(e.data)
      } catch {
        return
      }
      const updates = parseUpdates(data)
      updates.forEach((u) => {
        const hit = stalls.find((s) => s.stallId === u.stall_id || s.no === u.stall_id)
        if (hit) hit.status = u.status
      })
    }

    // 连接意外断开：静默重连（后端上线后自动恢复）
    ws.onclose = () => {
      if (closed) return
      timer = setTimeout(() => {
        start()
      }, 3000)
    }

    return () => {
      closed = true
      ws?.close()
      if (timer) clearTimeout(timer)
    }
  }

  const stop = () => {
    closed = true
    ws?.close()
    if (timer) clearTimeout(timer)
  }

  onBeforeUnmount(stop)
  return { start, stop }
}

/** 当前排队叫号状态（演示用计数器） */
export const callingFlag = ref(false)
