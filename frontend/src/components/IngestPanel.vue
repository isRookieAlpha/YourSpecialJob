<script setup>
// 微信摄入面板：群名 + 日期 → 后端直调读取（长耗时，期间请勿操作微信）
import { ref } from 'vue'
import { ingestGroup } from '../api/http'
import UploadZone from './UploadZone.vue'

const emit = defineEmits(['ingested'])

const groupName = ref('兼职1群')
const targetDate = ref(new Date().toISOString().slice(0, 10))
const loading = ref(false)
const stats = ref(null)
const error = ref('')

async function run() {
  if (loading.value || !groupName.value.trim()) return
  loading.value = true
  stats.value = null
  error.value = ''
  try {
    stats.value = await ingestGroup(groupName.value.trim(), targetDate.value)
    emit('ingested', stats.value)
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="ingest-panel">
    <div class="ingest-row">
      <input v-model="groupName" placeholder="群名" :disabled="loading" />
      <input v-model="targetDate" type="date" :disabled="loading" />
      <button :disabled="loading" @click="run">
        {{ loading ? '读取中…' : '读取微信' }}
      </button>
    </div>
    <div v-if="loading" class="hint">正在操作微信窗口，请勿手动操作微信（约 30~90 秒）</div>
    <div v-else-if="stats" class="stats">
      共 {{ stats.total_messages }} 条 · 有效 {{ stats.llm_valid }} · 新入库
      {{ stats.new_inserted }} · 重复 {{ stats.dup_raw + stats.dup_jsonl + stats.dup_db }}
    </div>
    <div v-else-if="error" class="err">{{ error }}</div>

    <!-- 微信读取失败时的兜底：截图多模态识别入库 -->
    <UploadZone :group-name="groupName" :target-date="targetDate" @ingested="emit('ingested', $event)" />
  </div>
</template>

<style scoped>
.ingest-panel {
  display: flex;
  flex-direction: column;
  gap: 8px;
  flex: 1;
  min-width: 0;
}
.ingest-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
input {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  width: 120px;
  outline: none;
  background: #fff;
}
input[type='date'] {
  width: 140px;
}
button {
  border: none;
  border-radius: 10px;
  padding: 8px 14px;
  font-size: 13px;
  background: #ecfdf5;
  color: #059669;
  cursor: pointer;
}
button:disabled {
  opacity: 0.6;
  cursor: default;
}
.hint {
  width: 100%;
  font-size: 12px;
  color: #d97706;
}
.stats {
  width: 100%;
  font-size: 12px;
  color: #059669;
}
.err {
  width: 100%;
  font-size: 12px;
  color: #b91c1c;
}
</style>