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
