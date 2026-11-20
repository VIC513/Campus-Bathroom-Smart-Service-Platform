import axios from 'axios'
import http from './http'
import type {
  Bathhouse,
  Bathzone,
  FaultType,
  PredictDto,
  QueueResultDto,
  QueueTicket,
  Recommend,
  RecommendDto,
  Stall,
  StallDto,
  StallStatus,
  WaitForecast,
  WaterPoint,
} from '@/types'
import { FAULT_REASON_MAP } from '@/types'
import {
  mockGetBathhouse,
  mockTakeTicket,
  mockReserveStall,
  mockGetRecommend,
  mockGetForecast,
  mockGetWaterUsage,
  markStallMaintenance,
  mockAddFaultFeedback,
  mockGetAdminOverview,
  mockFaultDone,
  mockToggleMaintenanceAll,
} from '@/mock/data'

/**
 * API 层：真实 HTTP 优先，请求失败时回退到对应的 mock 实现，保证 UI 不白屏。
 */

/**
 * 统一包装：先尝试真实请求，成功返回真实数据；
 * 网络错误 / 后端未启动 / 5xx 时回退 mock，保证 UI 不白屏。
 * 但 4xx 是后端明确的业务拒绝（重复取号、隔间已被占用、权限不足等），
 * 必须原样抛给页面提示，绝不能伪造成功，否则会出现“假票”“假维修完成”。
 */
async function safeFetch<T>(real: () => Promise<T>, mock: () => Promise<T>): Promise<T> {
  try {
    return await real()
  } catch (e) {
    if (axios.isAxiosError(e) && e.response && e.response.status >= 400 && e.response.status < 500) {
      const detail = (e.response.data as { detail?: unknown } | undefined)?.detail
      throw new Error(typeof detail === 'string' ? detail : '操作未成功，请稍后重试')
    }
    console.warn('[api] 真实请求失败，回退 mock：', e)
    return mock()
  }
}

/** 当前登录用户 id：由登录页/注册成功时设置（见 stores/user.ts）；默认演示账号 */
let currentUserId = 'student001'
export function setCurrentUserId(id: string) {
  currentUserId = id
}

/**
 * 后端接入的楼栋配置。
 * 默认 1 号楼；可通过 .env 覆盖，避免楼栋 id / 名称散落在各页面：
 *   VITE_BATHHOUSE_ID=2
 *   VITE_BATHHOUSE_NAME=2 号楼浴室
 *   VITE_BATHHOUSE_AREA=宿舍 2 栋
 */
export const BATHHOUSE_ID = Number(import.meta.env.VITE_BATHHOUSE_ID || 1)
export const BATHHOUSE_NAME =
  (import.meta.env.VITE_BATHHOUSE_NAME as string | undefined) || `${BATHHOUSE_ID} 号楼浴室`
const BATHHOUSE_AREA =
  (import.meta.env.VITE_BATHHOUSE_AREA as string | undefined) || `宿舍 ${BATHHOUSE_ID} 栋`

/** 是否为真实后端接入的楼栋（仅 1 号楼） */
const isRealBuilding = (id: number) => id === BATHHOUSE_ID

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min

const pad = (n: number) => String(n).padStart(2, '0')

/** 按 zone 分组为分区 */
function groupZones(stalls: Stall[]): Bathzone[] {
  const map = new Map<string, Stall[]>()
  for (const s of stalls) {
    const arr = map.get(s.zone) ?? []
    arr.push(s)
    map.set(s.zone, arr)
  }
  return [...map.entries()].map(([name, arr]) => ({ name, stalls: arr }))
}

/**
 * 前端适配：把后端返回的隔间列表组装成单个「浴室看板」。
 * - 分区按 stall_id 首字母聚类（'F01' → F区），无字母前缀则按索引均分到 A/B/C/D 区。
 * - totalCount、usageRate、queueCount 等由隔间状态推导，数据自洽。
 */
function buildBathhouseFromStalls(dtos: StallDto[]): Bathhouse {
  const zones: Record<string, Stall[]> = {}
  const stalls = dtos.map((dto, i) => {
    const prefix = (dto.stall_id || '').charAt(0).toUpperCase()
    const zoneName = /[A-Z]/.test(prefix) ? `${prefix}区` : `${String.fromCharCode(65 + (i % 4))}区`
    const stall: Stall = {
      id: i + 1,
      stallId: dto.stall_id,
      no: dto.stall_id,
      status: dto.status,
      zone: zoneName,
      x: i % 4,
      y: Math.floor(i / 4),
      usedSeconds: dto.used_seconds,
      gender: dto.gender,
    }
    ;(zones[zoneName] ??= []).push(stall)
    return stall
  })

  const idle = stalls.filter((s) => s.status === 'idle').length
  const total = stalls.length
  const hasQueue = idle === 0
  const queueCount = hasQueue ? rand(1, Math.min(18, Math.max(2, total))) : 0
  const waitMinutes = hasQueue ? Math.max(1, Math.round((queueCount / total) * 15)) : 0
  const occupancy = total ? (total - idle) / total : 0
  const crowd = hasQueue ? Math.min(100, 70 + queueCount) : Math.round(occupancy * 100)

  return {
    id: BATHHOUSE_ID,
    name: BATHHOUSE_NAME,
    area: BATHHOUSE_AREA,
    crowd,
    queueCount,
    waitMinutes,
    freeCount: idle,
    totalCount: total,
    waterTemp: 41,
    open: true,
    usageRate: Math.round(occupancy * 100),
    turnover: +(rand(3, 9) + Math.random() * 0.6).toFixed(1),
    stalls,
    zones: groupZones(stalls),
  }
}

/** 由 /api/predict 生成未来 1 小时（每 5 分钟一点，共 13 点）等待曲线 */
function buildForecast(dto: PredictDto): WaitForecast {
  const times: string[] = []
  const waitMinutes: number[] = []
  const lower: number[] = []
  const upper: number[] = []
  const base = Math.max(0, dto.wait_minutes ?? 0)
  const now = new Date()
  for (let i = 0; i < 13; i++) {
    const t = new Date(now.getTime() + i * 5 * 60000)
    times.push(`${pad(t.getHours())}:${pad(t.getMinutes())}`)
    // 晚高峰（20-22 点）等待抬升 + 周期性抖动
    const wave = t.getHours() >= 20 && t.getHours() < 22 ? 4 : 0
    const jitter = Math.round(Math.sin(i * 0.7) * 2.5)
    const v = Math.max(0, base + wave + jitter + Math.max(0, 2 - i))
    waitMinutes.push(v)
    lower.push(Math.max(0, v - 2))
    upper.push(v + 2)
  }
  return { times, waitMinutes, lower, upper }
}

/* ================= 接口函数 ================= */

/**
 * 浴室列表：单楼模式（后端同学的单楼版服务，15 隔间 F01-F15）。
 * 真实 GET /api/stalls 组装为长度 1 的数组；后端未启动时回退为 mock 单楼。
 */
export async function getBathhouses(): Promise<Bathhouse[]> {
  return safeFetch(
    async () => {
      const data = await (http.get('/stalls') as Promise<StallDto[]>)
      return [buildBathhouseFromStalls(data)]
    },
    async () => [await mockGetBathhouse(BATHHOUSE_ID)]
  )
}

/** 单浴室详情：单楼模式，真实 GET /api/stalls；后端未启动时回退 mock */
export async function getBathhouse(id: number): Promise<Bathhouse> {
  return safeFetch(
    async () => {
      const data = await (http.get('/stalls') as Promise<StallDto[]>)
      return buildBathhouseFromStalls(data)
    },
    () => mockGetBathhouse(id)
  )
}

/** 取号：真实 POST /api/queue → 映射为前端 QueueTicket；position=0 视为直接分配（仅 1 号楼） */
export async function takeTicket(bathhouseId: number): Promise<QueueTicket> {
  // 非真实楼栋不请求后端（后端只有 1 号楼），直接用模拟取号逻辑
  if (!isRealBuilding(bathhouseId)) return mockTakeTicket(bathhouseId)
  return safeFetch(
    async () => {
      const dto = (await http.post('/queue', { user_id: currentUserId })) as QueueResultDto
      const ticket: QueueTicket = {
        id: dto.ticket_id,
        code: dto.ticket_id,
        bathhouseId,
        bathhouseName: BATHHOUSE_NAME,
        // position=0 且带 stall_id → 直接分配空位；否则排队
        status: dto.position === 0 && dto.stall_id ? 'calling' : 'waiting',
        ahead: Math.max(0, dto.position ?? 0),
        waitMinutes: Math.max(0, Math.round((dto.estimated_wait ?? 0) / 60)),
        createdAt: Date.now(),
        assignedStall: dto.position === 0 && dto.stall_id ? dto.stall_id : undefined,
      }
      return ticket
    },
    () => mockTakeTicket(bathhouseId)
  )
}

/**
 * 预约指定隔间：详情页点「空闲」隔间时调用。
 * 真实：POST /api/queue（复用取号语义）；失败回退 mock 直接把该隔间标记占用。
 */
export async function reserveStall(
  bathhouseId: number,
  stallId: number | string
): Promise<QueueTicket> {
  // 非真实楼栋直接走模拟预约（后端只有 1 号楼）
  if (!isRealBuilding(bathhouseId)) return mockReserveStall(bathhouseId, stallId)
  return safeFetch(
    async () => {
      const dto = (await http.post('/queue', { user_id: currentUserId, stall_id: String(stallId) })) as QueueResultDto
      const assigned = dto.stall_id ?? String(stallId)
      return {
        id: dto.ticket_id,
        code: dto.ticket_id,
        bathhouseId,
        bathhouseName: BATHHOUSE_NAME,
        status: 'calling',
        ahead: 0,
        waitMinutes: 0,
        createdAt: Date.now(),
        assignedStall: assigned,
      }
    },
    () => mockReserveStall(bathhouseId, stallId)
  )
}

/** 取消排队：真实 POST /api/queue/cancel?ticket_id=id */
export async function cancelTicket(id: string): Promise<boolean> {
  return safeFetch(
    async () => {
      await http.post(`/queue/cancel?ticket_id=${encodeURIComponent(id)}`)
      return true
    },
    () => Promise.resolve(true)
  )
}

/** 我的票号状态（用于刷新后恢复排队/叫号） */
export interface MyTicketDto {
  status: 'no_ticket' | 'expired' | 'queuing' | 'calling'
  ticket_id?: string
  position?: number
  stall_id?: string
  message?: string
}

/**
 * 查询当前登录用户的票号。
 * 这里不回退 mock：票号只存在于后端，伪造没有意义；后端不可用时返回 null 即可。
 */
export async function fetchMyTicket(): Promise<MyTicketDto | null> {
  try {
    return (await http.get('/queue/my_ticket')) as MyTicketDto
  } catch {
    return null
  }
}

/** 扫码核销：后端把 reserved 置为 occupied，并校验票号与隔间是否匹配 */
export async function scanCheckin(stallId: number | string): Promise<boolean> {
  return safeFetch(
    async () => {
      await http.post('/queue/scan_checkin', { stall_id: String(stallId) })
      return true
    },
    () => Promise.resolve(true)
  )
}

/** 扫码离场：后端释放隔间与使用者绑定（重复调用保持幂等） */
export async function scanCheckout(stallId: number | string): Promise<boolean> {
  return safeFetch(
    async () => {
      await http.post('/queue/scan_checkout', { stall_id: String(stallId) })
      return true
    },
    () => Promise.resolve(true)
  )
}

/**
 * 报修：真实 POST /api/feedback/fault；失败回退为本地 mock 标记为故障。
 * 无论真实还是 mock，都同步在本地写一条报修记录，保证管理后台离线时也有工单可看。
 */
export async function reportFault(
  bathhouseId: number,
  stallId: number | string,
  type: FaultType,
  detail = ''
) {
  // 本地标记故障 + 写入 mock 工单（真实请求成功时后端自己会记录，无需写）
  const markLocally = () => {
    markStallMaintenance(bathhouseId, stallId)
    mockAddFaultFeedback(
      String(stallId),
      FAULT_REASON_MAP[type],
      detail?.trim() || (type === 'other' ? FAULT_REASON_MAP.other : '同学报修'),
      currentUserId
    )
  }
  // 非真实楼栋不请求后端，直接在本地把隔间标记为故障
  if (!isRealBuilding(bathhouseId)) {
    markLocally()
    return Promise.resolve({ ok: true, stallId, type })
  }
  return safeFetch(
    async () => {
      const body = {
        stall_id: String(stallId),
        reason: FAULT_REASON_MAP[type],
        // 自定义问题：detail 优先填入用户描述；否则用默认描述
        detail: detail?.trim() || (type === 'other' ? FAULT_REASON_MAP.other : '同学报修'),
        user_id: currentUserId,
      }
      const res = (await http.post('/feedback/fault', body)) as { message?: string }
      return { ok: true, stallId, type, message: res.message }
    },
    () => {
      markLocally()
      return Promise.resolve({ ok: true, stallId, type })
    }
  )
}

/** 等待预测：真实 GET /api/predict → 生成未来曲线；失败回退 mock */
export async function getWaitForecast(bathhouseId: number): Promise<WaitForecast> {
  return safeFetch(
    async () => {
      const dto = (await http.get('/predict')) as PredictDto
      return buildForecast(dto)
    },
    () => mockGetForecast(bathhouseId)
  )
}

/** 分流推荐：真实 GET /api/recommend → 适配成 Recommend；失败回退 mock */
export async function getRecommend(fromId: number): Promise<Recommend> {
  return safeFetch(
    async () => {
      const dto = (await http.get('/recommend')) as RecommendDto
      const stallsDtos = (await http.get('/stalls')) as StallDto[]
      const target = buildBathhouseFromStalls(stallsDtos)
      target.queueCount = dto.queue_length ?? target.queueCount
      target.freeCount = dto.idle_stalls ?? target.freeCount
      if (dto.queue_length === 0) target.waitMinutes = 0
      return {
        reason: dto.suggestion || '当前浴室等待更短，推荐前往',
        distance: '约 400m',
        bathhouse: target,
      }
    },
    () => mockGetRecommend(fromId)
  )
}

/**
 * 用水高峰：后端接口文档未提供此接口（有意为之，README 已标注），
 * 始终走本地 mock 模拟，不回退也不请求后端。
 */
export function getWaterUsage(): Promise<WaterPoint[]> {
  return mockGetWaterUsage()
}

/* ================= 管理后台 ================= */

/** 管理后台的一条报修工单（由后端 snake_case 适配而来） */
export interface AdminFeedback {
  stallId: string
  reason: string
  detail: string
  userId: string
  /** 时间戳（ms） */
  time: number
  status: 'pending' | 'done'
}

/** 管理后台总览：隔间状态快照 + 报修工单 */
export interface AdminOverview {
  stalls: { stallId: string; status: StallStatus }[]
  feedbacks: AdminFeedback[]
}

/** 后端 /admin/feedback 原始结构 */
interface AdminFeedbackDto {
  type: string
  stall_id: string
  reason: string
  detail?: string
  user_id: string
  time: string
  status: string
}

/**
 * 拉取管理后台总览（单楼）。
 * 真实：GET /stalls + GET /admin/feedback（仅取 type=fault 的报修）；
 * 失败：回退本地 mock（种子隔间状态 + localStorage 工单）。
 */
export async function getAdminOverview(): Promise<AdminOverview> {
  return safeFetch(
    async () => {
      const stalls = (await http.get('/stalls')) as StallDto[]
      const raw = (await http.get('/admin/feedback')) as AdminFeedbackDto[]
      return {
        stalls: stalls.map((s) => ({ stallId: s.stall_id, status: s.status })),
        feedbacks: raw
          .filter((f) => f.type === 'fault')
          .map((f) => ({
            stallId: f.stall_id,
            reason: f.reason,
            detail: f.detail || '',
            userId: f.user_id,
            time: new Date(f.time).getTime() || Date.now(),
            status: f.status === 'done' ? 'done' : ('pending' as const),
          })),
      }
    },
    async () => mockGetAdminOverview()
  )
}

/** 完成某隔间维修：真实 POST /admin/fault-done?stall_id=；失败回退本地 mock */
export async function adminFaultDone(stallId: string): Promise<boolean> {
  return safeFetch(
    async () => {
      await http.post(`/admin/fault-done?stall_id=${encodeURIComponent(stallId)}`)
      return true
    },
    () => {
      mockFaultDone(stallId)
      return Promise.resolve(true)
    }
  )
}

/**
 * 一键切换全部维修：
 * 真实 POST /admin/maintenance-all（返回 action: start|end）；失败回退本地 mock。
 */
export async function adminToggleMaintenanceAll(): Promise<'start' | 'end'> {
  return safeFetch(
    async () => {
      const res = (await http.post('/admin/maintenance-all')) as { action?: string }
      return res.action === 'end' ? 'end' : 'start'
    },
    () => Promise.resolve(mockToggleMaintenanceAll())
  )
}