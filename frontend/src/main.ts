import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'

import App from './App.vue'
import router from './router'
import { setUnauthorizedHandler } from './api/http'
import { useAdminAuthStore } from './stores/adminAuth'
import { useAuthStore } from './stores/auth'
import './styles/main.css'

const app = createApp(App)
const pinia = createPinia()

app.use(pinia)
app.use(router)
app.use(ElementPlus)

// 会话失效统一处理：按请求来源区分考生端 / 管理端，两套 Store 与登录页互不干扰
setUnauthorizedHandler((scope) => {
  const current = router.currentRoute.value
  if (scope === 'admin') {
    useAdminAuthStore(pinia).clear()
    if (current.path !== '/console/login') {
      router.push({ path: '/console/login', query: { redirect: current.fullPath } })
    }
  } else {
    useAuthStore(pinia).clear()
    if (current.path !== '/login') {
      router.push({ path: '/login', query: { redirect: current.fullPath } })
    }
  }
})

app.mount('#app')
