<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast, showSuccessToast, showConfirmDialog } from 'vant'
import { useBathhouseStore } from '@/stores/bathhouse'
import { countStalls } from '@/mock/data'
import { useBathhouseChannel } from '@/socket'
import { getWaterUsage } from '@/api'
import WaterPeakChart from '@/components/WaterPeakChart.vue'
import type { Bathhouse, Stall, FaultType } from '@/types'
import FaultReportPopup from '@/components/FaultReportPopup.vue'

const route = useRoute()
const router = useRouter()
const store = useBathhouseStore()

const id = Number(route.params.id)
const bathhouse = ref<Bathhouse | null>(null)
const stalls = ref<Stall[]>([])
const loading = ref(true)

onMounted(async () => {
  // 优先复用 store 中已加载的楼数据（与列表联动），否则单独拉取
  const cached = store.bathhouses.find((b) => b.id === id)
  if (cached) {
    bathhouse.value = cached
    stalls.value = cached.stalls
  } else {
    bathhouse.value = await store.fetchBathhouse(id)
    stalls.value = bathhouse.value!.stalls
  }
  loading.value = false
  // 订阅后端实时状态推送（后端未启动时静默等待，不影响页面）
  useBathhouseChannel(stalls.value).start()
  // 拉取 24 小时基础用水曲线（本地模拟），供本楼大盘缩放使用
  getWaterUsage().then((w) => {
    baseWater.value = w.map((p) => p.usage)
  })
})

/* ===== 本楼数据大盘：用水高峰曲线 + 使用率/翻台率 ===== */
const baseWater = ref<number[]>([])

/** 本楼当前使用率：随实时推送动态变化（occupied 含 cleaning，与顶部统计一致） */
const usageRate = computed(() => {
  const total = stalls.value.length
  if (!total) return 0
  return Math.round((stat.value.occupied / total) * 100)
})

/** 翻台率：1 号楼（真实后端）按「使用率 × 基准 8 次/小时」估算，其余楼栋取模拟值 */
const turnover = computed(() =>
  bathhouse.value?.id === 1
    ? +((usageRate.value / 100) * 8).toFixed(1)
    : (bathhouse.value?.turnover ?? 0)
)

/**
 * 本楼用水曲线：把全校规模的基准曲线压缩到单栋量级。
 * factor = 楼隔间数相对 15 的规模 × 使用率加权 × 0.5 压缩，
 * 使 15 隔间浴室晚高峰约 200-300 人次，随实时使用率小幅变化。
 */
const scaledWater = computed(() => {
  const factor = (stalls.value.length / 15) * (0.35 + usageRate.value / 100) * 0.5
  return baseWater.value.map((u) => Math.max(5, Math.round(u * factor)))
})

/** 峰值摘要文案 */
const peakText = computed(() => {
  const arr = scaledWater.value
  if (!arr.length) return ''
  const idx = arr.reduce((mi, u, i) => (u > arr[mi] ? i : mi), 0)
  return `峰值 ${arr[idx]} 人次 · ${String(idx).padStart(2, '0')}:00`
})

/** 各分区使用率：该分区内「使用中」占比（随实时推送变化） */
const zoneRates = computed(() =>
  zones.value.map((z) => ({
    name: z.name,
    rate: z.stalls.length
      ? Math.round((countStalls(z.stalls).occupied / z.stalls.length) * 100)
      : 0,
  }))
)

/** 使用率颜色：<50 绿（从容）、<80 橙（偏忙）、否则红（饱和） */
function rateColor(v: number): string {
  if (v < 50) return 'var(--free)'
  if (v < 80) return 'var(--busy)'
  return 'var(--full)'
}

/** 三态统计（空闲/使用中/维修） */
const stat = computed(() => countStalls(stalls.value))

/** 按分区重新分组，保证预约后状态变化立即反映 */
const zones = computed(() => {
  const map = new Map<string, Stall[]>()
  for (const s of stalls.value) {
    const arr = map.get(s.zone) ?? []
    arr.push(s)
    map.set(s.zone, arr)
  }
  return [...map.entries()].map(([name, list]) => ({ name, stalls: list }))
})

/** 状态展示：返回文字与样式类（cleaning 一并视为使用中） */
function statusOf(s: Stall) {
  if (s.status === 'idle') return { l: '空闲', cls: 'st-idle' }
  if (s.status === 'fault') return { l: '维修', cls: 'st-fault' }
  return { l: '使用中', cls: 'st-busy' }
}

/** 点击隔间：仅「空闲」可预约 */
async function onStallTap(s: Stall) {
  if (s.status !== 'idle') {
    // 全满时引导去排队，而非简单提示不可选
    if (stat.value.idle === 0) {
      showToast('本楼已满，可排队等叫号')
    } else {
      showToast('该隔间不可选')
    }
    return
  }
  try {
    await showConfirmDialog({
      title: '确认预约',
      message: `确认预约 ${s.stallId} 号隔间吗？`,
      confirmButtonText: '确认预约',
      confirmButtonColor: 'var(--free)',
    })
  } catch {
    // 用户取消预约
    return
  }
  try {
    await store.reserveStallAt(id, s.id)
    // 同步本地隔间状态为占用（带上 uid 用于去重预约提示）
    const local = stalls.value.find((x) => x.id === s.id || x.stallId === s.stallId)
    if (local) local.status = 'occupied'
    showSuccessToast(`已预约 ${s.stallId}，请前往`)
    router.push('/queue')
  } catch {
    showToast('该隔间不可选')
  }
}

/* ===== 排队取号：全满时排队等叫号 ===== */
const queuing = ref(false)

/** 本楼是否已满（无空闲位） */
const isFull = computed(() => stat.value.idle === 0 && stat.value.occupied > 0)

/** 排队取号：进入队列，等有空位时自动分配 */
async function onQueue() {
  if (queuing.value) return
  queuing.value = true
  try {
    const t = await store.takeTicketAt(id)
    showSuccessToast(`取号成功 ${t.code}，到号将提醒`)
    router.push('/queue')
  } finally {
    queuing.value = false
  }
}

/* ===== 设备故障报修（自定义） ===== */
const repairShow = ref(false)

/** 报修提交：上报后端 + 联动本地隔间标记「维修」 */
async function onReported(stallId: number, type: FaultType, detail: string) {
  const s = stalls.value.find((x) => x.id === stallId)
  // 状态本地联动，保持网格实时反映
  if (s) s.status = 'fault'
  await store.submitFault(id, stallId, type, detail)
}
</script>

<template>
  <div class="page" v-if="bathhouse">
    <!-- 顶部导航：当前楼名 -->
    <van-nav-bar :title="bathhouse.name" left-arrow fixed placeholder @click-left="router.back()">
      <template #right>
        <span class="nav-repair" @click="repairShow = true">报修</span>
      </template>
    </van-nav-bar>

    <!-- 顶部小结：三态数量 -->
    <div class="stats">
      <div class="stat"><b class="c-free">{{ stat.idle }}</b><span>空闲</span></div>
      <div class="stat"><b class="c-busy">{{ stat.occupied }}</b><span>使用中</span></div>
      <div class="stat"><b class="c-full">{{ stat.fault }}</b><span>维修</span></div>
    </div>

    <!-- 排队取号：有空位→提示直接点选；全满→高亮排队按钮 -->
    <div class="queue-bar" :class="{ full: isFull }">
      <div class="queue-info">
        <span class="queue-title">{{ isFull ? '本楼已满，排队等叫号' : '当前有空位' }}</span>
        <span class="queue-desc">
          {{ isFull ? '排队人数 ' + (bathhouse.queueCount || 0) + '，到号将自动提醒' : '直接点击绿色「空闲」隔间即可预约' }}
        </span>
      </div>
      <button class="queue-btn" :disabled="queuing || !isFull" @click="onQueue">
        {{ queuing ? '排队中…' : '排队等叫号' }}
      </button>
    </div>

    <!-- 按分区分组展示隔间网格（文字状态标签） -->
    <div v-for="z in zones" :key="z.name" class="zone">
      <div class="zone-head">{{ z.name }}</div>
      <div class="grid">
        <div
          v-for="s in z.stalls"
          :key="s.id"
          class="cell"
          :class="statusOf(s).cls"
          @click="onStallTap(s)"
        >
          <span class="cell-no">{{ s.stallId }}</span>
          <span class="cell-status">{{ statusOf(s).l }}</span>
        </div>
      </div>
    </div>

    <div class="tip">点击「空闲」隔间即可预约，预约后该隔间归你使用</div>

    <!-- 本楼数据大盘：用水高峰曲线 + 使用率/翻台率（每栋楼都插入） -->
    <div class="dash">
      <div class="dash-head">本楼数据大盘</div>

      <div class="dash-card">
        <div class="dash-title">
          <span>每日用水高峰曲线</span>
          <span class="dash-badge">{{ peakText }}</span>
        </div>
        <WaterPeakChart :usage="scaledWater" :height="120" />
        <div class="dash-note">曲线振幅随本楼实时使用率变化（1 号楼为后端实时数据）</div>
      </div>

      <div class="dash-card">
        <div class="dash-title">
          <span>各分区使用率</span>
          <span class="dash-badge">本楼 {{ usageRate }}%</span>
        </div>
        <div v-for="z in zoneRates" :key="z.name" class="rate-row">
          <span class="rate-name">{{ z.name }}</span>
          <van-progress
            :percentage="z.rate"
            :show-pivot="false"
            :color="rateColor(z.rate)"
            :stroke-width="8"
            class="rate-bar"
          />
          <span class="rate-val" :style="{ color: rateColor(z.rate) }">{{ z.rate }}%</span>
        </div>
        <div class="rate-row">
          <span class="rate-name">本楼翻台率</span>
          <span class="turnover">{{ turnover }} 次/时</span>
        </div>
      </div>
    </div>

    <!-- 设备故障报修弹窗 -->
    <FaultReportPopup
      v-model:show="repairShow"
      :bath-name="bathhouse.name"
      :zones="bathhouse.zones"
      @reported="onReported"
    />
  </div>

  <div v-else class="loading">正在加载浴室状态…</div>
</template>

<style scoped>
.page {
  padding-bottom: 20px;
}
.live {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  font-size: 12px;
  color: var(--free);
  font-weight: 600;
}
.nav-repair {
  font-size: 13px;
  color: var(--free);
  border: 1px solid rgba(15, 220, 120, 0.5);
  padding: 2px 10px;
  border-radius: 999px;
  cursor: pointer;
}
.pulse {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--free);
  animation: pulse 1.5s ease-in-out infinite;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.3; }
}

/* 顶部三态小结 */
.stats {
  display: flex;
  gap: 10px;
  padding: 14px;
}
.stat {
  flex: 1;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 10px;
  text-align: center;
  padding: 10px 0;
}
.stat b {
  display: block;
  font-size: 20px;
  font-weight: 700;
}
.stat span {
  font-size: 11px;
  color: var(--text-sub);
}
.c-free { color: var(--free); }
.c-busy { color: var(--busy); }
.c-full { color: var(--full); }

/* 排队取号栏 */
.queue-bar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin: 0 14px 14px;
  padding: 10px 12px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 10px;
}
.queue-bar.full {
  border-color: rgba(255, 152, 0, 0.5);
  background: rgba(255, 152, 0, 0.08);
}
.queue-title {
  font-size: 14px;
  font-weight: 600;
}
.queue-bar.full .queue-title {
  color: var(--busy);
}
.queue-desc {
  display: block;
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 2px;
}
.queue-btn {
  flex-shrink: 0;
  border: none;
  background: var(--busy);
  color: #0b0f14;
  font-weight: 700;
  font-size: 13px;
  padding: 8px 14px;
  border-radius: 999px;
  cursor: pointer;
}
.queue-btn:disabled {
  opacity: 0.4;
  cursor: not-allowed;
}
.queue-bar:not(.full) .queue-btn {
  background: var(--line);
  color: var(--text-sub);
}

/* 隔间网格 */
.zone {
  margin: 0 14px 16px;
}
.zone-head {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 10px;
}
.grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
}

/* 隔间格子：文字状态 + 底色淡色区分 */
.cell {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 8px 6px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  cursor: pointer;
  background: var(--bg-card);
}
.cell-no {
  font-size: 15px;
  font-weight: 700;
  color: var(--text-main);
}
.cell-status {
  font-size: 12px;
  border-radius: 999px;
  padding: 1px 8px;
}
.st-idle {
  border-color: rgba(15, 220, 120, 0.4);
}
.st-idle .cell-status {
  color: var(--free);
  background: rgba(15, 220, 120, 0.12);
}
.st-busy {
  border-color: rgba(255, 152, 0, 0.4);
}
.st-busy .cell-status {
  color: var(--busy);
  background: rgba(255, 152, 0, 0.12);
}
.st-fault {
  border-color: rgba(232, 70, 58, 0.4);
}
.st-fault .cell-status {
  color: var(--full);
  background: rgba(232, 70, 58, 0.12);
}
.tip {
  margin: 0 14px;
  text-align: center;
  font-size: 12px;
  color: var(--text-sub);
}

/* 本楼数据大盘 */
.dash {
  margin: 16px 14px 20px;
}
.dash-head {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
}
.dash-card {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 12px;
  margin-bottom: 12px;
}
.dash-title {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 13px;
  font-weight: 600;
  margin-bottom: 8px;
}
.dash-badge {
  font-size: 11px;
  font-weight: 500;
  color: var(--text-sub);
  background: var(--bg-page);
  border: 1px solid var(--line);
  border-radius: 999px;
  padding: 2px 8px;
}
.dash-note {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 8px;
}
.rate-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
}
.rate-name {
  font-size: 12px;
  color: var(--text-sub);
  width: 48px;
  flex-shrink: 0;
}
.rate-bar {
  flex: 1;
}
.rate-val {
  font-size: 12px;
  font-weight: 700;
  width: 40px;
  text-align: right;
}
.turnover {
  font-size: 13px;
  font-weight: 700;
  color: var(--free);
}
.loading {
  text-align: center;
  color: var(--text-sub);
  padding: 80px 0;
}
</style>