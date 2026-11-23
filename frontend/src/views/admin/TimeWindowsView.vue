<template>
  <div class="windows">
    <div class="panel banner" :class="list.active_window_id ? 'active' : 'idle'">
      <div class="banner-left">
        <div class="banner-title">
          {{ list.active_window_id ? '当前处于开放时段内' : '当前不在开放时段' }}
        </div>
        <div class="banner-sub" v-if="list.active_window_id">
          剩余 {{ formatSeconds(remainingSec) }}（每秒刷新）
        </div>
        <div class="banner-sub" v-else>考生无法提交生成，请尽快开放时段</div>
      </div>
      <div class="banner-right">
        <span class="muted">服务器时间：{{ formatDateTime(list.server_time) }}</span>
        <el-button @click="reload">刷新</el-button>
        <el-button type="primary" @click="openCreate">新增时段</el-button>
      </div>
    </div>

    <div class="panel">
      <el-table :data="list.items" v-loading="loading" stripe>
        <el-table-column prop="name" label="时段名称" min-width="150" />
        <el-table-column label="开始时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.start_at) }}</template>
        </el-table-column>
        <el-table-column label="结束时间" width="170">
          <template #default="{ row }">{{ formatDateTime(row.end_at) }}</template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <el-tag v-if="row.is_active" type="success" size="small">生效中</el-tag>
            <el-tag v-else :type="row.status === 1 ? 'info' : 'danger'" size="small">
              {{ row.status === 1 ? '已启用' : '已停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column prop="remark" label="备注" min-width="140" show-overflow-tooltip />
        <el-table-column label="操作" width="240" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="warning" size="small" @click="openExtend(row)">临时延长</el-button>
            <el-button link type="primary" size="small" @click="onToggle(row)">
              {{ row.status === 1 ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? '编辑时段' : '新增时段'" width="520px">
      <el-form :model="form" label-width="90px">
        <el-form-item label="名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="开始时间">
          <el-date-picker v-model="form.start_at" type="datetime" placeholder="选择开始时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="结束时间">
          <el-date-picker v-model="form.end_at" type="datetime" placeholder="选择结束时间" style="width: 100%" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="备注"><el-input v-model="form.remark" /></el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>

    <el-dialog v-model="extendVisible" title="临时延长时段" width="480px">
      <el-alert
        type="warning"
        :closable="false"
        show-icon
        :title="`原结束时间：${formatDateTime(extendTarget?.end_at || null)}`"
      />
      <el-form class="mt-12" label-width="110px">
        <el-form-item label="延长方式">
          <el-radio-group v-model="extendMode">
            <el-radio-button label="minutes">按分钟</el-radio-button>
            <el-radio-button label="datetime">指定新结束时间</el-radio-button>
          </el-radio-group>
        </el-form-item>
        <el-form-item v-if="extendMode === 'minutes'" label="延长分钟数">
          <el-input-number v-model="extendMinutes" :min="1" :max="1440" />
        </el-form-item>
        <el-form-item v-else label="新结束时间">
          <el-date-picker v-model="extendAt" type="datetime" style="width: 100%" />
        </el-form-item>
        <el-form-item label="延长原因">
          <el-input v-model="extendRemark" placeholder="例如：考场临时加时 30 分钟" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="extendVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onExtend">确认延长</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import * as adminApi from '@/api/admin'
import type { TimeWindowList, TimeWindowRow } from '@/api/adminTypes'
import { formatDateTime, formatSeconds } from '@/utils/format'

const list = ref<TimeWindowList>({ server_time: null, active_window_id: null, total: 0, items: [] })
const loading = ref(false)
const saving = ref(false)
const remainingSec = ref(0)
let ticker: number | null = null

const dialogVisible = ref(false)
const editing = ref<TimeWindowRow | null>(null)
const form = reactive({
  name: '',
  start_at: null as Date | null,
  end_at: null as Date | null,
  status: 1,
  remark: '',
})

const extendVisible = ref(false)
const extendTarget = ref<TimeWindowRow | null>(null)
const extendMode = ref<'minutes' | 'datetime'>('minutes')
const extendMinutes = ref(30)
const extendAt = ref<Date | null>(null)
const extendRemark = ref('')

async function reload(): Promise<void> {
  loading.value = true
  try {
    list.value = await adminApi.listTimeWindows()
    const active = list.value.items.find((item) => item.id === list.value.active_window_id)
    remainingSec.value = active?.remaining_sec ?? 0
  } finally {
    loading.value = false
  }
}

onMounted(async () => {
  await reload()
  ticker = window.setInterval(() => {
    if (remainingSec.value > 0) {
      remainingSec.value -= 1
      if (remainingSec.value === 0) void reload()
    }
  }, 1000)
})

onBeforeUnmount(() => {
  if (ticker) window.clearInterval(ticker)
})

function openCreate(): void {
  editing.value = null
  form.name = ''
  form.start_at = new Date()
  form.end_at = new Date(Date.now() + 2 * 3600 * 1000)
  form.status = 1
  form.remark = ''
  dialogVisible.value = true
}

function openEdit(row: TimeWindowRow): void {
  editing.value = row
  form.name = row.name
  form.start_at = row.start_at ? new Date(row.start_at) : null
  form.end_at = row.end_at ? new Date(row.end_at) : null
  form.status = row.status
  form.remark = row.remark || ''
  dialogVisible.value = true
}

async function onSave(): Promise<void> {
  if (!form.name.trim() || !form.start_at || !form.end_at) {
    ElMessage.warning('请填写名称与起止时间')
    return
  }
  if (form.end_at <= form.start_at) {
    ElMessage.error('结束时间必须晚于开始时间')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name.trim(),
      start_at: form.start_at.toISOString(),
      end_at: form.end_at.toISOString(),
      status: form.status,
      remark: form.remark.trim() || null,
    }
    if (editing.value) {
      await adminApi.updateTimeWindow(editing.value.id, payload as unknown as Partial<TimeWindowRow>)
      ElMessage.success('时段已更新')
    } else {
      await adminApi.createTimeWindow(payload)
      ElMessage.success('时段已创建')
    }
    dialogVisible.value = false
    await reload()
  } finally {
    saving.value = false
  }
}

function openExtend(row: TimeWindowRow): void {
  extendTarget.value = row
  extendMode.value = 'minutes'
  extendMinutes.value = 30
  extendAt.value = row.end_at ? new Date(new Date(row.end_at).getTime() + 30 * 60 * 1000) : new Date()
  extendRemark.value = ''
  extendVisible.value = true
}

async function onExtend(): Promise<void> {
  if (!extendTarget.value) return
  await ElMessageBox.confirm('延长时段会立即生效，考生可继续提交生成。确认延长？', '二次确认', { type: 'warning' })
  saving.value = true
  try {
    const payload =
      extendMode.value === 'minutes'
        ? { extend_minutes: extendMinutes.value, confirm: true, remark: extendRemark.value || undefined }
        : { new_end_at: (extendAt.value || new Date()).toISOString(), confirm: true, remark: extendRemark.value || undefined }
    await adminApi.extendTimeWindow(extendTarget.value.id, payload)
    ElMessage.success('已延长时段')
    extendVisible.value = false
    await reload()
  } finally {
    saving.value = false
  }
}

async function onToggle(row: TimeWindowRow): Promise<void> {
  await adminApi.updateTimeWindow(row.id, { status: row.status === 1 ? 0 : 1 } as Partial<TimeWindowRow>)
  ElMessage.success('状态已更新')
  await reload()
}

async function onDelete(row: TimeWindowRow): Promise<void> {
  await ElMessageBox.confirm(`删除时段「${row.name}」？`, '删除时段', { type: 'warning' })
  await adminApi.deleteTimeWindow(row.id)
  ElMessage.success('已删除')
  await reload()
}
</script>

<style scoped>
.banner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  margin-bottom: 12px;
  border-left: 6px solid #16a34a;
}

.banner.idle {
  border-left-color: #d97706;
  background: #fffbeb;
}

.banner-title {
  font-size: 16px;
  font-weight: 700;
}

.banner-sub {
  font-size: 13px;
  color: var(--i2v-text-sub);
  margin-top: 4px;
}

.banner-right {
  display: flex;
  align-items: center;
  gap: 12px;
}

.mt-12 {
  margin-top: 12px;
}
</style>
