<template>
  <div class="questions">
    <div class="panel toolbar">
      <span class="muted">共 {{ rows.length }} 道题（含停用）</span>
      <div class="right">
        <el-button @click="reload">刷新</el-button>
        <el-button type="primary" @click="openCreate">新增题目</el-button>
      </div>
    </div>

    <div class="panel">
      <el-table :data="rows" v-loading="loading" stripe>
        <el-table-column prop="code" label="题号" width="90" />
        <el-table-column prop="title" label="标题" min-width="160" />
        <el-table-column label="参考图" width="100">
          <template #default="{ row }">
            <el-image
              v-if="row.ref_image_url"
              :src="row.ref_image_url"
              :preview-src-list="[row.ref_image_url]"
              fit="cover"
              style="width: 56px; height: 40px; border-radius: 4px"
            />
            <span v-else class="muted">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="default_quota" label="默认次数" width="90" />
        <el-table-column prop="sort_order" label="排序" width="80" />
        <el-table-column label="示例提示词" width="110">
          <template #default="{ row }">{{ (row.prompt_examples || []).length }} 条</template>
        </el-table-column>
        <el-table-column label="状态" width="90">
          <template #default="{ row }">
            <el-tag :type="row.status === 1 ? 'success' : 'info'" size="small">
              {{ row.status === 1 ? '启用' : '停用' }}
            </el-tag>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="210" fixed="right">
          <template #default="{ row }">
            <el-button link type="primary" size="small" @click="openEdit(row)">编辑</el-button>
            <el-button link type="primary" size="small" @click="onToggle(row)">
              {{ row.status === 1 ? '停用' : '启用' }}
            </el-button>
            <el-button link type="danger" size="small" @click="onDelete(row)">删除</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="dialogVisible" :title="editing ? `编辑题目 ${form.code}` : '新增题目'" width="640px">
      <el-form :model="form" label-width="96px">
        <el-form-item label="题号">
          <el-input v-model="form.code" placeholder="留空自动生成 Q1/Q2…" :disabled="!!editing" />
        </el-form-item>
        <el-form-item label="标题"><el-input v-model="form.title" /></el-form-item>
        <el-form-item label="题目要求">
          <el-input v-model="form.description" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="默认次数">
          <el-input-number v-model="form.default_quota" :min="0" :max="100" />
        </el-form-item>
        <el-form-item label="排序">
          <el-input-number v-model="form.sort_order" :min="0" :max="9999" />
        </el-form-item>
        <el-form-item label="状态">
          <el-switch v-model="form.status" :active-value="1" :inactive-value="0" active-text="启用" inactive-text="停用" />
        </el-form-item>
        <el-form-item label="示例提示词">
          <div class="examples-editor">
            <div v-for="(_, index) in form.prompt_examples" :key="index" class="example-row">
              <el-input v-model="form.prompt_examples[index]" :placeholder="`示例 ${index + 1}`" maxlength="200" />
              <el-button link type="danger" @click="removeExample(index)">删除</el-button>
            </div>
            <el-button v-if="form.prompt_examples.length < 3" link type="primary" @click="addExample">
              + 添加示例（最多 3 条）
            </el-button>
          </div>
        </el-form-item>
        <el-form-item label="参考图">
          <div class="ref-upload">
            <el-image v-if="form.ref_image_url" :src="form.ref_image_url" fit="contain" class="ref-preview" />
            <input ref="refInput" type="file" accept="image/jpeg,image/png,image/webp" @change="onRefPicked" />
            <div class="muted">JPG / PNG / WEBP，≤10MB；保存题目后可上传（新增时先保存再上传）</div>
          </div>
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
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import type { AxiosError } from 'axios'

import * as adminApi from '@/api/admin'
import type { ApiError } from '@/api/types'
import type { QuestionRow } from '@/api/adminTypes'

const rows = ref<QuestionRow[]>([])
const loading = ref(false)
const dialogVisible = ref(false)
const saving = ref(false)
const editing = ref<QuestionRow | null>(null)
const refInput = ref<HTMLInputElement | null>(null)

const form = reactive({
  code: '',
  title: '',
  description: '',
  default_quota: 2,
  sort_order: 0,
  status: 1,
  prompt_examples: [] as string[],
  ref_image_url: '' as string | null,
})

async function reload(): Promise<void> {
  loading.value = true
  try {
    rows.value = await adminApi.listQuestions()
  } finally {
    loading.value = false
  }
}

onMounted(reload)

function resetForm(): void {
  form.code = ''
  form.title = ''
  form.description = ''
  form.default_quota = 2
  form.sort_order = rows.value.length * 10
  form.status = 1
  form.prompt_examples = []
  form.ref_image_url = null
}

function openCreate(): void {
  editing.value = null
  resetForm()
  dialogVisible.value = true
}

function openEdit(row: QuestionRow): void {
  editing.value = row
  form.code = row.code
  form.title = row.title
  form.description = row.description
  form.default_quota = row.default_quota
  form.sort_order = row.sort_order
  form.status = row.status
  form.prompt_examples = [...(row.prompt_examples || [])]
  form.ref_image_url = row.ref_image_url
  dialogVisible.value = true
}

function addExample(): void {
  if (form.prompt_examples.length < 3) form.prompt_examples.push('')
}

function removeExample(index: number): void {
  form.prompt_examples.splice(index, 1)
}

async function onSave(): Promise<void> {
  if (!form.title.trim() || !form.description.trim()) {
    ElMessage.warning('标题与题目要求不能为空')
    return
  }
  saving.value = true
  try {
    const payload = {
      title: form.title.trim(),
      description: form.description.trim(),
      default_quota: form.default_quota,
      sort_order: form.sort_order,
      status: form.status,
      prompt_examples: form.prompt_examples.filter((item) => item.trim()).slice(0, 3),
    }
    if (editing.value) {
      await adminApi.updateQuestion(editing.value.id, payload)
      ElMessage.success('题目已更新')
    } else {
      await adminApi.createQuestion({ ...payload, code: form.code.trim() || undefined })
      ElMessage.success('题目已创建，已为全体考生补建配额')
    }
    dialogVisible.value = false
    await reload()
  } finally {
    saving.value = false
  }
}

async function onRefPicked(event: Event): Promise<void> {
  const target = event.target as HTMLInputElement
  const file = target.files?.[0]
  if (!file) return
  if (!editing.value) {
    ElMessage.warning('请先保存题目，再上传参考图')
    target.value = ''
    return
  }
  try {
    const updated = await adminApi.uploadRefImage(editing.value.id, file)
    form.ref_image_url = updated.ref_image_url
    ElMessage.success('参考图已上传')
    await reload()
  } finally {
    target.value = ''
  }
}

async function onToggle(row: QuestionRow): Promise<void> {
  const next = row.status === 1 ? 0 : 1
  await adminApi.updateQuestion(row.id, { status: next })
  ElMessage.success(next === 1 ? '题目已启用（已兜底补建配额）' : '题目已停用')
  await reload()
}

async function onDelete(row: QuestionRow): Promise<void> {
  await ElMessageBox.confirm(`删除题目 ${row.code}（${row.title}）？`, '删除题目', { type: 'warning' })
  try {
    await adminApi.deleteQuestion(row.id)
    ElMessage.success('题目已删除')
  } catch (error) {
    const axiosError = error as AxiosError<ApiError>
    if (axiosError.response?.status === 409) {
      await ElMessageBox.confirm(
        '该题目已有作品记录，不能物理删除。是否改为「停用」？（考生端不再展示，历史作品与配额保留）',
        '无法删除',
        { type: 'warning', confirmButtonText: '改为停用', cancelButtonText: '取消' },
      )
      await adminApi.updateQuestion(row.id, { status: 0 })
      ElMessage.success('已改为停用')
    }
  }
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
}

.right {
  display: flex;
  gap: 8px;
}

.examples-editor {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.example-row {
  display: flex;
  gap: 8px;
  align-items: center;
}

.ref-upload {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.ref-preview {
  width: 160px;
  height: 100px;
  border: 1px solid var(--i2v-border);
  border-radius: 6px;
}
</style>
