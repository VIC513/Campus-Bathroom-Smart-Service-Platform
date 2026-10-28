<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { showConfirmDialog, showSuccessToast, showToast } from 'vant'
import { getAdminOverview, adminFaultDone, adminToggleMaintenanceAll } from '@/api'
import type { AdminOverview } from '@/api'
import { useBathhouseStore } from '@/stores/bathhouse'

/**
 * 管理后台：维修工单处理 + 批量维修开关。
 * 真实接口优先（/admin/feedback、/admin/fault-done、/admin/maintenance-all），
 * 后端离线时由 api 层自动回退本地 mock，保证演示可用。
 */
const store = useBathhouseStore()

const overview = ref<AdminOverview | null>(null)
const loading = ref(true)
/** 操作锁：防止连点导致重复请求 */
const busy = ref(false)

/** 隔间状态统计（单楼 1 号楼） */
const idleCount = computed(() => overview.value?.stalls.filter((s) => s.status === 'idle').length ?? 0)
const occupiedCount = computed(
  () => overview.value?.stalls.filter((s) => s.status === 'occupied' || s.status === 'cleaning').length ?? 0
)
const faultStalls = computed(() => overview.value?.stalls.filter((s) => s.status === 'fault') ?? [])
/** 待处理工单（pending）与已处理工单 */
const pendingList = computed(() => overview.value?.feedbacks.filter((f) => f.status === 'pending') ?? [])
const doneCount = computed(() => overview.value?.feedbacks.filter((f) => f.status === 'done').length ?? 0)

/** 是否已全部处于维修状态（决定按钮文案） */
const allFault = computed(
  () => !!overview.value && overview.value.stalls.length > 0 && faultStalls.value.length === overview.value.stalls.length
)

/** 时间文案：MM-DD HH:mm */
function fmtTime(t: number): string {
  const d = new Date(t)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 拉取后台数据 */
async function reload() {
  try {
    overview.value = await getAdminOverview()
  } finally {
    loading.value = false
  }
}

/** 刷新后台 + 首页数据（维修完成后让首页隔间状态同步恢复） */
async function refreshAll() {
  await reload()
  await store.fetchBathhouses()
}

/** 完成单个隔间维修：二次确认后调用接口 */
async function onFaultDone(stallId: string) {
  try {
    await showConfirmDialog({
      title: '完成维修',
      message: `确认 ${stallId} 已维修完成？隔间将恢复为空闲状态。`,
      confirmButtonText: '确认完成',
      cancelButtonText: '取消',
    })
  } catch {
    return // 取消
  }
  if (busy.value) return
  busy.value = true
  try {
    await adminFaultDone(stallId)
    showSuccessToast(`${stallId} 已完成维修`)
    await refreshAll()
  } finally {
    busy.value = false
  }
}

/** 一键切换全部维修（进入维修 / 全部完成维修） */
async function onToggleAll() {
  const entering = !allFault.value
  try {
    await showConfirmDialog({
      title: entering ? '全部进入维修' : '全部完成维修',
      message: entering
        ? `将把全部 ${overview.value?.stalls.length ?? 0} 个隔间设为维修状态，期间不可使用。`
        : '将把全部隔间恢复为空闲，并关闭所有待处理工单。',
      confirmButtonText: '确认',
      cancelButtonText: '取消',
    })
  } catch {
    return
  }
  if (busy.value) return
  busy.value = true
  try {
    const action = await adminToggleMaintenanceAll()
    showSuccessToast(action === 'end' ? '已全部完成维修' : '已全部进入维修')
    await refreshAll()
  } finally {
    busy.value = false
  }
}

/** 手动刷新 */
async function onRefresh() {
  loading.value = true
  await refreshAll()
  showToast('已刷新')
}

onMounted(reload)
</script>

<template>
  <div class="page">
    <div class="hero">
      <div>
        <div class="hero-title">管理后台</div>
        <div class="hero-sub">1 号楼浴室 · 维修工单处理</div>
      </div>
      <button class="refresh-btn" :disabled="loading" @click="onRefresh">
        <van-icon name="replay" />
      </button>
    </div>

    <!-- 状态统计 -->
    <div class="stats">
      <div class="st">
        <b class="free">{{ idleCount }}</b>
        <span>空闲</span>
      </div>
      <div class="st">
        <b class="used">{{ occupiedCount }}</b>
        <span>使用中</span>
      </div>
      <div class="st">
        <b class="fault">{{ faultStalls.length }}</b>
        <span>维修中</span>
      </div>
    </div>

    <!-- 批量维修开关 -->
    <div class="card action">
      <div class="action-info">
        <div class="action-title">{{ allFault ? '当前全部隔间处于维修中' : '批量维修控制' }}</div>
        <div class="action-desc">
          {{ allFault ? '点击按钮将所有隔间恢复为空闲，并关闭全部工单' : '将全部隔间一次性设为维修状态，用于集中检修' }}
        </div>
      </div>
      <button class="toggle-btn" :class="{ end: allFault }" :disabled="busy || loading" @click="onToggleAll">
        {{ allFault ? '全部完成维修' : '全部进入维修' }}
      </button>
    </div>

    <!-- 待处理报修工单 -->
    <div class="section-head">
      <span>待处理报修</span>
      <span class="count">{{ pendingList.length }} 条</span>
    </div>
    <div v-if="loading" class="tip">加载中…</div>
    <div v-else-if="!pendingList.length" class="tip">暂无待处理工单，一切正常</div>
    <div v-else class="list">
      <div v-for="f in pendingList" :key="`${f.stallId}-${f.time}`" class="ticket">
        <div class="t-main">
          <div class="t-title">
            <b>{{ f.stallId }}</b>
            <span class="reason">{{ f.reason }}</span>
          </div>
          <div v-if="f.detail" class="t-detail">{{ f.detail }}</div>
          <div class="t-meta">{{ fmtTime(f.time) }} · 上报人 {{ f.userId }}</div>
        </div>
        <button class="done-btn" :disabled="busy" @click="onFaultDone(f.stallId)">完成维修</button>
      </div>
    </div>

    <!-- 维修中隔间（可直接点选完成维修） -->
    <div class="section-head">
      <span>维修中隔间</span>
      <span class="count">{{ faultStalls.length }} 个</span>
    </div>
    <div v-if="!faultStalls.length" class="tip">没有处于维修中的隔间</div>
    <div v-else class="stalls">
      <button
        v-for="s in faultStalls"
        :key="s.stallId"
        class="stall-chip"
        :disabled="busy"
        @click="onFaultDone(s.stallId)"
      >
        <span class="dot"></span>
        {{ s.stallId }}
      </button>
    </div>

    <!-- 已处理统计 -->
    <div v-if="doneCount" class="footnote">已处理工单 {{ doneCount }} 条</div>
  </div>
</template>

<style scoped>
.page {
  padding: 16px 14px 80px;
  max-width: 520px;
  margin: 0 auto;
}
.hero {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 14px;
}
.hero-title {
  font-size: 22px;
  font-weight: 800;
}
.hero-sub {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 3px;
}
.refresh-btn {
  width: 34px;
  height: 34px;
  border-radius: 50%;
  border: 1px solid var(--line);
  background: var(--bg-card);
  color: var(--text-sub);
  font-size: 16px;
  cursor: pointer;
}
/* 三格状态统计 */
.stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 8px;
  margin-bottom: 12px;
}
.st {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 12px;
  text-align: center;
  padding: 14px 4px;
}
.st b {
  display: block;
  font-size: 22px;
  font-weight: 800;
}
.st b.free {
  color: var(--free);
}
.st b.used {
  color: var(--busy);
}
.st b.fault {
  color: var(--full);
}
.st span {
  font-size: 11px;
  color: var(--text-sub);
}
/* 批量维修卡片 */
.card {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 12px;
  padding: 14px;
  margin-bottom: 16px;
}
.action-info {
  margin-bottom: 12px;
}
.action-title {
  font-size: 14px;
  font-weight: 700;
}
.action-desc {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 4px;
  line-height: 1.5;
}
.toggle-btn {
  width: 100%;
  border: none;
  border-radius: 10px;
  background: var(--full);
  color: #fff;
  font-size: 14px;
  font-weight: 700;
  padding: 11px 0;
  cursor: pointer;
}
.toggle-btn.end {
  background: var(--free);
  color: #0b0f14;
}
.toggle-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
/* 区块标题 */
.section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 15px;
  font-weight: 700;
  margin: 0 2px 8px;
}
.section-head .count {
  font-size: 11px;
  font-weight: 400;
  color: var(--text-sub);
}
.tip {
  font-size: 12px;
  color: var(--text-sub);
  background: var(--bg-card);
  border: 1px dashed var(--line);
  border-radius: 10px;
  padding: 14px;
  margin-bottom: 16px;
  text-align: center;
}
/* 工单卡片 */
.list {
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 16px;
}
.ticket {
  display: flex;
  align-items: center;
  gap: 10px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-left: 3px solid var(--full);
  border-radius: 10px;
  padding: 12px;
}
.t-main {
  flex: 1;
  min-width: 0;
}
.t-title {
  display: flex;
  align-items: center;
  gap: 8px;
}
.t-title b {
  font-size: 15px;
  font-weight: 800;
}
.reason {
  font-size: 12px;
  color: var(--full);
  background: rgba(235, 87, 87, 0.12);
  border-radius: 999px;
  padding: 2px 8px;
}
.t-detail {
  font-size: 12px;
  color: var(--text-main);
  margin-top: 5px;
  word-break: break-all;
}
.t-meta {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 5px;
}
.done-btn {
  flex-shrink: 0;
  border: 1px solid var(--brand);
  background: var(--brand-dim);
  color: var(--brand);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 700;
  padding: 7px 10px;
  cursor: pointer;
}
.done-btn:disabled {
  opacity: 0.5;
}
/* 维修中隔间 chips */
.stalls {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 16px;
}
.stall-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  border: 1px solid rgba(235, 87, 87, 0.4);
  background: rgba(235, 87, 87, 0.08);
  color: var(--full);
  border-radius: 999px;
  font-size: 13px;
  font-weight: 700;
  padding: 7px 13px;
  cursor: pointer;
}
.stall-chip .dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--full);
}
.stall-chip:disabled {
  opacity: 0.5;
}
.footnote {
  font-size: 11px;
  color: var(--text-sub);
  text-align: center;
}
</style>
