/**
 * Axios 实例与全局错误拦截。
 *
 * 统一处理：
 * - 401：会话过期 / 被同账号后登录踢下线 → 清本地态并跳登录页
 * - 403：不在开放时段 / 账号被禁用
 * - 409：本题次数已用完（配额不足）
 * - 410：初始密码过期等
 * - 429：登录锁定
 */
import axios, { type AxiosError } from 'axios'
import { ElMessage } from 'element-plus'

import type { ApiError } from './types'

export const http = axios.create({
  baseURL: '/',
  timeout: 60_000,
  withCredentials: true,
})

type UnauthorizedHandler = (scope: 'candidate' | 'admin') => void
let unauthorizedHandler: UnauthorizedHandler | null = null

export function setUnauthorizedHandler(handler: UnauthorizedHandler): void {
  unauthorizedHandler = handler
}

const SILENT_401_PATHS = new Set(['/api/login', '/admin/auth/login'])

http.interceptors.response.use(
  (response) => response,
  (error: AxiosError<ApiError>) => {
    const status = error.response?.status
    const body = error.response?.data
    const message = body?.message || '网络异常，请检查后端服务是否已启动'
    const url = error.config?.url || ''
    // 双端鉴权隔离：/admin/* 属管理端，其余属考生端
    const scope: 'candidate' | 'admin' = url.startsWith('/admin') ? 'admin' : 'candidate'

    if (!error.response) {
      ElMessage.error('无法连接服务器，请确认后端 uvicorn 已在 127.0.0.1:8000 运行')
      return Promise.reject(error)
    }

    if (status === 401) {
      if (SILENT_401_PATHS.has(url)) {
        ElMessage.error(message)
      } else {
        unauthorizedHandler?.(scope)
        ElMessage.warning(
          scope === 'admin' ? '管理端登录已失效，请重新登录' : '登录状态已失效，请重新登录',
        )
      }
    } else if (status === 403) {
      ElMessage.warning(message)
    } else if (status === 409) {
      ElMessage.warning(message)
    } else if (status === 410) {
      ElMessage.info(message)
    } else if (status === 422) {
      ElMessage.error(message)
    } else if (status === 429) {
      ElMessage.error(message)
    } else {
      ElMessage.error(message)
    }
    return Promise.reject(error)
  },
)
