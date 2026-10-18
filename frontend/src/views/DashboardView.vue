<script setup lang="ts">
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { getWaterUsage } from '@/api'
import { useBathhouseStore } from '@/stores/bathhouse'
import WaterPeakChart from '@/components/WaterPeakChart.vue'
import type { Bathhouse, Bathzone, WaterPoint } from '@/types'

const router = useRouter()
const store = useBathhouseStore()

/**
 * 数据大盘看板（单楼模式）：
 * - 每日用水高峰曲线（本地模拟 24 小时数据，微信/小程序端无后端对应接口）
 * - 本浴室各分区使用率及翻台率（后端在线时为真实数据，离线时为模拟数据）
 */
const water = ref<WaterPoint[]>([])
const loading = ref(true)

/** 真实接入的楼栋 id（与 api/index.ts 的 BATHHOUSE_ID 保持一致） */
const REAL_ID = 1

onMounted(async () => {
  try {
    const w = await getWaterUsage()
    water.value = w
    if (!store.bathhouses.length) await store.fetchBathhouses()
  } finally {
    loading.value = false
  }
})

/* ================= 用水高峰曲线（复用 WaterPeakChart 组件） ================= */

/** 峰值摘要文案 */
const peakText = computed(() => {
  if (!water.value.length) return ''
  const idx = water.value.reduce((mi, p, i) => (p.usage > water.value[mi].usage ? i : mi), 0)
  const p = water.value[idx]
  return `峰值 ${p.usage} 人次 · ${String(p.hour).padStart(2, '0')}:00`
})

/** 当前浴室（单楼，可能为空） */
const current = computed<Bathhouse | null>(() => store.bathhouses[0] ?? null)

/* ================= 分区使用率及翻台率 ================= */

interface ZoneRow {
  name: string
  rate: number
  turnover: number
  real: boolean
}

/** 各分区使用率 = 该分区占用格数 / 总数；翻台率 = 使用率 × 基准 8 次/小时 */
const zoneRows = computed<ZoneRow[]>(() => {
  const b = current.value
  if (!b) return []
  return b.zones.map((z: Bathzone) => {
    const total = z.stalls.length
    const occupied = z.stalls.filter((s) => s.status === 'occupied' || s.status === 'cleaning').length
    const rate = total ? Math.round((occupied / total) * 100) : 0
    return {
      name: z.name,
      rate,
      turnover: +((rate / 100) * 8).toFixed(1),
      real: b.id === REAL_ID,
    }
  })
})

/** 使用率颜色：<50 绿（从容）、<80 橙（偏忙）、否则红（饱和） */
function rateColor(v: number): string {
  if (v < 50) return 'var(--free)'
  if (v < 80) return 'var(--busy)'
  return 'var(--full)'
}
</script>

<template>
  <div class="page">
    <!-- 顶栏 -->
    <div class="header">
      <van-icon name="arrow-left" size="20" @click="router.back()" />
      <span class="title">数据大盘看板</span>
      <span style="width: 20px" />
    </div>

    <div v-if="loading" class="loading">加载中…</div>

    <template v-else>
      <!-- 一、每日用水高峰曲线 -->
      <div class="card">
        <div class="card-title">
          <span>每日用水高峰曲线</span>
          <span class="badge">{{ peakText }}</span>
        </div>
        <WaterPeakChart :usage="water.map((p) => p.usage)" />
        <div class="legend">深夜低谷（23-5 点） · 晨间小峰（7-9 点） · 晚间主峰（18-22 点）</div>
      </div>

      <!-- 二、本浴室各分区使用率及翻台率 -->
      <div class="card">
        <div class="card-title">
          <span>各分区使用率及翻台率</span>
          <span class="badge">共 {{ zoneRows.length }} 个分区</span>
        </div>
        <div class="rows">
          <div v-for="z in zoneRows" :key="z.name" class="row">
            <div class="row-top">
              <span class="b-name">
                {{ current?.name }} · {{ z.name }}
                <i v-if="z.real" class="real">实时</i>
                <i v-else class="mock">模拟</i>
              </span>
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
        <div class="legend">后端在线时使用率为真实实时数据（仿真器驱动），后端离线时为本地模拟</div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: 16px 14px 40px;
}
.header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}
.title {
  font-size: 18px;
  font-weight: 800;
}
.card {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  margin-bottom: 14px;
}
.card-title {
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
.chart {
  width: 100%;
  height: auto;
  display: block;
}
.axis {
  font-size: 10px;
  fill: var(--text-sub);
}
.legend {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 8px;
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
.b-name {
  font-size: 13px;
  font-weight: 600;
}
.real {
  font-style: normal;
  font-size: 10px;
  color: var(--free);
  border: 1px solid var(--free);
  border-radius: 4px;
  padding: 0 4px;
  margin-left: 6px;
}
.mock {
  font-style: normal;
  font-size: 10px;
  color: var(--text-sub);
  border: 1px solid var(--line);
  border-radius: 4px;
  padding: 0 4px;
  margin-left: 6px;
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
.loading {
  text-align: center;
  color: var(--text-sub);
  padding: 60px 0;
}
</style>
