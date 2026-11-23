import { createRouter, createWebHistory } from 'vue-router'

import AdminLayout from '@/layouts/AdminLayout.vue'
import CandidateLayout from '@/layouts/CandidateLayout.vue'
import AdminCandidatesView from '@/views/admin/CandidatesView.vue'
import AdminEnginesView from '@/views/admin/EnginesView.vue'
import AdminLoginView from '@/views/admin/AdminLoginView.vue'
import AdminLogsView from '@/views/admin/LogsView.vue'
import AdminPrintSlipsView from '@/views/admin/PrintSlipsView.vue'
import AdminQuestionsView from '@/views/admin/QuestionsView.vue'
import AdminQuotasView from '@/views/admin/QuotasView.vue'
import AdminStatsView from '@/views/admin/StatsView.vue'
import AdminTimeWindowsView from '@/views/admin/TimeWindowsView.vue'
import LoginView from '@/views/candidate/LoginView.vue'
import MyWorksView from '@/views/candidate/MyWorksView.vue'
import WorkspaceView from '@/views/candidate/WorkspaceView.vue'
import { useAdminAuthStore } from '@/stores/adminAuth'
import { useAuthStore } from '@/stores/auth'

/**
 * 路由约定（与后端 /admin/* API 物理隔离，避免刷新页面打到 JSON 接口）：
 *   考生端页面：/login、/workspace、/my-works
 *   管理端页面：/console/*（后端管理接口仍然是 /admin/*，由 Nginx / Vite 代理到 FastAPI）
 */
const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: LoginView,
      meta: { public: true, title: '考生登录' },
    },
    {
      path: '/',
      component: CandidateLayout,
      children: [
        { path: '', redirect: '/workspace' },
        {
          path: 'workspace',
          name: 'workspace',
          component: WorkspaceView,
          meta: { requiresAuth: true, title: '创作工作台' },
        },
        {
          path: 'my-works',
          name: 'my-works',
          component: MyWorksView,
          meta: { requiresAuth: true, title: '我的作品' },
        },
      ],
    },
    {
      path: '/console/login',
      name: 'admin-login',
      component: AdminLoginView,
      meta: { public: true, title: '管理后台登录' },
    },
    {
      // 打印页独立布局（不带导航与操作按钮，@media print 直接出 A4）
      path: '/console/candidates/print-slips',
      name: 'admin-print-slips',
      component: AdminPrintSlipsView,
      meta: { requiresAdmin: true, title: '打印密码条', standalone: true },
    },
    {
      path: '/console',
      component: AdminLayout,
      children: [
        { path: '', redirect: '/console/candidates' },
        {
          path: 'candidates',
          name: 'admin-candidates',
          component: AdminCandidatesView,
          meta: { requiresAdmin: true, title: '考生管理' },
        },
        {
          path: 'questions',
          name: 'admin-questions',
          component: AdminQuestionsView,
          meta: { requiresAdmin: true, title: '题目管理' },
        },
        {
          path: 'quotas',
          name: 'admin-quotas',
          component: AdminQuotasView,
          meta: { requiresAdmin: true, title: '配额中心' },
        },
        {
          path: 'time-windows',
          name: 'admin-time-windows',
          component: AdminTimeWindowsView,
          meta: { requiresAdmin: true, title: '开放时段' },
        },
        {
          path: 'engines',
          name: 'admin-engines',
          component: AdminEnginesView,
          meta: { requiresAdmin: true, title: '模型引擎' },
        },
        {
          path: 'stats',
          name: 'admin-stats',
          component: AdminStatsView,
          meta: { requiresAdmin: true, title: '用量统计' },
        },
        {
          path: 'logs',
          name: 'admin-logs',
          component: AdminLogsView,
          meta: { requiresAdmin: true, title: '操作日志' },
        },
      ],
    },
    { path: '/:pathMatch(.*)*', redirect: '/workspace' },
  ],
})

router.beforeEach(async (to) => {
  if (to.meta.requiresAdmin) {
    const adminAuth = useAdminAuthStore()
    if (!adminAuth.me) {
      await adminAuth.refreshMe()
    }
    if (!adminAuth.me) {
      return { path: '/console/login', query: { redirect: to.fullPath } }
    }
    return true
  }

  if (to.meta.requiresAuth) {
    const auth = useAuthStore()
    if (!auth.me) {
      try {
        await auth.refreshMe()
      } catch {
        return { path: '/login', query: { redirect: to.fullPath } }
      }
    }
    if (!auth.me) {
      return { path: '/login', query: { redirect: to.fullPath } }
    }
    return true
  }

  if (to.path === '/login') {
    const auth = useAuthStore()
    if (auth.me) return { path: '/workspace' }
  }
  if (to.path === '/console/login') {
    const adminAuth = useAdminAuthStore()
    if (adminAuth.me) return { path: '/console/candidates' }
  }
  return true
})

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) || '考生端'
  document.title = `${title} · 图生视频创作平台`
})

export default router
