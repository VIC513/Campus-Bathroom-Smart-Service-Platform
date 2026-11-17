<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showSuccessToast } from 'vant'
import { useUserStore } from '@/stores/user'

/**
 * 管理员登录页（独立入口 /admin/login）：
 * - 与 C 端登录页完全隔离：没有注册 tab，仅校验预置管理员账号
 * - 登录成功后进入 /admin 管理后台；普通用户账号无法通过此页登录
 */
const store = useUserStore()
const router = useRouter()
const route = useRoute()

const account = ref('')
const password = ref('')
const formError = ref('')
const submitting = ref(false)

/** 提交登录：校验通过后跳转后台（支持 redirect 回跳） */
async function submit() {
  formError.value = ''
  if (!account.value.trim()) {
    formError.value = '请输入管理员账号'
    return
  }
  if (!password.value) {
    formError.value = '请输入密码'
    return
  }
  submitting.value = true
  const result = await store.loginAdmin(account.value.trim(), password.value)
  submitting.value = false
  if (result) {
    formError.value = result
    return
  }
  showSuccessToast('管理员登录成功')
  // redirect 仅接受站内绝对路径，避免数组/外部地址造成跳转异常或二次循环
  const rawRedirect = route.query.redirect
  const redirect =
    typeof rawRedirect === 'string' && rawRedirect.startsWith('/') && !rawRedirect.startsWith('//')
      ? rawRedirect
      : '/admin'
  router.replace(redirect)
}

/** 返回 C 端登录页 */
function backToStudent() {
  router.replace('/login')
}
</script>

<template>
  <div class="admin-login-page">
    <!-- 管理端标识 -->
    <div class="brand">
      <div class="logo">管</div>
      <div class="brand-name">浴室智洗 · 管理端</div>
      <div class="brand-sub">仅限管理员登录 · 维修工单与调度控制</div>
    </div>

    <div class="form-card">
      <div class="field">
        <label>管理员账号</label>
        <input v-model="account" type="text" placeholder="请输入管理员账号" @keyup.enter="submit" />
      </div>
      <div class="field">
        <label>密码</label>
        <input
          v-model="password"
          type="password"
          placeholder="请输入管理员密码"
          @keyup.enter="submit"
        />
      </div>

      <div v-if="formError" class="form-error">{{ formError }}</div>

      <button class="submit-btn" :disabled="submitting" @click="submit">登 录</button>

      <div class="tips">
        <span>演示管理员账号：</span>
        <b>{{ store.adminDemo.account }} / {{ store.adminDemo.password }}</b>
      </div>
      <div class="tips switch-row">
        <span class="switch" @click="backToStudent">← 返回学生端登录</span>
      </div>
    </div>

    <div class="foot">管理员账号与普通用户账号相互独立</div>
  </div>
</template>

<style scoped>
.admin-login-page {
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
  /* 管理端用紫色渐变，与 C 端绿/蓝主色区分 */
  background: linear-gradient(135deg, #9a7cf5, var(--repair));
  color: #fff;
  font-size: 30px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 8px 20px rgba(0, 0, 0, 0.35);
}
.brand-name {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: 1px;
}
.brand-sub {
  margin-top: 6px;
  font-size: 12px;
  color: var(--text-sub);
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
  border-color: var(--repair);
}
.form-error {
  margin-top: 4px;
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
  margin-top: 10px;
  font-size: 16px;
  font-weight: 600;
  color: #fff;
  background: linear-gradient(90deg, var(--repair), #9a7cf5);
  border: none;
  border-radius: 12px;
  cursor: pointer;
}
.submit-btn:active {
  opacity: 0.85;
}
.submit-btn:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
.tips {
  margin-top: 14px;
  text-align: center;
  font-size: 12px;
  color: var(--text-sub);
}
.tips b {
  color: var(--repair);
  font-weight: 600;
}
.switch-row {
  margin-top: 10px;
}
.switch {
  color: var(--primary);
  cursor: pointer;
}
.foot {
  margin-top: auto;
  text-align: center;
  font-size: 11px;
  color: var(--text-sub);
  opacity: 0.7;
}
</style>
