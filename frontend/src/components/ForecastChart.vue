<script setup lang="ts">
import { ref, onMounted, onBeforeUnmount, watch } from 'vue'
import * as echarts from 'echarts/core'
import { LineChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import type { WaitForecast } from '@/types'

// 按需注册：折线图 + 网格 + 提示框 + Canvas 渲染
echarts.use([LineChart, GridComponent, TooltipComponent, CanvasRenderer])

const props = defineProps<{ forecast: WaitForecast }>()
const el = ref<HTMLDivElement>()
let chart: echarts.ECharts | null = null

/** 读取全局样式中的主题色，保证与深色科技风一致 */
function cssVar(name: string, fallback: string) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback
}

function render() {
  if (!chart) return
  const main = cssVar('--text-main', '#eaf2fb')
  const sub = cssVar('--text-sub', '#8ba0b4')
  const brand = cssVar('--brand', '#0fdc78')
  const line = cssVar('--line', 'rgba(234,242,251,0.08)')
  chart.setOption({
    animationDuration: 220,
    grid: { left: 6, right: 10, top: 20, bottom: 4, containLabel: true },
    tooltip: {
      trigger: 'axis',
      backgroundColor: cssVar('--bg-card', '#141b23'),
      borderColor: line,
      textStyle: { color: main, fontSize: 12 },
      valueFormatter: (v: number) => `${v} 分钟`,
    },
    xAxis: {
      type: 'category',
      boundaryGap: false,
      data: props.forecast.times,
      axisLine: { lineStyle: { color: line } },
      axisLabel: { color: sub, fontSize: 10, interval: 1 },
    },
    yAxis: {
      type: 'value',
      name: '分钟',
      nameTextStyle: { color: sub, fontSize: 10, padding: [0, 0, 0, -4] },
      axisLabel: { color: sub, fontSize: 10 },
      splitLine: { lineStyle: { color: line } },
    },
    series: [
      {
        type: 'line',
        data: props.forecast.waitMinutes,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: brand, width: 2.5 },
        areaStyle: {
          color: {
            type: 'linear',
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: 'rgba(15,220,120,0.25)' },
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

// 预测数据更新时重绘
watch(() => props.forecast, render, { deep: true })

onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  chart?.dispose()
  chart = null
})
</script>

<template>
  <div ref="el" class="chart" />
</template>

<style scoped>
.chart {
  width: 100%;
  height: 180px;
}
</style>
