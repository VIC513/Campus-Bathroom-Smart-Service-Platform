<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showSuccessToast, showFailToast, showToast } from 'vant'
import { useUserStore } from '@/stores/user'

/**
 * 登录/注册页：
 * - 未登录时唯一入口（路由守卫强制跳转）
 * - 登录：学号或手机号 + 密码（账号已注册的会提示直接登录）
 * - 注册：学号或正确手机号 + 密码 + 图形验证码；注册成功**不自动登录**，
 *   回到登录 tab 预填账号，由用户手动登录（演示「账号已记住」）
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
/** 提交失败的内联错误提示（比 toast 更醒目持久） */
const formError = ref('')

/** 切换登录/注册 tab：清空另一界面残留内容。keepAccount=true 时保留账号（注册成功回登录预填用） */
function switchMode(m: 'login' | 'register', keepAccount = false) {
  mode.value = m
  if (!keepAccount) account.value = ''
  nickname.value = ''
  password.value = ''
  confirmPwd.value = ''
  inputCode.value = ''
  formError.value = ''
}

/* ================= 图形验证码（注册时校验） ================= */

const inputCode = ref('')
/** 当前验证码内容（刷新时重新生成） */
const codeText = ref('')
/** 验证码画布元素（模板 ref 绑定） */
const canvasEl = ref<HTMLCanvasElement | null>(null)

/** 生成 4 位随机验证码（排除易混淆的 0/O/1/I）并绘制到画布 */
function genCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789'
  let s = ''
  for (let i = 0; i < 4; i++) s += chars[Math.floor(Math.random() * chars.length)]
  codeText.value = s
  drawCode()
}

/** 在 canvas 上绘制带干扰线/噪点的验证码 */
function drawCode() {
  const cv = canvasEl.value
  if (!cv) return
  const ctx = cv.getContext('2d')
  if (!ctx) return
  const W = cv.width
  const H = cv.height
  ctx.clearRect(0, 0, W, H)
  // 浅色背景
  ctx.fillStyle = 'rgba(64, 158, 255, 0.08)'
  ctx.fillRect(0, 0, W, H)
  // 逐字符绘制：随机颜色 + 随机上下偏移
  for (let i = 0; i < codeText.value.length; i++) {
    ctx.font = `${16 + Math.random() * 4}px "Arial Black", sans-serif`
    ctx.fillStyle = `hsl(${Math.floor(Math.random() * 360)}, 60%, 45%)`
    ctx.save()
    ctx.translate(10 + i * 17, 21 + (Math.random() * 6 - 3))
    ctx.rotate((Math.random() * 0.5 - 0.25) * Math.PI / 3)
    ctx.fillText(codeText.value[i], 0, 0)
    ctx.restore()
  }
  // 干扰线 + 噪点
  for (let i = 0; i < 3; i++) {
    ctx.strokeStyle = `rgba(64, 158, 255, ${0.2 + Math.random() * 0.3})`
    ctx.beginPath()
    ctx.moveTo(Math.random() * W, Math.random() * H)
    ctx.lineTo(Math.random() * W, Math.random() * H)
    ctx.stroke()
  }
  for (let i = 0; i < 20; i++) {
    ctx.fillStyle = 'rgba(64, 158, 255, 0.4)'
    ctx.fillRect(Math.random() * W, Math.random() * H, 1.5, 1.5)
  }
}

/** 校验表单：返回错误文案（无错误返回 null） */
function validate(): string | null {
  if (!account.value.trim()) return '请输入学号或手机号'
  if (!password.value) return '请输入密码'
  if (mode.value === 'register') {
    if (confirmPwd.value !== password.value) return '两次输入的密码不一致'
    if (!inputCode.value.trim()) return '请输入验证码'
    if (inputCode.value.trim().toUpperCase() !== codeText.value.toUpperCase()) return '验证码错误，请重新输入'
  }
  return null
}

async function submit() {
  const err = validate()
  if (err) {
    formError.value = err
    if (err.includes('验证码')) genCode() // 验证码错误时自动刷新一张
    return
  }
  formError.value = ''
  const accountVal = account.value.trim()
  if (mode.value === 'login') {
    const result = store.login(accountVal, password.value)
    if (result) {
      formError.value = result
      // 账号未注册：自动切到注册 tab 预填账号密码，补个验证码即可继续
      if (result.includes('未注册')) {
        showToast('该账号尚未注册，已帮你切到注册，补完信息即可')
        switchMode('register', true)
        inputCode.value = ''
      }
      return
    }
    showSuccessToast('登录成功')
    const redirect = (route.query.redirect as string) || '/'
    router.replace(redirect)
  } else {
    // 注册：成功不自动登录，回登录 tab 预填账号
    const result = store.register(accountVal, password.value, nickname.value.trim())
    if (result) {
      formError.value = result
      if (result.includes('验证码')) genCode()
      return
    }
    showSuccessToast('注册成功，请登录')
    // 回登录 tab（账号预留在输入框，密码已清空，验证码由 watch 刷新）
    switchMode('login', true)
  }
}

onMounted(genCode)

// canvas 元素挂载后可绘制验证码；切到注册 tab 时刷新验证码（防复用旧码）
watch(canvasEl, (cv) => {
  if (cv && codeText.value) drawCode()
})
watch(mode, (m) => {
  if (m === 'register') genCode()
})
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
        <input
          v-model="password"
          type="password"
          :placeholder="mode === 'login' ? '请输入密码' : '至少 6 位'"
        />
      </div>
      <div v-if="mode === 'register'" class="field">
        <label>昵称</label>
        <input v-model="nickname" type="text" placeholder="给自己起个昵称（可选，默认「同学」）" />
      </div>
      <div v-if="mode === 'register'" class="field">
        <label>确认密码</label>
        <input v-model="confirmPwd" type="password" placeholder="再输入一次密码" />
      </div>
      <div v-if="mode === 'register'" class="field">
        <label>图形验证码</label>
        <div class="code-row">
          <input
            v-model="inputCode"
            type="text"
            maxlength="4"
            placeholder="输入右侧验证码"
            class="code-input"
          />
          <canvas
            ref="canvasEl"
            class="code-canvas"
            width="88"
            height="34"
            title="看不清？点击刷新"
            @click="genCode"
          />
        </div>
      </div>

      <div v-if="formError" class="form-error">{{ formError }}</div>

      <button class="submit-btn" @click="submit">
        {{ mode === 'login' ? '登 录' : '注 册' }}
      </button>

      <div class="tips">
        <span v-if="mode === 'login'">第一次使用？</span>
        <span v-else>已有账号？</span>
        <span class="switch" @click="switchMode(mode === 'login' ? 'register' : 'login')">
          {{ mode === 'login' ? '去注册' : '去登录' }}
        </span>
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
/* 验证码行：输入框 + 画布 */
.code-row {
  display: flex;
  gap: 10px;
  align-items: stretch;
}
.code-row .code-input {
  flex: 1;
}
.code-canvas {
  width: 88px;
  height: 34px;
  border: 1px solid var(--line);
  border-radius: 10px;
  cursor: pointer;
  background: var(--bg-page);
}
/* 提交失败的内联错误提示 */
.form-error {
  margin-top: 14px;
  padding: 9px 12px;
  font-size: 13px;
  line-height: 1.5;
  color: var(--full);
  background: rgba(227, 62, 46, 0.08);
  border: 1px solid rgba(227, 62, 46, 0.25);
  border-radius: 10px;
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
