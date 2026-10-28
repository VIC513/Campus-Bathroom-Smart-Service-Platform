<script setup lang="ts">
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showDialog, showConfirmDialog } from 'vant'
import { useBathhouseStore } from '@/stores/bathhouse'
import { useUserStore } from '@/stores/user'
import { loadFrequent } from '@/utils/bathHistory'

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

/** 常去浴室：取号预约 + 扫码使用 合并统计（同一隔间多次累计），显示隔间号如「F12（N 次）」 */
const frequentList = computed(() => {
  const map = new Map<string, { no: string; bathName: string; count: number; last: number }>()
  // 1) 取号预约记录
  for (const t of store.myTickets) {
    if (t.status === 'canceled') continue // 已取消的号不计入常去
    const no = t.assignedStall || t.code || `#${t.bathhouseId}`
    const cur = map.get(no)
    if (cur) {
      cur.count++
      cur.last = Math.max(cur.last, t.createdAt)
    } else {
      map.set(no, { no, bathName: '', count: 1, last: t.createdAt })
    }
  }
  // 2) 扫码使用记录（扫码入场即累计）
  for (const f of loadFrequent(account.value)) {
    const cur = map.get(f.no)
    if (cur) {
      cur.count += f.count
      cur.last = Math.max(cur.last, f.last)
      if (!cur.bathName && f.bathName) cur.bathName = f.bathName
    } else {
      map.set(f.no, { no: f.no, bathName: f.bathName, count: f.count, last: f.last })
    }
  }
  return [...map.values()].sort((a, b) => b.last - a.last)
})

/** 常去浴室数字：取号预约 + 扫码使用 总次数 */
const frequentCount = computed(
  () =>
    store.myTickets.filter((t) => t.status !== 'canceled').length +
    loadFrequent(account.value).reduce((sum, f) => sum + f.count, 0),
)

/** 点击「常去浴室」：无记录时提示，有记录时弹出常去浴室列表（含浴室名与累计次数） */
async function onFrequentTap() {
  if (!frequentList.value.length) {
    showToast('还没有取号/扫码使用记录，去体验一下吧')
    return
  }
  await showDialog({
    title: '常去浴室号',
    message: frequentList.value.map((n) => `· ${n.bathName}${n.bathName ? ' ' : ''}${n.no}（${n.count} 次）`).join('\n'),
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
      <van-cell title="扫码使用" label="扫隔间二维码 · 一键预约 / 快速报修" icon="scan" is-link to="/scan" />
      <van-cell title="我的洗澡报告" label="查看每次洗浴记录与习惯分析" icon="notes-o" is-link to="/bath-reports" />
      <van-cell title="数据大盘看板" icon="bar-chart-o" is-link to="/dashboard" />
      <van-cell title="管理后台" label="维修工单处理与批量维修开关" icon="setting-o" is-link to="/admin" />
      <van-cell title="我的排队记录" icon="orders-o" is-link to="/queue" />
      <van-cell title="智能分流推荐" label="回首页查看实时等位建议与预测曲线" icon="guide-o" is-link to="/" />
      <van-cell title="使用帮助" icon="question-o" is-link />
      <van-cell title="关于本项目" icon="info-o" is-link />
      <!-- 退出登录：样式与其他功能入口保持一致，仅图标不同（power-o 在该字体子集缺失，用 user-o） -->
      <van-cell title="退出登录" icon="user-o" is-link @click="onLogout" />
    </van-cell-group>
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
</style>
