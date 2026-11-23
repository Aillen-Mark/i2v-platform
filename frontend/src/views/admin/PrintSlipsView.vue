<template>
  <div class="print-page">
    <div class="screen-toolbar">
      <el-button @click="router.back()">← 返回</el-button>
      <span class="muted">共 {{ slips.length }} 张密码条（含已过期项会标注）</span>
      <el-button type="primary" @click="onPrint">打印 / 另存为 PDF</el-button>
    </div>

    <div class="sheet">
      <div class="sheet-head">
        <div class="sys">图生视频创作平台 · 考生初始密码条</div>
        <div class="date">{{ today }}</div>
      </div>

      <div class="grid">
        <div v-for="slip in slips" :key="slip.id" class="slip">
          <div class="slip-sys">图生视频创作平台</div>
          <div class="slip-row"><span class="k">姓名</span><span class="v">{{ slip.name }}</span></div>
          <div class="slip-row"><span class="k">考号</span><span class="v mono">{{ slip.account }}</span></div>
          <div class="slip-row">
            <span class="k">密码</span>
            <span class="v mono pwd">{{ slip.password || '已过期，请联系监考重置' }}</span>
          </div>
          <div class="slip-tip">凭此考号密码登录考生端；密码仅本人使用，请勿外传。</div>
        </div>
        <el-empty v-if="!slips.length" description="没有可打印的密码条" />
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { fetchPasswordSlips } from '@/api/admin'
import type { PasswordSlip } from '@/api/adminTypes'

const route = useRoute()
const router = useRouter()
const slips = ref<PasswordSlip[]>([])

const today = new Date().toLocaleDateString('zh-CN')

onMounted(async () => {
  const raw = route.query.ids
  const ids = typeof raw === 'string' && raw.trim()
    ? raw.split(',').map((item) => Number(item)).filter((item) => Number.isFinite(item))
    : undefined
  slips.value = await fetchPasswordSlips(ids && ids.length ? ids : undefined)
})

function onPrint(): void {
  window.print()
}
</script>

<style scoped>
.print-page {
  background: #f1f5f9;
  min-height: 100vh;
  padding: 16px;
}

.screen-toolbar {
  display: flex;
  align-items: center;
  gap: 12px;
  margin-bottom: 16px;
}

.sheet {
  background: #fff;
  max-width: 900px;
  margin: 0 auto;
  padding: 24px;
  border-radius: 8px;
  box-shadow: 0 6px 24px rgba(15, 23, 42, 0.08);
}

.sheet-head {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  border-bottom: 2px solid #0f172a;
  padding-bottom: 8px;
  margin-bottom: 16px;
}

.sys {
  font-size: 16px;
  font-weight: 700;
}

.date {
  font-size: 12px;
  color: #64748b;
}

.grid {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 12px;
}

.slip {
  border: 1px dashed #94a3b8;
  border-radius: 6px;
  padding: 12px 14px;
  break-inside: avoid;
}

.slip-sys {
  font-size: 12px;
  color: #2563eb;
  font-weight: 600;
  margin-bottom: 8px;
}

.slip-row {
  display: flex;
  gap: 8px;
  font-size: 13px;
  line-height: 1.9;
}

.k {
  width: 36px;
  color: #64748b;
  flex: 0 0 36px;
}

.v {
  font-weight: 600;
}

.pwd {
  letter-spacing: 2px;
  font-size: 15px;
}

.slip-tip {
  margin-top: 8px;
  font-size: 11px;
  color: #94a3b8;
  line-height: 1.5;
}

@media print {
  .screen-toolbar {
    display: none !important;
  }

  .print-page {
    background: #fff;
    padding: 0;
  }

  .sheet {
    box-shadow: none;
    border-radius: 0;
    max-width: none;
    padding: 0;
  }

  .grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 10mm;
  }

  .slip {
    border: 1px dashed #000;
  }

  @page {
    size: A4;
    margin: 12mm;
  }
}
</style>
