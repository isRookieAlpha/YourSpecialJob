<script setup>
// 通用设置：默认群名/默认日期（读取微信与截图入库用）、导出目录（ToDo PDF）、地图城市（geocode 消歧）
defineProps({
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
})
const emit = defineEmits(['save'])
const defaultGroup = defineModel('defaultGroup', { type: String, default: '' })
const defaultDate = defineModel('defaultDate', { type: String, default: '' })
const exportDir = defineModel('exportDir', { type: String, default: '' })
const amapCity = defineModel('amapCity', { type: String, default: '' })
</script>

<template>
  <div class="card">
    <div class="card-title">通用设置</div>
    <div class="row">
      <label>
        <span>默认群名（「+」菜单读取微信用）</span>
        <input v-model="defaultGroup" placeholder="如 兼职1群" />
      </label>
      <label>
        <span>默认日期（留空=今天）</span>
        <input v-model="defaultDate" type="date" />
      </label>
    </div>
    <label>
      <span>PDF 导出目录（留空=桌面）</span>
      <input v-model="exportDir" placeholder="例如 C:\Users\你\Desktop" />
    </label>
    <label>
      <span>地图城市（静态地图地址消歧用，可选）</span>
      <input v-model="amapCity" placeholder="如 上海" />
    </label>
    <p class="hint">默认群名/日期仅用于「+」菜单的读取微信；截图识别与粘贴识别使用独立来源标识。地图城市用于静态地图地址消歧（如"静安寺"）。</p>
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
.row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.row label {
  flex: 1;
  min-width: 180px;
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
  font-family: inherit;
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