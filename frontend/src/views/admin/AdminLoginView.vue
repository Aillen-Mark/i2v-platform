<template>
  <div class="admin-login">
    <div class="card panel">
      <div class="head">
        <div class="logo">i2v</div>
        <h1>管理后台</h1>
        <p class="muted">图生视频创作平台 · 监考与配置中心</p>
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
  </div>
</template>

<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { AxiosError } from 'axios'

import { useAdminAuthStore } from '@/stores/adminAuth'
import type { ApiError } from '@/api/types'

const adminAuth = useAdminAuthStore()
const router = useRouter()
const route = useRoute()

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
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #0f172a 0%, #1e293b 55%, #0b1220 100%);
}

.card {
  width: 400px;
  padding: 32px;
  border: none;
  box-shadow: 0 20px 60px rgba(0, 0, 0, 0.35);
}

.head {
  text-align: center;
  margin-bottom: 18px;
}

.logo {
  display: inline-block;
  background: var(--i2v-primary);
  color: #fff;
  font-weight: 700;
  border-radius: 8px;
  padding: 4px 10px;
  margin-bottom: 10px;
}

h1 {
  font-size: 20px;
  margin: 6px 0;
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
