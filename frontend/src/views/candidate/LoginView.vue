<template>
  <div class="login-page">
    <!-- 全屏深海蓝放射网格底纹（内联 SVG，离线自包含） -->
    <svg class="radial-bg" viewBox="0 0 1440 900" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g stroke="#22B8CF" stroke-opacity="0.13" stroke-width="1">
        <line v-for="(spoke, index) in spokes" :key="index" :x1="spoke.x1" :y1="spoke.y1" :x2="spoke.x2" :y2="spoke.y2" />
      </g>
      <g fill="none" stroke="#FFFFFF" stroke-opacity="0.06" stroke-width="1">
        <circle v-for="r in rings" :key="r" cx="1120" cy="640" :r="r" />
      </g>
      <g stroke="#FFFFFF" stroke-opacity="0.035" stroke-width="1">
        <line v-for="y in rows" :key="`h${y}`" x1="0" :y1="y" x2="1440" :y2="y" />
        <line v-for="x in cols" :key="`v${x}`" :x1="x" y1="0" :x2="x" y2="900" />
      </g>
    </svg>

    <div class="login-shell">
      <!-- 左侧主视觉 -->
      <div class="visual">
        <div class="visual-kicker"><span class="kicker-bar" />AI VIDEO STUDIO · COMPETITION PORTAL</div>
        <h1 class="visual-title">图生视频<br />数字创作平台</h1>
        <div class="visual-rule" />
        <p class="visual-sub">上传首帧视觉素材 · 撰写镜头动态提示词<br />生成 15 秒创意视频作品</p>
        <div class="visual-specs">
          <span>单题创作任务</span>
          <span>15s H.264 / AAC</span>
          <span>768P 高清成片</span>
          <span>签名安全下载</span>
        </div>
        <div class="visual-foot">© 原创界面设计 · 全部几何图形为内联 SVG 绘制</div>
      </div>

      <!-- 右侧硬朗白色登录卡 -->
      <div class="login-card">
        <div class="card-top" />
        <div class="card-head">
          <svg class="card-mark" width="34" height="38" viewBox="0 0 42 46" aria-hidden="true">
            <polygon points="21,1 40,12 40,34 21,45 2,34 2,12" fill="#0085A1" />
            <polygon points="21,1 40,12 21,23 2,12" fill="#22B8CF" />
            <polygon points="2,12 21,23 21,45 2,34" fill="#062B56" />
            <polygon points="40,12 21,23 21,45 40,34" fill="#0A3D73" />
          </svg>
          <div>
            <div class="card-title">选手登录</div>
            <div class="muted">使用考号与初始密码进入考场</div>
          </div>
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

        <div class="card-foot muted">连续输错 5 次将锁定 5 分钟；如忘记密码请联系监考老师重置。</div>
      </div>
    </div>

    <!-- 右下角大型 3D 等轴测立体魔方 -->
    <GeoCubeDecor class="login-cubes" variant="login" :width="440" :height="300" aria-label="原创等轴测立体几何装饰" />
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { AxiosError } from 'axios'

import GeoCubeDecor from '@/components/GeoCubeDecor.vue'
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

/** 放射网格线（以右下魔方区域为中心） */
const spokes = Array.from({ length: 24 }, (_, index) => {
  const angle = (Math.PI * 2 * index) / 24
  return { x1: 1120, y1: 640, x2: 1120 + Math.cos(angle) * 1400, y2: 640 + Math.sin(angle) * 1400 }
})
const rings = [90, 180, 270, 360, 450, 540]
const rows = Array.from({ length: 15 }, (_, index) => index * 60)
const cols = Array.from({ length: 25 }, (_, index) => index * 60)
</script>

<style scoped>
.login-page {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  overflow: hidden;
  background: linear-gradient(120deg, var(--i2v-navy-deep) 0%, var(--i2v-navy) 45%, var(--i2v-blue-radial) 100%);
}

.radial-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.login-shell {
  position: relative;
  z-index: 2;
  width: 100%;
  max-width: 1120px;
  margin: 0 auto;
  padding: 40px 32px;
  display: grid;
  grid-template-columns: 1fr 420px;
  gap: 60px;
  align-items: center;
}

.visual-kicker {
  display: flex;
  align-items: center;
  gap: 10px;
  color: var(--i2v-cyan);
  font-size: 12px;
  letter-spacing: 3px;
  font-weight: 700;
  margin-bottom: 20px;
}

.kicker-bar {
  width: 30px;
  height: 3px;
  background: var(--i2v-amber);
}

.visual-title {
  margin: 0;
  color: #fff;
  font-size: 52px;
  line-height: 1.25;
  font-weight: 800;
  letter-spacing: 6px;
  text-shadow: 0 4px 20px rgba(0, 0, 0, 0.3);
}

.visual-rule {
  width: 70px;
  height: 3px;
  background: linear-gradient(90deg, var(--i2v-cyan), var(--i2v-amber));
  margin: 22px 0 18px;
}

.visual-sub {
  color: rgba(255, 255, 255, 0.82);
  font-size: 15px;
  line-height: 2;
  letter-spacing: 1px;
}

.visual-specs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 20px;
}

.visual-specs span {
  padding: 5px 12px;
  font-size: 12px;
  color: rgba(255, 255, 255, 0.9);
  border: 1px solid rgba(34, 184, 207, 0.35);
  background: rgba(0, 133, 161, 0.2);
  border-radius: 3px;
  letter-spacing: 0.5px;
}

.visual-foot {
  margin-top: 40px;
  color: rgba(255, 255, 255, 0.38);
  font-size: 11px;
}

.login-card {
  position: relative;
  overflow: hidden;
  background: #fff;
  border: 1px solid var(--i2v-border);
  border-radius: var(--i2v-radius-lg);
  box-shadow: var(--i2v-shadow-lg);
  padding: 32px 30px 24px;
}

.card-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--i2v-primary), var(--i2v-cyan) 60%, var(--i2v-amber));
}

.card-head {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 24px;
}

.card-title {
  color: var(--i2v-navy);
  font-size: 20px;
  font-weight: 800;
  letter-spacing: 2px;
}

.error-box {
  margin-bottom: 12px;
}

.submit {
  width: 100%;
  margin-top: 4px;
  height: 44px;
  letter-spacing: 4px;
}

.card-foot {
  margin-top: 18px;
  text-align: center;
  line-height: 1.7;
}

.login-cubes {
  position: absolute;
  right: -20px;
  bottom: -24px;
  z-index: 1;
  opacity: 0.95;
}

@media (max-width: 900px) {
  .login-shell {
    grid-template-columns: 1fr;
    gap: 24px;
    max-width: 480px;
  }
  .visual {
    text-align: center;
  }
  .visual-title {
    font-size: 34px;
  }
  .visual-rule {
    margin: 16px auto;
  }
  .login-cubes {
    opacity: 0.35;
  }
}
</style>
