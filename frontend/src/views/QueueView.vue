<script setup lang="ts">
import { computed } from 'vue'
import { showConfirmDialog, showNotify } from 'vant'
import { useBathhouseStore } from '@/stores/bathhouse'

const store = useBathhouseStore()
// Pinia setup store 解构时 computed 会被自动解包，这里用 computed 包回 ref 以在脚本中访问 .value
const active = computed(() => store.activeTicket)

/** 模拟“叫号推送”演示：更新状态 + 震动提醒 */
function onSimCall() {
  const t = active.value
  if (!t) return
  t.status = 'calling'
  navigator.vibrate?.([200, 100, 200])
  showNotify({ type: 'success', message: `📢 ${t.bathhouseName} 叫到 ${t.code} 号了！` })
}

/** 取消排队 */
async function onCancel(ticketId: string) {
  await showConfirmDialog({ title: '取消排队', message: '确定要取消当前排队吗？' })
  await store.cancelMyTicket(ticketId)
}
</script>

<template>
  <div class="page">
    <div class="hero">我的排队</div>

    <!-- 当前进行中的排队：票根卡片 -->
    <div v-if="active" class="ticket">
      <div class="t-head">
        <span class="t-bath">{{ active.bathhouseName }}</span>
        <van-tag round :color="active.status === 'calling' ? 'var(--busy)' : 'var(--free)'" text-color="#0b0f14">
          {{ active.status === 'calling' ? '到号啦' : '等待中' }}
        </van-tag>
      </div>
      <div class="t-code">{{ active.code }}</div>
      <div class="t-meta">
        <div class="t-m">
          <b>{{ active.ahead }}</b>
          <span>前面还有(人)</span>
        </div>
        <div class="t-m">
          <b>{{ active.waitMinutes }}</b>
          <span>预计等待(分)</span>
        </div>
        <div class="t-m">
          <b>实时</b>
          <span>叫号推送</span>
        </div>
      </div>
      <div class="t-actions">
        <button class="btn-ghost" @click="onCancel(active.id)">取消排队</button>
        <button class="btn-primary" @click="onSimCall">模拟叫号推送</button>
      </div>
      <div class="t-note">到号将通过 WebSocket 实时提醒，无需守在浴室门口</div>
    </div>

    <!-- 空态 -->
    <div v-else class="empty">
      <div class="empty-icon">🎫</div>
      <div>当前没有进行中的排队</div>
      <div class="empty-sub">去浴室页面取号，就能在这里看到实时进度</div>
    </div>

    <!-- 历史记录 -->
    <template v-if="store.myTickets.length">
      <div class="sec-title">最近记录</div>
      <div class="history">
        <div v-for="t in store.myTickets" :key="t.id" class="h-row">
          <div>
            <div class="h-name">{{ t.bathhouseName }}</div>
            <div class="h-sub">{{ t.code }} · {{ new Date(t.createdAt).toLocaleTimeString('zh-CN') }}</div>
          </div>
          <span class="h-status" :class="t.status">{{ t.status }}</span>
        </div>
      </div>
    </template>
  </div>
</template>

<style scoped>
.page {
  padding: 16px 14px 80px;
}
.hero {
  font-size: 24px;
  font-weight: 700;
  padding: 10px 4px 16px;
}
.ticket {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 18px 16px;
  position: relative;
  overflow: hidden;
}
.ticket::before {
  content: "";
  position: absolute;
  left: 0;
  top: 0;
  bottom: 0;
  width: 3px;
  background: var(--free);
}
.t-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.t-bath {
  font-size: 14px;
  color: var(--text-sub);
}
.t-code {
  font-size: 46px;
  font-weight: 800;
  font-variant-numeric: tabular-nums;
  margin: 12px 0;
  background: linear-gradient(90deg, #5cf5a8, #0fdc78);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}
.t-meta {
  display: flex;
  margin: 6px 0 14px;
}
.t-m {
  flex: 1;
  text-align: center;
}
.t-m b {
  display: block;
  font-size: 18px;
}
.t-m span {
  font-size: 11px;
  color: var(--text-sub);
}
.t-actions {
  display: flex;
  gap: 10px;
}
.btn-ghost {
  flex: 1;
  border: 1px solid var(--line);
  background: transparent;
  color: var(--text-sub);
  padding: 10px 0;
  border-radius: 999px;
  font-size: 14px;
  cursor: pointer;
}
.btn-primary {
  flex: 1.4;
  border: none;
  background: var(--free);
  color: #0b0f14;
  font-weight: 700;
  padding: 10px 0;
  border-radius: 999px;
  font-size: 14px;
  cursor: pointer;
}
.t-note {
  margin-top: 12px;
  font-size: 11px;
  color: var(--text-sub);
  text-align: center;
}
.empty {
  text-align: center;
  color: var(--text-sub);
  padding: 60px 0;
  font-size: 14px;
}
.empty-icon {
  font-size: 44px;
  margin-bottom: 12px;
}
.empty-sub {
  font-size: 12px;
  margin-top: 6px;
}
.sec-title {
  font-size: 15px;
  font-weight: 700;
  margin: 24px 0 10px;
}
.history {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 4px 14px;
}
.h-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.h-row:last-child {
  border-bottom: none;
}
.h-name {
  font-size: 14px;
}
.h-sub {
  font-size: 11px;
  color: var(--text-sub);
  margin-top: 2px;
}
.h-status {
  font-size: 12px;
  color: var(--text-sub);
}
.h-status.done { color: var(--free); }
.h-status.canceled { color: var(--full); }
</style>
