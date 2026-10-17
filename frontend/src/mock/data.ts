import type {
  Bathhouse,
  Bathzone,
  QueueTicket,
  Recommend,
  Stall,
  StallStatus,
  WaitForecast,
  WaterPoint,
} from '@/types'

/**
 * 模拟数据层：当前处于纯前端阶段，用假数据驱动完整 UI 闭环。
 * 后端（FastAPI + Redis + WebSocket）就绪后，把下面各处 MOCK 开关关掉即可无缝切换。
 */
export const MOCK_ENABLED = true

const rand = (min: number, max: number) => Math.floor(Math.random() * (max - min + 1)) + min

/**
 * 为一栋楼按分区生成隔间，并按 zone 划区。
 * perZone 与 zones 等长，表示每个分区各有多少个隔间。编号按分区前缀 + 序号（如 A01、A02…）。
 */
function makeStalls(zones: string[], perZone: number[]): Stall[] {
  const stalls: Stall[] = []
  let id = 1
  zones.forEach((zone, zi) => {
    for (let n = 0; n < perZone[zi]; n++) {
      const w = Math.random()
      // 后端枚举：idle/occupied/fault（无 cleaning，用户不要保洁态；满员并入 occupied，离线并入 fault）
      const status: StallStatus = w < 0.45 ? 'idle' : w < 0.9 ? 'occupied' : 'fault'
      const no = `${zone.charAt(0)}${String(n + 1).padStart(2, '0')}`
      stalls.push({ id: id++, stallId: no, no, status, zone, x: n, y: zi })
    }
  })
  return stalls
}

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

/** 统计淋浴位状态数量（三态：空闲/占用/故障；cleaning 并入 occupied，统一不含保洁） */
export function countStalls(stalls: Stall[]) {
  let idle = 0
  let occupied = 0
  let fault = 0
  for (const s of stalls) {
    if (s.status === 'idle') idle++
    else if (s.status === 'fault') fault++
    else occupied++ // occupied 与 cleaning 均视为占用
  }
  return { idle, occupied, fault }
}

function makeBathhouse(
  id: number,
  name: string,
  area: string,
  zones: string[],
  perZone: number[]
): Bathhouse {
  const stalls = makeStalls(zones, perZone)
  const all = groupZones(stalls)
  const count = countStalls(stalls)
  const total = stalls.length
  const idle = count.idle
  // 数据自洽：有空闲位 → 无人排队、等待为 0；全部占用 → 才产生排队与等待
  const hasQueue = idle === 0
  const queueCount = hasQueue ? rand(1, 18) : 0
  // 等待估算：排队人数 / 总位 × 每轮约 15 分钟轮换
  const waitMinutes = hasQueue ? Math.max(1, Math.round((queueCount / total) * 15)) : 0
  // 拥挤度：有排队时至少 70（展示为「高峰/拥挤」，绝不再是绿色）；否则按占用率
  const occupancy = (total - idle) / total
  const crowd = hasQueue ? Math.min(100, 70 + queueCount) : Math.round(occupancy * 100)
  return {
    id,
    name,
    area,
    crowd,
    queueCount,
    waitMinutes,
    freeCount: idle,
    totalCount: total,
    waterTemp: rand(38, 43),
    open: true,
    usageRate: Math.round(occupancy * 100),
    turnover: +(rand(3, 9) + Math.random() * 0.6).toFixed(1),
    stalls,
    zones: all,
  }
}

/**
 * 分区风格池：31 栋楼循环使用这些分区划分，让各楼风格有变化但总体一致。
 * 统一使用字母分区（A区/B区/C区…），不使用「一层/二层/三层」楼层命名，
 * 这样隔间编号即为 A01/B01/C01 等，直观清晰。
 */
const ZONE_POOLS: string[][] = [
  ['A区', 'B区', 'C区', 'D区'],
  ['A区', 'B区', 'C区'],
  ['A区', 'B区'],
  ['A区', 'B区', 'C区', 'D区', 'E区'],
]

/**
 * 生成全部 31 栋宿舍楼浴室（id=1…31，name 为「X号楼浴室」）。
 * 每栋楼隔间数在 12~30 之间浮动，均分到各分区（余数归第一区），保证总隔间数与生成一致。
 */
function makeAllBathhouses(): Bathhouse[] {
  const list: Bathhouse[] = []
  for (let i = 1; i <= 31; i++) {
    const zones = ZONE_POOLS[(i - 1) % ZONE_POOLS.length]
    const total = rand(12, 30)
    const base = Math.floor(total / zones.length)
    const rem = total - base * zones.length
    const perZone = zones.map((_, zi) => (zi === 0 ? base + rem : base))
    list.push(makeBathhouse(i, `${i}号楼浴室`, `宿舍 ${i} 栋`, zones, perZone))
  }
  return list
}

/** 浴室种子数据：31 栋宿舍楼 */
export const bathhouses: Bathhouse[] = makeAllBathhouses()

/** 模拟网络延迟 */
const delay = (ms = 300) => new Promise((r) => setTimeout(r, ms))

/** 报修联动：把某浴室某隔间标记为故障/维修中（同步修改种子数据，全站可见） */
export function markStallMaintenance(bathhouseId: number, stallId: number | string) {
  const b = bathhouses.find((x) => x.id === bathhouseId)
  const s = b?.stalls.find((x) => x.id === stallId || x.stallId === stallId)
  if (s) s.status = 'fault'
}

/* ================= Mock API ================= */

export async function mockGetBathhouses(): Promise<Bathhouse[]> {
  await delay()
  // 返回副本，避免外部修改污染种子数据
  return JSON.parse(JSON.stringify(bathhouses))
}

export async function mockGetBathhouse(id: number): Promise<Bathhouse> {
  await delay()
  const b = bathhouses.find((x) => x.id === id)
  if (!b) throw new Error('浴室不存在')
  return JSON.parse(JSON.stringify(b))
}

/**
 * 取号：若当前浴室有空闲位，直接随机分配一个空闲隔间（标记为占用，无需排队）；
 * 若全部占用，则进入排队队列。
 */
export async function mockTakeTicket(bathhouseId: number): Promise<QueueTicket> {
  await delay(400)
  const b = bathhouses.find((x) => x.id === bathhouseId)!
  const open = b.stalls.filter((s) => s.status === 'idle')
  if (open.length > 0) {
    // 有空位 → 随机拿一个空闲隔间直接分配
    const stall = open[Math.floor(Math.random() * open.length)]
    stall.status = 'occupied'
    return {
      id: `t${Date.now()}`,
      code: stall.stallId,
      bathhouseId,
      bathhouseName: b.name,
      status: 'calling',
      ahead: 0,
      waitMinutes: 0,
      createdAt: Date.now(),
      assignedStall: stall.stallId,
    }
  }
  // 全部占用 → 进入排队
  const ahead = Math.max(0, b.queueCount - rand(0, 2))
  return {
    id: `t${Date.now()}`,
    code: `A-${String(rand(100, 999))}`,
    bathhouseId,
    bathhouseName: b.name,
    status: 'waiting',
    ahead,
    waitMinutes: Math.max(1, Math.round(ahead * 1.8)),
    createdAt: Date.now(),
  }
}

/**
 * 预约指定隔间：用户点详情页的空闲隔间时，直接把它标记为占用并返回一张「叫号中」票。
 * 该逻辑用于同步更新 store 内存数据，保证 UI 联动变化。
 */
export async function mockReserveStall(
  bathhouseId: number,
  stallId: number | string
): Promise<QueueTicket> {
  await delay(300)
  const b = bathhouses.find((x) => x.id === bathhouseId)
  if (!b) throw new Error('浴室不存在')
  const s = b.stalls.find((x) => x.id === stallId || x.stallId === stallId)
  if (!s) throw new Error('隔间不存在')
  if (s.status !== 'idle') throw new Error('该隔间不可选')
  s.status = 'occupied'
  return {
    id: `t${Date.now()}`,
    code: s.stallId,
    bathhouseId,
    bathhouseName: b.name,
    status: 'calling',
    ahead: 0,
    waitMinutes: 0,
    createdAt: Date.now(),
    assignedStall: s.stallId,
  }
}

export async function mockGetRecommend(fromId: number): Promise<Recommend> {
  await delay(500)
  // 推荐一个当前等待更短的浴室
  const others = bathhouses.filter((b) => b.id !== fromId).sort((a, b) => a.waitMinutes - b.waitMinutes)
  const target = others[0]
  return {
    reason: target.crowd < 50 ? '空闲率高，几乎不用排队' : '等待时间比当前少，人更少',
    distance: rand(3, 12) === 1 ? '约 200m' : '约 400m',
    bathhouse: JSON.parse(JSON.stringify(target)),
  }
}

/**
 * 未来 1 小时等待预测（模拟 LightGBM 输出）：
 * 每 5 分钟一个点，叠加晚高峰（20:00 后）抬升 + 周期性抖动。
 */
export async function mockGetForecast(bathhouseId: number): Promise<WaitForecast> {
  await delay(400)
  const b = bathhouses.find((x) => x.id === bathhouseId)!
  const now = new Date()
  const times: string[] = []
  const waitMinutes: number[] = []
  const lower: number[] = []
  const upper: number[] = []
  let base = Math.max(1, b.waitMinutes)
  for (let i = 0; i < 13; i++) {
    const t = new Date(now.getTime() + i * 5 * 60000)
    times.push(`${String(t.getHours()).padStart(2, '0')}:${String(t.getMinutes()).padStart(2, '0')}`)
    // 晚高峰时段等待抬升，随时间缓慢回落
    const h = t.getHours()
    const wave = h >= 20 && h < 22 ? 4 : 0
    const jitter = Math.round(Math.sin(i * 0.7) * 2.5)
    const v = Math.max(1, base + wave + jitter + Math.max(0, 2 - i))
    waitMinutes.push(v)
    lower.push(Math.max(0, v - 2))
    upper.push(v + 2)
  }
  return { times, waitMinutes, lower, upper }
}

/**
 * 每日用水高峰曲线（模拟全天 24 小时用水量）。
 * 形态：深夜低谷、午间小峰、晚间（18-22 点）主高峰，符合校园作息。
 */
export async function mockGetWaterUsage(): Promise<WaterPoint[]> {
  await delay(300)
  return Array.from({ length: 24 }, (_, hour) => {
    // 基础唤醒曲线：模拟一天人流的峰谷
    let usage: number
    if (hour >= 7 && hour <= 9) usage = 300 + rand(-40, 40) // 晨间
    else if (hour === 12) usage = 200 + rand(-30, 30) // 午间小峰
    else if (hour >= 18 && hour <= 22) usage = 420 + (hour - 18) * 30 + rand(-50, 50) // 晚间主峰
    else if (hour >= 23 || hour <= 5) usage = 40 + rand(0, 30) // 深夜低谷
    else usage = 120 + rand(-40, 60) // 平段
    return { hour, usage: Math.max(5, Math.round(usage)) }
  })
}
