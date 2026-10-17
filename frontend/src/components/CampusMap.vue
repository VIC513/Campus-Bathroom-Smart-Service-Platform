<script setup lang="ts">
import type { Bathhouse } from '@/types'

defineProps<{ bathhouses: Bathhouse[] }>()

/** 状态色：体检点击定位到对应配色 */
const statusColor = (b: Bathhouse) => {
  if (b.crowd < 40) return 'var(--free)'
  if (b.crowd < 70) return 'var(--busy)'
  return 'var(--full)'
}

// 俯视图上各浴室的大致地理位置百分比坐标（按数量循环取）
const COORDS = [
  { top: '22%', left: '30%' }, // 西区
  { top: '50%', left: '50%' }, // 东区
  { top: '76%', left: '30%' }, // 南区
  { top: '40%', left: '70%' }, // 北区
  { top: '70%', left: '70%' }, // 中心
]
const posOf = (i: number) => COORDS[i % COORDS.length]
</script>

<template>
  <!-- 俯视图：按浴室大致地理位置排布（示意），点击定位详情 -->
  <div class="campus">
    <router-link
      v-for="(b, i) in bathhouses"
      :key="b.id"
      :to="`/bathhouse/${b.id}`"
      class="school"
      :style="{ top: posOf(i).top, left: posOf(i).left, borderColor: statusColor(b) }"
    >
      <span class="s-name">{{ b.name }}</span>
      <span class="s-meta">{{ b.waitMinutes }}min · {{ b.freeCount }} 空闲</span>
    </router-link>
  </div>
</template>

<style scoped>
.campus {
  position: relative;
  height: 300px;
  background:
    linear-gradient(var(--line) 1px, transparent 1px),
    linear-gradient(90deg, var(--line) 1px, transparent 1px);
  background-size: 100% 24px, 24px 100%;
  border: 1px solid var(--line);
  border-radius: var(--radius);
  overflow: hidden;
}
.school {
  position: absolute;
  transform: translate(-50%, -50%);
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 8px 10px;
  border-radius: 8px;
  background: var(--bg-card);
  border: 1px solid var(--brand);
  min-width: 96px;
}
.s-name {
  font-size: 12px;
  font-weight: 600;
}
.s-meta {
  font-size: 10px;
  color: var(--text-sub);
}
</style>