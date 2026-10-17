<script setup lang="ts">
import { computed, getCurrentInstance } from 'vue'

/**
 * 每日用水高峰曲线（手写 SVG 面积图，无第三方图表依赖）。
 * 输入按小时索引的用水量数组（长度 24，索引即小时 0-23），
 * 输出面积图折线 + 峰值红点标记 + 每 3 小时 X 轴刻度。
 * 首页大盘页与各楼详情页共用。
 */
const props = withDefaults(
  defineProps<{
    usage: number[] // 0-23 点每小时用水量
    height?: number // 图高（viewBox 坐标）
  }>(),
  { height: 170 }
)

// 每个实例使用独立渐变 id，避免同页多个图表 id 冲突（SVG url(#id) 全局解析）
const uid = getCurrentInstance()?.uid ?? 0
const gradId = `wpeak-${uid}`

const W = 340
const PAD = 26 // 左右留白

/** 由 24 个点生成折线/面积 path，并定位峰值索引 */
const chart = computed(() => {
  const pts = props.usage
  const max = Math.max(5, ...pts)
  const H = props.height
  const innerW = W - PAD * 2
  const innerH = H - PAD - 18 // 底部预留 X 轴刻度文字
  const px = (i: number) => PAD + (i / (pts.length - 1 || 1)) * innerW
  const py = (u: number) => PAD + (1 - u / max) * innerH
  // 折线 path（M 起点 + L 逐点连线）
  const line = pts
    .map((u, i) => `${i === 0 ? 'M' : 'L'}${px(i).toFixed(1)},${py(u).toFixed(1)}`)
    .join(' ')
  // 面积 path：折线闭合到 X 轴
  const area = `${line} L${px(pts.length - 1).toFixed(1)},${H - 10} L${PAD},${H - 10} Z`
  // 峰值：24 小时中用水量最高的一小时
  let peakIdx = 0
  pts.forEach((u, i) => {
    if (u > pts[peakIdx]) peakIdx = i
  })
  return { line, area, max, px, py, peakIdx, H }
})

/** X 轴刻度：每 3 小时标注一次 */
const xTicks = [0, 3, 6, 9, 12, 15, 18, 21, 23]
</script>

<template>
  <div>
    <svg :viewBox="`0 0 ${W} ${chart.H}`" class="chart">
      <defs>
        <linearGradient :id="gradId" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stop-color="var(--free)" stop-opacity="0.45" />
          <stop offset="100%" stop-color="var(--free)" stop-opacity="0.02" />
        </linearGradient>
      </defs>
      <!-- 面积 + 折线 -->
      <path :d="chart.area" :fill="`url(#${gradId})`" />
      <path
        :d="chart.line"
        fill="none"
        stroke="var(--free)"
        stroke-width="2"
        stroke-linejoin="round"
      />
      <!-- 峰值标记 -->
      <circle
        :cx="chart.px(chart.peakIdx)"
        :cy="chart.py(props.usage[chart.peakIdx] ?? 0)"
        r="4"
        fill="var(--full)"
      />
      <!-- X 轴刻度（每 3 小时） -->
      <text
        v-for="h in xTicks"
        :key="h"
        :x="chart.px(h)"
        :y="chart.H - 6"
        text-anchor="middle"
        class="axis"
      >
        {{ h }}:00
      </text>
    </svg>
  </div>
</template>

<style scoped>
.chart {
  width: 100%;
  height: auto;
  display: block;
}
.axis {
  font-size: 10px;
  fill: var(--text-sub);
}
</style>
