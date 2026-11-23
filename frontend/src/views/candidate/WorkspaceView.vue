<template>
  <div class="workspace page">
    <div class="cols">
      <!-- 左：题目列表 -->
      <aside class="panel questions">
        <div class="panel-title">题目列表（{{ auth.questions.length }}）</div>
        <div class="q-list">
          <button
            v-for="item in auth.questions"
            :key="item.id"
            class="q-item"
            :class="{ active: current?.id === item.id }"
            type="button"
            @click="selectQuestion(item)"
          >
            <div class="q-head">
              <span class="q-code mono">{{ item.code }}</span>
              <el-tag size="small" :type="item.remaining > 0 ? 'success' : 'info'" effect="plain">
                {{ item.remaining }} / {{ item.quota_total }} 次
              </el-tag>
              <el-tag v-if="item.adjusted_flag" size="small" type="warning" effect="dark">已调整</el-tag>
            </div>
            <div class="q-title">{{ item.title }}</div>
          </button>
          <el-empty v-if="!auth.questions.length" description="暂无启用题目" :image-size="60" />
        </div>
      </aside>

      <!-- 中：题目详情与示例 -->
      <section class="panel detail">
        <template v-if="current">
          <div class="panel-title">{{ current.code }} · {{ current.title }}</div>
          <div class="detail-body">
            <p class="desc">{{ current.description }}</p>

            <div v-if="current.ref_image_url" class="block">
              <div class="block-label">参考图（点击放大）</div>
              <el-image
                :src="current.ref_image_url"
                :preview-src-list="[current.ref_image_url]"
                fit="contain"
                class="ref-image"
                hide-on-click-modal
              />
            </div>

            <div class="block">
              <div class="block-label">示例提示词（点击填入右侧输入框）</div>
              <div class="examples">
                <button
                  v-for="(example, index) in examples"
                  :key="index"
                  type="button"
                  class="example"
                  @click="applyExample(example)"
                >
                  <span class="ex-index">{{ index + 1 }}</span>
                  <span class="ex-text">{{ example }}</span>
                </button>
                <div v-if="!examples.length" class="muted">该题目暂未配置示例提示词</div>
              </div>
            </div>
          </div>
        </template>
        <el-empty v-else description="请选择左侧题目" />
      </section>

      <!-- 右：创作与提交 -->
      <section class="panel compose">
        <div class="panel-title">创作与提交</div>
        <div class="compose-body" v-if="current">
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
              <el-button link type="primary" size="small" @click="clearCurrentDraft">清空草稿</el-button>
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

          <div class="block-label mt-12">
            提示词（{{ prompt.length }} / 300）
          </div>
          <el-input
            v-model="prompt"
            type="textarea"
            :rows="5"
            maxlength="300"
            show-word-limit
            resize="none"
            placeholder="描述你想要的画面，例如：一只橘猫在雪地里奔跑，夕阳逆光，电影质感"
          />

          <div class="ratio-row mt-8">
            <span class="muted">画幅</span>
            <el-radio-group v-model="aspectRatio" size="small">
              <el-radio-button label="16:9" />
              <el-radio-button label="9:16" />
              <el-radio-button label="1:1" />
            </el-radio-group>
            <el-button link type="primary" size="small" @click="clearCurrentDraft">清空草稿</el-button>
          </div>

          <el-alert
            v-if="!auth.inWindow"
            type="warning"
            :closable="false"
            show-icon
            class="mt-12"
            :title="windowBlockedText"
          />
          <el-alert
            v-else-if="current.remaining <= 0"
            type="error"
            :closable="false"
            show-icon
            class="mt-12"
            title="本题机会已用完，无法继续提交"
          />

          <el-button
            type="primary"
            size="large"
            class="submit"
            :loading="submitting"
            :disabled="!canSubmit"
            @click="submit"
          >
            {{ submitting ? '提交中…' : '提交生成（消耗本题 1 次机会）' }}
          </el-button>
        </div>
      </section>
    </div>

    <!-- 任务状态卡片 -->
    <div v-if="task" class="panel task mt-16">
      <div class="panel-title flex" style="align-items: center; justify-content: space-between">
        <span>当前任务 · {{ task.task_no }}</span>
        <span class="muted">状态：{{ task.status_text }}</span>
      </div>
      <div class="task-body">
        <div v-if="task.task_status === 'queued'" class="state state-queued">
          <div class="state-title">排队中</div>
          <div class="muted">
            前面还有 {{ task.queue_ahead ?? 0 }} 人，预计等待约 {{ formatSeconds(task.estimated_wait_sec) }}
          </div>
          <el-progress :percentage="35" :show-text="false" status="warning" :stroke-width="8" class="mt-8" />
        </div>

        <div v-else-if="task.task_status === 'running'" class="state state-running">
          <div class="state-title">模型正在生成视频中…</div>
          <div class="muted">通常需要 1~3 分钟，请保持页面打开</div>
          <el-progress :percentage="70" :show-text="false" :indeterminate="true" :duration="2" :stroke-width="8" class="mt-8" />
        </div>

        <div v-else-if="task.task_status === 'succeeded'" class="state state-succeeded">
          <div class="state-title text-success">出片成功，可在线预览与下载</div>
          <video v-if="task.video_url" :src="task.video_url" controls class="video" preload="metadata" />
          <div class="mt-8 flex gap-8">
            <el-button type="primary" @click="downloadTask">下载 MP4</el-button>
            <el-button @click="goMyWorks">查看我的作品</el-button>
          </div>
        </div>

        <div v-else-if="task.task_status === 'failed'" class="state state-failed">
          <div class="state-title text-danger">生成失败</div>
          <div class="text-danger">{{ task.error_msg || '未知错误' }}</div>
          <div class="muted mt-8">本次消耗的 1 次机会已自动退还，可重新提交。</div>
        </div>

        <div v-else class="state">
          <div class="state-title">草稿已创建</div>
          <div class="muted">等待提交生成</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'

import { useAuthStore } from '@/stores/auth'
import {
  createWork,
  fetchMyWorks,
  fetchWork,
  generateWork,
  requestDownloadUrl,
} from '@/api/candidate'
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

const current = ref<QuestionPublic | null>(null)
const prompt = ref('')
const aspectRatio = ref('16:9')
const image = ref<LocalImage | null>(null)
const dragging = ref(false)
const submitting = ref(false)
const restoredNotice = ref(false)
const restoredAt = ref('')
const fileInput = ref<HTMLInputElement | null>(null)

const task = ref<WorkDetail | null>(null)
let pollTimer: number | null = null
let saveTimer: number | null = null

const examples = computed<string[]>(() => current.value?.prompt_examples ?? [])

const windowBlockedText = computed(() => {
  const next = auth.me?.next_window_start
  return next
    ? `当前不在开放时段，无法提交。下次开放时间：${formatDateTime(next)}`
    : '当前不在开放时段，无法提交，请等待管理员开放'
})

const canSubmit = computed(() => {
  if (!current.value || !image.value || submitting.value) return false
  if (!auth.inWindow) return false
  if (current.value.remaining <= 0) return false
  const text = prompt.value.trim()
  return text.length > 0 && text.length <= 300
})

onMounted(async () => {
  if (!auth.questions.length) {
    await auth.refreshAll()
  }
  if (auth.questions.length) {
    selectQuestion(auth.questions[0])
  }
  await resumeInflight()
})

onBeforeUnmount(() => {
  stopPolling()
  if (saveTimer) window.clearTimeout(saveTimer)
})

function selectQuestion(item: QuestionPublic): void {
  if (current.value?.id === item.id) return
  stopPolling()
  task.value = null
  current.value = item
  prompt.value = ''
  image.value = null
  restoredNotice.value = false
  restoreDraft(item)
}

function restoreDraft(item: QuestionPublic): void {
  const me = auth.me
  if (!me) return
  const draft = loadDraft(draftKey(me.id, item.id))
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
  const me = auth.me
  if (!me || !current.value) return
  if (!prompt.value.trim() && !image.value) return
  const key = draftKey(me.id, current.value.id)
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

function clearCurrentDraft(): void {
  const me = auth.me
  if (!me || !current.value) return
  clearDraft(draftKey(me.id, current.value.id))
  prompt.value = ''
  image.value = null
  restoredNotice.value = false
  ElMessage.success('已清空本题草稿')
}

function applyExample(example: string): void {
  prompt.value = example.slice(0, 300)
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
  if (!canSubmit.value || !current.value || !image.value) return
  const me = auth.me
  if (!me) return

  submitting.value = true
  try {
    const blob = image.value.blob ?? dataUrlToBlob(image.value.dataUrl)
    const work = await createWork({
      questionId: current.value.id,
      prompt: prompt.value.trim(),
      aspectRatio: aspectRatio.value,
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
    clearDraft(draftKey(me.id, current.value.id))
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
    const inflight = works.items.find(
      (item) => item.task_status === 'queued' || item.task_status === 'running',
    )
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
    triggerDownload(data.download_url)
    ElMessage.success(`开始下载：${data.filename}`)
  } catch {
    /* 拦截器已提示 */
  }
}

function triggerDownload(url: string): void {
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.rel = 'noopener'
  anchor.target = '_blank'
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
}

async function goMyWorks(): Promise<void> {
  await router.push('/my-works')
}
</script>

<style scoped>
.workspace {
  max-width: 1560px;
  margin: 0 auto;
}

.cols {
  display: grid;
  grid-template-columns: 260px minmax(320px, 1fr) 460px;
  gap: 16px;
  align-items: start;
}

.questions,
.detail,
.compose {
  min-height: 520px;
}

.q-list {
  padding: 8px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 640px;
  overflow-y: auto;
}

.q-item {
  text-align: left;
  border: 1px solid var(--i2v-border);
  background: #fff;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  transition: all 0.15s;
}

.q-item:hover {
  border-color: #bfdbfe;
  background: #f8fbff;
}

.q-item.active {
  border-color: var(--i2v-primary);
  background: #eff6ff;
}

.q-head {
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}

.q-code {
  font-weight: 700;
  color: var(--i2v-primary);
}

.q-title {
  margin-top: 6px;
  font-size: 13px;
  color: var(--i2v-text);
}

.detail-body,
.compose-body {
  padding: 14px 16px 18px;
}

.desc {
  white-space: pre-wrap;
  line-height: 1.7;
  margin: 0 0 14px;
}

.block {
  margin-bottom: 16px;
}

.block-label {
  font-size: 12px;
  color: var(--i2v-text-sub);
  margin-bottom: 6px;
}

.ref-image {
  width: 100%;
  max-height: 260px;
  border-radius: 8px;
  border: 1px solid var(--i2v-border);
  background: #fafafa;
  cursor: zoom-in;
}

.examples {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.example {
  display: flex;
  gap: 10px;
  align-items: flex-start;
  text-align: left;
  border: 1px dashed var(--i2v-border);
  background: #fcfcfd;
  border-radius: 8px;
  padding: 10px 12px;
  cursor: pointer;
  font-size: 13px;
  line-height: 1.5;
}

.example:hover {
  border-color: var(--i2v-primary);
  background: #f5f9ff;
}

.ex-index {
  width: 20px;
  height: 20px;
  flex: 0 0 20px;
  border-radius: 50%;
  background: #e0edff;
  color: var(--i2v-primary);
  font-size: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
  font-weight: 700;
}

.dropzone {
  border: 2px dashed var(--i2v-border);
  border-radius: 10px;
  min-height: 150px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 12px;
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
  max-height: 180px;
  border-radius: 8px;
  object-fit: contain;
}

.drop-hint {
  font-size: 14px;
  color: var(--i2v-text);
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
  margin-top: 16px;
}

.restore-alert {
  margin-bottom: 12px;
}

.task-body {
  padding: 14px 16px 18px;
}

.state-title {
  font-size: 15px;
  font-weight: 600;
  margin-bottom: 6px;
}

.video {
  width: 100%;
  max-height: 420px;
  border-radius: 8px;
  background: #000;
  margin-top: 10px;
}
</style>
