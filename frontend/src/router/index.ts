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
      path: '/admin/login',
      name: 'admin-login',
      // 管理员登录：独立入口，仅校验预置管理员账号（admin/admin123）
      component: () => import('@/views/AdminLoginView.vue'),
    },
    {
      path: '/admin',
      name: 'admin',
      // 管理后台：维修工单处理 + 批量维修开关（仅管理员可进）
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

/**
 * 全局路由守卫：
 * 1. /admin/login（管理员登录页）：仅管理员会话会自动跳回后台，其他人可访问
 * 2. /admin*（管理后台）：没有管理员身份一律跳管理员登录页（普通用户账号无效）
 * 3. C 端页面：未登录跳 /login；管理员会话不允许进入 C 端，统一回后台
 */
router.beforeEach((to) => {
  const user = useUserStore()

  // 登录页必须最先放行：未登录时若也在这里跳 /login，会形成
  // 「/login → /login?redirect=/login → …」的无限重定向，被 vue-router 中止后页面白屏
  if (to.path === '/login') {
    if (!user.isLoggedIn) return true
    return user.isAdmin ? '/admin' : '/'
  }
  if (to.path === '/admin/login') {
    return user.isAdmin ? '/admin' : true
  }
  if (to.path.startsWith('/admin')) {
    if (!user.isAdmin) {
      return { path: '/admin/login', query: { redirect: to.fullPath } }
    }
    return true
  }
  // C 端页面：未登录跳登录页；管理员会话统一回后台
  if (!user.isLoggedIn) {
    return { path: '/login', query: { redirect: to.fullPath } }
  }
  if (user.isAdmin) return '/admin'
  return true
})

export default router
