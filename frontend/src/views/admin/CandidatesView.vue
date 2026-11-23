<template>
  <div class="candidates">
    <div class="panel toolbar">
      <div class="left">
        <el-button @click="onDownloadTemplate">下载 Excel 模板</el-button>
        <el-button type="primary" @click="importVisible = true">批量导入 (.xlsx)</el-button>
        <el-button @click="createVisible = true">新增考生</el-button>
        <el-dropdown @command="onExport">
          <el-button>
            导出<el-icon class="el-icon--right">▾</el-icon>
          </el-button>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="xlsx">导出 Excel（勾选/全部）</el-dropdown-item>
              <el-dropdown-item command="csv">导出 CSV（勾选/全部）</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
        <el-button type="success" plain @click="goPrintSlips">打印密码条</el-button>
      </div>
      <div class="right">
        <el-input
          v-model="query.keyword"
          placeholder="按考号 / 姓名搜索"
          clearable
          style="width: 220px"
          @keyup.enter="reload(1)"
          @clear="reload(1)"
        />
        <el-select v-model="query.status" placeholder="全部状态" clearable style="width: 120px" @change="reload(1)">
          <el-option label="启用" :value="1" />
          <el-option label="禁用" :value="0" />
        </el-select>
        <el-button @click="reload(1)">查询</el-button>
      </div>
    </div>

    <div class="panel batch-bar" v-if="selection.length">
      <span class="muted">已选 {{ selection.length }} 人</span>
      <el-button size="small" @click="onBatchReset">批量重置密码</el-button>
      <el-button size="small" type="success" plain @click="onBatchStatus(1)">批量启用</el-button>
      <el-button size="small" type="danger" plain @click="onBatchStatus(0)">批量禁用</el-button>
      <el-button size="small" type="warning" plain @click="goBatchQuota">批量加减配额</el-button>
    </div>

    <div class="panel">
      <el-table :data="rows" v-loading="loading" @selection-change="onSelectionChange" stripe>
        <el-table-column type="selection" width="44" />
        <el-table-column prop="account" label="考号" width="150" />
        <el-table-column prop="name" label="姓名" width="110" />
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
              {{ row.status === 1 ? '启用' : '禁用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="配额（已用 / 总数）" width="150">
          <template #default="{ row }">{{ row.quota_used_sum }} / {{ row.quota_total_sum }}</template>
        </el-table-column>
        <el-table-column label="初始密码" width="110">
          <template #default="{ row }">
            <el-tag :type="row.init_pwd_viewable ? 'success' : 'info'" size="small">
              {{ row.init_pwd_viewable ? '可查看' : '已过期' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="最后登录" width="160">
          <template #default="{ row }">{{ formatDateTime(row.last_login_at) }}</template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="120" show-overflow-tooltip />
        <el-table-column label="操作" width="260" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="onViewPassword(row)">查看密码</el-button>
            <el-button link type="primary" size="small" @click="onResetOne(row)">重置密码</el-button>
            <el-button link type="primary" size="small" @click="onToggleOne(row)">
              {{ row.status === 1 ? '禁用' : '启用' }}
            </el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>

      <div class="pager">
        <el-pagination
          background
          layout="total, sizes, prev, pager, next"
          :total="total"
          :current-page="query.page"
          :page-size="query.size"
          :page-sizes="[10, 20, 50, 100]"
          @current-change="(page: number) => reload(page)"
          @size-change="(size: number) => { query.size = size; reload(1) }"
        />
      </div>
    </div>

    <!-- 导入结果 -->
    <el-dialog v-model="importVisible" title="批量导入考生" width="560px">
      <div class="muted">选择按模板填写的 .xlsx 文件（姓名必填，考号可留空自动生成）</div>
      <input ref="fileInput" type="file" accept=".xlsx" class="mt-12" @change="onFilePicked" />
      <div v-if="importResult" class="import-result mt-16">
        <el-alert
          :type="importResult.failed_count ? 'warning' : 'success'"
          :closable="false"
          show-icon
          :title="`共 ${importResult.total_rows} 行：成功 ${importResult.success_count} 行，失败 ${importResult.failed_count} 行（耗时 ${importResult.elapsed_ms} ms）`"
        />
        <el-table v-if="importResult.errors.length" :data="importResult.errors" size="small" class="mt-12" max-height="260">
          <el-table-column prop="row" label="行号" width="70" />
          <el-table-column prop="account" label="考号" width="140" />
          <el-table-column prop="name" label="姓名" width="100" />
          <el-table-column prop="reason" label="失败原因" />
        </el-table>
      </div>
      <template #footer>
        <el-button @click="importVisible = false">关闭</el-button>
        <el-button type="primary" :loading="importing" :disabled="!pickedFile" @click="onImport">开始导入</el-button>
      </template>
    </el-dialog>

    <!-- 新增考生 -->
    <el-dialog v-model="createVisible" title="新增考生" width="420px">
      <el-form :model="createForm" label-width="80px">
        <el-form-item label="姓名"><el-input v-model="createForm.name" /></el-form-item>
        <el-form-item label="考号"><el-input v-model="createForm.account" placeholder="留空自动生成" /></el-form-item>
        <el-form-item label="备注"><el-input v-model="createForm.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="creating" @click="onCreate">创建</el-button>
      </template>
    </el-dialog>

    <!-- 新密码回显 -->
    <el-dialog v-model="passwordVisible" title="密码信息" width="460px">
      <el-alert v-if="passwordNotice" :title="passwordNotice" :type="passwordNoticeType" :closable="false" show-icon />
      <el-table v-if="passwordItems.length" :data="passwordItems" size="small" class="mt-12">
        <el-table-column prop="account" label="考号" width="150" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="password" label="密码" />
      </el-table>
      <template #footer>
        <el-button type="primary" @click="passwordVisible = false">知道了</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { AxiosError } from 'axios'

import * as adminApi from '@/api/admin'
import type { ApiError } from '@/api/types'
import type { CandidateRow, ImportResult } from '@/api/adminTypes'
import { formatDateTime } from '@/utils/format'

const router = useRouter()

const rows = ref<CandidateRow[]>([])
const total = ref(0)
const loading = ref(false)
const selection = ref<CandidateRow[]>([])
const query = reactive({ page: 1, size: 20, keyword: '', status: null as number | null })

const importVisible = ref(false)
const importing = ref(false)
const pickedFile = ref<File | null>(null)
const importResult = ref<ImportResult | null>(null)
const fileInput = ref<HTMLInputElement | null>(null)

const createVisible = ref(false)
const creating = ref(false)
const createForm = reactive({ name: '', account: '', remark: '' })

const passwordVisible = ref(false)
const passwordItems = ref<{ account: string; name: string; password: string }[]>([])
const passwordNotice = ref('')
const passwordNoticeType = ref<'success' | 'info' | 'warning'>('success')

async function reload(page = query.page): Promise<void> {
  query.page = page
  loading.value = true
  try {
    const data = await adminApi.listCandidates({
      page: query.page,
      size: query.size,
      keyword: query.keyword || undefined,
      status: query.status,
    })
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

onMounted(() => reload(1))

function onSelectionChange(value: CandidateRow[]): void {
  selection.value = value
}

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

async function onDownloadTemplate(): Promise<void> {
  const blob = await adminApi.downloadTemplate()
  downloadBlob(blob, 'candidate_import_template.xlsx')
}

async function onExport(format: 'xlsx' | 'csv'): Promise<void> {
  const ids = selection.value.map((item) => item.id)
  const { blob, filename } = await adminApi.exportCandidates(format, ids)
  downloadBlob(blob, filename)
  ElMessage.success(ids.length ? `已导出勾选的 ${ids.length} 名考生` : '已导出全部考生')
}

function goPrintSlips(): void {
  const ids = selection.value.map((item) => item.id)
  router.push({ path: '/console/candidates/print-slips', query: ids.length ? { ids: ids.join(',') } : {} })
}

function goBatchQuota(): void {
  const ids = selection.value.map((item) => item.id)
  router.push({ path: '/console/quotas', query: { candidate_ids: ids.join(',') } })
}

function onFilePicked(event: Event): void {
  const target = event.target as HTMLInputElement
  pickedFile.value = target.files?.[0] ?? null
  importResult.value = null
}

async function onImport(): Promise<void> {
  if (!pickedFile.value) return
  importing.value = true
  try {
    importResult.value = await adminApi.importCandidates(pickedFile.value)
    ElMessage.success(`导入完成：成功 ${importResult.value.success_count} 行`)
    await reload(1)
  } finally {
    importing.value = false
  }
}

async function onCreate(): Promise<void> {
  if (!createForm.name.trim()) {
    ElMessage.warning('请填写姓名')
    return
  }
  creating.value = true
  try {
    const item = await adminApi.createCandidate({
      name: createForm.name.trim(),
      account: createForm.account.trim() || null,
      remark: createForm.remark.trim() || null,
    })
    passwordItems.value = [{ account: item.account, name: item.name, password: item.password }]
    passwordNotice.value = '考生创建成功，初始密码仅在本次弹窗显示'
    passwordNoticeType.value = 'success'
    passwordVisible.value = true
    createVisible.value = false
    createForm.name = ''
    createForm.account = ''
    createForm.remark = ''
    await reload(1)
  } finally {
    creating.value = false
  }
}

async function onViewPassword(row: CandidateRow): Promise<void> {
  try {
    const slip = await adminApi.viewInitPassword(row.id)
    passwordItems.value = [{ account: slip.account, name: slip.name, password: slip.password || '' }]
    passwordNotice.value = ''
    passwordNoticeType.value = 'success'
    passwordVisible.value = true
  } catch (error) {
    const axiosError = error as AxiosError<ApiError>
    if (axiosError.response?.status === 410) {
      passwordItems.value = []
      passwordNotice.value = '初始密码已过期（可查看期 7 天），请使用「重置密码」生成新密码'
      passwordNoticeType.value = 'warning'
      passwordVisible.value = true
    }
  }
}

async function onResetOne(row: CandidateRow): Promise<void> {
  await ElMessageBox.confirm(`确定重置 ${row.name}（${row.account}）的密码？旧会话会被立即踢下线。`, '重置密码', {
    type: 'warning',
  })
  const item = await adminApi.resetPassword(row.id)
  passwordItems.value = [{ account: item.account, name: item.name, password: item.password }]
  passwordNotice.value = '新密码已生成，请及时转告考生'
  passwordNoticeType.value = 'success'
  passwordVisible.value = true
  await reload()
}

async function onBatchReset(): Promise<void> {
  const ids = selection.value.map((item) => item.id)
  await ElMessageBox.confirm(`确定重置选中 ${ids.length} 名考生的密码？其在线会话会被立即踢下线。`, '批量重置密码', {
    type: 'warning',
  })
  const items = await adminApi.batchResetPassword(ids)
  passwordItems.value = items.map((item) => ({ account: item.account, name: item.name, password: item.password }))
  passwordNotice.value = `已重置 ${items.length} 名考生的密码（请及时分发）`
  passwordNoticeType.value = 'success'
  passwordVisible.value = true
  await reload()
}

async function onBatchStatus(status: 0 | 1): Promise<void> {
  const ids = selection.value.map((item) => item.id)
  const verb = status === 1 ? '启用' : '禁用'
  await ElMessageBox.confirm(`确定${verb}选中 ${ids.length} 名考生？`, `批量${verb}`, { type: 'warning' })
  await adminApi.batchStatus(ids, status)
  ElMessage.success(`已${verb} ${ids.length} 名考生`)
  await reload()
}

async function onToggleOne(row: CandidateRow): Promise<void> {
  const status = row.status === 1 ? 0 : 1
  await adminApi.batchStatus([row.id], status as 0 | 1)
  ElMessage.success(status === 1 ? '已启用' : '已禁用（会话已踢下线）')
  await reload()
}

async function onDelete(row: CandidateRow): Promise<void> {
  await ElMessageBox.confirm(`确定删除考生 ${row.name}（${row.account}）？其配额将一并清理，历史作品保留。`, '删除考生', {
    type: 'warning',
  })
  await adminApi.deleteCandidate(row.id)
  ElMessage.success('已删除')
  await reload()
}
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  margin-bottom: 12px;
  gap: 12px;
  flex-wrap: wrap;
}

.left,
.right {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 16px;
  margin-bottom: 12px;
  background: #fffbeb;
  border-color: #fde68a;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 16px;
}

.import-result {
  margin-top: 12px;
}

.mt-12 {
  margin-top: 12px;
}

.mt-16 {
  margin-top: 16px;
}
</style>
