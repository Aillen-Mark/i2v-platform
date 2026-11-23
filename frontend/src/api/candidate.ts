/** 考生端接口封装（全部走真实 FastAPI，无任何假数据） */
import { http } from './http'
import type {
  CandidateMe,
  DownloadUrl,
  HealthStatus,
  MyWorks,
  QuestionPublic,
  SubmitResult,
  WorkDetail,
} from './types'

export async function login(account: string, password: string): Promise<CandidateMe> {
  const { data } = await http.post<CandidateMe>('/api/login', { account, password })
  return data
}

export async function logout(): Promise<void> {
  await http.post('/api/logout')
}

export async function fetchMe(): Promise<CandidateMe> {
  const { data } = await http.get<CandidateMe>('/api/me')
  return data
}

export async function fetchQuestions(): Promise<QuestionPublic[]> {
  const { data } = await http.get<QuestionPublic[]>('/api/questions')
  return data
}

export interface CreateWorkPayload {
  questionId: number
  prompt: string
  aspectRatio: string
  image: Blob
  filename: string
}

export async function createWork(payload: CreateWorkPayload): Promise<WorkDetail> {
  const form = new FormData()
  form.append('question_id', String(payload.questionId))
  form.append('prompt', payload.prompt)
  form.append('aspect_ratio', payload.aspectRatio)
  form.append('image', payload.image, payload.filename)
  const { data } = await http.post<WorkDetail>('/api/works', form)
  return data
}

export async function generateWork(workId: number): Promise<SubmitResult> {
  const { data } = await http.post<SubmitResult>(`/api/works/${workId}/generate`)
  return data
}

export async function fetchWork(workId: number): Promise<WorkDetail> {
  const { data } = await http.get<WorkDetail>(`/api/works/${workId}`)
  return data
}

export async function fetchMyWorks(): Promise<MyWorks> {
  const { data } = await http.get<MyWorks>('/api/me/works')
  return data
}

export async function requestDownloadUrl(workId: number): Promise<DownloadUrl> {
  const { data } = await http.get<DownloadUrl>(`/api/works/${workId}/download`)
  return data
}

/** 打包下载：拿二进制流 + 从 Content-Disposition 里解出规范文件名 */
export async function downloadAllZip(): Promise<{ blob: Blob; filename: string }> {
  const response = await http.get<Blob>('/api/me/download-all', { responseType: 'blob' })
  const disposition = String(response.headers['content-disposition'] || '')
  return { blob: response.data, filename: parseFilename(disposition) }
}

export function parseFilename(disposition: string): string {
  const star = /filename\*=UTF-8''([^;]+)/i.exec(disposition)
  if (star?.[1]) {
    try {
      return decodeURIComponent(star[1].trim().replace(/^"|"$/g, ''))
    } catch {
      /* 解码失败则回退下面的普通 filename */
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(disposition)
  return plain?.[1]?.trim() || 'download.zip'
}

export async function fetchHealth(): Promise<HealthStatus> {
  const { data } = await http.get<HealthStatus>('/healthz')
  return data
}
