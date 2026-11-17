<script setup>
// 高德 MCP 配置：Remote URL（streamable HTTP）优先，留空回退本地 npx
defineProps({
  configured: { type: Boolean, default: false },
  embedded: { type: Boolean, default: true },
})
const apiKey = defineModel('apiKey', { type: String, default: '' })
const url = defineModel('url', { type: String, default: '' })
</script>

<template>
  <div class="card" :class="{ embedded }">
    <div class="card-title">
      高德魔搭 MCP
      <span v-if="configured" class="badge">key 已配置</span>
    </div>
    <label>
      <span>AMAP_MAPS_API_KEY</span>
      <input
        v-model="apiKey"
        type="password"
        :placeholder="configured ? '已配置，留空表示不修改' : '未配置（省市区/天气/静态地图需要）'"
      />
    </label>
    <label>
      <span>MCP URL（streamable HTTP，留空使用本地 npx）</span>
      <input v-model="url" placeholder="https://mcp.api-inference.modelscope.net/…/mcp" />
    </label>
    <p class="hint">
      Remote URL 只用于通勤 MCP 计算；省市区选择、天气、静态地图等 REST 接口仍需要 AMAP_MAPS_API_KEY。
      清空 URL 保存即回退本地 npx。
    </p>
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
</style>
