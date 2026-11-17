<script setup>
// 通用确认弹窗
defineProps({
  title: { type: String, default: '确认' },
  message: { type: String, default: '' },
  confirmText: { type: String, default: '确认' },
  cancelText: { type: String, default: '取消' },
})
const emit = defineEmits(['confirm', 'cancel'])
const open = defineModel('open', { type: Boolean, default: false })
</script>

<template>
  <div v-if="open" class="overlay" @click.self="emit('cancel')">
    <div class="dialog">
      <div class="dialog-title">{{ title }}</div>
      <p class="dialog-text">{{ message }}</p>
      <div class="dialog-actions">
        <button class="btn-cancel" @click="emit('cancel')">{{ cancelText }}</button>
        <button class="btn-confirm" @click="emit('confirm')">{{ confirmText }}</button>
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
}
.dialog {
  width: 420px;
  max-width: calc(100vw - 40px);
  background: #fff;
  border-radius: 18px;
  padding: 20px 22px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18);
}
.dialog-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 10px;
}
.dialog-text {
  font-size: 13px;
  color: #475569;
  line-height: 1.6;
  margin: 0 0 18px;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
}
.btn-cancel {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.btn-confirm {
  border: none;
  background: #3b82f6;
  color: #fff;
  border-radius: 10px;
  padding: 8px 16px;
  font-size: 12px;
  cursor: pointer;
}</style>