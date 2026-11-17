<script setup>
// Embedding 配置卡片（简历 RAG / 长期记忆索引使用）
defineProps({
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
const dims = defineModel('dims', { type: Number, default: 1024 })
</script>

<template>
  <div class="card" :class="{ embedded }">
    <div class="card-title">
      Embedding（向量化）
      <span v-if="configured" class="badge">key 已配置</span>
    </div>
    <label>
      <span>模型名</span>
      <input v-model="model" placeholder="如 text-embedding-v4 / bge-m3" />
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
    <label>
      <span>向量维度</span>
      <input v-model.number="dims" type="number" min="1" />
    </label>
    <p class="hint">换 embedding 模型后需重建简历索引（M4 提供）。</p>
    <div class="save-row">
      <span v-if="message" class="ok">{{ message }}</span>
      <span v-if="error" class="err">{{ error }}</span>
      <span class="spacer" />
      <button v-if="showSave" class="save-btn" :disabled="saving" @click="emit('save')">
        {{ saving ? '保存中…' : '保存本卡片' }}
      </button>
    </div>
  </div>
</template>

<style scoped>
.card.embedded {
  box-shadow: none;
  border: 1px solid #f1f5f9;
  background: #f8fafc;
  border-radius: 12px;
  padding: 12px 14px;
}
.card {
  background: #fff;
  border-radius: 16px;
  padding: 16px 18px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
  display: flex;
  flex-direction: column;
  gap: 10px;
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
.hint {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
}
.save-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 2px;
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