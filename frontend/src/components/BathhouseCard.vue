<script setup lang="ts">
import type { Bathhouse } from '@/types'

defineProps<{ bathhouse: Bathhouse }>()

/** 拥挤度对应文案与颜色 */
function crowdLevel(crowd: number) {
  if (crowd < 40) return { text: '空闲', color: 'var(--free)' }
  if (crowd < 70) return { text: '高峰', color: 'var(--busy)' }
  return { text: '拥挤', color: 'var(--full)' }
}
</script>

<template>
  <router-link :to="`/bathhouse/${bathhouse.id}`" class="card">
    <!-- 状态灯 + 名称 -->
    <div class="top">
      <span class="dot" :style="{ background: crowdLevel(bathhouse.crowd).color }" />
      <div class="names">
        <div class="name">{{ bathhouse.name }}</div>
        <div class="area">{{ bathhouse.area }}</div>
      </div>
      <van-tag round :color="crowdLevel(bathhouse.crowd).color" text-color="#0b0f14">
        {{ crowdLevel(bathhouse.crowd).text }}
      </van-tag>
    </div>

    <!-- 核心指标 -->
    <div class="metrics">
      <div class="metric">
        <div class="num">{{ bathhouse.waitMinutes }}</div>
        <div class="label">预计等待(分)</div>
      </div>
      <div class="divider" />
      <div class="metric">
        <div class="num">{{ bathhouse.queueCount }}</div>
        <div class="label">排队人数</div>
      </div>
      <div class="divider" />
      <div class="metric">
        <div class="num">{{ bathhouse.freeCount }}/{{ bathhouse.totalCount }}</div>
        <div class="label">空闲淋浴位</div>
      </div>
      <div class="divider" />
      <div class="metric">
        <div class="num">{{ bathhouse.waterTemp }}°</div>
        <div class="label">水温</div>
      </div>
    </div>

    <!-- 拥挤度条 -->
    <div class="crowd-track">
      <div
        class="crowd-fill"
        :style="{ width: bathhouse.crowd + '%', background: crowdLevel(bathhouse.crowd).color }"
      />
    </div>
  </router-link>
</template>

<style scoped>
.card {
  display: block;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px 16px;
  margin-bottom: 12px;
}
.top {
  display: flex;
  align-items: center;
  gap: 8px;
}
.dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  box-shadow: 0 0 8px currentColor;
}
.names {
  flex: 1;
}
.name {
  font-size: 16px;
  font-weight: 600;
}
.area {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 2px;
}
.metrics {
  display: flex;
  align-items: center;
  margin: 12px 0 10px;
}
.metric {
  flex: 1;
  text-align: center;
}
.num {
  font-size: 18px;
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}
.label {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 2px;
}
.divider {
  width: 1px;
  height: 24px;
  background: var(--line);
}
.crowd-track {
  height: 4px;
  border-radius: 4px;
  background: var(--bg-card-2);
  overflow: hidden;
}
.crowd-fill {
  height: 100%;
  border-radius: 4px;
  transition: width 0.4s ease;
}
</style>
