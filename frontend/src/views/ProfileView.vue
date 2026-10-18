<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showDialog, showConfirmDialog } from 'vant'
import { useBathhouseStore } from '@/stores/bathhouse'
import { useUserStore } from '@/stores/user'

const store = useBathhouseStore()
const userStore = useUserStore()
const router = useRouter()

// 当前登录用户展示信息（昵称默认「同学」）
const account = computed(() => userStore.current?.account ?? '')
const nickname = computed(() => userStore.current?.nickname || '同学')
const avatarChar = computed(() => nickname.value.charAt(0))

// 演示数据：累计取号次数与节省等待
const todayCount = computed(() => store.myTickets.length)
const savedMinutes = computed(() => store.myTickets.reduce((sum, t) => sum + t.waitMinutes, 0))

/** 常去浴室：按取号/预约记录去重统计（同一楼栋多次取号只计一次） */
const frequentList = computed(() => {
  const names = new Map<number, string>()
  for (const t of store.myTickets) {
    if (t.status === 'canceled') continue // 已取消的号不计入常去
    if (!names.has(t.bathhouseId)) names.set(t.bathhouseId, t.bathhouseName || `#${t.bathhouseId}`)
  }
  return [...names.values()]
})
const frequentCount = computed(() => frequentList.value.length)

/** 点击「常去浴室」：无记录时提示，有记录时弹出常去楼列表 */
async function onFrequentTap() {
  if (!frequentList.value.length) {
    showToast('还没有取号记录，取号后这里自动统计')
    return
  }
  await showDialog({
    title: '常去浴室',
    message: frequentList.value.map((n) => `· ${n}`).join('\n'),
    confirmButtonText: '知道了',
  })
}

/** 退出登录：确认后清除会话并跳转登录页（取消则不退出） */
async function onLogout() {
  try {
    await showConfirmDialog({
      title: '退出登录',
      message: '确定要退出当前账号吗？',
      confirmButtonText: '退出',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户点了「取消」或关闭弹窗 → 不退出
  }
  userStore.logout()
  router.replace('/login')
}
</script>

<template>
  <div class="page">
    <!-- 用户卡片 -->
    <div class="user">
      <div class="avatar">{{ avatarChar }}</div>
      <div class="info">
        <div class="name">{{ nickname }}</div>
        <div class="sid">{{ account }} · 校园浴室智洗</div>
      </div>
    </div>

    <!-- 今日统计 -->
    <div class="stats">
      <div class="stat">
        <b>{{ todayCount }}</b>
        <span>今日取号(次)</span>
      </div>
      <div class="stat">
        <b>{{ savedMinutes }}</b>
        <span>累计节省(分)</span>
      </div>
      <div class="stat" @click="onFrequentTap">
        <b>{{ frequentCount }}</b>
        <span>常去浴室</span>
      </div>
    </div>

    <!-- 功能入口 -->
    <van-cell-group inset :style="{ marginTop: '12px', background: 'transparent' }">
      <van-cell title="数据大盘看板" icon="bar-chart-o" is-link to="/dashboard" />
      <van-cell title="我的排队记录" icon="orders-o" is-link to="/queue" />
      <van-cell title="智能分流推荐" icon="guide-o" is-link to="/" />
      <van-cell title="使用帮助" icon="question-o" is-link />
      <van-cell title="关于本项目" icon="info-o" is-link />
      <!-- 退出登录：样式与其他功能入口保持一致，仅图标不同（power-o 在该字体子集缺失，用 user-o） -->
      <van-cell title="退出登录" icon="user-o" is-link @click="onLogout" />
    </van-cell-group>

    <div class="footer">浴室智洗 BathSight · 无侵入物联网 · 数字孪生式状态感知</div>
  </div>
</template>

<style scoped>
.page {
  padding: 16px 14px 80px;
}
.user {
  display: flex;
  align-items: center;
  gap: 14px;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 18px 16px;
}
.avatar {
  width: 52px;
  height: 52px;
  border-radius: 50%;
  background: linear-gradient(135deg, #0fdc78, #05994f);
  color: #0b0f14;
  font-size: 22px;
  font-weight: 800;
  display: flex;
  align-items: center;
  justify-content: center;
}
.name {
  font-size: 18px;
  font-weight: 700;
}
.sid {
  font-size: 12px;
  color: var(--text-sub);
  margin-top: 4px;
}
.stats {
  display: flex;
  gap: 10px;
  margin-top: 12px;
}
.stat {
  flex: 1;
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: 10px;
  text-align: center;
  padding: 14px 0;
}
/* 常去浴室可点击：hover/激活反馈 */
.stat:active {
  opacity: 0.7;
}
.stat b {
  display: block;
  font-size: 20px;
  font-weight: 700;
  color: var(--free);
}
.stat span {
  font-size: 11px;
  color: var(--text-sub);
}
.footer {
  text-align: center;
  color: var(--text-sub);
  font-size: 11px;
  margin-top: 40px;
}
</style>
