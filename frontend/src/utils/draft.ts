/**
 * 本地草稿：按 `candidate_id + question_id` 隔离存在 localStorage。
 * 图片以 DataURL 形式暂存；容量超限（QuotaExceededError）时降级为只存提示词，并给出提示。
 */

export interface DraftPayload {
  prompt: string
  imageDataUrl: string | null
  imageName: string | null
  imageWidth: number | null
  imageHeight: number | null
  savedAt: number
}

const PREFIX = 'i2v:draft'

export function draftKey(candidateId: number, questionId: number): string {
  return `${PREFIX}:${candidateId}:${questionId}`
}

export function saveDraft(key: string, payload: DraftPayload): 'ok' | 'text-only' | 'failed' {
  try {
    localStorage.setItem(key, JSON.stringify(payload))
    return 'ok'
  } catch {
    // 常见于 LocalStorage 5MB 上限：退化为只保存提示词，保证文字不丢
    try {
      localStorage.setItem(key, JSON.stringify({ ...payload, imageDataUrl: null }))
      return 'text-only'
    } catch {
      return 'failed'
    }
  }
}

export function loadDraft(key: string): DraftPayload | null {
  try {
    const raw = localStorage.getItem(key)
    if (!raw) return null
    const parsed = JSON.parse(raw) as DraftPayload
    if (!parsed || typeof parsed.prompt !== 'string') return null
    return parsed
  } catch {
    return null
  }
}

export function clearDraft(key: string): void {
  try {
    localStorage.removeItem(key)
  } catch {
    /* 忽略清理失败 */
  }
}
