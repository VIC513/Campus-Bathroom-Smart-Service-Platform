import { createApp } from 'vue'
import { createPinia } from 'pinia'
import App from './App.vue'
import router from './router'
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
