<template>
  <div class="login-page">
    <div class="login-card panel">
      <div class="head">
        <div class="logo">i2v</div>
        <h1>图生视频创作平台</h1>
        <p class="muted">考生登录 · 使用考号与初始密码进入考场</p>
      </div>

      <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="考号">
          <el-input
            v-model="form.account"
            placeholder="请输入考号，如 C2026000001"
            size="large"
            autocomplete="username"
            :disabled="submitting"
            @keyup.enter="onSubmit"
          />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            placeholder="请输入密码"
            size="large"
            show-password
            autocomplete="current-password"
            :disabled="submitting"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-alert
          v-if="errorText"
          :title="errorText"
          :type="errorType"
          :closable="false"
          show-icon
          class="error-box"
        />

        <el-button
          type="primary"
          size="large"
          class="submit"
          :loading="submitting"
          :disabled="submitting || lockRemain > 0"
          @click="onSubmit"
        >
          {{ lockRemain > 0 ? `账号已锁定，请 ${lockRemain} 秒后再试` : '登 录' }}
        </el-button>
      </el-form>

      <div class="footer muted">
        连续输错 5 次将锁定 5 分钟；如忘记密码请联系监考老师重置。
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { AxiosError } from 'axios'

import { useAuthStore } from '@/stores/auth'
import type { ApiError } from '@/api/types'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const form = reactive({ account: '', password: '' })
const submitting = ref(false)
const errorText = ref('')
const errorType = ref<'error' | 'warning'>('error')
const lockRemain = ref(0)
let lockTimer: number | null = null

function startLockCountdown(seconds: number): void {
  lockRemain.value = Math.max(0, Math.floor(seconds))
  if (lockTimer) window.clearInterval(lockTimer)
  lockTimer = window.setInterval(() => {
    lockRemain.value = Math.max(0, lockRemain.value - 1)
    if (lockRemain.value === 0 && lockTimer) {
      window.clearInterval(lockTimer)
      lockTimer = null
    }
  }, 1000)
}

onBeforeUnmount(() => {
  if (lockTimer) window.clearInterval(lockTimer)
})

async function onSubmit(): Promise<void> {
  if (submitting.value || lockRemain.value > 0) return
  const account = form.account.trim()
  const password = form.password
  if (!account || !password) {
    errorText.value = '请输入考号与密码'
    errorType.value = 'warning'
    return
  }

  submitting.value = true
  errorText.value = ''
  try {
    const me = await auth.login(account, password)
    await auth.refreshQuestions()
    ElMessage.success(`欢迎，${me.name}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/workspace'
    await router.push(redirect)
  } catch (error) {
    const axiosError = error as AxiosError<ApiError>
    const status = axiosError.response?.status
    const body = axiosError.response?.data
    if (status === 429) {
      const retryAfter = Number((body?.detail as { retry_after?: number } | undefined)?.retry_after ?? 300)
      startLockCountdown(retryAfter)
      errorText.value = body?.message || '账号已锁定，请稍后再试'
      errorType.value = 'error'
    } else if (status === 403) {
      errorText.value = body?.message || '账号已被禁用，请联系监考老师'
      errorType.value = 'error'
    } else if (status === 401) {
      const remaining = (body?.detail as { remaining_attempts?: number } | undefined)?.remaining_attempts
      errorText.value = remaining !== undefined
        ? `${body?.message || '账号或密码错误'}（还剩 ${remaining} 次尝试机会）`
        : body?.message || '账号或密码错误'
      errorType.value = 'error'
    } else {
      errorText.value = body?.message || '登录失败，请稍后重试'
      errorType.value = 'error'
    }
  } finally {
    submitting.value = false
  }
}
</script>

<style scoped>
.login-page {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #eff6ff 0%, #f8fafc 45%, #eef2ff 100%);
}

.login-card {
  width: 420px;
  padding: 32px 32px 24px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.08);
}

.head {
  text-align: center;
  margin-bottom: 20px;
}

.logo {
  display: inline-block;
  font-weight: 700;
  color: #fff;
  background: var(--i2v-primary);
  border-radius: 8px;
  padding: 4px 10px;
  margin-bottom: 10px;
}

h1 {
  font-size: 20px;
  margin: 6px 0;
}

.error-box {
  margin-bottom: 12px;
}

.submit {
  width: 100%;
  margin-top: 4px;
}

.footer {
  margin-top: 16px;
  text-align: center;
}
</style>
