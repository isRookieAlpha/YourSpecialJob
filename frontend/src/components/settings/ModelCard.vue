<script setup>
// 模型配置卡片（强/弱/备用复用），含连通性测试
import { ref } from 'vue'
import { testModel } from '../../api/http'

const props = defineProps({
  kind: { type: String, required: true },
  title: { type: String, required: true },
  configured: { type: Boolean, default: false },
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
  showSave: { type: Boolean, default: true },
  embedded: { type: Boolean, default: false },
})
const emit = defineEmits(['save'])
const model = defineModel('model', { type: String, default: '' })
const baseUrl = defineModel('baseUrl', { type: String, default: '' })
const apiKey = defineModel('apiKey', { type: String, default: '' })

const testing = ref(false)
const testResult = ref('')

async function runTest() {
  testing.value = true
  testResult.value = ''
  try {
    const r = await testModel(props.kind)
    testResult.value = r.ok ? `连通正常（${r.elapsed_sec}s）` : `失败：${r.error}`
  } catch (e) {
    testResult.value = `失败：${e.message}`
  } finally {
    testing.value = false
  }
}
</script>

<template>
  <div class="card" :class="{ embedded }">
    <div class="card-title">
      {{ title }}
      <span v-if="configured" class="badge">key 已配置</span>
    </div>
    <label>
      <span>模型名</span>
      <input v-model="model" placeholder="如 qwen3.5-omni-plus" />
    </label>
    <label>
      <span>Base URL</span>
      <input v-model="baseUrl" placeholder="https://…/compatible-mode/v1" />
    </label>
    <label>
      <span>API Key</span>
      <input
        v-model="apiKey"
        type="password"
        :placeholder="configured ? '已配置，留空表示不修改' : '未配置'"
      />
    </label>
    <div class="card-actions">
      <button class="test-btn" :disabled="testing" @click="runTest">
        {{ testing ? '测试中…' : '测试连通' }}
      </button>
      <span v-if="testResult" class="test-result">{{ testResult }}</span>
      <span class="spacer" />
      <span v-if="message" class="ok">{{ message }}</span>
      <span v-if="error" class="err">{{ error }}</span>
      <button v-if="showSave" class="save-btn" :disabled="saving" @click="emit('save')">
        {{ saving ? '保存中…' : '保存本卡片' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.card {
  background: #fff;
  border-radius: 16px;
  padding: 16px 18px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.card.embedded {
  box-shadow: none;
  border: 1px solid #f1f5f9;
  background: #f8fafc;
  border-radius: 12px;
  padding: 12px 14px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  display: flex;
  align-items: center;
  gap: 8px;
}
.badge {
  font-size: 11px;
  font-weight: 400;
  color: #059669;
  background: #ecfdf5;
  border-radius: 8px;
  padding: 2px 8px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
}
input {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  outline: none;
  color: #0f172a;
}
input:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(147, 197, 253, 0.25);
}
.card-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}
.test-btn {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  padding: 6px 12px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.test-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.test-result {
  font-size: 12px;
  color: #64748b;
}
.spacer {
  flex: 1;
}
.save-btn {
  border: none;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  background: #3b82f6;
  color: #fff;
  cursor: pointer;
}
.save-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.ok {
  font-size: 12px;
  color: #059669;
}
.err {
  font-size: 12px;
  color: #b91c1c;
}
</style>