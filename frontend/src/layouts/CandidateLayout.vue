<template>
  <div class="layout">
    <!-- ==================== 双层深海蓝顶栏 ==================== -->
    <header class="site-header">
      <div class="status-strip">
        <div class="strip-left">
          <span class="strip-label">赛场状态</span>
          <span v-if="auth.inWindow && remainingSec > 0" class="window-live">
            <i class="dot" />开放中 · 本时段剩余 <b>{{ formatSeconds(remainingSec) }}</b>
          </span>
          <span v-else class="window-closed">
            <i class="dot" />当前非开放时段
            <template v-if="auth.me?.next_window_start"> · 下次开放 {{ formatDateTime(auth.me.next_window_start) }}</template>
          </span>
        </div>
        <div class="strip-right" v-if="auth.me">
          <span class="strip-who">{{ auth.me.name }} · <b class="mono">{{ auth.me.account }}</b></span>
          <span class="strip-sep" />
          <button class="logout-link" type="button" @click="onLogout">退出登录</button>
        </div>
      </div>

      <div class="main-nav">
        <div class="brand">
          <!-- 原创六边形赛事徽标（非官方会徽） -->
          <svg class="brand-mark" width="42" height="46" viewBox="0 0 42 46" aria-hidden="true">
            <polygon points="21,1 40,12 40,34 21,45 2,34 2,12" fill="#0085A1" />
            <polygon points="21,1 40,12 21,23 2,12" fill="#22B8CF" />
            <polygon points="2,12 21,23 21,45 2,34" fill="#062B56" />
            <polygon points="40,12 21,23 21,45 40,34" fill="#0A3D73" />
            <polygon points="21,7 34,14 21,21 8,14" fill="#F5A623" opacity="0.9" />
          </svg>
          <div class="brand-text">
            <div class="brand-title">图生视频数字创作平台</div>
            <div class="brand-en">AI VIDEO CREATION · COMPETITION PORTAL</div>
          </div>
        </div>

        <nav class="nav">
          <router-link to="/workspace" class="nav-item" active-class="active">创作工作台</router-link>
          <router-link to="/my-works" class="nav-item" active-class="active">我的作品库</router-link>
        </nav>

        <div class="quota-box">
          <span class="quota-label">剩余创作机会</span>
          <span class="quota-num"><b>{{ auth.totalRemaining }}</b> / {{ auth.totalQuota }}</span>
        </div>
      </div>
    </header>

    <!-- ==================== Hero 巨幕 ==================== -->
    <section class="hero">
      <svg class="hero-radial" viewBox="0 0 1200 220" preserveAspectRatio="xMidYMid slice" aria-hidden="true">
        <g stroke="#22B8CF" stroke-opacity="0.16" stroke-width="1">
          <line v-for="(spoke, index) in spokes" :key="index" :x1="spoke.x1" :y1="spoke.y1" :x2="spoke.x2" :y2="spoke.y2" />
        </g>
        <g fill="none" stroke="#22B8CF" stroke-opacity="0.11" stroke-width="1">
          <circle v-for="r in rings" :key="r" cx="960" cy="110" :r="r" />
        </g>
        <g stroke="#FFFFFF" stroke-opacity="0.05" stroke-width="1">
          <line v-for="y in gridRows" :key="`h${y}`" x1="0" :y1="y" x2="1200" :y2="y" />
          <line v-for="x in gridCols" :key="`v${x}`" :x1="x" y1="0" :x2="x" y2="220" />
        </g>
      </svg>

      <div class="hero-inner">
        <div class="hero-copy">
          <div class="hero-kicker"><span class="kicker-bar" />AI VIDEO STUDIO · 数字创作赛项</div>
          <h1 class="hero-title">图生视频数字创作平台</h1>
          <p class="hero-sub">上传首帧视觉素材，撰写镜头动态提示词，生成 15 秒创意视频作品</p>
          <div class="hero-specs">
            <span>15s 成片时长</span>
            <span>H.264 / AAC</span>
            <span>768P 高清</span>
            <span>签名安全下载</span>
          </div>
        </div>
        <GeoCubeDecor class="hero-cubes" variant="hero" :width="300" :height="200" aria-label="原创等轴测立体几何装饰" />
      </div>
    </section>

    <!-- ==================== 主内容 ==================== -->
    <main class="content">
      <router-view />
    </main>

    <!-- ==================== 深蓝页脚 ==================== -->
    <footer class="site-footer">
      <GeoCubeDecor class="footer-cubes" variant="footer" :width="140" :height="96" aria-label="原创等轴测立体几何装饰" />
      <div class="footer-inner">
        <div class="footer-title">图生视频数字创作平台</div>
        <div class="footer-spec">
          技术规格：单题创作任务 · 2 次生成机会 · 15 秒 H.264/AAC MP4 · 30 分钟有效签名下载 · 断点续传 Range
        </div>
        <div class="footer-rule">
          比赛纪律：请在开放时段内独立完成创作，勿刷新或关闭页面中断排队；作品将由系统统一留存与评审。
        </div>
        <div class="footer-copy">© AI VIDEO STUDIO · 原创界面设计 · 本页所有几何图形均为内联 SVG 绘制</div>
      </div>
      <div class="footer-watermark">AI VIDEO STUDIO</div>
    </footer>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import GeoCubeDecor from '@/components/GeoCubeDecor.vue'
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
  if (!auth.me) await auth.refreshMe()
  tickTimer = window.setInterval(() => {
    if (!auth.inWindow) {
      remainingSec.value = 0
      return
    }
    if (remainingSec.value > 0) remainingSec.value -= 1
    if (remainingSec.value === 0) void auth.refreshMe()
  }, 1000)
  syncTimer = window.setInterval(() => void auth.refreshMe(), 30_000)
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

/** 放射网格线（以右侧魔方位置为中心发散） */
const spokes = Array.from({ length: 20 }, (_, index) => {
  const angle = (Math.PI * 2 * index) / 20
  return {
    x1: 960,
    y1: 110,
    x2: 960 + Math.cos(angle) * 900,
    y2: 110 + Math.sin(angle) * 900,
  }
})
const rings = [60, 120, 180, 240, 300]
const gridRows = [28, 56, 84, 112, 140, 168, 196]
const gridCols = Array.from({ length: 17 }, (_, index) => index * 75)
</script>

<style scoped>
.layout {
  min-height: 100%;
  display: flex;
  flex-direction: column;
  background: var(--i2v-bg);
}

/* ---------------- 顶栏 ---------------- */
.site-header {
  position: sticky;
  top: 0;
  z-index: 20;
}

.status-strip {
  height: 34px;
  padding: 0 28px;
  background: var(--i2v-navy-deep);
  color: rgba(255, 255, 255, 0.82);
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-size: 12px;
  border-bottom: 1px solid rgba(34, 184, 207, 0.22);
}

.strip-left,
.strip-right {
  display: flex;
  align-items: center;
  gap: 10px;
}

.strip-label {
  color: var(--i2v-cyan);
  font-weight: 700;
  letter-spacing: 1px;
}

.dot {
  display: inline-block;
  width: 6px;
  height: 6px;
  border-radius: 50%;
  margin-right: 6px;
  vertical-align: 1px;
}

.window-live .dot {
  background: var(--i2v-cyan);
  box-shadow: 0 0 0 3px rgba(34, 184, 207, 0.18);
}

.window-closed .dot {
  background: var(--i2v-amber);
}

.window-live b {
  color: #fff;
  font-variant-numeric: tabular-nums;
}

.logout-link {
  background: none;
  border: none;
  color: rgba(255, 255, 255, 0.82);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 0;
}

.logout-link:hover {
  color: var(--i2v-cyan);
}

.strip-sep {
  width: 1px;
  height: 12px;
  background: rgba(255, 255, 255, 0.25);
}

.main-nav {
  height: 74px;
  padding: 0 28px;
  background: linear-gradient(90deg, var(--i2v-navy) 0%, var(--i2v-navy-2) 100%);
  display: flex;
  align-items: center;
  gap: 32px;
  box-shadow: 0 4px 18px rgba(6, 43, 86, 0.22);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: 0 0 auto;
}

.brand-mark {
  flex: 0 0 auto;
  filter: drop-shadow(0 4px 8px rgba(0, 0, 0, 0.3));
}

.brand-title {
  color: #fff;
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 1px;
}

.brand-en {
  color: rgba(255, 255, 255, 0.5);
  font-size: 10px;
  letter-spacing: 2px;
  margin-top: 2px;
}

.nav {
  flex: 1;
  display: flex;
  justify-content: center;
  align-items: stretch;
  height: 100%;
  gap: 8px;
}

.nav-item {
  position: relative;
  display: flex;
  align-items: center;
  padding: 0 22px;
  color: rgba(255, 255, 255, 0.72);
  font-size: 15px;
  letter-spacing: 1px;
  transition: color 0.15s;
}

.nav-item:hover {
  color: #fff;
}

.nav-item.active {
  color: #fff;
  font-weight: 700;
}

.nav-item.active::after {
  content: '';
  position: absolute;
  bottom: 16px;
  left: 22px;
  right: 22px;
  height: 3px;
  background: var(--i2v-cyan);
  border-radius: 2px;
  box-shadow: 0 0 10px rgba(34, 184, 207, 0.6);
}

.quota-box {
  flex: 0 0 auto;
  display: flex;
  flex-direction: column;
  align-items: flex-end;
  padding: 6px 14px;
  border-left: 1px solid rgba(255, 255, 255, 0.18);
}

.quota-label {
  color: rgba(255, 255, 255, 0.6);
  font-size: 11px;
  letter-spacing: 1px;
}

.quota-num {
  color: #fff;
  font-size: 14px;
  font-variant-numeric: tabular-nums;
}

.quota-num b {
  color: var(--i2v-amber-light);
  font-size: 22px;
  line-height: 1.1;
}

/* ---------------- Hero ---------------- */
.hero {
  position: relative;
  height: 200px;
  overflow: hidden;
  background: linear-gradient(115deg, var(--i2v-navy) 0%, var(--i2v-blue-radial) 100%);
}

.hero-radial {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  pointer-events: none;
}

.hero-inner {
  position: relative;
  height: 100%;
  max-width: 1240px;
  margin: 0 auto;
  padding: 0 32px;
  display: flex;
  align-items: center;
}

.hero-copy {
  position: relative;
  z-index: 2;
}

.hero-kicker {
  display: flex;
  align-items: center;
  gap: 8px;
  color: var(--i2v-cyan);
  font-size: 12px;
  letter-spacing: 2px;
  margin-bottom: 10px;
}

.kicker-bar {
  width: 24px;
  height: 3px;
  background: var(--i2v-amber);
}

.hero-title {
  margin: 0;
  color: #fff;
  font-size: 34px;
  font-weight: 800;
  letter-spacing: 3px;
  text-shadow: 0 2px 12px rgba(0, 0, 0, 0.25);
}

.hero-sub {
  margin: 10px 0 14px;
  color: rgba(255, 255, 255, 0.82);
  font-size: 14px;
  letter-spacing: 0.5px;
}

.hero-specs {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}

.hero-specs span {
  padding: 4px 10px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.88);
  border: 1px solid rgba(34, 184, 207, 0.35);
  background: rgba(0, 133, 161, 0.18);
  border-radius: 3px;
  letter-spacing: 0.5px;
}

.hero-cubes {
  position: absolute;
  right: 8px;
  bottom: -12px;
  z-index: 1;
}

/* ---------------- 内容区 ---------------- */
.content {
  flex: 1;
  min-height: 0;
  position: relative;
}

/* ---------------- 页脚 ---------------- */
.site-footer {
  position: relative;
  overflow: hidden;
  background: linear-gradient(120deg, var(--i2v-navy-deep) 0%, var(--i2v-navy) 60%, var(--i2v-navy-2) 100%);
  color: rgba(255, 255, 255, 0.76);
  padding: 30px 32px 26px;
  min-height: 170px;
}

.footer-cubes {
  position: absolute;
  left: 18px;
  bottom: -8px;
  opacity: 0.9;
}

.footer-inner {
  position: relative;
  z-index: 2;
  max-width: 900px;
  margin: 0 auto;
  text-align: center;
  padding-left: 60px;
}

.footer-title {
  color: #fff;
  font-size: 16px;
  font-weight: 700;
  letter-spacing: 2px;
  margin-bottom: 10px;
}

.footer-spec {
  font-size: 12px;
  line-height: 1.9;
  color: rgba(255, 255, 255, 0.8);
}

.footer-rule {
  font-size: 12px;
  line-height: 1.9;
  margin-top: 4px;
}

.footer-copy {
  margin-top: 12px;
  font-size: 11px;
  color: rgba(255, 255, 255, 0.45);
}

.footer-watermark {
  position: absolute;
  right: -10px;
  bottom: -18px;
  font-size: 84px;
  font-weight: 800;
  letter-spacing: 12px;
  color: rgba(255, 255, 255, 0.04);
  white-space: nowrap;
  pointer-events: none;
}

@media (max-width: 900px) {
  .hero-cubes {
    opacity: 0.45;
    right: -40px;
  }
  .nav-item {
    padding: 0 12px;
  }
  .brand-en {
    display: none;
  }
  .footer-inner {
    padding-left: 0;
  }
}
</style>
