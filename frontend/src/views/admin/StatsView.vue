<template>
  <div class="stats">
    <div class="cards">
      <div class="card panel" v-for="card in cards" :key="card.label">
        <div class="card-label">{{ card.label }}</div>
        <div class="card-value" :class="card.cls">{{ card.value }}</div>
      </div>
    </div>

    <div class="panel main">
      <div class="toolbar">
        <el-tabs v-model="tab" class="tab-inline">
          <el-tab-pane label="按考生" name="candidate" />
          <el-tab-pane label="按模型" name="engine" />
          <el-tab-pane label="按天" name="day" />
        </el-tabs>
        <div class="exports">
          <el-dropdown @command="onExportCsv">
            <el-button>导出统计明细 CSV ▾</el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="works">作品明细 (works)</el-dropdown-item>
                <el-dropdown-item command="candidates">考生明细 (candidates)</el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
          <el-button @click="onExportLinks">导出成片下载清单</el-button>
        </div>
      </div>

      <el-table v-if="tab === 'candidate'" :data="byCandidate" v-loading="loading" size="small" stripe>
        <el-table-column prop="account" label="考号" width="150" />
        <el-table-column prop="name" label="姓名" width="110" />
        <el-table-column label="配额（已用/总）" width="140">
          <template #default="{ row }">{{ row.quota_used }} / {{ row.quota_total }}</template>
        </el-table-column>
        <el-table-column prop="quota_remaining" label="剩余" width="80" />
        <el-table-column prop="success_count" label="成功" width="80" />
        <el-table-column prop="failed_count" label="失败" width="80" />
        <el-table-column label="最后活跃" width="170">
          <template #default="{ row }">{{ formatDateTime(row.last_active_at) }}</template>
        </el-table-column>
      </el-table>

      <el-table v-else-if="tab === 'engine'" :data="byEngine" v-loading="loading" size="small" stripe>
        <el-table-column prop="code" label="引擎" width="130" />
        <el-table-column prop="name" label="名称" min-width="150" />
        <el-table-column prop="resolution" label="分辨率" width="90" />
        <el-table-column prop="calls" label="调用数" width="90" />
        <el-table-column prop="succeeded" label="成功" width="80" />
        <el-table-column prop="total_duration_sec" label="累计时长(s)" width="120" />
        <el-table-column label="占比" width="90">
          <template #default="{ row }">{{ row.share_rate }}%</template>
        </el-table-column>
        <el-table-column label="折算费用(元)" width="130">
          <template #default="{ row }">{{ Number(row.cost).toFixed(4) }}</template>
        </el-table-column>
      </el-table>

      <el-table v-else :data="byDay" v-loading="loading" size="small" stripe>
        <el-table-column prop="date" label="日期" width="140" />
        <el-table-column prop="total" label="生成总量" width="110" />
        <el-table-column prop="succeeded" label="成功" width="90" />
        <el-table-column prop="failed" label="失败" width="90" />
        <el-table-column label="失败率" width="100">
          <template #default="{ row }">{{ row.fail_rate }}%</template>
        </el-table-column>
      </el-table>

      <div v-if="tab === 'candidate'" class="pager">
        <el-pagination
          background
          layout="total, prev, pager, next"
          :total="candidateTotal"
          :current-page="candidatePage"
          :page-size="20"
          @current-change="loadCandidates"
        />
      </div>
    </div>

    <el-dialog v-model="linksVisible" title="成片下载清单（30 分钟有效签名直链）" width="900px">
      <div class="muted">共 {{ videoLinks.length }} 条；点击「复制」可直接发给运维批量下载。</div>
      <el-table :data="videoLinks" size="small" max-height="420" class="mt-12">
        <el-table-column prop="filename" label="规范文件名" min-width="260" />
        <el-table-column prop="account" label="考号" width="140" />
        <el-table-column prop="question_code" label="题号" width="90" />
        <el-table-column label="链接" width="100">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="copy(row.download_url)">复制</el-button>
          </template>
        </el-table-column>
      </el-table>
      <template #footer>
        <el-button @click="linksVisible = false">关闭</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'

import * as adminApi from '@/api/admin'
import type {
  StatsByCandidate,
  StatsByDay,
  StatsByEngine,
  StatsOverview,
  VideoLink,
} from '@/api/adminTypes'
import { formatDateTime } from '@/utils/format'

const tab = ref<'candidate' | 'engine' | 'day'>('candidate')
const loading = ref(false)
const overview = ref<StatsOverview | null>(null)
const byCandidate = ref<StatsByCandidate[]>([])
const byEngine = ref<StatsByEngine[]>([])
const byDay = ref<StatsByDay[]>([])
const candidateTotal = ref(0)
const candidatePage = ref(1)

const linksVisible = ref(false)
const videoLinks = ref<VideoLink[]>([])

const cards = computed(() => [
  { label: '考生总数', value: overview.value?.candidates_total ?? 0, cls: '' },
  { label: '生成总数', value: overview.value?.works_total ?? 0, cls: '' },
  { label: '成功 / 失败', value: `${overview.value?.succeeded ?? 0} / ${overview.value?.failed ?? 0}`, cls: '' },
  { label: '成功率', value: `${overview.value?.success_rate ?? 0}%`, cls: 'text-success' },
  { label: '累计时长', value: `${overview.value?.total_duration_sec ?? 0}s`, cls: '' },
  { label: '预估费用', value: `${Number(overview.value?.estimated_cost ?? 0).toFixed(2)} 元`, cls: 'text-warning' },
])

async function loadOverview(): Promise<void> {
  overview.value = await adminApi.statsOverview()
}

async function loadCandidates(page = 1): Promise<void> {
  candidatePage.value = page
  loading.value = true
  try {
    const data = await adminApi.statsByCandidate({ page, size: 20 })
    byCandidate.value = data.items
    candidateTotal.value = data.total
  } finally {
    loading.value = false
  }
}

async function loadTab(): Promise<void> {
  loading.value = true
  try {
    if (tab.value === 'candidate') await loadCandidates(1)
    else if (tab.value === 'engine') byEngine.value = await adminApi.statsByEngine()
    else byDay.value = await adminApi.statsByDay(30)
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await Promise.all([loadOverview(), loadTab()])
})

watch(tab, () => {
  void loadTab()
})

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  setTimeout(() => URL.revokeObjectURL(url), 3000)
}

async function onExportCsv(dimension: 'works' | 'candidates'): Promise<void> {
  const { blob, filename } = await adminApi.exportStatsCsv(dimension)
  downloadBlob(blob, filename)
  ElMessage.success('CSV 已导出（UTF-8 BOM，Excel 可直接打开）')
}

async function onExportLinks(): Promise<void> {
  const data = await adminApi.exportVideoLinks({ format: 'json' })
  videoLinks.value = Array.isArray(data) ? data : []
  if (!videoLinks.value.length) {
    ElMessage.info('当前没有已出片的作品')
    return
  }
  linksVisible.value = true
}

async function copy(text: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text)
    ElMessage.success('链接已复制')
  } catch {
    ElMessage.warning('浏览器拒绝了剪贴板访问，请手动复制')
  }
}
</script>

<style scoped>
.cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
  gap: 12px;
  margin-bottom: 12px;
}

.card {
  padding: 14px 16px;
}

.card-label {
  font-size: 12px;
  color: var(--i2v-text-sub);
}

.card-value {
  font-size: 22px;
  font-weight: 700;
  margin-top: 6px;
  font-variant-numeric: tabular-nums;
}

.main {
  padding: 4px 16px 12px;
}

.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.tab-inline {
  flex: 1;
}

.exports {
  display: flex;
  gap: 8px;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 10px 0;
}

.mt-12 {
  margin-top: 12px;
}
</style>
