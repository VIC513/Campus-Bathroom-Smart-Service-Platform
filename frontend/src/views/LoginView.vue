<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showSuccessToast, showFailToast } from 'vant'
import { useUserStore } from '@/stores/user'

/**
 * 登录/注册页：
 * - 未登录时唯一入口（路由守卫强制跳转）
 * - 登录：学号或手机号 + 密码
 * - 第一次使用可切换到「注册」，注册成功自动登录
 */
const store = useUserStore()
const router = useRouter()
const route = useRoute()

// tab：login 登录 / register 注册
const mode = ref<'login' | 'register'>('login')

// 表单数据
const account = ref('')
const nickname = ref('')
const password = ref('')
const confirmPwd = ref('')

function switchMode(m: 'login' | 'register') {
  mode.value = m
  password.value = ''
  confirmPwd.value = ''
}

/** 校验登录/注册表单，返回错误文案（无错误返回 null） */
function validate(): string | null {
  if (!account.value.trim()) return '请输入学号或手机号'
  if (!password.value) return '请输入密码'
  if (mode.value === 'register') {
    if (confirmPwd.value !== password.value) return '两次输入的密码不一致'
  }
  return null
}

async function submit() {
  const err = validate()
  if (err) {
    showFailToast(err)
    return
  }
  const result = mode.value === 'login'
    ? store.login(account.value.trim(), password.value)
    : store.register(account.value.trim(), password.value, nickname.value.trim())
  if (result) {
    showFailToast(result)
    return
  }
  showSuccessToast(mode.value === 'login' ? '登录成功' : '注册成功，已自动登录')
  // 有回跳地址则回跳（如点详情页被拦截时），否则回首页
  const redirect = (route.query.redirect as string) || '/'
  router.replace(redirect)
}
</script>

<template>
  <div class="login-page">
    <!-- 品牌区 -->
    <div class="brand">
      <div class="logo">淋</div>
      <div class="brand-name">浴室智洗</div>
      <div class="brand-sub">校园浴室智能调度 · 扫码即用</div>
    </div>

    <!-- 登录/注册 Tab -->
    <div class="tabs">
      <div class="tab" :class="{ active: mode === 'login' }" @click="switchMode('login')">登录</div>
      <div class="tab" :class="{ active: mode === 'register' }" @click="switchMode('register')">注册</div>
    </div>

    <div class="form-card">
      <div class="field">
        <label>学号 / 手机号</label>
        <input
          v-model="account"
          type="text"
          inputmode="numeric"
          placeholder="9 位学号或 11 位手机号"
        />
      </div>
      <div class="field">
        <label>密码</label>
        <input v-model="password" type="password" placeholder="至少 6 位" />
      </div>
      <div v-if="mode === 'register'" class="field">
        <label>昵称</label>
        <input v-model="nickname" type="text" placeholder="给自己起个昵称（可选，默认「同学」）" />
      </div>
      <div v-if="mode === 'register'" class="field">
        <label>确认密码</label>
        <input v-model="confirmPwd" type="password" placeholder="再输入一次密码" />
      </div>

      <button class="submit-btn" @click="submit">
        {{ mode === 'login' ? '登 录' : '注 册' }}
      </button>

      <div class="tips">
        <span v-if="mode === 'login'">第一次使用？</span>
        <span v-else>已有账号？</span>
        <b @click="switchMode(mode === 'login' ? 'register' : 'login')">
          {{ mode === 'login' ? '立即注册' : '去登录' }}
        </b>
      </div>
    </div>

    <div class="foot">仅用于课程演示，账号数据保存在本机</div>
  </div>
</template>

<style scoped>
.login-page {
  min-height: 100vh;
  padding: 48px 28px 24px;
  display: flex;
  flex-direction: column;
  background: var(--bg-page);
}
.brand {
  text-align: center;
  margin-bottom: 36px;
}
.logo {
  width: 64px;
  height: 64px;
  margin: 0 auto 12px;
  border-radius: 20px;
  background: linear-gradient(135deg, var(--free), var(--primary));
  color: #fff;
  font-size: 32px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.35);
}
.brand-name {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 2px;
}
.brand-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-sub);
}
.tabs {
  display: flex;
  gap: 12px;
  margin-bottom: 14px;
}
.tab {
  flex: 1;
  text-align: center;
  padding: 10px 0;
  font-size: 15px;
  font-weight: 600;
  color: var(--text-sub);
  border: 1px solid var(--line);
  border-radius: 12px;
  transition: all 0.2s;
}
.tab.active {
  color: var(--primary);
  border-color: var(--primary);
  background: rgba(64, 158, 255, 0.1);
}
.form-card {
  background: var(--bg-card);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  padding: 20px 18px;
}
.field {
  margin-bottom: 16px;
}
.field label {
  display: block;
  font-size: 12px;
  color: var(--text-sub);
  margin-bottom: 6px;
}
.field input {
  width: 100%;
  box-sizing: border-box;
  padding: 11px 12px;
  font-size: 15px;
  color: var(--text-main);
  background: var(--bg-page);
  border: 1px solid var(--line);
  border-radius: 10px;
  outline: none;
}
.field input:focus {
  border-color: var(--primary);
}
.submit-btn {
  width: 100%;
  padding: 13px 0;
  margin-top: 6px;
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(90deg, var(--primary), #2f8feb);
  border: none;
  border-radius: 12px;
  cursor: pointer;
}
.submit-btn:active {
  opacity: 0.85;
}
.tips {
  margin-top: 18px;
  text-align: center;
  font-size: 13px;
  color: var(--text-sub);
}
.tips b {
  color: var(--primary);
  font-weight: 600;
}
.foot {
  margin-top: auto;
  text-align: center;
  font-size: 11px;
  color: var(--text-sub);
  opacity: 0.7;
}
</style>
