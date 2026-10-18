<script setup lang="ts">
import { onMounted, onBeforeUnmount } from 'vue'
import { useRouter } from 'vue-router'
import { showConfirmDialog, showToast } from 'vant'
import { useBathhouseStore } from '@/stores/bathhouse'
import { countStalls } from '@/mock/data'
import type { Bathhouse, Stall } from '@/types'

const store = useBathhouseStore()
const router = useRouter()

// 顶部问候语：按当前小时选择不同措辞
const hour = new Date().getHours()
const greeting = hour < 6 ? '夜深了' : hour < 12 ? '早上好' : hour < 18 ? '下午好' : '晚上好'

/** 当前浴室（单楼模式：后端同学的单楼版服务，15 隔间 F01-F15） */
const current = (): Bathhouse | undefined => store.bathhouses[0]

/** 全满时是否可排队（无空闲隔间即全满） */
const isFull = () => (current() ? countStalls(current()!.stalls).idle === 0 : false)

/** 状态文字与颜色：空闲/使用中/维修 三态（后端 cleaning 归入使用中） */
function stateOf(s: Stall) {
  if (s.status === 'idle') return { l: '空闲', cls: 'st-idle' }
  if (s.status === 'fault') return { l: '维修', cls: 'st-fault' }
  return { l: '使用中', cls: 'st-busy' }
}

/** 点击空闲隔间 → 确认后预约（走后端 /queue，后端离线自动回退 mock） */
async function tapStall(s: Stall) {
  if (s.status !== 'idle') {
    showToast(stateOf(s).l + '中，不可预约')
    return
  }
  try {
    await showConfirmDialog({
      title: '预约选位',
      message: `确认预约 ${s.stallId} 吗？`,
      confirmButtonText: '确认',
      cancelButtonText: '再看看',
    })
  } catch {
    return // 用户取消
  }
  await store.reserveStallAt(current()!.id, s.stallId)
  showToast(`已分配 ${s.stallId}，请前往`)
}

/** 全满时排队等叫号（走后端 /queue，后端离线自动回退 mock） */
async function joinQueue() {
  const t = await store.takeTicketAt(current()!.id)
  showToast(`取号成功，前面还有 ${t.ahead} 人`)
  router.push('/queue')
}

/* ===== 持续仿真：模拟「线下有人进/出 + 设备故障」，数据一直自动变动 =====
 * 无需手动开关：页面加载即自动运行。
 * 后端在线时以 WebSocket 推送为准（本地改动会被覆盖），后端离线时由本仿真兜底，
 * 保证看板数据始终在流动。预约/取号/报修过的隔间有 5 秒保护期，仿真不会抢走。
 */
let simTimer: ReturnType<typeof setInterval> | null = null

/** 每 2.5s 对随机一个空闲/占用隔间触发一个随机事件 */
function simTick() {
  const b = current()
  if (!b) return
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
      <div class="sub">校园宿舍淋浴选位 · 单栋实时看板</div>
    </div>

    <!-- 加载态 -->
    <div v-if="!store.bathhouses.length" class="loading">正在加载浴室数据…</div>

    <template v-else>
      <!-- 单楼概览卡：三态统计 -->
      <div class="card" @click="router.push(`/bathhouse/${current()!.id}`)">
        <div class="left">
          <div class="name">{{ current()!.name }}</div>
          <div class="loc">{{ current()!.area }} · 共 {{ current()!.stalls.length }} 个隔间</div>
        </div>
        <div class="mid">
          <div class="s s-free"><b>{{ countStalls(current()!.stalls).idle }}</b><span>空闲</span></div>
          <div class="s s-busy"><b>{{ countStalls(current()!.stalls).occupied }}</b><span>使用中</span></div>
          <div class="s s-full"><b>{{ countStalls(current()!.stalls).fault }}</b><span>维修</span></div>
        </div>
        <div class="right">
          <van-icon name="arrow" color="var(--text-sub)" />
        </div>
      </div>

      <!-- 隔间网格：点空闲格直接预约 -->
      <div class="sec-title">
        <span>隔间选位</span>
        <span class="tip">点「空闲」直接预约</span>
      </div>
      <div class="grid">
        <div
          v-for="s in current()!.stalls"
          :key="s.stallId"
          class="stall"
          :class="stateOf(s).cls"
          @click="tapStall(s)"
        >
          <b>{{ s.stallId }}</b>
          <span>{{ stateOf(s).l }}</span>
        </div>
      </div>

      <!-- 全满时排队入口；有空位时不显示（直接点格子即可） -->
      <button v-if="isFull()" class="queue-btn" @click="joinQueue">
        <van-icon name="volume-o" />
        当前无空闲隔间，排队等叫号
      </button>
    </template>
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
.loading {
  text-align: center;
  color: var(--text-sub);
  padding: 80px 0;
}

/* 单楼概览卡 */
.card {
  display: flex;
  align-items: center;
  gap: 12px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 14px;
  cursor: pointer;
  margin-bottom: 18px;
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
}

/* 隔间网格 */
.sec-title {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
}
.tip {
  font-size: 12px;
  font-weight: 400;
  color: var(--text-sub);
}
.grid {
  display: grid;
  grid-template-columns: repeat(5, 1fr);
  gap: 10px;
}
.stall {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 10px 0;
  text-align: center;
  cursor: pointer;
  transition: transform 0.15s;
}
.stall:active {
  transform: scale(0.95);
}
.stall b {
  display: block;
  font-size: 14px;
  font-weight: 700;
  margin-bottom: 4px;
}
.stall span {
  font-size: 11px;
}
.st-idle { color: var(--free); border-color: rgba(15, 220, 120, 0.35); }
.st-busy { color: var(--busy); border-color: rgba(255, 152, 0, 0.35); }
.st-fault { color: var(--full); border-color: rgba(232, 70, 58, 0.35); }

/* 全满排队按钮 */
.queue-btn {
  width: 100%;
  margin-top: 18px;
  padding: 13px 0;
  border: none;
  border-radius: var(--radius);
  background: linear-gradient(135deg, #2f86ff, #1f66ff);
  color: #fff;
  font-size: 15px;
  font-weight: 600;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  cursor: pointer;
}
</style>
