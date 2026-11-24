<template>
  <div class="workspace page">
    <div class="shell">
      <!-- 顶部：任务说明与剩余次数 -->
      <header class="hero panel">
        <div class="hero-main">
          <h1 class="hero-title">{{ question?.title || '图生视频创作' }}</h1>
          <p class="hero-desc">{{ question?.description || '请上传首帧图片并输入提示词生成 15 秒视频作品' }}</p>
        </div>
        <div class="hero-side">
          <el-tag
            size="large"
            :type="remaining > 0 ? 'success' : 'info'"
            effect="dark"
            class="quota-tag"
          >
            剩余 {{ remaining }} / 共 {{ total }} 次
          </el-tag>
          <div class="muted mt-8" v-if="!auth.inWindow">{{ windowBlockedText }}</div>
        </div>
      </header>

      <!-- 创作卡片 -->
      <section class="panel card">
        <el-alert
          v-if="restoredNotice"
          type="info"
          :closable="true"
          show-icon
          class="restore-alert"
          @close="restoredNotice = false"
        >
          <template #title>
            已恢复上次未提交草稿（{{ restoredAt }}）
            <el-button link type="primary" size="small" @click="clearDraftNow">清空草稿</el-button>
          </template>
        </el-alert>

        <div class="block-label">首帧图片（JPG / PNG / WEBP，≤10MB，长边自动压到 1920px）</div>
        <div
          class="dropzone"
          :class="{ dragging }"
          @dragover.prevent="dragging = true"
          @dragleave.prevent="dragging = false"
          @drop.prevent="onDrop"
          @click="pickFile"
        >
          <template v-if="image">
            <img :src="image.dataUrl" alt="首帧预览" class="preview" />
            <div class="preview-meta muted">
              {{ image.originalWidth }}×{{ image.originalHeight }}
              <template v-if="image.compressed"> → 已压缩 {{ image.width }}×{{ image.height }}</template>
              · {{ formatSize(image.size) }}
            </div>
          </template>
          <template v-else>
            <div class="drop-hint">点击选择或把图片拖到这里</div>
            <div class="muted">支持 JPG / PNG / WEBP，最大 10MB</div>
          </template>
        </div>
        <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp" class="hidden-input" @change="onFileChange" />

        <div class="block-label mt-16">提示词（{{ prompt.length }} / 300）</div>
        <el-input
          v-model="prompt"
          type="textarea"
          :rows="5"
          maxlength="300"
          show-word-limit
          resize="none"
          placeholder="描述你想要的画面，例如：一只橘猫在雪地里奔跑，夕阳逆光，电影质感"
        />

        <div class="ratio-row mt-12">
          <span class="muted">画幅</span>
          <el-tag v-if="image" type="success" effect="plain">{{ detectedRatio }}</el-tag>
          <el-tag v-else type="info" effect="plain">上传首帧后自动匹配</el-tag>
          <span class="muted">{{ ratioHint }}</span>
          <el-button link type="primary" size="small" @click="clearDraftNow">清空草稿</el-button>
        </div>

        <el-alert
          v-if="!auth.inWindow"
          type="warning"
          :closable="false"
          show-icon
          class="mt-16"
          :title="windowBlockedText"
        />
        <el-alert
          v-else-if="remaining <= 0"
          type="error"
          :closable="false"
          show-icon
          class="mt-16"
          title="生成次数已用完，无法继续提交"
        />

        <el-button
          type="primary"
          size="large"
          class="submit"
          :loading="submitting"
          :disabled="!canSubmit"
          @click="submit"
        >
          {{ submitting ? '提交中…' : '提交生成（消耗 1 次机会）' }}
        </el-button>
      </section>

      <!-- 任务进度卡片 -->
      <section v-if="task" class="panel card mt-16">
        <div class="card-head">
          <span class="card-title">本次生成进度</span>
          <span class="muted mono">{{ task.task_no }}</span>
        </div>

        <div v-if="task.task_status === 'queued'" class="state">
          <div class="state-title">排队中</div>
          <div class="muted">前面还有 {{ task.queue_ahead ?? 0 }} 人，预计等待约 {{ formatSeconds(task.estimated_wait_sec) }}</div>
          <el-progress :percentage="35" :show-text="false" status="warning" :stroke-width="8" class="mt-8" />
        </div>

        <div v-else-if="task.task_status === 'running'" class="state">
          <div class="state-title">模型正在生成视频中…</div>
          <div class="muted">通常需要 1~3 分钟，请保持页面打开</div>
          <el-progress :percentage="70" :show-text="false" :indeterminate="true" :duration="2" :stroke-width="8" class="mt-8" />
        </div>

        <div v-else-if="task.task_status === 'succeeded'" class="state">
          <div class="state-title text-success">出片成功，可在线预览与下载</div>
          <video v-if="task.video_url" :src="task.video_url" controls class="video" preload="metadata" />
          <div class="mt-12 flex gap-8">
            <el-button type="primary" @click="downloadTask">下载 MP4</el-button>
            <el-button @click="goMyWorks">查看我的作品</el-button>
          </div>
        </div>

        <div v-else-if="task.task_status === 'failed'" class="state">
          <div class="state-title text-danger">生成失败</div>
          <div class="text-danger">{{ task.error_msg || '未知错误' }}</div>
          <div class="muted mt-8">本次消耗的 1 次机会已自动退还，可重新提交。</div>
        </div>

        <div v-else class="state">
          <div class="state-title">草稿已创建</div>
          <div class="muted">等待提交生成</div>
        </div>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'
import { createWork, fetchMyWorks, fetchWork, generateWork, requestDownloadUrl } from '@/api/candidate'
import type { QuestionPublic, WorkDetail } from '@/api/types'
import { clearDraft, draftKey, loadDraft, saveDraft } from '@/utils/draft'
import { compressImage, dataUrlToBlob } from '@/utils/image'
import { formatDateTime, formatLocal, formatSeconds, formatSize } from '@/utils/format'

interface LocalImage {
  blob: Blob | null
  dataUrl: string
  filename: string
  width: number
  height: number
  originalWidth: number
  originalHeight: number
  originalSize: number
  size: number
  compressed: boolean
}

const auth = useAuthStore()
const router = useRouter()

/** 单题模式：静默绑定唯一启用题目，不做任何题目切换交互 */
const question = computed<QuestionPublic | null>(() => auth.questions[0] ?? null)
const remaining = computed(() => question.value?.remaining ?? 0)
const total = computed(() => question.value?.quota_total ?? 0)

/**
 * 画幅自适应首帧：按上传图片的实际宽高比，就近匹配后端允许的 5 种画幅。
 * 用 log 距离比较，横图/竖图两个方向对称，不会把 3:2 判成 9:16。
 */
const ALLOWED_RATIOS = ['16:9', '4:3', '1:1', '3:4', '9:16'] as const

const detectedRatio = computed<string>(() => {
  const img = image.value
  if (!img || !img.width || !img.height) return '16:9'
  const actual = Math.log(img.width / img.height)
  let best: string = ALLOWED_RATIOS[0]
  let bestGap = Number.POSITIVE_INFINITY
  for (const candidate of ALLOWED_RATIOS) {
    const [w, h] = candidate.split(':').map(Number)
    const gap = Math.abs(actual - Math.log(w / h))
    if (gap < bestGap) {
      bestGap = gap
      best = candidate
    }
  }
  return best
})

const ratioHint = computed(() => {
  const img = image.value
  if (!img) return '按首帧图片比例自动决定，无需手动选择'
  const [w, h] = detectedRatio.value.split(':').map(Number)
  const actual = img.width / img.height
  const deviation = Math.abs(actual - w / h) / actual
  if (deviation < 0.02) return `与首帧 ${img.width}×${img.height} 一致`
  if (deviation > 0.15) {
    return `首帧 ${img.width}×${img.height} 与 ${detectedRatio.value} 相差 ${Math.round(deviation * 100)}%，成片会按 ${detectedRatio.value} 裁切或补边`
  }
  return `首帧 ${img.width}×${img.height}，就近匹配 ${detectedRatio.value}`
})

const prompt = ref('')
const image = ref<LocalImage | null>(null)
const dragging = ref(false)
const submitting = ref(false)
const restoredNotice = ref(false)
const restoredAt = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const task = ref<WorkDetail | null>(null)
let pollTimer: number | null = null
let saveTimer: number | null = null

const windowBlockedText = computed(() => {
  const next = auth.me?.next_window_start
  return next
    ? `当前不在开放时段，无法提交。下次开放时间：${formatDateTime(next)}`
    : '当前不在开放时段，无法提交，请等待管理员开放'
})

const canSubmit = computed(() => {
  if (!question.value || !image.value || submitting.value) return false
  if (!auth.inWindow) return false
  if (question.value.remaining <= 0) return false
  const text = prompt.value.trim()
  return text.length > 0 && text.length <= 300
})

onMounted(async () => {
  if (!auth.questions.length) {
    await auth.refreshAll()
  }
  restoreDraft()
  await resumeInflight()
})

onBeforeUnmount(() => {
  stopPolling()
  if (saveTimer) window.clearTimeout(saveTimer)
})

function draftStorageKey(): string | null {
  const me = auth.me
  if (!me || !question.value) return null
  return draftKey(me.id, question.value.id)
}

function restoreDraft(): void {
  const key = draftStorageKey()
  if (!key) return
  const draft = loadDraft(key)
  if (!draft) return
  prompt.value = draft.prompt || ''
  if (draft.imageDataUrl) {
    image.value = {
      blob: null,
      dataUrl: draft.imageDataUrl,
      filename: draft.imageName || 'draft.jpg',
      width: draft.imageWidth ?? 0,
      height: draft.imageHeight ?? 0,
      originalWidth: draft.imageWidth ?? 0,
      originalHeight: draft.imageHeight ?? 0,
      originalSize: 0,
      size: 0,
      compressed: false,
    }
  }
  restoredAt.value = formatLocal(draft.savedAt)
  restoredNotice.value = true
}

function persistDraft(): void {
  const key = draftStorageKey()
  if (!key) return
  if (!prompt.value.trim() && !image.value) return
  const result = saveDraft(key, {
    prompt: prompt.value,
    imageDataUrl: image.value?.dataUrl ?? null,
    imageName: image.value?.filename ?? null,
    imageWidth: image.value?.width ?? null,
    imageHeight: image.value?.height ?? null,
    savedAt: Date.now(),
  })
  if (result === 'text-only') {
    ElMessage.warning('本地存储空间不足，已仅保存提示词（图片未暂存）')
  }
}

watch([prompt, image], () => {
  if (saveTimer) window.clearTimeout(saveTimer)
  saveTimer = window.setTimeout(persistDraft, 600)
})

function clearDraftNow(): void {
  const key = draftStorageKey()
  if (key) clearDraft(key)
  prompt.value = ''
  image.value = null
  restoredNotice.value = false
  ElMessage.success('已清空草稿')
}


function pickFile(): void {
  fileInput.value?.click()
}

function onFileChange(event: Event): void {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (file) void handleFile(file)
  target.value = ''
}

function onDrop(event: DragEvent): void {
  dragging.value = false
  const file = event.dataTransfer?.files?.[0]
  if (file) void handleFile(file)
}

async function handleFile(file: File): Promise<void> {
  try {
    const result = await compressImage(file)
    image.value = {
      blob: result.blob,
      dataUrl: result.dataUrl,
      filename: result.filename,
      width: result.width,
      height: result.height,
      originalWidth: result.originalWidth,
      originalHeight: result.originalHeight,
      originalSize: result.originalSize,
      size: result.size,
      compressed: result.compressed,
    }
    if (result.compressed) {
      ElMessage.success(
        `已压缩：${result.originalWidth}×${result.originalHeight} → ${result.width}×${result.height}（${formatSize(result.size)}）`,
      )
    } else {
      ElMessage.info(`图片长边 ${result.originalWidth}px，未超过 1920px，保持原图上传`)
    }
    persistDraft()
  } catch (error) {
    ElMessage.error((error as Error).message || '图片处理失败')
  }
}

async function submit(): Promise<void> {
  if (!canSubmit.value || !question.value || !image.value) return
  const me = auth.me
  if (!me) return

  submitting.value = true
  try {
    const blob = image.value.blob ?? dataUrlToBlob(image.value.dataUrl)
    const work = await createWork({
      questionId: question.value.id,
      prompt: prompt.value.trim(),
      aspectRatio: detectedRatio.value,
      image: blob,
      filename: image.value.filename,
    })
    const result = await generateWork(work.id)
    ElMessage.success(result.message || '已提交生成')
    task.value = {
      ...work,
      task_status: result.task_status,
      status_text: result.status_text,
      seq: result.seq,
      engine_code: result.engine_code,
      queue_ahead: result.queue_ahead,
      estimated_wait_sec: result.estimated_wait_sec,
      quota_remaining: result.quota_remaining,
    }
    clearDraft(draftKey(me.id, question.value.id))
    prompt.value = ''
    image.value = null
    restoredNotice.value = false
    await auth.refreshAll()
    startPolling(work.id)
  } catch {
    // 拦截器已统一提示（403 时段外 / 409 配额不足等）
  } finally {
    submitting.value = false
  }
}

function startPolling(workId: number): void {
  stopPolling()
  pollTimer = window.setInterval(async () => {
    try {
      const detail = await fetchWork(workId)
      task.value = detail
      if (detail.task_status === 'succeeded' || detail.task_status === 'failed') {
        stopPolling()
        await auth.refreshAll()
      }
    } catch {
      stopPolling()
    }
  }, 3000)
}

function stopPolling(): void {
  if (pollTimer) {
    window.clearInterval(pollTimer)
    pollTimer = null
  }
}

async function resumeInflight(): Promise<void> {
  try {
    const works = await fetchMyWorks()
    const inflight = works.items.find((item) => item.task_status === 'queued' || item.task_status === 'running')
    if (!inflight) return
    task.value = await fetchWork(inflight.id)
    ElMessage.info('检测到进行中的任务，已自动恢复进度跟踪')
    startPolling(inflight.id)
  } catch {
    /* 恢复失败不影响正常使用 */
  }
}

async function downloadTask(): Promise<void> {
  if (!task.value) return
  try {
    const data = await requestDownloadUrl(task.value.id)
    const anchor = document.createElement('a')
    anchor.href = data.download_url
    anchor.rel = 'noopener'
    anchor.target = '_blank'
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    ElMessage.success(`开始下载：${data.filename}`)
  } catch {
    /* 拦截器已提示 */
  }
}

async function goMyWorks(): Promise<void> {
  await router.push('/my-works')
}
</script>

<style scoped>
.workspace {
  padding: 24px 20px 48px;
}

.shell {
  max-width: 1000px;
  margin: 0 auto;
}

.hero {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 20px;
  padding: 20px 24px;
  margin-bottom: 16px;
  background: linear-gradient(135deg, #eff6ff 0%, #ffffff 60%);
}

.hero-title {
  font-size: 22px;
  margin: 0 0 8px;
  font-weight: 700;
}

.hero-desc {
  margin: 0;
  color: var(--i2v-text-sub);
  line-height: 1.6;
  font-size: 13px;
}

.hero-side {
  text-align: right;
  flex: 0 0 auto;
}

.quota-tag {
  font-weight: 700;
  font-variant-numeric: tabular-nums;
}

.card {
  padding: 20px 24px 24px;
}

.card-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 14px;
}

.card-title {
  font-size: 15px;
  font-weight: 600;
}

.block-label {
  font-size: 12px;
  color: var(--i2v-text-sub);
  margin-bottom: 8px;
}

.dropzone {
  border: 2px dashed var(--i2v-border);
  border-radius: 10px;
  min-height: 180px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 16px;
  cursor: pointer;
  background: #fcfcfd;
  transition: all 0.15s;
}

.dropzone.dragging,
.dropzone:hover {
  border-color: var(--i2v-primary);
  background: #f5f9ff;
}

.preview {
  max-width: 100%;
  max-height: 220px;
  border-radius: 8px;
  object-fit: contain;
}

.drop-hint {
  font-size: 14px;
}

.preview-meta {
  font-size: 12px;
}

.hidden-input {
  display: none;
}


.ratio-row {
  display: flex;
  align-items: center;
  gap: 10px;
}

.submit {
  width: 100%;
  margin-top: 20px;
  height: 46px;
  font-size: 15px;
}

.restore-alert {
  margin-bottom: 16px;
}

.state-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 6px;
}

.video {
  width: 100%;
  max-height: 460px;
  border-radius: 8px;
  background: #000;
  margin-top: 10px;
}
</style>
