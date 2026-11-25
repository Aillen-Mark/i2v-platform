<template>
  <div class="admin-login">
    <svg class="radial-bg" viewBox="0 0 1200 800" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
      <g stroke="#22B8CF" stroke-opacity="0.12" stroke-width="1">
        <line v-for="(spoke, index) in spokes" :key="index" :x1="spoke.x1" :y1="spoke.y1" :x2="spoke.x2" :y2="spoke.y2" />
      </g>
      <g fill="none" stroke="#FFFFFF" stroke-opacity="0.05" stroke-width="1">
        <circle v-for="r in rings" :key="r" cx="960" cy="600" :r="r" />
      </g>
    </svg>

    <div class="card panel">
      <div class="card-top" />
      <div class="head">
        <svg class="head-mark" width="38" height="42" viewBox="0 0 42 46" aria-hidden="true">
          <polygon points="21,1 40,12 40,34 21,45 2,34 2,12" fill="#0085A1" />
          <polygon points="21,1 40,12 21,23 2,12" fill="#22B8CF" />
          <polygon points="2,12 21,23 21,45 2,34" fill="#062B56" />
          <polygon points="40,12 21,23 21,45 40,34" fill="#0A3D73" />
        </svg>
        <h1>管理控制台</h1>
        <p class="muted">图生视频数字创作平台 · 监考与配置中心</p>
      </div>

      <el-form :model="form" label-position="top" @submit.prevent="onSubmit">
        <el-form-item label="管理员账号">
          <el-input v-model="form.username" size="large" placeholder="admin" :disabled="loading" @keyup.enter="onSubmit" />
        </el-form-item>
        <el-form-item label="密码">
          <el-input
            v-model="form.password"
            type="password"
            size="large"
            show-password
            :disabled="loading"
            @keyup.enter="onSubmit"
          />
        </el-form-item>

        <el-alert v-if="errorText" :title="errorText" type="error" :closable="false" show-icon class="mb-12" />

        <el-button type="primary" size="large" class="submit" :loading="loading" @click="onSubmit">登 录</el-button>
      </el-form>

      <div class="footer muted">
        考生入口：<router-link to="/login">/login</router-link>
      </div>
    </div>

    <GeoCubeDecor class="cubes" variant="footer" :width="220" :height="150" aria-label="原创等轴测立体几何装饰" />
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { AxiosError } from 'axios'

import GeoCubeDecor from '@/components/GeoCubeDecor.vue'
import { useAdminAuthStore } from '@/stores/adminAuth'
import type { ApiError } from '@/api/types'

const adminAuth = useAdminAuthStore()
const router = useRouter()
const route = useRoute()

const spokes = Array.from({ length: 20 }, (_, index) => {
  const angle = (Math.PI * 2 * index) / 20
  return { x1: 960, y1: 600, x2: 960 + Math.cos(angle) * 1200, y2: 600 + Math.sin(angle) * 1200 }
})
const rings = [80, 160, 240, 320, 400]

const form = reactive({ username: 'admin', password: '' })
const loading = ref(false)
const errorText = ref('')

async function onSubmit(): Promise<void> {
  if (loading.value) return
  if (!form.username.trim() || !form.password) {
    errorText.value = '请输入账号与密码'
    return
  }
  loading.value = true
  errorText.value = ''
  try {
    const me = await adminAuth.login(form.username.trim(), form.password)
    ElMessage.success(`欢迎，${me.username}`)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : '/console/candidates'
    await router.push(redirect)
  } catch (error) {
    const axiosError = error as AxiosError<ApiError>
    errorText.value = axiosError.response?.data?.message || '登录失败，请稍后重试'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.admin-login {
  position: relative;
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: linear-gradient(125deg, var(--i2v-navy-deep) 0%, var(--i2v-navy) 50%, var(--i2v-blue-radial) 100%);
}

.radial-bg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.card {
  position: relative;
  z-index: 2;
  width: 400px;
  padding: 32px;
  border: 1px solid var(--i2v-border);
  border-radius: var(--i2v-radius-lg);
  box-shadow: var(--i2v-shadow-lg);
  overflow: hidden;
}

.card-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--i2v-primary), var(--i2v-cyan) 60%, var(--i2v-amber));
}

.head {
  text-align: center;
  margin-bottom: 18px;
}

.head-mark {
  margin-bottom: 8px;
}

h1 {
  font-size: 20px;
  color: var(--i2v-navy);
  letter-spacing: 2px;
  margin: 6px 0;
}

.cubes {
  position: absolute;
  right: 10px;
  bottom: -10px;
  z-index: 1;
  opacity: 0.85;
}

.submit {
  width: 100%;
}

.mb-12 {
  margin-bottom: 12px;
}

.footer {
  margin-top: 16px;
  text-align: center;
}
</style>
