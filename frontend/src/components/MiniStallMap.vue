<script setup lang="ts">
import { computed } from 'vue'
import type { Stall, StallStatus } from '@/types'
import { countStalls } from '@/mock/data'

const props = defineProps<{ stalls: Stall[] }>()

/** 按分区聚合：每个分区独立成组，互不混排 */
const zones = computed(() => {
  const map = new Map<string, Stall[]>()
  for (const s of props.stalls) {
    const arr = map.get(s.zone) ?? []
    arr.push(s)
    map.set(s.zone, arr)
  }
  return [...map.entries()].map(([name, stalls]) => ({ name, stalls }))
})

const dotCls: Record<StallStatus, string> = {
  idle: 'd-free',
  reserved: 'd-used',
  occupied: 'd-used',
  cleaning: 'd-repair',
  fault: 'd-off',
}

// 聚合统计给父组件复用
defineExpose({ countStalls })
</script>

<template>
  <!-- 迷你看板拓扑：按分区划分，每个分区一排小色块 -->
  <div class="zones">
    <div v-for="z in zones" :key="z.name" class="zone">
      <span class="zone-name">{{ z.name }}</span>
      <div class="zone-dots">
        <div
          v-for="s in z.stalls"
          :key="s.id"
          class="dot"
          :class="dotCls[s.status]"
          :title="`${z.name} · ${s.no} 号 · ${s.status}`"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.zones {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.zone {
  display: flex;
  align-items: center;
  gap: 10px;
}
.zone-name {
  flex: 0 0 36px;
  font-size: 13px;
  color: var(--text-sub);
}
.zone-dots {
  display: grid;
  grid-auto-flow: column;
  grid-auto-columns: 1fr;
  gap: 5px;
  flex: 1;
}
.dot {
  aspect-ratio: 1;
  border-radius: 3px;
  transition: background 0.3s ease;
}
.d-free { background: var(--free); }
.d-used { background: var(--busy); }
.d-full { background: var(--full); }
.d-repair { background: var(--repair); }
.d-off { background: var(--offline); }
</style>