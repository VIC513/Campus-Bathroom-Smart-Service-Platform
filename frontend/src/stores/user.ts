import { ref, computed } from 'vue'
import { defineStore } from 'pinia'
import { setCurrentUserId } from '@/api'

/**
 * 用户登录/注册（前端本地实现，演示用）：
 * - 后端未提供认证接口（算法后端不可改动），账号数据存于 localStorage
 * - 密码不存明文：用简单字符串哈希后存储
 * - 登录/注册成功后把账号写入 api 层的 currentUserId，取号/报修以此作为 user_id
 */

export interface UserRecord {
  account: string
  nickname?: string
}

interface StoredUser extends UserRecord {
  /** 哈希后的密码（非明文） */
  pw: string
}

const USERS_KEY = 'bath_users'
const SESSION_KEY = 'bath_session'

/** 预置演示账号：学号 202400001，密码 123456 */
const DEMO_ACCOUNT = '202400001'

/** 简易哈希（演示用，避免 localStorage 里出现明文密码） */
function hashPwd(s: string): string {
  let h = 5381
  for (let i = 0; i < s.length; i++) h = ((h << 5) + h + s.charCodeAt(i)) | 0
  return `h${(h >>> 0).toString(36)}`
}

function loadUsers(): StoredUser[] {
  try {
    const list = JSON.parse(localStorage.getItem(USERS_KEY) || '[]') as StoredUser[]
    // 首次打开预置演示账号，无需先注册即可登录（答辩演示友好）
    if (!list.length) {
      const demo: StoredUser = { account: DEMO_ACCOUNT, pw: hashPwd('123456'), nickname: '同学' }
      saveUsers([demo])
      return [demo]
    }
    return list
  } catch {
    return []
  }
}

function saveUsers(users: StoredUser[]) {
  localStorage.setItem(USERS_KEY, JSON.stringify(users))
}

export const useUserStore = defineStore('user', () => {
  // 恢复已登录会话（刷新页面免登录）
  let restored: UserRecord | null = null
  try {
    restored = JSON.parse(localStorage.getItem(SESSION_KEY) || 'null') as UserRecord | null
  } catch {
    restored = null
  }
  const current = ref<UserRecord | null>(restored)

  const isLoggedIn = computed(() => !!current.value)

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

  /** 登录成功返回 null，失败返回错误文案 */
  function login(account: string, password: string): string | null {
    const u = loadUsers().find((x) => x.account === account)
    if (!u) return '账号未注册，请先注册'
    if (u.pw !== hashPwd(password)) return '密码错误'
    persist({ account: u.account, nickname: u.nickname })
    return null
  }

  /** 注册：账号已存 localStorage（密码哈希），注册成功后不自动登录，需回登录页手动登录 */
  function register(account: string, password: string, nickname = ''): string | null {
    if (!validAccount(account)) return '请输入 9 位学号或正确的 11 位手机号'
    if (password.length < 6) return '密码至少 6 位'
    const users = loadUsers()
    if (users.some((x) => x.account === account)) return '该账号已注册，请直接登录'
    const pw = hashPwd(password)
    users.push({ account, pw, nickname: nickname || undefined })
    saveUsers(users)
    return null
  }

  /** 退出登录 */
  function logout() {
    persist(null)
  }

  return { current, isLoggedIn, login, register, logout }
})
