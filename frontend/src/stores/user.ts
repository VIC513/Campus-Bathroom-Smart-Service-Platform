import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { setCurrentUserId } from '@/api'
import http from '@/api/http'

/**
 * 账号体系：C 端账号已迁到后端（FastAPI /auth/register、/auth/login）。
 * - 登录成功后把后端 JWT 写入会话，http 拦截器统一附加 Authorization: Bearer <token>
 * - 普通用户与管理员共用后端账号表，用 role 区分；管理员账号由后端启动时预置
 * - 昵称后端不存储，按账号缓存在 localStorage 中（仅用于展示）
 */

export type UserRole = 'user' | 'admin'

export interface UserRecord {
  account: string
  nickname?: string
  role: UserRole
  /** 后端签发的 JWT，请求鉴权用 */
  token: string
}

const SESSION_KEY = 'bath_session'
const NICKNAME_KEY = 'bath_nicknames'

/** 从后端错误响应中提取可读文案 */
function apiError(e: unknown): string {
  const err = e as {
    response?: { data?: { detail?: string | Array<unknown> } }
    code?: string
  }
  const detail = err.response?.data?.detail
  if (typeof detail === 'string') return detail
  // FastAPI 422 参数校验失败时 detail 是数组
  if (Array.isArray(detail)) return '账号或密码格式不正确'
  if (!err.response) return '无法连接服务器，请确认后端已启动'
  return '请求失败，请稍后重试'
}

/** 读取昵称缓存（账号 → 昵称） */
function loadNicknames(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(NICKNAME_KEY) || '{}') as Record<string, string>
  } catch {
    return {}
  }
}

function saveNickname(account: string, nickname: string) {
  if (!nickname) return
  const map = loadNicknames()
  map[account] = nickname
  localStorage.setItem(NICKNAME_KEY, JSON.stringify(map))
}

/** 读取会话；没有后端 token 的旧本地会话视为失效，需重新登录 */
function loadSession(): UserRecord | null {
  try {
    const raw = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') as Partial<UserRecord> | null
    if (!raw || typeof raw.account !== 'string' || !raw.token) return null
    return {
      account: raw.account,
      nickname: raw.nickname || loadNicknames()[raw.account],
      role: raw.role === 'admin' ? 'admin' : 'user',
      token: raw.token,
    }
  } catch {
    return null
  }
}

interface LoginResponse {
  access_token: string
  role: UserRole
  username: string
}

export const useUserStore = defineStore('user', () => {
  const current = ref<UserRecord | null>(loadSession())

  const isLoggedIn = computed(() => !!current.value)
  /** 是否管理员会话（管理后台路由守卫以此为准） */
  const isAdmin = computed(() => current.value?.role === 'admin')

  /** 持久化会话 + 同步 api 层 user_id */
  function persist(u: UserRecord | null) {
    current.value = u
    if (u) localStorage.setItem(SESSION_KEY, JSON.stringify(u))
    else localStorage.removeItem(SESSION_KEY)
    setCurrentUserId(u?.account ?? 'student001')
  }

  /** 账号格式校验：9 位学号，或「1 开头、第二位 3-9、共 11 位」的正确手机号 */
  function validAccount(account: string): boolean {
    return /^\d{9}$/.test(account) || /^1[3-9]\d{9}$/.test(account)
  }

  /** 普通用户登录：走后端 /auth/login，拒绝管理员账号走学生端入口 */
  async function login(account: string, password: string): Promise<string | null> {
    try {
      const res = (await http.post('/auth/login', { username: account, password })) as LoginResponse
      if (res.role === 'admin') return '该账号为管理员专用，请从管理员入口登录'
      persist({
        account: res.username,
        nickname: loadNicknames()[res.username],
        role: 'user',
        token: res.access_token,
      })
      return null
    } catch (e) {
      return apiError(e)
    }
  }

  /** 管理员登录：校验后端账号 role=admin，不区分账号/密码错误以免暴露信息 */
  async function loginAdmin(account: string, password: string): Promise<string | null> {
    try {
      const res = (await http.post('/auth/login', { username: account, password })) as LoginResponse
      if (res.role !== 'admin') return '管理员账号或密码错误'
      persist({
        account: res.username,
        nickname: '系统管理员',
        role: 'admin',
        token: res.access_token,
      })
      return null
    } catch (e) {
      const msg = apiError(e)
      return msg.includes('无法连接') ? msg : '管理员账号或密码错误'
    }
  }

  /** 注册：走后端 /auth/register，成功后不自动登录，需回登录页手动登录 */
  async function register(account: string, password: string, nickname = ''): Promise<string | null> {
    if (account === 'admin') return '该账号为系统保留，请更换'
    if (!validAccount(account)) return '请输入 9 位学号或正确的 11 位手机号'
    if (password.length < 6) return '密码至少 6 位'
    try {
      await http.post('/auth/register', { username: account, password })
    } catch (e) {
      const msg = apiError(e)
      return msg.includes('已存在') ? '该账号已注册，请直接登录' : msg
    }
    saveNickname(account, nickname.trim())
    return null
  }

  /** 退出登录：清除会话（昵称缓存保留） */
  function logout() {
    persist(null)
  }

  return { current, isLoggedIn, isAdmin, login, loginAdmin, register, logout }
})
