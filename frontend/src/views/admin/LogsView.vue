<template>
  <div class="logs">
    <div class="panel toolbar">
      <div class="left">
        <el-input v-model="query.admin_username" placeholder="操作人账号" clearable style="width: 160px" />
        <el-select v-model="query.action" placeholder="动作类型" clearable style="width: 190px">
          <el-option v-for="item in actionOptions" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <el-date-picker
          v-model="range"
          type="datetimerange"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          style="width: 360px"
        />
        <el-button type="primary" @click="reload(1)">查询</el-button>
        <el-button @click="onReset">重置</el-button>
      </div>
    </div>

    <div class="panel">
      <el-table :data="rows" v-loading="loading" size="small" stripe>
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="diff">
              <div class="diff-col">
                <div class="diff-title">变更前 before_val</div>
                <pre>{{ pretty(row.before_val) }}</pre>
              </div>
              <div class="diff-col">
                <div class="diff-title">变更后 after_val</div>
                <pre>{{ pretty(row.after_val) }}</pre>
              </div>
            </div>
          </template>
        </el-table-column>
        <el-table-column label="时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
        </el-table-column>
        <el-table-column prop="admin_username" label="操作人" width="120" />
        <el-table-column label="动作" width="190">
          <template #default="{ row }">
            <el-tag size="small" :type="actionType(row.action)">{{ actionLabel(row.action) }}</el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="target_type" label="对象类型" width="110" />
        <el-table-column prop="target_id" label="对象 ID" width="130" show-overflow-tooltip />
        <el-table-column prop="remark" label="备注" min-width="200" show-overflow-tooltip />
        <el-table-column prop="ip_address" label="IP" width="130" />
      </el-table>

      <div class="pager">
        <el-pagination
          background
          layout="total, prev, pager, next"
          :total="total"
          :current-page="query.page"
          :page-size="query.size"
          @current-change="(page: number) => reload(page)"
        />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'

import * as adminApi from '@/api/admin'
import type { AuditLogRow } from '@/api/adminTypes'
import { formatDateTime } from '@/utils/format'

const rows = ref<AuditLogRow[]>([])
const total = ref(0)
const loading = ref(false)
const range = ref<[Date, Date] | null>(null)

const query = reactive({
  page: 1,
  size: 20,
  admin_username: '',
  action: '',
})

const actionOptions = [
  { value: 'import_users', label: '导入考生' },
  { value: 'reset_pwd', label: '重置密码' },
  { value: 'adjust_quota', label: '调整配额' },
  { value: 'refund_quota', label: '失败退还配额' },
  { value: 'create_question', label: '新增题目' },
  { value: 'update_question', label: '编辑题目' },
  { value: 'delete_question', label: '删除题目' },
  { value: 'create_time_window', label: '新增时段' },
  { value: 'update_time_window', label: '编辑时段' },
  { value: 'extend_window', label: '延长时段' },
  { value: 'delete_time_window', label: '删除时段' },
  { value: 'update_engine', label: '更新引擎' },
  { value: 'switch_default_engine', label: '切换默认引擎' },
  { value: 'toggle_status', label: '启禁用考生' },
]

const ACTION_LABELS: Record<string, string> = Object.fromEntries(
  actionOptions.map((item) => [item.value, item.label]),
)

function actionLabel(action: string): string {
  return ACTION_LABELS[action] || action
}

function actionType(action: string): 'success' | 'warning' | 'danger' | 'info' {
  if (action === 'refund_quota' || action === 'delete_question' || action === 'delete_time_window') return 'danger'
  if (action === 'adjust_quota' || action === 'switch_default_engine' || action === 'extend_window') return 'warning'
  if (action.startsWith('create')) return 'success'
  return 'info'
}

async function reload(page = query.page): Promise<void> {
  query.page = page
  loading.value = true
  try {
    const data = await adminApi.listAuditLogs({
      page: query.page,
      size: query.size,
      admin_username: query.admin_username || undefined,
      action: query.action || undefined,
      start_at: range.value?.[0] ? range.value[0].toISOString() : undefined,
      end_at: range.value?.[1] ? range.value[1].toISOString() : undefined,
    })
    rows.value = data.items
    total.value = data.total
  } finally {
    loading.value = false
  }
}

onMounted(() => reload(1))

function onReset(): void {
  query.admin_username = ''
  query.action = ''
  range.value = null
  void reload(1)
}

function pretty(value: unknown): string {
  if (value === null || value === undefined) return '—'
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}
</script>

<style scoped>
.toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 16px;
  margin-bottom: 12px;
}

.left {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.pager {
  display: flex;
  justify-content: flex-end;
  padding: 12px 16px;
}

.diff {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 8px 16px 12px;
}

.diff-title {
  font-size: 12px;
  color: var(--i2v-text-sub);
  margin-bottom: 4px;
}

pre {
  margin: 0;
  background: #0f172a;
  color: #e2e8f0;
  padding: 10px 12px;
  border-radius: 6px;
  font-size: 12px;
  max-height: 220px;
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}
</style>
