<template>
  <div class="engines">
    <div class="panel degrade">
      <div class="degrade-left">
        <div class="degrade-title">应急降级开关</div>
        <div class="muted">
          当前默认引擎：<b>{{ currentDefault?.code || '—' }}</b>
          （{{ currentDefault?.resolution || '—' }}）
          <span v-if="currentDefault?.code === 'wq-v1-lite'" class="text-warning">· 已处于 480P 降级状态</span>
        </div>
      </div>
      <div class="degrade-right">
        <el-button @click="reload">刷新</el-button>
        <el-button v-if="currentDefault?.code !== 'wq-v1-lite'" type="warning" :loading="switching" @click="switchTo('wq-v1-lite')">
          一键切换至 480P 降级 (wq-v1-lite)
        </el-button>
        <el-button v-else type="primary" :loading="switching" @click="switchTo('wq-v1')">
          切回 768P 主引擎 (wq-v1)
        </el-button>
      </div>
    </div>

    <div class="panel">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column label="引擎" width="170">
          <template #default="{ row }">
            <div class="code mono">{{ row.code }}</div>
            <div class="muted">{{ row.name }}</div>
          </template>
        </el-table-column>
        <el-table-column prop="model_name" label="上游模型" width="180" />
        <el-table-column prop="resolution" label="分辨率" width="90" />
        <el-table-column prop="duration" label="时长(s)" width="80" />
        <el-table-column label="单价(元/秒)" width="110">
          <template #default="{ row }">{{ Number(row.price_per_sec).toFixed(4) }}</template>
        </el-table-column>
        <el-table-column prop="max_concurrency" label="并发上限" width="90" />
        <el-table-column label="健康度" width="230">
          <template #default="{ row }">
            <el-tag :type="row.is_healthy ? 'success' : 'danger'" size="small">
              {{ row.is_healthy ? '正常' : '异常' }}
            </el-tag>
            <span class="muted ml-8">
              成功 {{ row.health.recent_success }} / 失败 {{ row.health.recent_fail }}
              <template v-if="row.health.avg_cost_sec"> · 均值 {{ row.health.avg_cost_sec.toFixed(1) }}s</template>
              <template v-if="row.health.circuit_open"> · 熔断中</template>
            </span>
          </template>
        </el-table-column>
        <el-table-column label="默认" width="80">
          <template #default="{ row }">
            <el-tag v-if="row.is_default" type="success" size="small">默认</el-tag>
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column label="状态" width="80">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
              {{ row.status === 1 ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="180" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" :disabled="row.is_default" @click="setDefault(row)">
              设为默认
            </el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="`编辑引擎 ${editing?.code || ''}`" width="480px">
      <el-form label-width="110px">
        <el-form-item label="展示名称"><el-input v-model="form.name" /></el-form-item>
        <el-form-item label="单价(元/秒)"><el-input-number v-model="form.price" :min="0" :max="10000" :precision="4" :step="0.01" /></el-form-item>
        <el-form-item label="并发上限"><el-input-number v-model="form.max_concurrency" :min="1" :max="500" /></el-form-item>
        <el-form-item label="启用状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="停用" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="dialogVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="onSave">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'

import * as adminApi from '@/api/admin'
import type { EngineRow } from '@/api/adminTypes'

const rows = ref<EngineRow[]>([])
const loading = ref(false)
const saving = ref(false)
const switching = ref(false)
const dialogVisible = ref(false)
const editing = ref<EngineRow | null>(null)

const form = reactive({ name: '', price: 0, max_concurrency: 25, status: 1 })

const currentDefault = computed(() => rows.value.find((item) => item.is_default) || null)

async function reload(): Promise<void> {
  loading.value = true
  try {
    rows.value = await adminApi.listEngines()
  } finally {
    loading.value = false
  }
}

onMounted(reload)

function openEdit(row: EngineRow): void {
  editing.value = row
  form.name = row.name
  form.price = Number(row.price_per_sec)
  form.max_concurrency = row.max_concurrency
  form.status = row.status
  dialogVisible.value = true
}

async function onSave(): Promise<void> {
  if (!editing.value) return
  saving.value = true
  try {
    await adminApi.updateEngine(editing.value.id, {
      name: form.name.trim(),
      price_per_sec: form.price,
      max_concurrency: form.max_concurrency,
      status: form.status,
    })
    ElMessage.success('引擎配置已更新，缓存已刷新')
    dialogVisible.value = false
    await reload()
  } finally {
    saving.value = false
  }
}

async function setDefault(row: EngineRow): Promise<void> {
  await ElMessageBox.confirm(`将默认引擎切换为 ${row.code}（${row.resolution}）？新提交的任务会立即使用该引擎。`, '切换默认引擎', {
    type: 'warning',
  })
  await adminApi.setDefaultEngine(row.id)
  ElMessage.success(`默认引擎已切换为 ${row.code}`)
  await reload()
}

async function switchTo(code: string): Promise<void> {
  const target = rows.value.find((item) => item.code === code)
  if (!target) {
    ElMessage.error(`未找到引擎 ${code}`)
    return
  }
  const label = code === 'wq-v1-lite' ? '480P 降级引擎' : '768P 主引擎'
  await ElMessageBox.confirm(
    `确认把默认引擎切换为 ${label}（${code}）？切换后考生下一次提交立即生效。`,
    '应急降级开关',
    { type: 'warning', confirmButtonText: '确认切换' },
  )
  switching.value = true
  try {
    await adminApi.setDefaultEngine(target.id)
    ElMessage.success(`已切换至 ${label}`)
    await reload()
  } finally {
    switching.value = false
  }
}
</script>

<style scoped>
.degrade {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 16px 20px;
  margin-bottom: 12px;
  border-left: 6px solid #d97706;
  background: #fffbeb;
}

.degrade-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 4px;
}

.degrade-right {
  display: flex;
  gap: 10px;
}

.code {
  font-weight: 700;
  color: var(--i2v-primary);
}

.ml-8 {
  margin-left: 8px;
}
</style>
