/** 淋浴位状态：空闲 / 已预约 / 占用 / 保洁中 / 故障（与后端 FastAPI status 对齐） */
export type StallStatus = 'idle' | 'reserved' | 'occupied' | 'cleaning' | 'fault'

/** 故障类型（报修弹窗）。other 表示用户自定义问题 */
export type FaultType = 'water_temp' | 'showerhead' | 'drainage' | 'lock' | 'other'

/**
 * 故障类型 → 后端中文 reason 映射（后端固定枚举，api 层上报时使用）。
 * water_temp→水温异常、showerhead→花洒堵塞、drainage→水压不足、lock→门锁损坏。
 * other→自定义问题（reason 用 detail 填写，后端归为「其他」）。
 */
export const FAULT_REASON_MAP: Record<FaultType, string> = {
  water_temp: '水温异常',
  showerhead: '花洒堵塞',
  drainage: '排水堵塞',
  lock: '门锁损坏',
  other: '其他',
}

/** 故障类型选项 */
export const FAULT_TYPES: { value: FaultType; label: string }[] = [
  { value: 'water_temp', label: '水温异常' },
  { value: 'showerhead', label: '花洒故障' },
  { value: 'drainage', label: '排水堵塞' },
  { value: 'lock', label: '门锁损坏' },
  { value: 'other', label: '其他（自定义）' },
]

/** 淋浴位（隔间） */
export interface Stall {
  id: number
  /** 后端隔间编码，如 "F01" */
  stallId: string
  /** 编号，如 "F01"（兼容旧 UI 用 no 展示） */
  no: string
  status: StallStatus
  /** 所属分区名，如 "A区"、"B区" */
  zone: string
  /** 平面图坐标（栅格），x/y 为列/行 */
  x: number
  y: number
  /** 已使用秒数（后端占用位提供） */
  usedSeconds?: number
  /** 性别属性（后端提供，如 female/male） */
  gender?: string
}

/* ================= 后端 DTO（snake_case，与接口文档一致） ================= */

/** GET /api/stalls 单个元素 */
export interface StallDto {
  stall_id: string
  status: StallStatus
  gender?: string
  used_seconds?: number
}

/** GET /api/stats */
export interface StatsDto {
  total_stalls: number
  idle_stalls: number
  occupied_stalls: number
  cleaning_stalls: number
  fault_stalls: number
  queue_length: number
  usage_rate: number
}

/** GET /api/predict */
export interface PredictDto {
  queue_length: number
  idle_stalls: number
  avg_duration_used: number
  wait_seconds: number
  wait_minutes: number
  level: 'low' | 'medium' | 'high'
  current_hour: number
}

/** GET /api/recommend */
export interface RecommendDto {
  level: string
  suggestion: string
  idle_stalls: number
  queue_length: number
}

/** POST /api/queue */
export interface QueueResultDto {
  ticket_id: string
  position: number
  estimated_wait: number
  stall_id?: string
}

/** POST /api/feedback/fault 、/api/feedback/cleaning */
export interface FeedbackResultDto {
  message: string
  stall_id?: string
  reason?: string
}

/** 浴室分区（每区包含若干淋浴位） */
export interface Bathzone {
  name: string
  stalls: Stall[]
}

/** 浴室 */
export interface Bathhouse {
  id: number
  name: string
  area: string
  /** 拥挤度 0-100 */
  crowd: number
  queueCount: number
  /** 预计等待（分钟） */
  waitMinutes: number
  freeCount: number
  totalCount: number
  /** 水温 ℃ */
  waterTemp: number
  open: boolean
  /** 当前使用率 0-100（占用位 / 总位） */
  usageRate: number
  /** 翻台率：每小时完成人次 / 总位数 */
  turnover: number
  /** 淋浴位（平铺，含 zone） */
  stalls: Stall[]
  /** 分区分组（按 zone 聚合） */
  zones: Bathzone[]
}

/** 每日用水数据点 */
export interface WaterPoint {
  /** 小时，如 0、1、…、23 */
  hour: number
  /** 该时段用水人次 / 使用率 */
  usage: number
}

/** 排队号 */
export interface QueueTicket {
  id: string
  code: string
  bathhouseId: number
  bathhouseName: string
  status: 'waiting' | 'calling' | 'done' | 'canceled'
  /** 前面还有多少人 */
  ahead: number
  waitMinutes: number
  createdAt: number
  /** 有空位时直接分配的隔间编码（如 "F01"）。有值表示无需排队、直接前往该隔间 */
  assignedStall?: string
}

/** 分流推荐结果 */
export interface Recommend {
  reason: string
  distance: string
  bathhouse: Bathhouse
}

/** 未来等待时长预测（配合算法队友的 LightGBM 输出） */
export interface WaitForecast {
  /** 预测时间点，如 20:05 */
  times: string[]
  /** 预测等待（分钟） */
  waitMinutes: number[]
  /** 置信下界 */
  lower: number[]
  /** 置信上界 */
  upper: number[]
}
