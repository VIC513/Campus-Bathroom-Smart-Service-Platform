<script setup lang="ts">
import { ref, computed, onMounted, watch, onBeforeUnmount } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { WaterPoint } from '@/types'

echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ data: WaterPoint[]; height?: number }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

const chartStyle = computed(() => ({ height: (props.height ?? 150) + 'px' }))

function cssVar(name: string, fallback: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

function render() {
  if (!chart || !props.data.length) return
  const main = cssVar('--text-main', '#eaf2fb')
  const sub = cssVar('--text-sub', '#8ba0b4')
  const brand = cssVar('--brand', '#0fdc78')
  const line = cssVar('--line', 'rgba(234,242,251,0.08)')
  const full = cssVar('--full', '#e8463a')

  // 找出主高峰时段，用于高亮
  const peakHour = props.data.reduce((m, p) => (p.usage > (m?.usage ?? -1) ? p : m), props.data[0])?.hour

  chart.setOption({
    animationDuration: 220,
    grid: { left: 6, right: 10, top: 18, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: cssVar('--bg-card', '#141b23'),
      borderColor: line,
      textStyle: { color: main, fontSize: 12 },
      formatter: (ps: any[]) => `${ps[0].axisValue}:00 时<br/><span style="font-weight:700">${ps[0].value}</span> 人次`,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: props.data.map((p) => p.hour),
      axisLine: { lineStyle: { color: line } },
      axisLabel: { color: sub, fontSize: 9, interval: (i: number) => i % 3 === 0 },
    },
    yAxis: {
      type: 'value',
      name: '人次',
      nameTextStyle: { color: sub, fontSize: 9, padding: [0, 0, 0, -2] },
      axisLabel: { color: sub, fontSize: 9, show: false },
      splitLine: { lineStyle: { color: line } },
    },
    series: [
      {
        type: 'line',
        data: props.data.map((p) => p.usage),
        smooth: true,
        symbol: 'none',
        lineStyle: { color: brand, width: 2.2 },
        // 高峰点加红色圆点标记
        markPoint: {
          symbol: 'circle',
          symbolSize: 7,
          itemStyle: { color: full },
          data: [{ coord: [peakHour, null] }],
        },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(15,220,120,0.28)' },
              { offset: 1, color: 'rgba(15,220,120,0.02)' },
            ],
          },
        },
      },
    ],
  })
}

function onResize() {
  chart?.resize()
}

onMounted(() => {
  chart = echarts.init(el.value!)
  render()
  window.addEventListener('resize', onResize)
})

watch(() => props.data, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="chart" :style="chartStyle" />
</template>

<style scoped>
.chart {
  width: 100%;
}
</style>