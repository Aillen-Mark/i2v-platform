<template>
  <div class="quotas">
    <el-tabs v-model="tab" class="panel tabs">
      <!-- 按题批量 -->
      <el-tab-pane label="按题批量设置" name="question">
        <div class="form-block">
          <el-form label-width="120px">
            <el-form-item label="题目">
              <el-select v-model="qForm.question_id" placeholder="选择题目" style="width: 320px">
                <el-option
                  v-for="item in questions"
                  :key="item.id"
                  :label="`${item.code} · ${item.title}`"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="目标次数">
              <el-input-number v-model="qForm.quota_total" :min="0" :max="100" />
              <span class="muted ml-8">该题全体考生统一设为该值</span>
            </el-form-item>
            <el-form-item label="调整原因">
              <el-input v-model="qForm.remark" placeholder="必填，将写入操作审计" style="width: 420px" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="saving" @click="submitByQuestion">保存</el-button>
            </el-form-item>
          </el-form>
        </div>
      </el-tab-pane>

      <!-- 按考生 -->
      <el-tab-pane label="按考生调整" name="candidate">
        <div class="form-block">
          <el-form label-width="120px">
            <el-form-item label="选择考生">
              <el-select
                v-model="cForm.candidate_id"
                filterable
                remote
                :remote-method="searchCandidates"
                :loading="searching"
                placeholder="输入考号或姓名搜索"
                style="width: 360px"
                @change="loadCandidateQuotas"
              >
                <el-option
                  v-for="item in candidateOptions"
                  :key="item.id"
                  :label="`${item.account} · ${item.name}`"
                  :value="item.id"
                />
              </el-select>
            </el-form-item>
            <el-form-item label="一键统一为" v-if="cForm.candidate_id">
              <el-input-number v-model="cForm.uniform" :min="0" :max="100" />
              <el-button class="ml-8" @click="applyUniform">应用到全部启用题目</el-button>
            </el-form-item>
            <el-form-item label="调整原因">
              <el-input v-model="cForm.remark" placeholder="必填，将写入操作审计" style="width: 420px" />
            </el-form-item>
          </el-form>

          <el-table :data="candidateQuotas" v-loading="loadingQuotas" size="small" max-height="360">
            <el-table-column prop="question_code" label="题号" width="90" />
            <el-table-column prop="question_title" label="题目" min-width="160" />
            <el-table-column prop="quota_used" label="已用" width="80" />
            <el-table-column label="总次数" width="150">
              <template #default="{ row }">
                <el-input-number v-model="row.quota_total" :min="0" :max="100" size="small" />
              </template>
            </el-table-column>
            <el-table-column label="剩余" width="80">
              <template #default="{ row }">{{ Math.max(row.quota_total - row.quota_used, 0) }}</template>
            </el-table-column>
            <el-table-column label="已调整" width="90">
              <template #default="{ row }">
                <el-tag v-if="row.adjusted_flag" size="small" type="warning">已调整</el-tag>
                <span v-else class="muted">—</span>
              </template>
            </el-table-column>
          </el-table>
          <div class="mt-12">
            <el-button type="primary" :disabled="!cForm.candidate_id || !candidateQuotas.length" :loading="saving" @click="submitByCandidate">
              保存该考生配额
            </el-button>
          </div>
        </div>
      </el-tab-pane>

      <!-- 多人批量加减 -->
      <el-tab-pane label="多人批量加减" name="batch">
        <div class="form-block">
          <el-form label-width="120px">
            <el-form-item label="考生（多人）">
              <el-select
                v-model="bForm.candidate_ids"
                multiple
                filterable
                remote
                :remote-method="searchCandidates"
                :loading="searching"
                placeholder="输入考号或姓名搜索并多选"
                style="width: 520px"
              >
                <el-option
                  v-for="item in candidateOptions"
                  :key="item.id"
                  :label="`${item.account} · ${item.name}`"
                  :value="item.id"
                />
              </el-select>
              <span class="muted ml-8">已选 {{ bForm.candidate_ids.length }} 人</span>
            </el-form-item>
            <el-form-item label="题目">
              <el-select v-model="bForm.question_id" placeholder="选择题目" style="width: 320px">
                <el-option v-for="item in questions" :key="item.id" :label="`${item.code} · ${item.title}`" :value="item.id" />
              </el-select>
            </el-form-item>
            <el-form-item label="增减次数">
              <el-input-number v-model="bForm.delta" :min="-100" :max="100" />
              <span class="muted ml-8">正数为加、负数为减；减到低于已用次数会触发二次确认</span>
            </el-form-item>
            <el-form-item label="调整原因">
              <el-input v-model="bForm.remark" placeholder="必填，将写入操作审计" style="width: 420px" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" :loading="saving" @click="submitBatch">提交调整</el-button>
            </el-form-item>
          </el-form>
        </div>
      </el-tab-pane>
    </el-tabs>

    <!-- 下调保护二次确认 -->
    <el-dialog v-model="confirmVisible" title="配额下调二次确认" width="520px" :close-on-click-modal="false">
      <el-alert type="warning" :closable="false" show-icon :title="confirmTitle" />
      <el-table v-if="confirmSamples.length" :data="confirmSamples" size="small" class="mt-12" max-height="220">
        <el-table-column prop="account" label="考号" width="150" />
        <el-table-column prop="name" label="姓名" width="100" />
        <el-table-column prop="quota_used" label="已用" width="80" />
        <el-table-column prop="target" label="目标" width="80" />
      </el-table>
      <el-form class="mt-12" label-width="90px">
        <el-form-item label="调整原因" required>
          <el-input v-model="confirmRemark" type="textarea" :rows="2" placeholder="必填：说明为什么下调到低于已用次数" />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="confirmVisible = false">取消</el-button>
        <el-button type="danger" :disabled="!confirmRemark.trim()" :loading="saving" @click="confirmForce">确认下调</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import type { AxiosError } from 'axios'

import * as adminApi from '@/api/admin'
import type { ApiError } from '@/api/types'
import type { CandidateRow, QuestionRow, QuotaRow } from '@/api/adminTypes'

const route = useRoute()

const tab = ref<'question' | 'candidate' | 'batch'>('question')
const questions = ref<QuestionRow[]>([])
const saving = ref(false)

const qForm = reactive({ question_id: null as number | null, quota_total: 2, remark: '' })
const cForm = reactive({ candidate_id: null as number | null, uniform: 2, remark: '' })
const bForm = reactive({ candidate_ids: [] as number[], question_id: null as number | null, delta: 1, remark: '' })

const candidateOptions = ref<CandidateRow[]>([])
const searching = ref(false)
const candidateQuotas = ref<QuotaRow[]>([])
const loadingQuotas = ref(false)

const confirmVisible = ref(false)
const confirmTitle = ref('')
const confirmSamples = ref<{ account: string; name: string; quota_used: number; target: number }[]>([])
const confirmRemark = ref('')
let pendingAction: (() => Promise<void>) | null = null

onMounted(async () => {
  questions.value = await adminApi.listQuestions()
  const preset = route.query.candidate_ids
  if (typeof preset === 'string' && preset.trim()) {
    bForm.candidate_ids = preset.split(',').map((item) => Number(item)).filter((item) => Number.isFinite(item))
    tab.value = 'batch'
  }
  await searchCandidates('')
})

async function searchCandidates(keyword: string): Promise<void> {
  searching.value = true
  try {
    const data = await adminApi.listCandidates({ page: 1, size: 50, keyword: keyword || undefined })
    candidateOptions.value = data.items
  } finally {
    searching.value = false
  }
}

async function loadCandidateQuotas(): Promise<void> {
  if (!cForm.candidate_id) return
  loadingQuotas.value = true
  try {
    const data = await adminApi.listQuotas({ candidate_id: cForm.candidate_id, page: 1, size: 100 })
    candidateQuotas.value = data.items
  } finally {
    loadingQuotas.value = false
  }
}

function applyUniform(): void {
  candidateQuotas.value = candidateQuotas.value.map((row) => ({ ...row, quota_total: cForm.uniform }))
}

function requireRemark(value: string): boolean {
  if (!value.trim()) {
    ElMessage.warning('请填写调整原因（将写入操作审计）')
    return false
  }
  return true
}

function handleConflict(error: unknown, retry: () => Promise<void>): void {
  const axiosError = error as AxiosError<ApiError>
  if (axiosError.response?.status !== 409) return
  const detail = axiosError.response.data.detail as
    | { affected_count?: number; samples?: { account: string; name: string; quota_used: number; target?: number }[] }
    | undefined
  confirmTitle.value = `有 ${detail?.affected_count ?? 0} 名考生的已用次数大于目标值，继续下调会导致「已用 > 总数」。确认要继续吗？`
  confirmSamples.value = (detail?.samples ?? []).map((item) => ({
    account: item.account,
    name: item.name,
    quota_used: item.quota_used,
    target: item.target ?? qForm.quota_total,
  }))
  confirmRemark.value = ''
  pendingAction = retry
  confirmVisible.value = true
}

async function confirmForce(): Promise<void> {
  if (!pendingAction) return
  if (!confirmRemark.value.trim()) {
    ElMessage.warning('必须填写调整原因')
    return
  }
  saving.value = true
  try {
    await pendingAction()
    confirmVisible.value = false
    pendingAction = null
  } finally {
    saving.value = false
  }
}

async function submitByQuestion(): Promise<void> {
  if (!qForm.question_id) {
    ElMessage.warning('请选择题目')
    return
  }
  if (!requireRemark(qForm.remark)) return
  saving.value = true
  try {
    await adminApi.batchByQuestion({
      question_id: qForm.question_id,
      quota_total: qForm.quota_total,
      remark: qForm.remark,
    })
    ElMessage.success('已按题批量更新配额')
    qForm.remark = ''
  } catch (error) {
    handleConflict(error, async () => {
      await adminApi.batchByQuestion({
        question_id: qForm.question_id as number,
        quota_total: qForm.quota_total,
        force_confirm: true,
        remark: confirmRemark.value,
      })
      ElMessage.success('已在二次确认后完成下调')
      qForm.remark = ''
    })
  } finally {
    saving.value = false
  }
}

async function submitByCandidate(): Promise<void> {
  if (!cForm.candidate_id) return
  if (!requireRemark(cForm.remark)) return
  const items = candidateQuotas.value.map((row) => ({ question_id: row.question_id, quota_total: row.quota_total }))
  saving.value = true
  try {
    await adminApi.byCandidate(cForm.candidate_id, { items, remark: cForm.remark })
    ElMessage.success('已更新该考生配额')
    cForm.remark = ''
    await loadCandidateQuotas()
  } catch (error) {
    handleConflict(error, async () => {
      await adminApi.byCandidate(cForm.candidate_id as number, {
        items,
        force_confirm: true,
        remark: confirmRemark.value,
      })
      ElMessage.success('已在二次确认后完成下调')
      cForm.remark = ''
      await loadCandidateQuotas()
    })
  } finally {
    saving.value = false
  }
}

async function submitBatch(): Promise<void> {
  if (!bForm.candidate_ids.length || !bForm.question_id) {
    ElMessage.warning('请选择考生与题目')
    return
  }
  if (!requireRemark(bForm.remark)) return
  saving.value = true
  try {
    await adminApi.batchAdjust({
      candidate_ids: bForm.candidate_ids,
      question_id: bForm.question_id,
      delta: bForm.delta,
      remark: bForm.remark,
    })
    ElMessage.success('批量调整完成')
    bForm.remark = ''
  } catch (error) {
    handleConflict(error, async () => {
      await adminApi.batchAdjust({
        candidate_ids: bForm.candidate_ids,
        question_id: bForm.question_id as number,
        delta: bForm.delta,
        force_confirm: true,
        remark: confirmRemark.value,
      })
      ElMessage.success('已在二次确认后完成批量下调')
      bForm.remark = ''
    })
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.tabs {
  padding: 8px 16px 16px;
}

.form-block {
  padding: 8px 4px;
}

.ml-8 {
  margin-left: 8px;
}

.mt-12 {
  margin-top: 12px;
}
</style>
