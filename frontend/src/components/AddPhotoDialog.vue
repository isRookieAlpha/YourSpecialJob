<script setup>
// 添加照片弹窗：显示默认群名/日期（设置页配置），内部复用 UploadZone 完成截图识别入库
import UploadZone from './UploadZone.vue'

const props = defineProps({
  groupName: { type: String, default: '' },
  targetDate: { type: String, default: '' },
})
const emit = defineEmits(['close', 'ingested'])

function onIngested(stats) {
  emit('ingested', stats)
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog">
      <div class="dialog-title">添加照片</div>

      <div class="meta">
        <span>群名：<b>{{ groupName || '未配置' }}</b></span>
        <span>日期：<b>{{ targetDate || '今天' }}</b></span>
      </div>
      <p v-if="!groupName" class="warn">请先在「设置 → 通用设置」配置默认群名后再上传截图。</p>

      <div v-if="groupName" class="zone-wrap">
        <UploadZone :group-name="groupName" :target-date="targetDate" @ingested="onIngested" />
      </div>

      <div class="actions">
        <button class="close-btn" @click="emit('close')">关闭</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
  padding: 20px;
}
.dialog {
  width: 100%;
  max-width: 480px;
  background: #fff;
  border-radius: 18px;
  padding: 20px 22px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.25);
}
.dialog-title {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
  margin-bottom: 10px;
}
.meta {
  display: flex;
  gap: 16px;
  font-size: 13px;
  color: #64748b;
  margin-bottom: 10px;
}
.warn {
  font-size: 13px;
  color: #b45309;
  background: #fffbeb;
  border-radius: 10px;
  padding: 10px 12px;
}
.zone-wrap {
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 10px;
}
.actions {
  display: flex;
  justify-content: flex-end;
  margin-top: 14px;
}
.close-btn {
  border: none;
  border-radius: 10px;
  padding: 8px 18px;
  font-size: 13px;
  background: #f1f5f9;
  color: #475569;
  cursor: pointer;
}
</style>