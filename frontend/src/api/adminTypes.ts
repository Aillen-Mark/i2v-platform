/** 管理端接口的 TypeScript 类型（与 backend/app/schemas 对应） */

export interface AdminMe {
  id: number
  username: string
  role: string
  last_login_at: string | null
}

export interface CandidateRow {
  id: number
  account: string
  name: string
  status: number
  remark: string | null
  last_login_at: string | null
  last_login_ip: string | null
  created_at: string | null
  quota_total_sum: number
  quota_used_sum: number
  init_pwd_viewable: boolean
}

export interface PageResult<T> {
  total: number
  page: number
  size: number
  items: T[]
}

export interface ImportRowError {
  row: number
  name: string | null
  account: string | null
  reason: string
}

export interface ImportResult {
  batch_no: string
  total_rows: number
  success_count: number
  failed_count: number
  errors: ImportRowError[]
  elapsed_ms: number
}

export interface PasswordSlip {
  id: number
  account: string
  name: string
  password: string | null
  expire_at: string | null
  expired: boolean
}

export interface ResetPasswordItem {
  id: number
  account: string
  name: string
  password: string
}

export interface QuestionRow {
  id: number
  code: string
  title: string
  description: string
  ref_image_key: string | null
  ref_image_url: string | null
  prompt_examples: string[] | null
  default_quota: number
  sort_order: number
  status: number
  created_at: string | null
  updated_at: string | null
}

export interface QuotaRow {
  id: number
  candidate_id: number
  account: string
  name: string
  question_id: number
  question_code: string
  question_title: string
  quota_total: number
  quota_used: number
  quota_remaining: number
  adjusted_flag: boolean
  updated_at: string | null
}

export interface QuotaChange {
  updated: number
  blocked: number
  items: QuotaRow[]
}

export interface TimeWindowRow {
  id: number
  name: string
  start_at: string | null
  end_at: string | null
  status: number
  remark: string | null
  created_at: string | null
  updated_at: string | null
  is_active: boolean
  remaining_sec: number | null
}

export interface TimeWindowList {
  server_time: string | null
  active_window_id: number | null
  total: number
  items: TimeWindowRow[]
}

export interface EngineHealth {
  recent_success: number
  recent_fail: number
  avg_cost_sec: number | null
  circuit_open: boolean
  source: string
}

export interface EngineRow {
  id: number
  code: string
  name: string
  provider: string
  model_name: string
  resolution: string
  duration: number
  price_per_sec: string | number
  max_concurrency: number
  is_default: boolean
  status: number
  updated_at: string | null
  health: EngineHealth
  fail_rate: number
  is_healthy: boolean
}

export interface StatsOverview {
  candidates_total: number
  works_total: number
  succeeded: number
  failed: number
  pending: number
  success_rate: number
  total_duration_sec: number
  estimated_cost: string | number
}

export interface StatsByCandidate {
  candidate_id: number
  account: string
  name: string
  status: number
  quota_total: number
  quota_used: number
  quota_remaining: number
  success_count: number
  failed_count: number
  last_active_at: string | null
}

export interface StatsByQuestion {
  question_id: number
  code: string
  title: string
  status: number
  total: number
  succeeded: number
  failed: number
  fail_rate: number
  avg_cost_sec: number | null
}

export interface StatsByEngine {
  code: string
  name: string
  resolution: string
  calls: number
  succeeded: number
  total_duration_sec: number
  share_rate: number
  cost: string | number
}

export interface StatsByDay {
  date: string
  total: number
  succeeded: number
  failed: number
  fail_rate: number
}

export interface VideoLink {
  filename: string
  account: string
  question_code: string
  download_url: string
  expires_in: number
}

export interface AuditLogRow {
  id: number
  admin_id: number
  admin_username: string
  action: string
  target_type: string
  target_id: string | null
  before_val: unknown
  after_val: unknown
  remark: string | null
  ip_address: string | null
  created_at: string | null
}
