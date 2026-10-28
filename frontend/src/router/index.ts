import { createRouter, createWebHistory } from 'vue-router'
import { useUserStore } from '@/stores/user'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    {
      path: '/login',
      name: 'login',
      // 登录/注册：未登录时唯一可访问页面
      component: () => import('@/views/LoginView.vue'),
    },
    {
      path: '/',
      name: 'home',
      // 首页：浴室总览
      component: () => import('@/views/HomeView.vue'),
    },
    {
      path: '/bathhouse/:id',
      name: 'bathhouse',
      // 浴室详情：平面拓扑图 + 排队取号 + 分流推荐
      component: () => import('@/views/BathhouseView.vue'),
    },
    {
      path: '/queue',
      name: 'queue',
      // 我的排队：票根卡片 + 叫号状态
      component: () => import('@/views/QueueView.vue'),
    },
    {
      path: '/dashboard',
      name: 'dashboard',
      // 数据大盘：每日用水高峰曲线 + 各区域浴室使用率/翻台率
      component: () => import('@/views/DashboardView.vue'),
    },
    {
      path: '/scan',
      name: 'scan',
      // 扫码使用：扫隔间二维码 → 一键预约 / 快速报修
      component: () => import('@/views/ScanView.vue'),
    },
    {
      path: '/bath-reports',
      name: 'bath-reports',
      // 我的洗澡报告：扫码使用完成后的历史记录与习惯分析
      component: () => import('@/views/BathReportsView.vue'),
    },
    {
      path: '/admin',
      name: 'admin',
      // 管理后台：维修工单处理 + 批量维修开关
      component: () => import('@/views/AdminView.vue'),
    },
    {
      path: '/profile',
      name: 'profile',
      // 个人中心：排队历史
      component: () => import('@/views/ProfileView.vue'),
    },
  ],
})

// 全局登录守卫：除 /login 外，未登录一律跳转登录页（带回跳地址）
router.beforeEach((to) => {
  const user = useUserStore()
  if (to.path !== '/login' && !user.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  // 已登录访问登录页 → 直接回首页
  if (to.path === '/login' && user.isLoggedIn) return '/'
})

export default router
