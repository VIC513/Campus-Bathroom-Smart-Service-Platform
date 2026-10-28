<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { useUserStore } from '@/stores/user'
import { loadReports } from '@/utils/bathHistory'

const userStore = useUserStore()
const router = useRouter()

/** 我的洗澡报告列表：从本地持久化读取（扫码使用完成后自动写入），新记录在前 */
const reports = computed(() => loadReports(userStore.current?.account ?? ''))

/** 报告时长文案：x 分 y 秒 */
function reportTime(minutes: number): string {
  const totalSec = Math.round(minutes * 60)
  const m = Math.floor(totalSec / 60)
  const s = totalSec % 60
  return m > 0 ? `${m} 分 ${s} 秒` : `${s} 秒`
}

/** 报告时间文案：MM-DD HH:mm */
function reportAt(at: number): string {
  const d = new Date(at)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** ============ 统计摘要（填满页面空白） ============ */

/** 洗澡次数 */
const totalCount = computed(() => reports.value.length)
/** 累计时长（分钟，向下取整） */
const totalMinutes = computed(() => Math.floor(reports.value.reduce((s, r) => s + r.minutes, 0)))
/** 平均水温 */
const avgWater = computed(() =>
  reports.value.length ? reports.value.reduce((s, r) => s + r.water, 0) / reports.value.length : 0,
)
/** 最常去隔间（取出现次数最多的隔间号） */
const topStall = computed(() => {
  const map = new Map<string, number>()
  reports.value.forEach((r) => map.set(r.stallId, (map.get(r.stallId) ?? 0) + 1))
  let best = ''
  let max = 0
  map.forEach((c, k) => {
    if (c > max) {
      max = c
      best = k
    }
  })
  return best || '—'
})

/** 洗澡人格：按水温与时长给每次记录归类，聚合出使用最多的前 2 种人格标签 */
const habitTags = computed(() => {
  const counts: Record<string, number> = {}
  reports.value.forEach((r) => {
    const tag = r.water > 42 ? '热水控' : r.water < 39 ? '凉爽派' : r.minutes < 10 ? '清爽派' : '从容派'
    counts[tag] = (counts[tag] ?? 0) + 1
  })
  return Object.entries(counts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 2)
    .map(([t]) => t)
})

/** ============ 近 7 次洗澡时长趋势（纯 CSS 柱状图，填满剩余空白） ============ */

/** 日期短格式 MM-DD */
function fmtDay(at: number): string {
  const d = new Date(at)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())}`
}

/** 趋势数据：最近 7 次记录的时长（分钟），并归一化为柱高百分比 */
const trend = computed(() => {
  const items = reports.value.slice(0, 7).map((r) => ({
    label: fmtDay(r.at),
    minutes: Math.round(r.minutes),
  }))
  const max = Math.max(1, ...items.map((x) => x.minutes))
  return items.map((x) => ({ ...x, pct: Math.max(10, Math.round((x.minutes / max) * 100)) }))
})
</script>

<template>
  <div class="page">
    <div class="hero">我的洗澡报告</div>

    <!-- 无记录空态：引导去扫码 -->
    <div v-if="!reports.length" class="empty">
      <van-icon name="scan" />
      <span>还没有洗澡记录，去扫码体验一下吧</span>
      <button class="go-scan" @click="router.push('/scan')">去扫码</button>
    </div>

    <!-- 统计摘要：次数 / 累计时长 / 平均水温 / 常去隔间（填满页面空白） -->
    <div v-if="reports.length" class="summary">
      <div class="s-item">
        <b>{{ totalCount }}</b>
        <span>洗澡次数</span>
      </div>
      <div class="s-item">
        <b>{{ totalMinutes }}分</b>
        <span>累计时长</span>
      </div>
      <div class="s-item">
        <b>{{ avgWater.toFixed(1) }}℃</b>
        <span>平均水温</span>
      </div>
      <div class="s-item">
        <b>{{ topStall }}</b>
        <span>常去隔间</span>
      </div>
    </div>

    <!-- 洗澡人格：聚合历史记录的习惯倾向标签 -->
    <div v-if="habitTags.length" class="persona">
      <span class="p-label">我的洗澡人格</span>
      <span v-for="t in habitTags" :key="t" class="p-tag">{{ t }}</span>
    </div>

    <!-- 近 7 次洗澡时长趋势：纯 CSS 柱状图 -->
    <div v-if="trend.length" class="trend">
      <div class="t-head">
        <van-icon name="bar-chart-o" />
        <span>近 7 次洗澡时长（分钟）</span>
      </div>
      <div class="t-bars">
        <div v-for="(t, i) in trend" :key="i" class="t-col">
          <span class="t-val">{{ t.minutes }}</span>
          <div class="t-bar-wrap">
            <div class="t-bar" :style="{ height: t.pct + '%' }"></div>
          </div>
          <span class="t-date">{{ t.label }}</span>
        </div>
      </div>
    </div>

    <!-- 报告列表：每张卡片展示一次洗浴记录 -->
    <div v-if="reports.length" class="list">
      <div v-for="(r, i) in reports" :key="i" class="item">
        <div class="i-left">
          <div class="i-title">{{ r.stallId }} <span class="i-bath">{{ r.bathName }}</span></div>
          <div class="i-meta">{{ reportAt(r.at) }} · {{ reportTime(r.minutes) }}</div>
          <div class="i-tip">{{ r.tip }}</div>
        </div>
        <div class="i-right">
          <b>{{ r.water.toFixed(1) }}℃</b>
          <span>平均水温</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.page {
  padding: 16px 14px 80px;
  max-width: 520px;
  margin: 0 auto;
}
.hero {
  font-size: 24px;
  font-weight: 800;
  margin-bottom: 4px;
}
/* 空态引导 */
.empty {
  margin-top: 60px;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 10px;
  color: var(--text-sub);
  font-size: 13px;
}
.empty .van-icon {
  font-size: 42px;
  color: var(--line);
}
.go-scan {
  border: none;
  border-radius: 999px;
  background: var(--free);
  color: #0b0f14;
  font-size: 13px;
  font-weight: 600;
  padding: 7px 18px;
  cursor: pointer;
}
/* 统计摘要：四格指标卡，填满中下部空白 */
.summary {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin-top: 14px;
}
.s-item {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 12px;
  text-align: center;
  padding: 16px 4px;
}
.s-item b {
  display: block;
  font-size: 20px;
  font-weight: 800;
  color: var(--free);
}
.s-item span {
  font-size: 11px;
  color: var(--text-sub);
}
/* 洗澡人格：聚合习惯倾向标签 */
.persona {
  display: flex;
  align-items: center;
  gap: 8px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px;
  margin-top: 10px;
}
.p-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
  margin-right: 2px;
}
.p-tag {
  background: var(--brand-dim);
  border: 1px solid rgba(7, 163, 90, 0.3);
  color: var(--free);
  font-size: 12px;
  font-weight: 600;
  border-radius: 999px;
  padding: 4px 12px;
}
/* 近 7 次洗澡时长趋势：柱状图卡片 */
.trend {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 12px 14px 10px;
  margin-top: 10px;
}
.t-head {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-main);
  margin-bottom: 10px;
}
.t-head .van-icon {
  color: var(--free);
}
.t-bars {
  display: flex;
  align-items: flex-end;
  gap: 8px;
}
.t-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  min-width: 0;
}
.t-val {
  font-size: 11px;
  font-weight: 700;
  color: var(--free);
}
.t-bar-wrap {
  width: 100%;
  height: 72px;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}
.t-bar {
  width: 70%;
  min-width: 8px;
  border-radius: 6px 6px 2px 2px;
  background: linear-gradient(180deg, var(--free), rgba(7, 163, 90, 0.55));
}
.t-date {
  font-size: 10px;
  color: var(--text-sub);
}
/* 报告列表 */
.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-top: 14px;
}
.item {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 14px;
  padding: 18px 18px;
}
.i-left {
  flex: 1;
  min-width: 0;
}
.i-title {
  font-size: 18px;
  font-weight: 700;
}
.i-bath {
  font-size: 13px;
  font-weight: 400;
  color: var(--text-sub);
  margin-left: 6px;
}
.i-meta {
  font-size: 13px;
  color: var(--text-sub);
  margin-top: 4px;
}
.i-tip {
  font-size: 13px;
  color: var(--text-main);
  background: var(--brand-dim);
  border: 1px solid rgba(7, 163, 90, 0.25);
  border-radius: 9px;
  padding: 8px 10px;
  margin-top: 10px;
}
.i-right {
  text-align: right;
  flex-shrink: 0;
}
.i-right b {
  display: block;
  font-size: 22px;
  font-weight: 700;
  color: var(--free);
}
.i-right span {
  font-size: 12px;
  color: var(--text-sub);
}
</style>
