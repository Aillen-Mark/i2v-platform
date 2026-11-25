<template>
  <div class="admin-layout">
    <aside class="sidebar">
      <div class="brand">
        <svg class="brand-mark" width="32" height="36" viewBox="0 0 42 46" aria-hidden="true">
          <polygon points="21,1 40,12 40,34 21,45 2,34 2,12" fill="#0085A1" />
          <polygon points="21,1 40,12 21,23 2,12" fill="#22B8CF" />
          <polygon points="2,12 21,23 21,45 2,34" fill="#041F40" />
          <polygon points="40,12 21,23 21,45 40,34" fill="#0A3D73" />
        </svg>
        <div class="brand-text">
          <div>管理控制台</div>
          <small>ADMIN CONSOLE</small>
        </div>
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
  // 单题极简模式：全场固定 1 道创作任务，题目管理入口已屏蔽（QuestionsView.vue 源码保留，
  // 将来恢复多题模式时把这一行放开、并去掉 router 里的 /console/questions 重定向即可）
  // { path: '/console/questions', label: '题目管理' },
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
  background: var(--i2v-bg);
}

.sidebar {
  width: 208px;
  flex: 0 0 208px;
  background: linear-gradient(180deg, var(--i2v-navy-deep) 0%, var(--i2v-navy) 60%, var(--i2v-navy-2) 100%);
  color: rgba(255, 255, 255, 0.72);
  display: flex;
  flex-direction: column;
  box-shadow: 4px 0 18px rgba(6, 43, 86, 0.14);
}

.brand {
  height: 68px;
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 16px;
  border-bottom: 1px solid rgba(34, 184, 207, 0.2);
}

.brand-mark {
  flex: 0 0 auto;
}

.brand-text {
  color: #fff;
  font-weight: 700;
  font-size: 14px;
  letter-spacing: 1px;
  line-height: 1.3;
}

.brand-text small {
  display: block;
  color: var(--i2v-cyan);
  font-size: 9px;
  letter-spacing: 2px;
  font-weight: 400;
  margin-top: 2px;
}

.menu {
  flex: 1;
  padding: 14px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
}

.menu-item {
  display: block;
  padding: 10px 14px;
  border-radius: var(--i2v-radius);
  color: rgba(255, 255, 255, 0.72);
  font-size: 14px;
  border-left: 3px solid transparent;
}

.menu-item:hover {
  background: rgba(0, 133, 161, 0.2);
  color: #fff;
}

.menu-item.active {
  background: rgba(0, 133, 161, 0.3);
  border-left-color: var(--i2v-cyan);
  color: #fff;
  font-weight: 700;
}

.sidebar-footer {
  padding: 14px 16px;
  border-top: 1px solid rgba(34, 184, 207, 0.18);
}

.to-candidate {
  color: rgba(255, 255, 255, 0.58);
  font-size: 12px;
}

.to-candidate:hover {
  color: var(--i2v-cyan);
}

.main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 60px;
  background: #fff;
  border-bottom: 1px solid var(--i2v-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 0 20px;
  box-shadow: var(--i2v-shadow);
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
