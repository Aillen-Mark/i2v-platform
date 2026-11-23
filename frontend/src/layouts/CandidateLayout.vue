<template>
  <div class="layout">
    <header class="topbar">
      <div class="brand">
        <span class="logo">i2v</span>
        <span class="title">图生视频创作平台</span>
        <span class="sub">考生端</span>
      </div>

      <nav class="nav">
        <router-link to="/workspace" class="nav-item" active-class="active">创作工作台</router-link>
        <router-link to="/my-works" class="nav-item" active-class="active">我的作品</router-link>
      </nav>

      <div class="status">
        <el-tag type="info" effect="plain" size="large" class="quota-tag">
          总剩余次数：{{ auth.totalRemaining }} / {{ auth.totalQuota }} 次
        </el-tag>

        <el-tag v-if="auth.inWindow && remainingSec > 0" type="success" effect="dark" size="large" class="window-tag">
          本时段剩余 {{ formatSeconds(remainingSec) }}
        </el-tag>
        <el-tag v-else type="warning" effect="dark" size="large" class="window-tag">
          当前非开放时段
          <template v-if="auth.me?.next_window_start"> · 下次开放：{{ formatDateTime(auth.me.next_window_start) }}</template>
          <template v-else> · 暂无开放计划</template>
        </el-tag>

        <div class="who" v-if="auth.me">
          <span class="name">{{ auth.me.name }}</span>
          <span class="account mono">{{ auth.me.account }}</span>
        </div>

        <el-button size="small" @click="onLogout">退出登录</el-button>
      </div>
    </header>

    <main class="content">
      <router-view />
    </main>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'
import { formatDateTime, formatSeconds } from '@/utils/format'

const auth = useAuthStore()
const router = useRouter()

const remainingSec = ref(0)
let tickTimer: number | null = null
let syncTimer: number | null = null

const me = computed(() => auth.me)

watch(
  () => me.value?.current_window_remaining_sec,
  (value) => {
    remainingSec.value = Math.max(0, Number(value ?? 0))
  },
  { immediate: true },
)

onMounted(async () => {
  if (!auth.me) {
    await auth.refreshMe()
  }
  // 每秒本地递减，30 秒与服务端对齐一次（避免长时间漂移）
  tickTimer = window.setInterval(() => {
    if (!auth.inWindow) {
      remainingSec.value = 0
      return
    }
    if (remainingSec.value > 0) {
      remainingSec.value -= 1
    }
    if (remainingSec.value === 0) {
      void auth.refreshMe()
    }
  }, 1000)
  syncTimer = window.setInterval(() => {
    void auth.refreshMe()
  }, 30_000)
})

onBeforeUnmount(() => {
  if (tickTimer) window.clearInterval(tickTimer)
  if (syncTimer) window.clearInterval(syncTimer)
})

async function onLogout(): Promise<void> {
  await auth.logout()
  ElMessage.success('已退出登录')
  await router.push('/login')
}
</script>

<style scoped>
.layout {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

.topbar {
  height: 60px;
  display: flex;
  align-items: center;
  gap: 24px;
  padding: 0 20px;
  background: #ffffff;
  border-bottom: 1px solid var(--i2v-border);
  position: sticky;
  top: 0;
  z-index: 10;
}

.brand {
  display: flex;
  align-items: baseline;
  gap: 8px;
}

.logo {
  font-weight: 700;
  color: #ffffff;
  background: var(--i2v-primary);
  border-radius: 6px;
  padding: 2px 8px;
  font-size: 14px;
  letter-spacing: 0.5px;
}

.title {
  font-size: 16px;
  font-weight: 600;
}

.sub {
  font-size: 12px;
  color: var(--i2v-text-sub);
}

.nav {
  display: flex;
  gap: 6px;
  flex: 1;
}

.nav-item {
  padding: 6px 14px;
  border-radius: 6px;
  color: var(--i2v-text-sub);
  font-size: 14px;
}

.nav-item.active {
  background: #eff6ff;
  color: var(--i2v-primary);
  font-weight: 600;
}

.status {
  display: flex;
  align-items: center;
  gap: 12px;
}

.quota-tag {
  font-weight: 600;
}

.window-tag {
  font-variant-numeric: tabular-nums;
}

.who {
  display: flex;
  flex-direction: column;
  line-height: 1.2;
  text-align: right;
}

.who .name {
  font-weight: 600;
}

.who .account {
  font-size: 12px;
  color: var(--i2v-text-sub);
}

.content {
  flex: 1;
  min-height: 0;
}
</style>
