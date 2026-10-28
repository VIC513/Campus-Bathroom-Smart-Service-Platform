/**
 * 洗浴历史工具：洗澡报告与「常去浴室」（扫码使用）的本地持久化。
 * 按账号隔离存储，避免不同登录用户数据串台。
 */

/** 一条洗澡报告（扫码使用完成后生成，同步到「我的」页展示） */
export interface BathReportRecord {
  /** 账号（学号/手机号） */
  account: string
  /** 浴室 id */
  bathId: number
  /** 浴室名称（如 1号浴室） */
  bathName: string
  /** 隔间编码（如 F01） */
  stallId: string
  /** 洗澡时长（分钟） */
  minutes: number
  /** 平均水温 ℃ */
  water: number
  /** 习惯分析文案 */
  tip: string
  /** 结束时间戳 */
  at: number
}

/** 常去浴室条目：按隔间号累计使用次数（扫码使用 + 取号预约共用） */
export interface FrequentUse {
  /** 隔间号/号牌，如 F01 */
  no: string
  /** 浴室名称 */
  bathName: string
  /** 累计次数 */
  count: number
  /** 最近一次使用时间戳 */
  last: number
}

const REPORTS_KEY = 'bath_reports_'
const FREQ_KEY = 'bath_frequent_'
const ACTIVE_KEY = 'bath_active_session_'

/** 安全读取 localStorage JSON（损坏时回退空数组，不影响页面） */
function readList<T>(key: string): T[] {
  try {
    const raw = localStorage.getItem(key)
    return raw ? (JSON.parse(raw) as T[]) : []
  } catch {
    return []
  }
}

function writeList(key: string, list: unknown[]) {
  try {
    localStorage.setItem(key, JSON.stringify(list))
  } catch {
    /* 存储满/隐私模式时静默失败，不影响主流程 */
  }
}

/** 读取某账号的洗澡报告（新记录在前） */
export function loadReports(account: string): BathReportRecord[] {
  return readList<BathReportRecord>(REPORTS_KEY + account)
}

/** 追加一条洗澡报告（最多保留 50 条） */
export function pushReport(r: BathReportRecord) {
  const list = loadReports(r.account)
  list.unshift(r)
  writeList(REPORTS_KEY + r.account, list.slice(0, 50))
}

/** 读取某账号的常去浴室（扫码使用） */
export function loadFrequent(account: string): FrequentUse[] {
  return readList<FrequentUse>(FREQ_KEY + account)
}

/** 扫码使用成功后：该浴室/隔间使用次数 +1（常去浴室统计） */
export function bumpFrequent(account: string, no: string, bathName: string) {
  const list = loadFrequent(account)
  const hit = list.find((x) => x.no === no)
  if (hit) {
    hit.count++
    hit.last = Date.now()
  } else {
    list.push({ no, bathName, count: 1, last: Date.now() })
  }
  writeList(FREQ_KEY + account, list)
}

/* ============ 使用中会话与最近报告（跨页面持久化，离开扫码页不丢状态） ============ */

/** 正在使用中的会话快照（扫码入场时保存，结束时清除） */
export interface ActiveSession {
  /** 隔间编码 */
  stallId: string
  /** 浴室 id */
  bathId: number
  /** 基准水温 */
  waterBase: number
  /** 入场时间戳 */
  startAt: number
}

/** 读取未结束的使用中会话（无则 null） */
export function loadActiveSession(account: string): ActiveSession | null {
  try {
    const raw = localStorage.getItem(ACTIVE_KEY + account)
    return raw ? (JSON.parse(raw) as ActiveSession) : null
  } catch {
    return null
  }
}

/** 保存使用中会话 */
export function saveActiveSession(account: string, s: ActiveSession) {
  try {
    localStorage.setItem(ACTIVE_KEY + account, JSON.stringify(s))
  } catch {
    /* 隐私模式等场景静默失败，不影响主流程 */
  }
}

/** 清除使用中会话（正常结束时调用） */
export function clearActiveSession(account: string) {
  try {
    localStorage.removeItem(ACTIVE_KEY + account)
  } catch {
    /* 忽略 */
  }
}
