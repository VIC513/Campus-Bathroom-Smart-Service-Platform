<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { useBathhouseStore } from '@/stores/bathhouse'
import { countStalls } from '@/mock/data'
import type { Bathhouse, Stall } from '@/types'

const store = useBathhouseStore()
const router = useRouter()

// 顶部问候语：按当前小时选择不同措辞
const hour = new Date().getHours()
const greeting = hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

/** 拥挤度徽标：根据 crowd 值返回文字与样式类 */
function crowdOf(b: Bathhouse) {
  const c = b.crowd
  return c < 40
    ? { l: '空闲', cls: 'c-low' }
    : c < 70
      ? { l: '高峰', cls: 'c-mid' }
      : { l: '拥挤', cls: 'c-high' }
}

/** 点击卡片进入该楼浴室详情 */
function go(id: number) {
  router.push(`/bathhouse/${id}`)
}

/* ===== 持续仿真：模拟「线下有人进/出 + 设备故障」，数据一直自动变动 =====
 * 无需手动开关：页面加载即自动运行，覆盖全部 31 栋楼。
 * 1 号楼后端在线时以 WebSocket 推送为准（本地改动会被覆盖），后端离线时由本仿真兜底；
 * 其余 30 栋模拟楼栋完全由本仿真驱动，保证页面数据始终在流动。
 */
let simTimer: ReturnType<typeof setInterval> | null = null

/** 每 2.5s 对随机一栋楼触发一个随机事件：进/出/突发故障/维修完成 */
function simTick() {
  if (!store.bathhouses.length) return
  const b = store.bathhouses[Math.floor(Math.random() * store.bathhouses.length)]
  // 排除用户最近预约/取号/报修过的隔间（仿真保护期 5 秒，见 store.simProtect）
  const free = (ss: Stall[]) => ss.filter((s) => !store.isStallProtected(b.id, s.stallId))
  const idle = free(b.stalls.filter((s) => s.status === 'idle'))
  const busy = free(b.stalls.filter((s) => s.status === 'occupied' || s.status === 'cleaning'))
  const fault = free(b.stalls.filter((s) => s.status === 'fault'))
  const roll = Math.random()
  // 事件权重：有人进 45% / 有人出 40% / 突发故障 10% / 维修完成 5%
  if (roll < 0.45 && idle.length) {
    idle[Math.floor(Math.random() * idle.length)].status = 'occupied'
  } else if (roll < 0.85 && busy.length) {
    busy[Math.floor(Math.random() * busy.length)].status = 'idle'
  } else if (roll < 0.95 && idle.length) {
    idle[Math.floor(Math.random() * idle.length)].status = 'fault'
  } else if (fault.length) {
    fault[Math.floor(Math.random() * fault.length)].status = 'idle'
  }
}

onBeforeUnmount(() => {
  if (simTimer) clearInterval(simTimer)
})

onMounted(() => {
  store.fetchBathhouses()
  // 自动开启持续仿真，无需用户手动开关
  simTimer = setInterval(simTick, 2500)
})
</script>

<template>
  <div class="page">
    <!-- 顶部问候 + 副标题 -->
    <div class="topbar">
      <div class="hello">{{ greeting }}，同学</div>
      <div class="sub">校园宿舍淋浴选位 · 共 {{ store.bathhouses.length }} 栋</div>
    </div>

    <!-- 楼栋卡片列表（v-if 区分加载态） -->
    <div v-if="store.bathhouses.length" class="list">
      <div
        v-for="b in store.bathhouses"
        :key="b.id"
        class="card"
        @click="go(b.id)"
      >
        <div class="left">
          <div class="name">{{ b.name }}</div>
          <div class="loc">{{ b.area }}</div>
        </div>
        <div class="mid">
          <div class="s s-free"><b>{{ countStalls(b.stalls).idle }}</b><span>空闲</span></div>
          <div class="s s-busy"><b>{{ countStalls(b.stalls).occupied }}</b><span>使用中</span></div>
          <div class="s s-full"><b>{{ countStalls(b.stalls).fault }}</b><span>维修</span></div>
        </div>
        <div class="right">
          <span class="crowd" :class="crowdOf(b).cls">{{ crowdOf(b).l }}</span>
          <van-icon name="arrow" color="var(--text-sub)" />
        </div>
      </div>
    </div>

    <div v-else class="loading">正在加载浴室列表…</div>
  </div>
</template>

<style scoped>
.page {
  padding: 18px 16px 30px;
  width: 100%;
  margin: 0 auto;
}
.topbar {
  margin-bottom: 14px;
}
.hello {
  font-size: 28px;
  font-weight: 800;
  letter-spacing: 0.5px;
}
.sub {
  font-size: 14px;
  color: var(--text-sub);
  margin-top: 4px;
}

/* 卡片列表：参考微信小程序卡片样式 */
.list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  cursor: pointer;
}
.left {
  flex: 1 1 0;
  min-width: 0;
}
.name {
  font-size: 18px;
  font-weight: 700;
  color: var(--text-main);
}
.loc {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 3px;
}

/* 三态数量：文字 + 颜色区分 */
.mid {
  display: flex;
  gap: 12px;
}
.s {
  text-align: center;
}
.s b {
  display: block;
  font-size: 18px;
  font-weight: 700;
}
.s span {
  font-size: 11px;
  color: var(--text-sub);
}
.s-free b { color: var(--free); }
.s-busy b { color: var(--busy); }
.s-full b { color: var(--full); }

.right {
  display: flex;
  align-items: center;
  gap: 4px;
}
.crowd {
  font-size: 12px;
  padding: 2px 8px;
  border-radius: 999px;
}
.c-low {
  color: var(--free);
  background: rgba(15, 220, 120, 0.12);
}
.c-mid {
  color: var(--busy);
  background: rgba(255, 152, 0, 0.12);
}
.c-high {
  color: var(--full);
  background: rgba(232, 70, 58, 0.12);
}
.loading {
  text-align: center;
  color: var(--text-sub);
  padding: 80px 0;
}
</style>
