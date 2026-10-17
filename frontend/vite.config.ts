import { fileURLToPath, URL } from 'node:url'
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { VantResolver } from '@vant/auto-import-resolver'

// https://vite.dev/config/
export default defineConfig({
  plugins: [
    vue(),
    // Vant 组件按需自动导入，无需手动 import 注册
    Components({ resolvers: [VantResolver()] }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    host: true, // 允许局域网访问，方便手机真机调试
    port: 5173,
    proxy: {
      // 后端 FastAPI 地址：队友启动后自动接管；未启动时前端走 Mock 数据
      // /api/stalls → 剥离前缀转发为后端 /stalls（后端为根路由，无 /api 前缀）
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
        rewrite: (p) => p.replace(/^\/api/, ''),
      },
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
})
