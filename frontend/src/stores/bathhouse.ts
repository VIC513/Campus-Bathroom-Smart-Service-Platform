import { ref, computed } from 'vue'
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
  fetchMyTicket,
  scanCheckin,
  scanCheckout,
  BATHHOUSE_ID,
  BATHHOUSE_NAME,
} from '@/api'
import { countStalls } from '@/mock/data'

/** 全局状态：浴室列表 + 我的排队 */
export const useBathhouseStore = defineStore('bathhouse', () => {
  const bathhouses = ref<Bathhouse[]>([])
  const myTickets = ref<QueueTicket[]>([])
  const loading = ref(false)

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
    // 以「后端实际分配的隔间」为准：后端可能因并发把用户改派到其他空位
    const assigned = ticket.assignedStall ?? stallId
    const b = bathhouses.value.find((x) => x.id === bathhouseId)
    const s = b?.stalls.find((x) => x.id === assigned || x.stallId === assigned)
    if (s) s.status = 'occupied'
    return ticket
  }

  /** 刷新页面后从后端恢复自己的票号（排队中 / 已叫号） */
  let restoringTicket = false
  async function restoreTicket() {
    if (restoringTicket || activeTicket.value) return
    restoringTicket = true
    try {
      const dto = await fetchMyTicket()
      if (!dto || !dto.ticket_id) return
      if (dto.status === 'calling' && dto.stall_id) {
        myTickets.value.unshift({
          id: dto.ticket_id,
          code: dto.ticket_id,
          bathhouseId: BATHHOUSE_ID,
          bathhouseName: BATHHOUSE_NAME,
          status: 'calling',
          ahead: 0,
          waitMinutes: 0,
          createdAt: Date.now(),
          assignedStall: dto.stall_id,
        })
      } else if (dto.status === 'queuing') {
        const ahead = Math.max(0, (dto.position ?? 1) - 1)
        myTickets.value.unshift({
          id: dto.ticket_id,
          code: dto.ticket_id,
          bathhouseId: BATHHOUSE_ID,
          bathhouseName: BATHHOUSE_NAME,
          status: 'waiting',
          ahead,
          waitMinutes: ahead * 3,
          createdAt: Date.now(),
        })
      }
    } finally {
      restoringTicket = false
    }
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
  }

  /**
   * 扫码入场：先调用后端核销（reserved → occupied，并校验票号归属），
   * 成功后再把本地隔间标记为「使用中」。
   * 核销失败（票号不匹配 / 隔间已被占用）会抛错，由页面提示用户。
   */
  async function startUseAt(bathhouseId: number, stallId: number | string) {
    const b = bathhouses.value.find((x) => x.id === bathhouseId)
    const s = b?.stalls.find((x) => x.id === stallId || x.stallId === stallId)
    const code = String(s?.stallId ?? stallId)
    await scanCheckin(code)
    // 后端核销后状态为 occupied；本地允许从 idle 或 reserved 进入占用
    if (s && (s.status === 'idle' || s.status === 'reserved')) s.status = 'occupied'
    // 核销时后端已消费票号：本地票根同步标记完成，
    // 避免「我的排队」仍显示叫号、也避免重复取消
    const ticket = myTickets.value.find(
      (t) => t.assignedStall === code && (t.status === 'waiting' || t.status === 'calling')
    )
    if (ticket) ticket.status = 'done'
  }

  /**
   * 扫码离场：通知后端释放隔间与使用者绑定，再把本地隔间恢复为空闲。
   * 有故障的场景走 submitFault（提交报修 + 标记维修）。
   */
  async function endUseAt(bathhouseId: number, stallId: number | string) {
    const b = bathhouses.value.find((x) => x.id === bathhouseId)
    const s = b?.stalls.find((x) => x.id === stallId || x.stallId === stallId)
    const code = String(s?.stallId ?? stallId)
    try {
      await scanCheckout(code)
    } finally {
      // 后端已释放或网络回退时都要让界面回到空闲，避免停留在“使用中”
      if (s) s.status = 'idle'
    }
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
    fetchBathhouses,
    fetchBathhouse,
    takeTicketAt,
    reserveStallAt,
    restoreTicket,
    cancelMyTicket,
    fetchRecommend,
    fetchForecast,
    fetchWaterUsage,
    submitFault,
    startUseAt,
    endUseAt,
    countStalls,
  }
})
