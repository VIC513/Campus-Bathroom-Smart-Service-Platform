import axios from 'axios'
import { showToast } from 'vant'

/**
 * Axios 实例：统一 baseURL 与错误提示。
 * 后端 FastAPI 就绪后，代理已配置（见 vite.config.ts），无需改这里。
 */
const http = axios.create({
  baseURL: '/api',
  timeout: 8000,
})

// 响应拦截：统一报错提示，方便联调
http.interceptors.response.use(
  (res) => res.data,
  (err) => {
    showToast(err.response?.data?.detail || '网络开小差了，请稍后再试')
    return Promise.reject(err)
  }
)

export default http
