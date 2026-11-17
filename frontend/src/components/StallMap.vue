<script setup lang="ts">
import type { Stall, StallStatus } from '@/types'

defineProps<{ stalls: Stall[] }>()

/** 状态 → 样式类名 */
const cls: Record<StallStatus, string> = {
  idle: 's-free',
  reserved: 's-used',
  occupied: 's-used',
  cleaning: 's-repair',
  fault: 's-off',
}

/** 状态中文名：reserved 表示已被预约但人未到，按界面三态统一显示「使用中」 */
const names: Record<StallStatus, string> = {
  idle: '空闲',
  reserved: '使用中',
  occupied: '使用中',
  cleaning: '保洁中',
  fault: '故障',
}
</script>

<template>
  <!-- 淋浴位平面拓扑图：每个隔间实时变色 -->
  <div class="map">
    <div
      v-for="s in stalls"
      :key="s.id"
      class="stall"
      :class="cls[s.status]"
      :title="`${s.no} 号 · ${names[s.status]}`"
    >
      <span class="no">{{ s.no }}</span>
    </div>
  </div>
</template>

<style scoped>
.map {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 10px;
}
.stall {
  aspect-ratio: 1;
  border-radius: 10px;
  display: flex;
  align-items: center;
  justify-content: center;
  transition: background 0.3s ease, box-shadow 0.3s ease, transform 0.15s ease;
  position: relative;
}
.no {
  font-size: 13px;
  font-weight: 600;
}
/* 空闲：绿色呼吸感，体现“实时监测中” */
.s-free {
  background: rgba(15, 220, 120, 0.18);
  border: 1px solid rgba(15, 220, 120, 0.55);
  color: #b9fbd8;
}
/* 使用中：橙色 */
.s-used {
  background: rgba(239, 170, 23, 0.16);
  border: 1px solid rgba(239, 170, 23, 0.6);
  color: #ffd591;
}
/* 满员/异常：红色 */
.s-full {
  background: rgba(232, 70, 58, 0.16);
  border: 1px solid rgba(232, 70, 58, 0.6);
  color: #ffb3aa;
}
/* 离线：灰色虚线 */
.s-off {
  background: rgba(58, 71, 84, 0.2);
  border: 1px dashed rgba(58, 71, 84, 0.8);
  color: var(--text-sub);
}
/* 维修中：紫色，带警示意义 */
.s-repair {
  background: rgba(167, 139, 250, 0.16);
  border: 1px solid rgba(167, 139, 250, 0.6);
  color: #d6cafd;
}
</style>
