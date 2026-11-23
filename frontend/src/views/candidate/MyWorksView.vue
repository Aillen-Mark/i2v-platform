<template>
  <div class="my-works page">
    <div class="head panel">
      <div class="head-left">
        <div class="title">我的作品</div>
        <div class="muted">
          共 {{ works?.total ?? 0 }} 条作品，其中可下载 {{ downloadableCount }} 条
        </div>
      </div>
      <div class="head-right">
        <el-button :loading="loading" @click="reload">刷新</el-button>
        <el-button type="primary" :loading="zipping" :disabled="downloadableCount === 0" @click="downloadAll">
          一键打包下载全部作品 (.zip)
        </el-button>
      </div>
    </div>

    <el-empty v-if="!loading && (works?.total ?? 0) === 0" description="还没有作品，去创作工作台提交一条吧" />

    <div v-for="group in works?.groups ?? []" :key="group.question_id" class="panel group mt-16">
      <div class="panel-title flex" style="align-items: center; justify-content: space-between">
        <span>{{ group.question_code }} · {{ group.question_title }}</span>
        <span class="muted">{{ group.total }} 条</span>
      </div>
      <div class="cards">
        <div v-for="item in group.works" :key="item.id" class="card">
          <div class="card-head">
            <span class="mono task-no">{{ item.task_no }}</span>
            <el-tag size="small" :type="statusType(item.task_status)">{{ item.status_text }}</el-tag>
            <el-tag v-if="item.seq" size="small" effect="plain">第 {{ item.seq }} 次</el-tag>
          </div>
          <div class="prompt" :title="item.prompt">{{ item.prompt }}</div>
          <div class="meta muted">
            <span>{{ formatDateTime(item.created_at) }}</span>
            <span v-if="item.engine_code">· {{ item.engine_code }}</span>
            <span v-if="item.file_size">· {{ formatSize(item.file_size) }}</span>
          </div>
          <div v-if="item.task_status === 'failed'" class="text-danger err">
            {{ item.error_msg || '生成失败' }}
            <span class="muted">（本次机会已退还）</span>
          </div>
          <div class="actions">
            <el-button
              size="small"
              :disabled="!item.preview_url"
              @click="openPreview(item)"
            >
              在线预览
            </el-button>
            <el-button
              size="small"
              type="primary"
              :disabled="!item.download_ready"
              :loading="downloadingId === item.id"
              @click="downloadOne(item)"
            >
              下载 MP4
            </el-button>
          </div>
        </div>
      </div>
    </div>

    <el-dialog v-model="previewVisible" :title="previewTitle" width="720px" destroy-on-close>
      <video v-if="previewUrl" :src="previewUrl" controls autoplay class="dialog-video" />
      <template #footer>
        <el-button @click="previewVisible = false">关闭</el-button>
        <el-button v-if="previewItem" type="primary" @click="downloadOne(previewItem)">下载 MP4</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { downloadAllZip, fetchMyWorks, requestDownloadUrl } from '@/api/candidate'
import type { MyWorks, WorkItem, WorkStatus } from '@/api/types'
import { formatDateTime, formatSize } from '@/utils/format'

const works = ref<MyWorks | null>(null)
const loading = ref(false)
const zipping = ref(false)
const downloadingId = ref<number | null>(null)
const previewVisible = ref(false)
const previewUrl = ref('')
const previewTitle = ref('')
const previewItem = ref<WorkItem | null>(null)

const downloadableCount = computed(
  () => (works.value?.items ?? []).filter((item) => item.download_ready).length,
)

function statusType(status: WorkStatus): 'success' | 'warning' | 'danger' | 'info' {
  if (status === 'succeeded') return 'success'
  if (status === 'failed') return 'danger'
  if (status === 'queued' || status === 'running') return 'warning'
  return 'info'
}

async function reload(): Promise<void> {
  loading.value = true
  try {
    works.value = await fetchMyWorks()
  } finally {
    loading.value = false
  }
}

onMounted(reload)

function openPreview(item: WorkItem): void {
  if (!item.preview_url) {
    ElMessage.warning('该作品暂无可预览的成片')
    return
  }
  previewItem.value = item
  previewUrl.value = item.preview_url
  previewTitle.value = `${item.question_code} · 第 ${item.seq ?? 1} 次`
  previewVisible.value = true
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

async function downloadOne(item: WorkItem): Promise<void> {
  downloadingId.value = item.id
  try {
    const data = await requestDownloadUrl(item.id)
    triggerDownload(data.download_url)
    ElMessage.success(`开始下载：${data.filename}`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    downloadingId.value = null
  }
}

async function downloadAll(): Promise<void> {
  zipping.value = true
  try {
    const { blob, filename } = await downloadAllZip()
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = filename
    document.body.appendChild(anchor)
    anchor.click()
    document.body.removeChild(anchor)
    setTimeout(() => URL.revokeObjectURL(url), 4000)
    ElMessage.success(`已开始下载：${filename}`)
  } catch {
    /* 拦截器已提示 */
  } finally {
    zipping.value = false
  }
}
</script>

<style scoped>
.my-works {
  max-width: 1400px;
  margin: 0 auto;
}

.head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
}

.title {
  font-size: 17px;
  font-weight: 600;
}

.head-right {
  display: flex;
  gap: 10px;
}

.cards {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
  padding: 14px 16px 18px;
}

.card {
  border: 1px solid var(--i2v-border);
  border-radius: 10px;
  padding: 12px;
  display: flex;
  flex-direction: column;
  gap: 8px;
  background: #fcfcfd;
}

.card-head {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.task-no {
  font-size: 12px;
  color: var(--i2v-text-sub);
}

.prompt {
  font-size: 13px;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.meta {
  font-size: 12px;
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}

.err {
  font-size: 12px;
}

.actions {
  display: flex;
  gap: 8px;
  margin-top: 4px;
}

.dialog-video {
  width: 100%;
  max-height: 460px;
  background: #000;
  border-radius: 8px;
}
</style>
