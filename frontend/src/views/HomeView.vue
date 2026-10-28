<script setup lang="ts">
import { ref, computed, onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { showConfirmDialog, showToast } from 'vant'
import { useBathhouseStore } from '@/stores/bathhouse'
import { countStalls } from '@/mock/data'
import { getWaterUsage, getWaitForecast } from '@/api'
import WaterPeakChart from '@/components/WaterPeakChart.vue'
import ForecastChart from '@/components/ForecastChart.vue'
import FaultReportPopup from '@/components/FaultReportPopup.vue'
import type { Bathhouse, Bathzone, FaultType, Stall, WaterPoint, WaitForecast } from '@/types'

const store = useBathhouseStore()
const router = useRouter()

// 顶部问候语：按当前小时选择不同措辞
const hour = new Date().getHours()
const greeting = hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

/** 当前浴室（单楼模式：后端同学的单楼版服务，15 隔间 F01-F15） */
const current = (): Bathhouse | undefined => store.bathhouses[0]

/** 全满时是否可排队（无空闲隔间即全满） */
const isFull = () => (current() ? countStalls(current()!.stalls).idle === 0 : false)

/** 状态文字与颜色：空闲/使用中/维修 三态（后端 cleaning 归入使用中） */
function stateOf(s: Stall) {
  if (s.status === 'idle') return { l: '空闲', cls: 'st-idle' }
  if (s.status === 'fault') return { l: '维修', cls: 'st-fault' }
  return { l: '使用中', cls: 'st-busy' }
}

/** 点击空闲隔间 → 确认后预约（走后端 /queue，后端离线自动回退 mock） */
async function tapStall(s: Stall) {
  if (s.status !== 'idle') {
    showToast(stateOf(s).l + '中，不可预约')
    return
  }
  try {
    await showConfirmDialog({
      title: '预约选位',
      message: `确认预约 ${s.stallId} 吗？`,
      confirmButtonText: '确认',
      cancelButtonText: '再看看',
    })
  } catch {
    return // 用户取消
  }
  await store.reserveStallAt(current()!.id, s.stallId)
  showToast(`已分配 ${s.stallId}，请前往`)
}

/** 全满时排队等叫号（走后端 /queue，后端离线自动回退 mock） */
async function joinQueue() {
  // 还有空闲隔间时按钮为禁用态，点按仅提示不取号
  if (!isFull()) {
    showToast('当前还有空闲隔间，直接点格子选位即可')
    return
  }
  const t = await store.takeTicketAt(current()!.id)
  showToast(`取号成功，前面还有 ${t.ahead} 人`)
  router.push('/queue')
}

/* ===== 持续仿真：模拟「线下有人进/出 + 设备故障」，数据一直自动变动 =====
 * 无需手动开关：页面加载即自动运行。
 * 后端在线时以 WebSocket 推送为准（本地改动会被覆盖），后端离线时由本仿真兜底，
 * 保证看板数据始终在流动。预约/取号/报修过的隔间有 5 秒保护期，仿真不会抢走。
 */
let simTimer: ReturnType<typeof setInterval> | null = null

/** 每 2.5s 对随机一个空闲/占用隔间触发一个随机事件 */
function simTick() {
  const b = current()
  if (!b) return
  // 排除用户最近预约/取号/报修过的隔间（仿真保护期 5 秒，见 store.simProtect）
  const free = (ss: Stall[]) => ss.filter((s) => !store.isStallProtected(b.id, s.stallId))
  const idle = free(b.stalls.filter((s) => s.status === 'idle'))
  const busy = free(b.stalls.filter((s) => s.status === 'occupied' || s.status === 'cleaning'))
  const fault = free(b.stalls.filter((s) => s.status === 'fault'))
  const roll = Math.random()
  // 事件权重：有人进 45% / 有人出 40% / 突发故障 10% / 维修完成 5%
  if (roll < 0.45 && idle.length) {
    idle[Math.floor(Math.random() * idle.length)].status = 'occupied'
  } else if (roll < 0.85 && busy.length) {
    busy[Math.floor(Math.random() * busy.length)].status = 'idle'
  } else if (roll < 0.95 && idle.length) {
    idle[Math.floor(Math.random() * idle.length)].status = 'fault'
  } else if (fault.length) {
    fault[Math.floor(Math.random() * fault.length)].status = 'idle'
  }
}

/* ===== 报修（首页入口，弹窗与详情页共用组件） ===== */

const showReport = ref(false)

/** 报修提交：走后端 /feedback/fault（离线自动回退 mock），并联动该隔间变「维修」 */
function onReported(stallId: number, type: FaultType, detail: string) {
  store.submitFault(current()!.id, stallId, type, detail)
}

/* ================= 数据大盘（首页内嵌，与 /dashboard 逻辑一致） ================= */

const water = ref<WaterPoint[]>([])

/** 峰值摘要文案 */
const peakText = computed(() => {
  if (!water.value.length) return ''
  const idx = water.value.reduce((mi, p, i) => (p.usage > water.value[mi].usage ? i : mi), 0)
  const p = water.value[idx]
  return `峰值 ${p.usage} 人次 · ${String(p.hour).padStart(2, '0')}:00`
})

/** 各分区使用率 = 该分区占用格数 / 总数；翻台率 = 使用率 × 基准 8 次/小时 */
const zoneRows = computed(() => {
  const b = current()
  if (!b) return []
  return b.zones.map((z: Bathzone) => {
    const total = z.stalls.length
    const occupied = z.stalls.filter((s) => s.status === 'occupied' || s.status === 'cleaning').length
    const rate = total ? Math.round((occupied / total) * 100) : 0
    return { name: z.name, rate, turnover: +((rate / 100) * 8).toFixed(1) }
  })
})

/** 使用率颜色：<50 绿（从容）、<80 橙（偏忙）、否则红（饱和） */
function rateColor(v: number): string {
  if (v < 50) return 'var(--free)'
  if (v < 80) return 'var(--busy)'
  return 'var(--full)'
}

/* ================= 智能分流推荐 =================
 * 单楼模式下后端仅提供 1 栋浴室，不再做「跨楼推荐」；
 * 这里把它做成「本浴室分流/等位建议」：基于实时占用给出结论，
 * 并结合未来 1 小时等待预测曲线，建议立即使用还是错峰前往。
 */
const recommended = ref<WaitForecast | null>(null)

/** 策略文案：根据当前空闲数与排队人数生成一句话建议 */
const recommendText = computed(() => {
  const b = current()
  if (!b) return ''
  const { idle } = countStalls(b.stalls)
  const queue = b.queueCount ?? 0
  if (idle === 0) {
    return queue > 0
      ? `当前全满，已有 ${queue} 人排队。建议取号等待，或选择错峰时段前往。`
      : '当前全满，最近空位稍后释放，可点下方排队取号。'
  }
  return idle >= 3
    ? `当前空闲 ${idle} 格，人员充足，建议直接点格子选位，无需等待。`
    : `当前空闲 ${idle} 格，略有紧张，建议尽快前往或稍作等待。`
})

/** 峰值摘要文案 */

onMounted(() => {
  store.fetchBathhouses()
  // 首页内嵌大盘：加载每日用水高峰曲线
  getWaterUsage().then((w) => (water.value = w))
  // 加载未来 1 小时等位预测（供智能分流推荐曲线；离线自动回退 mock）
  getWaitForecast(current()?.id ?? 1).then((f) => (recommended.value = f))
  // 自动开启持续仿真，无需用户手动开关
  simTimer = setInterval(simTick, 2500)
})
</script>

<template>
  <div class="page">
    <!-- 顶部问候 + 副标题 + 扫一扫入口 -->
    <div class="topbar">
      <div class="tb-left">
        <div class="hello">{{ greeting }}，同学</div>
        <div class="sub">校园宿舍淋浴选位 · 单栋实时看板</div>
      </div>
      <!-- 扫码使用：扫隔间二维码 → 一键预约/报修 -->
      <button class="scan-entry" @click="router.push('/scan')">
        <van-icon name="scan" />
        <span>扫一扫</span>
      </button>
    </div>

    <!-- 加载态 -->
    <div v-if="!store.bathhouses.length" class="loading">正在加载浴室数据…</div>

    <template v-else>
      <!-- 单楼概览卡：三态统计 -->
      <div class="card" @click="router.push(`/bathhouse/${current()!.id}`)">
        <div class="left">
          <div class="name">{{ current()!.name }}</div>
          <div class="loc">{{ current()!.area }} · 共 {{ current()!.stalls.length }} 个隔间</div>
        </div>
        <div class="mid">
          <div class="s s-free"><b>{{ countStalls(current()!.stalls).idle }}</b><span>空闲</span></div>
          <div class="s s-busy"><b>{{ countStalls(current()!.stalls).occupied }}</b><span>使用中</span></div>
          <div class="s s-full"><b>{{ countStalls(current()!.stalls).fault }}</b><span>维修</span></div>
        </div>
        <div class="right">
          <van-icon name="arrow" color="var(--text-sub)" />
        </div>
      </div>

      <!-- 隔间网格：点空闲格直接预约 -->
      <div class="sec-title">
        <span>隔间选位</span>
        <span class="tip">点「空闲」直接预约</span>
      </div>
      <div class="grid">
        <div
          v-for="s in current()!.stalls"
          :key="s.stallId"
          class="stall"
          :class="stateOf(s).cls"
          @click="tapStall(s)"
        >
          <b>{{ s.stallId }}</b>
          <span>{{ stateOf(s).l }}</span>
        </div>
      </div>

      <!-- 操作行：排队（常驻，全满可点）+ 报修 -->
      <div class="btn-row">
        <button class="queue-btn" :class="{ disabled: !isFull() }" @click="joinQueue">
          <van-icon name="volume-o" />
          {{ isFull() ? '排队等叫号' : '点格子选位' }}
        </button>
        <button class="repair-btn" @click="showReport = true">
          <van-icon name="warning-o" />
          设备报修
        </button>
      </div>

      <!-- ========== 智能分流推荐 ========== -->
      <div class="sec-title dash-title">
        <span>智能分流推荐</span>
        <span class="tip">实时状态 · 等位预测</span>
      </div>

      <div class="dash-card">
        <!-- 策略结论 -->
        <div class="reco-line">
          <van-icon name="aim" color="var(--brand)" />
          <span class="reco-text">{{ recommendText }}</span>
        </div>
        <!-- 未来 1 小时等位预测曲线 -->
        <ForecastChart v-if="recommended" :forecast="recommended" />
        <div class="legend">曲线为未来 1 小时预计等待分钟数，红色虚线为 30 分钟阈值；错峰时段建议在推荐区间前往</div>
      </div>

      <!-- ========== 数据大盘（首页内嵌） ========== -->
      <div class="sec-title dash-title">
        <span>数据大盘</span>
        <span class="tip">每日用水高峰 · 分区使用率</span>
      </div>

      <!-- 每日用水高峰曲线 -->
      <div class="dash-card">
        <div class="dash-card-title">
          <span>每日用水高峰曲线</span>
          <span class="badge">{{ peakText }}</span>
        </div>
        <WaterPeakChart :usage="water.map((p) => p.usage)" :height="110" />
        <div class="legend">深夜低谷（23-5 点） · 晨间小峰（7-9 点） · 晚间主峰（18-22 点）</div>
      </div>

      <!-- 各分区使用率及翻台率 -->
      <div class="dash-card">
        <div class="dash-card-title">
          <span>各分区使用率及翻台率</span>
          <span class="badge">共 {{ zoneRows.length }} 个分区</span>
        </div>
        <div class="rows">
          <div v-for="z in zoneRows" :key="z.name" class="row">
            <div class="row-top">
              <span class="z-name">{{ z.name }}</span>
              <span class="b-tv">翻台 {{ z.turnover.toFixed(1) }} 次/时</span>
            </div>
            <div class="row-bar">
              <van-progress
                :percentage="z.rate"
                :show-pivot="false"
                :color="rateColor(z.rate)"
                :stroke-width="8"
              />
              <span class="b-rate" :style="{ color: rateColor(z.rate) }">{{ z.rate }}%</span>
            </div>
          </div>
        </div>
        <div class="legend">后端在线时使用率为真实实时数据，离线时为本地模拟</div>
      </div>

      <!-- 报修弹窗（与详情页共用组件：选隔间 + 故障类型 + 自定义描述） -->
      <FaultReportPopup
        v-model:show="showReport"
        :bath-name="current()!.name"
        :zones="current()!.zones"
        @reported="onReported"
      />
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: 18px 16px 30px;
  width: 100%;
  margin: 0 auto;
}
.topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.tb-left {
  min-width: 0;
}
.hello {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: 0.5px;
}
.sub {
  font-size: 14px;
  color: var(--text-sub);
  margin-top: 4px;
}
/* 扫一扫入口按钮 */
.scan-entry {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  border: 1px solid var(--line);
  background: var(--bg-card);
  border-radius: 12px;
  padding: 8px 14px;
  font-size: 11px;
  color: var(--text-sub);
  cursor: pointer;
  flex-shrink: 0;
}
.scan-entry .van-icon {
  font-size: 20px;
  color: var(--brand);
}
.scan-entry:active {
  opacity: 0.7;
}
.loading {
  text-align: center;
  color: var(--text-sub);
  padding: 80px 0;
}

/* 单楼概览卡 */
.card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  cursor: pointer;
  margin-bottom: 18px;
}
.left {
  flex: 1 1 0;
  min-width: 0;
}
.name {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-main);
}
.loc {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 3px;
}
.mid {
  display: flex;
  gap: 12px;
}
.s {
  text-align: center;
}
.s b {
  display: block;
  font-size: 18px;
  font-weight: 700;
}
.s span {
  font-size: 11px;
  color: var(--text-sub);
}
.s-free b { color: var(--free); }
.s-busy b { color: var(--busy); }
.s-full b { color: var(--full); }
.right {
  display: flex;
  align-items: center;
}

/* 隔间网格 */
.sec-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
}
.tip {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-sub);
}
.grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
}
.stall {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 0;
  text-align: center;
  cursor: pointer;
  transition: transform 0.15s;
}
.stall:active {
  transform: scale(0.95);
}
.stall b {
  display: block;
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 4px;
}
.stall span {
  font-size: 11px;
}
.st-idle { color: var(--free); border-color: rgba(15, 220, 120, 0.35); }
.st-busy { color: var(--busy); border-color: rgba(255, 152, 0, 0.35); }
.st-fault { color: var(--full); border-color: rgba(232, 70, 58, 0.35); }

/* 操作行：排队 + 报修 双按钮 */
.btn-row {
  display: flex;
  gap: 10px;
  margin-top: 18px;
}
.queue-btn {
  flex: 1;
  padding: 13px 0;
  border: none;
  border-radius: var(--radius);
  background: linear-gradient(135deg, #2f86ff, #1f66ff);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}
.queue-btn.disabled {
  opacity: 0.55;
  background: linear-gradient(135deg, #55606e, #3c4654);
  cursor: not-allowed;
}
.repair-btn {
  flex: 1;
  padding: 13px 0;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  background: var(--bg-card);
  color: var(--busy);
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}

/* ========== 首页内嵌数据大盘 ========== */
.dash-title {
  margin-top: 20px;
}
.dash-card {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  margin-bottom: 12px;
}
.dash-card-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
}
.badge {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-sub);
  background: var(--bg-page);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 8px;
}
.legend {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 8px;
}
/* 智能分流推荐：结论行 */
.reco-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 10px 12px;
  margin-bottom: 10px;
  border-radius: 10px;
  background: var(--brand-dim);
  border: 1px solid rgba(7, 163, 90, 0.25);
}
.reco-text {
  font-size: 13px;
  color: var(--text-main);
  line-height: 1.5;
}
.rows {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.row-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 4px;
}
.z-name {
  font-size: 13px;
  font-weight: 600;
}
.b-tv {
  font-size: 11px;
  color: var(--text-sub);
}
.row-bar {
  display: flex;
  align-items: center;
  gap: 8px;
}
.row-bar :deep(.van-progress) {
  flex: 1;
}
.b-rate {
  font-size: 12px;
  font-weight: 700;
  width: 40px;
  text-align: right;
}
</style>
