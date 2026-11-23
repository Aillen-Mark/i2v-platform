import { computed, ref } from 'vue'
import { defineStore } from 'pinia'

import * as api from '@/api/candidate'
import type { CandidateMe, QuestionPublic } from '@/api/types'

/**
 * 考生登录态与共享数据（我的信息 + 题目列表）。
 * 所有数据都来自真实接口，不做任何本地伪造。
 */
export const useAuthStore = defineStore('candidate', () => {
  const me = ref<CandidateMe | null>(null)
  const questions = ref<QuestionPublic[]>([])
  const loading = ref(false)

  const isLoggedIn = computed(() => me.value !== null)
  const totalRemaining = computed(() => me.value?.total_remaining ?? 0)
  const totalQuota = computed(() => me.value?.total_quota ?? 0)
  const inWindow = computed(() => me.value?.in_window ?? false)

  function clear(): void {
    me.value = null
    questions.value = []
  }

  async function login(account: string, password: string): Promise<CandidateMe> {
    const data = await api.login(account, password)
    me.value = data
    return data
  }

  async function logout(): Promise<void> {
    try {
      await api.logout()
    } finally {
      clear()
    }
  }

  async function refreshMe(): Promise<CandidateMe | null> {
    try {
      me.value = await api.fetchMe()
      return me.value
    } catch {
      return null
    }
  }

  async function refreshQuestions(): Promise<QuestionPublic[]> {
    questions.value = await api.fetchQuestions()
    return questions.value
  }

  async function refreshAll(): Promise<void> {
    loading.value = true
    try {
      await Promise.all([refreshMe(), refreshQuestions()])
    } finally {
      loading.value = false
    }
  }

  return {
    me,
    questions,
    loading,
    isLoggedIn,
    totalRemaining,
    totalQuota,
    inWindow,
    clear,
    login,
    logout,
    refreshMe,
    refreshQuestions,
    refreshAll,
  }
})
