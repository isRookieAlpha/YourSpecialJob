<script setup>
// 大模型设置：强/弱/备用 + Embedding 合并为一张卡片，统一保存
import AmapMcpCard from './AmapMcpCard.vue'
import EmbeddingCard from './EmbeddingCard.vue'
import ModelCard from './ModelCard.vue'

defineProps({
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
  configured: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['save'])

const strong = defineModel('strong', { type: Object, default: () => ({ model: '', baseUrl: '', apiKey: '' }) })
const weak = defineModel('weak', { type: Object, default: () => ({ model: '', baseUrl: '', apiKey: '' }) })
const backup = defineModel('backup', { type: Object, default: () => ({ model: '', baseUrl: '', apiKey: '' }) })
const embedding = defineModel('embedding', { type: Object, default: () => ({ model: '', baseUrl: '', apiKey: '', dims: 1024 }) })
const amap = defineModel('amap', { type: Object, default: () => ({ apiKey: '', url: '' }) })
</script>

<template>
  <div class="card">
    <div class="card-title">大模型与MCP设置</div>
    <p class="hint">强模型用于主推理与推荐；弱模型用于结构化提取/截图识别；备用模型可留空（fallback）。</p>

    <ModelCard
      kind="strong"
      title="强模型（主推理与推荐）"
      embedded
      :show-save="false"
      :configured="configured.strong"
      v-model:model="strong.model"
      v-model:base-url="strong.baseUrl"
      v-model:api-key="strong.apiKey"
    />
    <ModelCard
      kind="weak"
      title="弱模型（结构化提取）"
      embedded
      :show-save="false"
      :configured="configured.weak"
      v-model:model="weak.model"
      v-model:base-url="weak.baseUrl"
      v-model:api-key="weak.apiKey"
    />
    <ModelCard
      kind="backup"
      title="备用模型（fallback，可留空）"
      embedded
      :show-save="false"
      :configured="configured.backup"
      v-model:model="backup.model"
      v-model:base-url="backup.baseUrl"
      v-model:api-key="backup.apiKey"
    />
    <EmbeddingCard
      embedded
      :show-save="false"
      :configured="configured.embedding"
      v-model:model="embedding.model"
      v-model:base-url="embedding.baseUrl"
      v-model:api-key="embedding.apiKey"
      v-model:dims="embedding.dims"
    />

    <AmapMcpCard
      :configured="configured.amap"
      v-model:api-key="amap.apiKey"
      v-model:url="amap.url"
    />

    <div class="save-row">
      <span v-if="message" class="ok">{{ message }}</span>
      <span v-if="error" class="err">{{ error }}</span>
      <span class="spacer" />
      <button class="save-btn" :disabled="saving" @click="emit('save')">
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
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}
.hint {
  font-size: 12px;
  color: #94a3b8;
  margin: 0;
  line-height: 1.6;
}
.save-row {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 4px;
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
}</style>