import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { showToast } from 'vant'
import App from './App.vue'
import router from './router'
import { useUserStore } from './stores/user'
import './style.css'

// Vant 函数式组件样式（Toast/Dialog/Notify/Popup 等）需手动引入
import 'vant/es/toast/style'
import 'vant/es/dialog/style'
import 'vant/es/notify/style'
import 'vant/es/popup/style'

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount('#app')

/**
 * 会话过期统一处理：任意请求返回 401（JWT 过期 / 后端重置密钥）时，
 * 清除本地会话并跳回对应登录页，避免停留在无数据的页面继续操作。
 * - 管理员 → /admin/login；普通用户 → /login
 * - redirect 保留当前页面，重新登录后可回到原位置
 */
let handlingExpired = false
window.addEventListener('auth:expired', () => {
  if (handlingExpired) return
  const user = useUserStore()
  if (!user.isLoggedIn) return

  const wasAdmin = user.isAdmin
  const from = router.currentRoute.value.fullPath
  const target = wasAdmin ? '/admin/login' : '/login'
  user.logout()
  if (router.currentRoute.value.path === target) return

  handlingExpired = true
  showToast('登录已过期，请重新登录')
  router
    .replace({ path: target, query: { redirect: from, expired: '1' } })
    .finally(() => {
      handlingExpired = false
    })
})
