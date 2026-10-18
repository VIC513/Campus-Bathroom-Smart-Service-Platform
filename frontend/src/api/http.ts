import axios from 'axios'

/**
 * Axios 实例：统一 baseURL 与错误提示。
 * 后端 FastAPI 就绪后，代理已配置（见 vite.config.ts），无需改这里。
 */
const http = axios.create({
  baseURL: '/api',
  timeout: 8000,
})

// 响应拦截：仅透传错误，不再弹统一 toast。
// 所有业务请求都走 safeFetch（真实优先、失败回退 mock），
// 后端未启动时由 safeFetch 静默回退，避免每次请求都弹「网络开小差了」。
http.interceptors.response.use(
  (res) => res.data,
  (err) => Promise.reject(err)
)

export default http
