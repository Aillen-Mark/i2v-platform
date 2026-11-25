<template>
  <div class="workspace">
    <!-- X 型对角斜切灰白几何背景 + 超大淡英文水印（全部 CSS/SVG 绘制，无外链） -->
    <div class="geo-bg" aria-hidden="true">
      <svg class="geo-svg" viewBox="0 0 1200 760" preserveAspectRatio="none">
        <rect width="1200" height="760" fill="#FFFFFF" />
        <polygon points="0,0 1200,0 1200,150 0,410" fill="#EAECEF" />
        <polygon points="0,760 1200,760 1200,610 0,350" fill="#EEF1F5" />
        <polygon points="0,410 1200,150 1200,205 0,465" fill="#E5E9F0" opacity="0.8" />
        <polygon points="0,350 1200,610 1200,665 0,405" fill="#E5E9F0" opacity="0.7" />
      </svg>
      <div class="watermark">AI VIDEO STUDIO</div>
    </div>

    <div class="shell">
      <!-- 居中主标题组 -->
      <header class="stage-title">
        <div class="eyebrow"><span class="eyebrow-line" />CREATION STUDIO · 单题创作任务<span class="eyebrow-line" /></div>
        <h2 class="stage-h2">{{ question?.title || '图生视频创意作品制作' }}</h2>
        <div class="title-rule"><span /></div>
        <p class="stage-sub">{{ question?.description || '请上传首帧图片并输入提示词生成 15 秒视频作品' }}</p>
      </header>

      <!-- ================= 正中双步骤创作主工作舱 ================= -->
      <section class="studio panel">
        <div class="studio-top" />
        <div class="studio-intro">
          <span class="intro-badge">创作舱</span>
          <span class="muted">按 01 / 02 两步完成素材与镜头描述，确认后提交生成</span>
          <span class="quota-pill">剩余创作机会 <b>{{ remaining }}</b> / {{ total }} 次</span>
        </div>

        <div class="studio-grid">
          <!-- 01 首帧视觉素材 -->
          <div class="step">
            <div class="step-head">
              <span class="step-num">01</span>
              <div>
                <div class="step-title">首帧视觉素材</div>
                <div class="muted">JPG / PNG / WEBP，≤10MB，长边自动压至 1920px</div>
              </div>
            </div>

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
                <svg class="upload-icon" width="42" height="42" viewBox="0 0 42 42" aria-hidden="true">
                  <polygon points="21,2 39,12 39,30 21,40 3,30 3,12" fill="#E3F5F8" stroke="#0085A1" stroke-width="1.5" />
                  <path d="M21 13 L21 28 M14 21 L21 14 L28 21" fill="none" stroke="#0085A1" stroke-width="2" stroke-linecap="square" />
                </svg>
                <div class="drop-hint">点击选择或把图片拖到这里</div>
                <div class="muted">首帧决定画面基调，建议使用清晰横/竖构图</div>
              </template>
            </div>
            <input ref="fileInput" type="file" accept="image/jpeg,image/png,image/webp" class="hidden-input" @change="onFileChange" />

            <div class="asset-meta">
              <span class="ratio-label">画幅 <b>{{ detectedRatio }}</b></span>
              <span class="muted ratio-hint">{{ ratioHint }}</span>
              <button v-if="image" type="button" class="reselect" @click="pickFile">重新选图</button>
            </div>
          </div>

          <!-- 02 镜头动态提示词 -->
          <div class="step">
            <div class="step-head">
              <span class="step-num">02</span>
              <div>
                <div class="step-title">镜头动态提示词</div>
                <div class="muted">描述运镜、光线与主体动作，≤300 字</div>
              </div>
            </div>

            <el-input
              v-model="prompt"
              type="textarea"
              :rows="8"
              maxlength="300"
              show-word-limit
              resize="none"
              placeholder="例如：镜头缓慢推近，一只橘猫在雪地里奔跑，夕阳逆光，电影质感"
            />

            <div class="draft-hint muted">
              <span class="draft-dot" />本地草稿自动保存，刷新或断线后可继续编辑
              <button type="button" class="reselect" @click="clearDraftNow">清空草稿</button>
            </div>
          </div>
        </div>

        <el-alert v-if="restoredNotice" type="info" :closable="true" show-icon class="notice" @close="restoredNotice = false">
          <template #title>已恢复上次未提交草稿（{{ restoredAt }}）</template>
        </el-alert>

        <el-alert v-if="!auth.inWindow" type="warning" :closable="false" show-icon class="notice" :title="windowBlockedText" />
        <el-alert v-else-if="remaining <= 0" type="error" :closable="false" show-icon class="notice" title="生成次数已用完，无法继续提交" />

        <div class="submit-row">
          <span class="muted">提交将消耗 1 次创作机会，失败自动退还</span>
          <el-button
            type="primary"
            size="large"
            class="submit-btn"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="submit"
          >{{ submitting ? '提交中…' : '立即提交生成' }}</el-button>
        </div>
      </section>

      <!-- ================= 成片渲染与展映区 ================= -->
      <section v-if="task" class="screen panel">
        <div class="screen-head">
          <span class="screen-title">成片渲染与展映</span>
          <span class="muted mono">{{ task.task_no }}</span>
        </div>

        <!-- 排队 / 生成中：深蓝科技状态条 -->
        <div v-if="task.task_status === 'queued' || task.task_status === 'running'" class="render-state">
          <div class="render-bar">
            <span class="render-pulse" />
            <b>{{ task.task_status === 'queued' ? '任务排队中' : '模型正在渲染视频' }}</b>
            <span class="render-sub">
              <template v-if="task.task_status === 'queued'">前面还有 {{ task.queue_ahead ?? 0 }} 人 · 预计等待 {{ formatSeconds(task.estimated_wait_sec) }}</template>
              <template v-else>通常需要 1~3 分钟，请保持页面打开（每 3 秒自动刷新进度）</template>
            </span>
          </div>
          <el-progress :percentage="task.task_status === 'queued' ? 35 : 70" :show-text="false" :stroke-width="6" />
        </div>

        <!-- 成功：影院黑底播放器 -->
        <div v-else-if="task.task_status === 'succeeded'" class="screen-ok">
          <div class="cinema">
            <video v-if="task.video_url" :src="task.video_url" controls class="video" preload="metadata" />
          </div>
          <div class="spec-bar">
            <span>MP4 · H.264 / AAC</span>
            <span>15 秒</span>
            <span>{{ task.engine_code || 'wq-v1' }}</span>
            <span>{{ task.aspect_ratio || '16:9' }}</span>
          </div>
          <div class="screen-actions">
            <el-button type="primary" @click="downloadTask">下载 MP4</el-button>
            <el-button @click="goMyWorks">前往作品库</el-button>
          </div>
        </div>

        <!-- 失败 -->
        <div v-else-if="task.task_status === 'failed'" class="render-fail">
          <div class="fail-title">生成失败</div>
          <div class="text-danger">{{ task.error_msg || '未知错误' }}</div>
          <div class="muted mt-8">本次消耗的 1 次机会已自动退还，可重新提交。</div>
        </div>

        <div v-else class="render-state">
          <div class="render-bar"><b>草稿已创建</b><span class="render-sub">等待提交生成</span></div>
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

/** 画幅自适应首帧：按上传图片宽高比，就近匹配后端允许的 5 种画幅（log 距离，横竖对称） */
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
  if (!img) return '上传首帧后自动匹配画幅比例'
  const [w, h] = detectedRatio.value.split(':').map(Number)
  const actual = img.width / img.height
  const deviation = Math.abs(actual - w / h) / actual
  if (deviation < 0.02) return `与首帧 ${img.width}×${img.height} 一致`
  if (deviation > 0.15) return `首帧 ${img.width}×${img.height} 与 ${detectedRatio.value} 相差 ${Math.round(deviation * 100)}%，成片会按 ${detectedRatio.value} 裁切或补边`
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
  if (!auth.questions.length) await auth.refreshAll()
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
  if (result === 'text-only') ElMessage.warning('本地存储空间不足，已仅保存提示词（图片未暂存）')
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
      ElMessage.success(`已压缩：${result.originalWidth}×${result.originalHeight} → ${result.width}×${result.height}（${formatSize(result.size)}）`)
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
  position: relative;
  min-height: 100%;
  padding: 42px 20px 64px;
  overflow: hidden;
}

/* ---- X 型斜切几何背景 + 超大淡水印 ---- */
.geo-bg {
  position: absolute;
  inset: 0;
  pointer-events: none;
  z-index: 0;
}

.geo-svg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  opacity: 0.85;
}

.watermark {
  position: absolute;
  top: 180px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 108px;
  font-weight: 800;
  letter-spacing: 12px;
  white-space: nowrap;
  color: rgba(6, 43, 86, 0.04);
  line-height: 1;
}

.shell {
  position: relative;
  z-index: 1;
  max-width: 1040px;
  margin: 0 auto;
}

/* ---- 居中主标题组 ---- */
.stage-title {
  text-align: center;
  margin-bottom: 26px;
}

.eyebrow {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  color: var(--i2v-primary);
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 3px;
  margin-bottom: 12px;
}

.eyebrow-line {
  width: 28px;
  height: 1px;
  background: var(--i2v-primary);
  opacity: 0.5;
}

.stage-h2 {
  margin: 0;
  color: var(--i2v-navy);
  font-size: 30px;
  font-weight: 800;
  letter-spacing: 3px;
}

.title-rule {
  display: flex;
  justify-content: center;
  margin: 14px 0 12px;
}

.title-rule span {
  width: 54px;
  height: 3px;
  background: linear-gradient(90deg, var(--i2v-primary), var(--i2v-amber));
  border-radius: 2px;
}

.stage-sub {
  margin: 0;
  color: var(--i2v-text-sub);
  font-size: 13px;
  letter-spacing: 0.5px;
}

/* ---- 双步骤主工作舱 ---- */
.studio {
  position: relative;
  overflow: hidden;
  padding: 0 24px 24px;
}

.studio-top {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  height: 3px;
  background: linear-gradient(90deg, var(--i2v-primary) 0%, var(--i2v-cyan) 55%, var(--i2v-amber) 100%);
}

.studio-intro {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 18px 0 16px;
  border-bottom: 1px solid var(--i2v-border);
  margin-bottom: 20px;
}

.intro-badge {
  padding: 3px 10px;
  background: var(--i2v-navy);
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  letter-spacing: 1px;
  border-radius: 3px;
}

.quota-pill {
  margin-left: auto;
  padding: 5px 12px;
  background: var(--i2v-cyan-soft);
  color: var(--i2v-navy);
  font-size: 12px;
  border-radius: 3px;
  font-variant-numeric: tabular-nums;
}

.quota-pill b {
  color: var(--i2v-primary);
  font-size: 16px;
}

.studio-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}

.step {
  min-width: 0;
}

.step-head {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  margin-bottom: 12px;
}

.step-num {
  font-family: "Cascadia Mono", Consolas, monospace;
  font-size: 20px;
  font-weight: 800;
  color: var(--i2v-primary);
  line-height: 1.1;
  letter-spacing: -1px;
}

.step-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--i2v-navy);
}

/* 上传舱 */
.dropzone {
  border: 1.5px dashed #b9c9d8;
  border-radius: var(--i2v-radius);
  min-height: 214px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px;
  cursor: pointer;
  background: #fbfcfd;
  transition: border-color 0.15s, background 0.15s;
}

.dropzone.dragging,
.dropzone:hover {
  border-color: var(--i2v-primary);
  background: var(--i2v-cyan-soft);
}

.upload-icon {
  margin-bottom: 2px;
}

.preview {
  max-width: 100%;
  max-height: 176px;
  border-radius: var(--i2v-radius);
  object-fit: contain;
}

.preview-meta,
.drop-hint {
  font-size: 12px;
}

.hidden-input {
  display: none;
}

.asset-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 10px;
}

.ratio-label {
  font-size: 12px;
  padding: 3px 9px;
  background: var(--i2v-navy);
  color: #fff;
  border-radius: 3px;
}

.ratio-label b {
  color: var(--i2v-cyan);
  font-variant-numeric: tabular-nums;
}

.ratio-hint {
  flex: 1;
  min-width: 120px;
}

.reselect {
  border: none;
  background: none;
  color: var(--i2v-primary);
  cursor: pointer;
  font-size: 12px;
  padding: 2px 0;
  white-space: nowrap;
}

.reselect:hover {
  color: var(--i2v-primary-hover);
  text-decoration: underline;
}

/* 提示词区 */
.draft-hint {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-top: 10px;
}

.draft-dot {
  width: 6px;
  height: 6px;
  background: var(--i2v-success);
  border-radius: 50%;
  flex: 0 0 auto;
}

.draft-hint .reselect {
  margin-left: auto;
}


.notice {
  margin-top: 16px;
  border-radius: var(--i2v-radius);
}

.submit-row {
  margin-top: 20px;
  padding-top: 18px;
  border-top: 1px solid var(--i2v-border);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.submit-btn {
  min-width: 208px;
  height: 46px;
  font-size: 15px;
  letter-spacing: 2px;
}

/* ---- 成片渲染与展映区 ---- */
.screen {
  margin-top: 20px;
  padding: 18px 24px 22px;
}

.screen-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--i2v-border);
  margin-bottom: 16px;
}

.screen-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--i2v-navy);
}

.render-state {
  background: linear-gradient(100deg, var(--i2v-navy) 0%, var(--i2v-navy-2) 100%);
  color: #fff;
  padding: 16px 20px;
  border-radius: var(--i2v-radius);
}

.render-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.render-bar b {
  letter-spacing: 1px;
}

.render-sub {
  color: rgba(255, 255, 255, 0.78);
  font-size: 12px;
  font-variant-numeric: tabular-nums;
}

.render-pulse {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  background: var(--i2v-cyan);
  box-shadow: 0 0 0 4px rgba(34, 184, 207, 0.2);
  animation: pulse 1.6s ease-in-out infinite;
}

@keyframes pulse {
  50% {
    box-shadow: 0 0 0 8px rgba(34, 184, 207, 0.05);
  }
}

.cinema {
  background: #050a12;
  border: 1px solid var(--i2v-border);
  border-radius: var(--i2v-radius);
  aspect-ratio: 16 / 9;
  display: flex;
  align-items: center;
  justify-content: center;
}

.video {
  width: 100%;
  height: 100%;
  max-height: 100%;
  object-fit: contain;
  background: #000;
}

.spec-bar {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  margin-top: 12px;
}

.spec-bar span {
  padding: 4px 10px;
  font-size: 11px;
  color: var(--i2v-text-sub);
  background: var(--i2v-bg);
  border: 1px solid var(--i2v-border);
  border-radius: 3px;
}

.screen-actions {
  display: flex;
  gap: 10px;
  margin-top: 14px;
}

.fail-title {
  font-size: 15px;
  font-weight: 700;
  color: var(--i2v-danger);
  margin-bottom: 6px;
}

@media (max-width: 900px) {
  .studio-grid {
    grid-template-columns: 1fr;
  }
  .watermark {
    font-size: 64px;
  }
  .submit-row {
    flex-direction: column;
    align-items: stretch;
  }
  .submit-btn {
    width: 100%;
  }
}
</style>
