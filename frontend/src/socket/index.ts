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

const WS_PATH = '/ws/stalls'

/**
 * 解析 WebSocket 地址：
 * 1. 优先读取 VITE_WS_URL（前后端分域部署时使用）
 *    - ws(s)://host/ws/stalls：直接使用
 *    - ws(s)://host 或 host:port：自动补 /ws/stalls
 *    - http(s)://host：自动转换为对应的 ws(s) 协议
 * 2. 未配置时按当前页面同域推导；https 页面自动使用 wss，
 *    避免浏览器以「混合内容」为由拦截连接。
 */
function resolveWsUrl(): string {
  const explicit = (import.meta.env.VITE_WS_URL as string | undefined)?.trim()
  if (explicit) {
    const value = explicit.replace(/\/$/, '')
    if (/^wss?:\/\//i.test(value)) {
      return value.endsWith(WS_PATH) ? value : `${value}${WS_PATH}`
    }
    if (/^https?:\/\//i.test(value)) {
      return `${value.replace(/^http/i, 'ws')}${WS_PATH}`
    }
    return `${location.protocol === 'https:' ? 'wss' : 'ws'}://${value}${WS_PATH}`
  }
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${location.host}${WS_PATH}`
}

const WS_URL = resolveWsUrl()

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
  let timer: ReturnType<typeof setTimeout> | null = null

  /** 断线重连：统一在这里清旧定时器，避免多次 onclose 叠加多个重连任务 */
  function scheduleReconnect() {
    if (closed) return
    if (timer) clearTimeout(timer)
    timer = setTimeout(start, 3000)
  }

  function start() {
    closed = false
    // 已连接或正在连接时不重复建连
    if (ws && ws.readyState <= WebSocket.OPEN) return

    try {
      ws = new WebSocket(WS_URL)
    } catch {
      // 地址非法等同步异常：保持静默并稍后重试，不影响页面
      scheduleReconnect()
      return
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
      ws = null
      scheduleReconnect()
    }
  }

  const stop = () => {
    closed = true
    if (timer) {
      clearTimeout(timer)
      timer = null
    }
    if (ws) {
      // 先摘掉回调再关闭，避免主动关闭触发重连
      ws.onclose = null
      ws.onmessage = null
      ws.close()
      ws = null
    }
  }

  onBeforeUnmount(stop)
  return { start, stop }
}

/** 当前排队叫号状态（演示用计数器） */
export const callingFlag = ref(false)
