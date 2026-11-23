/** 管理端接口封装（全部真实 FastAPI，无假数据） */
import { http } from './http'
import { parseFilename } from './candidate'
import type {
  AdminMe,
  AuditLogRow,
  CandidateRow,
  EngineRow,
  ImportResult,
  PageResult,
  PasswordSlip,
  QuestionRow,
  QuotaChange,
  QuotaRow,
  ResetPasswordItem,
  StatsByCandidate,
  StatsByDay,
  StatsByEngine,
  StatsByQuestion,
  StatsOverview,
  TimeWindowList,
  TimeWindowRow,
  VideoLink,
} from './adminTypes'

// ---------- 认证 ----------
export async function adminLogin(username: string, password: string): Promise<AdminMe> {
  const { data } = await http.post<AdminMe>('/admin/auth/login', { username, password })
  return data
}
export async function adminLogout(): Promise<void> {
  await http.post('/admin/auth/logout')
}
export async function adminMe(): Promise<AdminMe> {
  const { data } = await http.get<AdminMe>('/admin/auth/me')
  return data
}

// ---------- 考生 ----------
export interface CandidateQuery {
  page?: number
  size?: number
  keyword?: string
  status?: number | null
}
export async function listCandidates(query: CandidateQuery): Promise<PageResult<CandidateRow>> {
  const { data } = await http.get<PageResult<CandidateRow>>('/admin/candidates', { params: query })
  return data
}
export async function createCandidate(payload: { name: string; account?: string | null; remark?: string | null }) {
  const { data } = await http.post<ResetPasswordItem>('/admin/candidates', payload)
  return data
}
export async function updateCandidate(
  id: number,
  payload: { name?: string; remark?: string | null; status?: number; account?: string },
) {
  const { data } = await http.put<CandidateRow>(`/admin/candidates/${id}`, payload)
  return data
}
export async function deleteCandidate(id: number): Promise<void> {
  await http.delete(`/admin/candidates/${id}`)
}
export async function downloadTemplate(): Promise<Blob> {
  const { data } = await http.get<Blob>('/admin/candidates/template', { responseType: 'blob' })
  return data
}
export async function importCandidates(file: File): Promise<ImportResult> {
  const form = new FormData()
  form.append('file', file, file.name)
  const { data } = await http.post<ImportResult>('/admin/candidates/import', form, { timeout: 120_000 })
  return data
}
export async function exportCandidates(format: 'xlsx' | 'csv', ids?: number[]): Promise<{ blob: Blob; filename: string }> {
  const params: Record<string, string> = { format }
  if (ids && ids.length) params.ids = ids.join(',')
  const response = await http.get<Blob>('/admin/candidates/export', { params, responseType: 'blob' })
  return { blob: response.data, filename: parseFilename(String(response.headers['content-disposition'] || '')) }
}
export async function fetchPasswordSlips(ids?: number[]): Promise<PasswordSlip[]> {
  const params: Record<string, string> = {}
  if (ids && ids.length) params.ids = ids.join(',')
  const { data } = await http.get<PasswordSlip[]>('/admin/candidates/password-slips', { params })
  return data
}
export async function viewInitPassword(id: number): Promise<PasswordSlip> {
  const { data } = await http.get<PasswordSlip>(`/admin/candidates/${id}/init-password`)
  return data
}
export async function resetPassword(id: number): Promise<ResetPasswordItem> {
  const { data } = await http.post<ResetPasswordItem>(`/admin/candidates/${id}/reset-password`)
  return data
}
export async function batchResetPassword(ids: number[]): Promise<ResetPasswordItem[]> {
  const { data } = await http.post<{ items: ResetPasswordItem[] }>('/admin/candidates/batch-reset-password', {
    candidate_ids: ids,
  })
  return data.items
}
export async function batchStatus(ids: number[], status: 0 | 1): Promise<void> {
  await http.post('/admin/candidates/batch-status', { candidate_ids: ids, status })
}

// ---------- 题目 ----------
export async function listQuestions(): Promise<QuestionRow[]> {
  const { data } = await http.get<QuestionRow[]>('/admin/questions')
  return data
}
export interface QuestionPayload {
  code?: string
  title: string
  description: string
  default_quota: number
  sort_order: number
  status: number
  prompt_examples?: string[] | null
  ref_image_key?: string | null
}
export async function createQuestion(payload: QuestionPayload): Promise<QuestionRow> {
  const { data } = await http.post<QuestionRow>('/admin/questions', payload)
  return data
}
export async function updateQuestion(id: number, payload: Partial<QuestionPayload>): Promise<QuestionRow> {
  const { data } = await http.put<QuestionRow>(`/admin/questions/${id}`, payload)
  return data
}
export async function deleteQuestion(id: number, soft = false): Promise<void> {
  await http.delete(`/admin/questions/${id}`, { params: soft ? { soft: true } : {} })
}
export async function uploadRefImage(questionId: number, file: File): Promise<QuestionRow> {
  const form = new FormData()
  form.append('question_id', String(questionId))
  form.append('file', file, file.name)
  const { data } = await http.post<QuestionRow>('/admin/questions/upload-ref-image', form)
  return data
}

// ---------- 配额 ----------
export interface QuotaQuery {
  page?: number
  size?: number
  keyword?: string
  candidate_id?: number
  question_id?: number
}
export async function listQuotas(query: QuotaQuery): Promise<PageResult<QuotaRow>> {
  const { data } = await http.get<PageResult<QuotaRow>>('/admin/quotas', { params: query })
  return data
}
export async function batchByQuestion(payload: {
  question_id: number
  quota_total: number
  force_confirm?: boolean
  remark?: string
}): Promise<QuotaChange> {
  const { data } = await http.post<QuotaChange>('/admin/quotas/batch-by-question', payload)
  return data
}
export async function byCandidate(
  candidateId: number,
  payload: {
    items?: { question_id: number; quota_total: number }[]
    uniform_quota?: number
    force_confirm?: boolean
    remark?: string
  },
): Promise<QuotaChange> {
  const { data } = await http.put<QuotaChange>(`/admin/quotas/by-candidate/${candidateId}`, payload)
  return data
}
export async function batchAdjust(payload: {
  candidate_ids: number[]
  question_id: number
  delta: number
  force_confirm?: boolean
  remark?: string
}): Promise<QuotaChange> {
  const { data } = await http.post<QuotaChange>('/admin/quotas/batch-adjust', payload)
  return data
}

// ---------- 时段 ----------
export async function listTimeWindows(): Promise<TimeWindowList> {
  const { data } = await http.get<TimeWindowList>('/admin/time-windows')
  return data
}
export async function createTimeWindow(payload: {
  name: string
  start_at: string
  end_at: string
  status: number
  remark?: string | null
}): Promise<TimeWindowRow> {
  const { data } = await http.post<TimeWindowRow>('/admin/time-windows', payload)
  return data
}
export async function updateTimeWindow(id: number, payload: Partial<TimeWindowRow>): Promise<TimeWindowRow> {
  const { data } = await http.put<TimeWindowRow>(`/admin/time-windows/${id}`, payload)
  return data
}
export async function deleteTimeWindow(id: number): Promise<void> {
  await http.delete(`/admin/time-windows/${id}`)
}
export async function extendTimeWindow(
  id: number,
  payload: { new_end_at?: string; extend_minutes?: number; confirm: boolean; remark?: string },
): Promise<TimeWindowRow> {
  const { data } = await http.post<TimeWindowRow>(`/admin/time-windows/${id}/extend`, payload)
  return data
}

// ---------- 引擎 ----------
export async function listEngines(): Promise<EngineRow[]> {
  const { data } = await http.get<EngineRow[]>('/admin/engines')
  return data
}
export async function updateEngine(
  id: number,
  payload: { name?: string; price_per_sec?: string | number; max_concurrency?: number; status?: number },
): Promise<EngineRow> {
  const { data } = await http.put<EngineRow>(`/admin/engines/${id}`, payload)
  return data
}
export async function setDefaultEngine(id: number): Promise<EngineRow> {
  const { data } = await http.post<EngineRow>(`/admin/engines/${id}/set-default`)
  return data
}

// ---------- 统计 ----------
export async function statsOverview(): Promise<StatsOverview> {
  const { data } = await http.get<StatsOverview>('/admin/stats/overview')
  return data
}
export async function statsByCandidate(params: { page?: number; size?: number; keyword?: string }) {
  const { data } = await http.get<PageResult<StatsByCandidate>>('/admin/stats/by-candidate', { params })
  return data
}
export async function statsByQuestion(): Promise<StatsByQuestion[]> {
  const { data } = await http.get<StatsByQuestion[]>('/admin/stats/by-question')
  return data
}
export async function statsByEngine(): Promise<StatsByEngine[]> {
  const { data } = await http.get<StatsByEngine[]>('/admin/stats/by-engine')
  return data
}
export async function statsByDay(days = 30): Promise<StatsByDay[]> {
  const { data } = await http.get<StatsByDay[]>('/admin/stats/by-day', { params: { days } })
  return data
}
export async function exportStatsCsv(dimension: 'works' | 'candidates') {
  const response = await http.get<Blob>('/admin/stats/export-csv', {
    params: { dimension },
    responseType: 'blob',
  })
  return { blob: response.data, filename: parseFilename(String(response.headers['content-disposition'] || '')) }
}
export async function exportVideoLinks(params: {
  question_id?: number
  candidate_id?: number
  format?: 'json' | 'csv'
}): Promise<VideoLink[] | { blob: Blob; filename: string }> {
  if (params.format === 'csv') {
    const response = await http.get<Blob>('/admin/stats/export-video-links', {
      params: { ...params, format: 'csv' },
      responseType: 'blob',
    })
    return { blob: response.data, filename: parseFilename(String(response.headers['content-disposition'] || '')) }
  }
  const { data } = await http.get<VideoLink[]>('/admin/stats/export-video-links', { params })
  return data
}

// ---------- 审计日志 ----------
export interface AuditQuery {
  page?: number
  size?: number
  admin_username?: string
  action?: string
  target_type?: string
  start_at?: string
  end_at?: string
}
export async function listAuditLogs(query: AuditQuery): Promise<PageResult<AuditLogRow>> {
  const { data } = await http.get<PageResult<AuditLogRow>>('/admin/audit-logs', { params: query })
  return data
}
