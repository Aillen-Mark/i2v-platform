/** 后端接口的 TypeScript 类型定义（与 backend/app/schemas 一一对应） */

export interface ApiError {
  code: string
  message: string
  detail?: unknown
}

export interface CandidateMe {
  id: number
  account: string
  name: string
  status: number
  last_login_at: string | null
  total_quota: number
  total_used: number
  total_remaining: number
  in_window: boolean
  current_window_remaining_sec: number | null
  next_window_start: string | null
  current_window_end: string | null
}

export interface QuestionPublic {
  id: number
  code: string
  title: string
  description: string
  ref_image_url: string | null
  prompt_examples: string[] | null
  default_quota: number
  quota_total: number
  quota_used: number
  remaining: number
  adjusted_flag: boolean
}

export type WorkStatus = 'draft' | 'queued' | 'running' | 'succeeded' | 'failed'

export interface WorkDetail {
  id: number
  task_no: string
  candidate_id: number
  question_id: number
  seq: number | null
  prompt: string
  aspect_ratio: string | null
  task_status: WorkStatus
  status_text: string
  engine_code: string | null
  image_url: string | null
  video_url: string | null
  error_code: string | null
  error_msg: string | null
  submitted_at: string | null
  finished_at: string | null
  created_at: string | null
  queue_ahead: number | null
  estimated_wait_sec: number | null
  quota_remaining: number | null
}

export interface SubmitResult {
  id: number
  task_no: string
  task_status: WorkStatus
  status_text: string
  seq: number | null
  engine_code: string | null
  quota_remaining: number | null
  queue_ahead: number | null
  estimated_wait_sec: number | null
  idempotent: boolean
  message: string
}

export interface WorkItem {
  id: number
  task_no: string
  question_id: number
  question_code: string
  question_title: string
  seq: number | null
  task_status: WorkStatus
  status_text: string
  prompt: string
  engine_code: string | null
  error_code: string | null
  error_msg: string | null
  duration_sec: number | null
  file_size: number | null
  created_at: string | null
  submitted_at: string | null
  finished_at: string | null
  preview_url: string | null
  download_ready: boolean
}

export interface QuestionGroup {
  question_id: number
  question_code: string
  question_title: string
  total: number
  works: WorkItem[]
}

export interface MyWorks {
  total: number
  groups: QuestionGroup[]
  items: WorkItem[]
}

export interface DownloadUrl {
  download_url: string
  filename: string
  expires_in: number
  file_size: number | null
}

export interface HealthStatus {
  status: string
  mysql: boolean
  redis: boolean
}
