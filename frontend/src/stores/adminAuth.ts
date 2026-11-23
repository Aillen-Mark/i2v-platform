import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as adminApi from '@/api/admin'
import type { AdminMe } from '@/api/adminTypes'

/**
 * 管理端登录态：与考生端 `useAuthStore` 完全独立（不同 store id、不同 Cookie admin_sid），
 * 同一浏览器可同时登录考生端与管理端且互不干扰。
 */
export const useAdminAuthStore = defineStore('admin', () => {
  const me = ref<AdminMe | null>(null)
  const isLoggedIn = computed(() => me.value !== null)

  function clear(): void {
    me.value = null
  }

  async function login(username: string, password: string): Promise<AdminMe> {
    me.value = await adminApi.adminLogin(username, password)
    return me.value
  }

  async function logout(): Promise<void> {
    try {
      await adminApi.adminLogout()
    } finally {
      clear()
    }
  }

  async function refreshMe(): Promise<AdminMe | null> {
    try {
      me.value = await adminApi.adminMe()
      return me.value
    } catch {
      return null
    }
  }

  return { me, isLoggedIn, clear, login, logout, refreshMe }
})
