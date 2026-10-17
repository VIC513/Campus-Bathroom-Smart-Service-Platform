import { ref, reactive, computed } from 'vue'
import { defineStore } from 'pinia'
import type { Bathhouse, FaultType, QueueTicket, Recommend, WaitForecast, WaterPoint } from '@/types'
import {
  getBathhouses,
  getBathhouse,
  takeTicket,
  reserveStall,
  cancelTicket,
  getRecommend,
  getWaitForecast,
  getWaterUsage,
  reportFault,
} from '@/api'
import { countStalls } from '@/mock/data'

/** 全局状态：浴室列表 + 我的排队 */
export const useBathhouseStore = defineStore('bathhouse', () => {
  const bathhouses = ref<Bathhouse[]>([])
  const myTickets = ref<QueueTicket[]>([])
  const loading = ref(false)

  /**
   * 仿真保护：记录最近被用户操作（预约/取号/报修）的隔间。
   * 持续仿真的自动事件在保护期内跳过这些隔间，避免「刚预约的格子马上被模拟别人抢走」。
   */
  const simProtect = reactive(new Map<string, number>())
  /** 给隔间加 5 秒仿真保护（key 为 bathhouseId:stallId） */
  function protectStall(bathhouseId: number, stallId: number | string) {
    simProtect.set(`${bathhouseId}:${stallId}`, Date.now() + 5000)
  }
  /** 隔间是否处于仿真保护期；过期自动清理 */
  function isStallProtected(bathhouseId: number, stallId: number | string): boolean {
    const key = `${bathhouseId}:${stallId}`
    const until = simProtect.get(key)
    if (!until) return false
    if (Date.now() < until) return true
    simProtect.delete(key)
    return false
  }

  /** 刷新浴室列表 */
  async function fetchBathhouses() {
    loading.value = true
    try {
      bathhouses.value = await getBathhouses()
    } finally {
      loading.value = false
    }
  }

  /** 拉取单浴室详情 */
  async function fetchBathhouse(id: number) {
    return getBathhouse(id)
  }

  /** 当前进行中的排队（取最新一张 waiting/calling 状态的票） */
  const activeTicket = computed(() =>
    myTickets.value.find((t) => t.status === 'waiting' || t.status === 'calling')
  )

  /** 取号 */
  async function takeTicketAt(bathhouseId: number) {
    const ticket = await takeTicket(bathhouseId)
    myTickets.value.unshift(ticket)
    // 排队若直接分配了空位，该格进入仿真保护期
    if (ticket.assignedStall) protectStall(bathhouseId, ticket.assignedStall)
    return ticket
  }

  /**
   * 预约指定隔间：
   * 1) 调用 api.reserveStall 拿到票并把 ticket 加入 myTickets（/queue 可见）；
   * 2) 把 store.bathhouses 中对应隔间标记为 occupied，联动 UI 更新，重复点同一格不再可选。
   */
  async function reserveStallAt(bathhouseId: number, stallId: number | string) {
    const ticket = await reserveStall(bathhouseId, stallId)
    // 加入我的票夹
    const exists = myTickets.value.find((t) => t.id === ticket.id)
    if (!exists) myTickets.value.unshift(ticket)
    // 联动更新内存中的楼隔间状态
    const b = bathhouses.value.find((x) => x.id === bathhouseId)
    const s = b?.stalls.find((x) => x.id === stallId || x.stallId === stallId)
    if (s) s.status = 'occupied'
    // 该格进入仿真保护期：5 秒内持续仿真不再改动它
    protectStall(bathhouseId, stallId)
    return ticket
  }

  /** 取消排队 */
  async function cancelMyTicket(id: string) {
    await cancelTicket(id)
    const t = myTickets.value.find((x) => x.id === id)
    if (t) t.status = 'canceled'
  }

  /** 拉取分流推荐 */
  async function fetchRecommend(fromId: number): Promise<Recommend> {
    return getRecommend(fromId)
  }

  /** 未来等待预测 */
  const forecast = ref<WaitForecast | null>(null)
  async function fetchForecast(bathhouseId: number) {
    forecast.value = await getWaitForecast(bathhouseId)
  }

  /** 报修：上报后端，并把隔间标记为「故障」以联动更新内存中的浴室数据 */
  async function submitFault(bathhouseId: number, stallId: number | string, type: FaultType, detail = '') {
    const b = bathhouses.value.find((x) => x.id === bathhouseId)
    const s = b?.stalls.find((x) => x.id === stallId || x.stallId === stallId)
    // 上报后端使用隔间编码（如 F02）；mock 回退时也能匹配本地隔间
    await reportFault(bathhouseId, s?.stallId ?? stallId, type, detail)
    if (s) s.status = 'fault'
    // 该格进入仿真保护期：避免仿真立刻把报修格改成别的状态
    protectStall(bathhouseId, s?.stallId ?? stallId)
  }

  /** 每日用水高峰数据 */
  const waterUsage = ref<WaterPoint[]>([])
  async function fetchWaterUsage() {
    waterUsage.value = await getWaterUsage()
  }

  return {
    bathhouses,
    myTickets,
    loading,
    forecast,
    waterUsage,
    activeTicket,
    protectStall,
    isStallProtected,
    fetchBathhouses,
    fetchBathhouse,
    takeTicketAt,
    reserveStallAt,
    cancelMyTicket,
    fetchRecommend,
    fetchForecast,
    fetchWaterUsage,
    submitFault,
    countStalls,
  }
})
