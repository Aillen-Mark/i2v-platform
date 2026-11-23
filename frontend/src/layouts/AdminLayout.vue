<template>
  <div class="admin-layout">
    <aside class="sidebar">
      <div class="brand">
        <span class="logo">i2v</span>
        <span class="brand-text">管理后台</span>
      </div>
      <nav class="menu">
        <router-link
          v-for="item in menu"
          :key="item.path"
          :to="item.path"
          class="menu-item"
          active-class="active"
        >
          <span class="menu-label">{{ item.label }}</span>
        </router-link>
      </nav>
      <div class="sidebar-footer">
        <router-link to="/workspace" class="to-candidate">← 返回考生端</router-link>
      </div>
    </aside>

    <div class="main">
      <header class="topbar">
        <div class="crumb">{{ currentTitle }}</div>
        <div class="right">
          <span class="who">{{ adminAuth.me?.username }}（{{ adminAuth.me?.role }}）</span>
          <el-button size="small" @click="onLogout">退出登录</el-button>
        </div>
      </header>
      <main class="content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAdminAuthStore } from '@/stores/adminAuth'

const adminAuth = useAdminAuthStore()
const router = useRouter()
const route = useRoute()

const menu = [
  { path: '/console/candidates', label: '考生管理' },
  { path: '/console/questions', label: '题目管理' },
  { path: '/console/quotas', label: '配额中心' },
  { path: '/console/time-windows', label: '开放时段' },
  { path: '/console/engines', label: '模型引擎' },
  { path: '/console/stats', label: '用量统计' },
  { path: '/console/logs', label: '操作日志' },
]

const currentTitle = computed(() => (route.meta.title as string) || '管理后台')

onMounted(async () => {
  if (!adminAuth.me) {
    await adminAuth.refreshMe()
  }
})

async function onLogout(): Promise<void> {
  await adminAuth.logout()
  ElMessage.success('已退出管理后台')
  await router.push('/console/login')
}
</script>

<style scoped>
.admin-layout {
  display: flex;
  min-height: 100vh;
  background: #f5f7fa;
}

.sidebar {
  width: 200px;
  flex: 0 0 200px;
  background: #0f172a;
  color: #cbd5e1;
  display: flex;
  flex-direction: column;
}

.brand {
  height: 56px;
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(255, 255, 255, 0.08);
}

.logo {
  background: var(--i2v-primary);
  color: #fff;
  font-weight: 700;
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 13px;
}

.brand-text {
  color: #fff;
  font-weight: 600;
  font-size: 14px;
}

.menu {
  flex: 1;
  padding: 10px 8px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.menu-item {
  display: block;
  padding: 9px 12px;
  border-radius: 6px;
  color: #cbd5e1;
  font-size: 14px;
}

.menu-item:hover {
  background: rgba(255, 255, 255, 0.06);
  color: #fff;
}

.menu-item.active {
  background: var(--i2v-primary);
  color: #fff;
  font-weight: 600;
}

.sidebar-footer {
  padding: 12px 16px;
  border-top: 1px solid rgba(255, 255, 255, 0.08);
}

.to-candidate {
  color: #94a3b8;
  font-size: 12px;
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 56px;
  background: #fff;
  border-bottom: 1px solid var(--i2v-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
}

.crumb {
  font-size: 15px;
  font-weight: 600;
}

.right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.who {
  font-size: 13px;
  color: var(--i2v-text-sub);
}

.content {
  flex: 1;
  padding: 16px 20px 32px;
  overflow: auto;
}
</style>
