<script setup>
// 中介待确认回复卡片：后台轮询中介会话后由 agent 草拟，用户确认后发送
const props = defineProps({
  items: { type: Array, default: () => [] },
  busy: { type: Boolean, default: false },
})
const emit = defineEmits(['approve', 'reject'])

function preview(text, max = 500) {
  const body = String(text || '')
  return body.length > max ? `${body.slice(0, max)}…` : body
}
</script>

<template>
  <div v-if="props.items.length" class="pending-card">
    <div class="pending-title">待确认的中介回复（{{ props.items.length }}）</div>
    <div v-for="item in props.items" :key="item.thread_id" class="pending-item">
      <div class="pending-head">
        联系人：{{ item.contact_name || '未知' }}
        <span v-if="item.mode === 'draft'" class="badge">草稿</span>
      </div>
      <div v-if="item.source_message" class="pending-block">
        <div class="label">中介消息</div>
        <div class="text">{{ preview(item.source_message, 300) }}</div>
      </div>
      <div v-if="item.draft" class="pending-block">
        <div class="label">回复草稿</div>
        <div class="text">{{ preview(item.draft) }}</div>
      </div>
      <div class="pending-actions">
        <button class="approve-btn" :disabled="props.busy" @click="emit('approve', item)">
          批准发送
        </button>
        <button class="reject-btn" :disabled="props.busy" @click="emit('reject', item)">
          拒绝
        </button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pending-card {
  background: #fffbeb;
  border: 1px solid #fde68a;
  border-radius: 14px;
  padding: 12px 16px;
  display: flex;
  flex-direction: column;
  gap: 10px;
  margin-bottom: 10px;
}
.pending-title {
  font-size: 13px;
  font-weight: 600;
  color: #92400e;
}
.pending-item {
  display: flex;
  flex-direction: column;
  gap: 6px;
  border-top: 1px dashed #fcd34d;
  padding-top: 8px;
}
.pending-item:first-of-type {
  border-top: none;
  padding-top: 0;
}
.pending-head {
  font-size: 12.5px;
  font-weight: 600;
  color: #78350f;
}
.badge {
  margin-left: 6px;
  font-size: 11px;
  font-weight: 400;
  color: #92400e;
  background: #fef3c7;
  border-radius: 6px;
  padding: 1px 6px;
}
.pending-block {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.label {
  font-size: 11.5px;
  color: #a16207;
}
.text {
  font-size: 12.5px;
  color: #422006;
  white-space: pre-wrap;
  word-break: break-word;
  background: #fffdf5;
  border-radius: 8px;
  padding: 6px 9px;
  line-height: 1.6;
}
.pending-actions {
  display: flex;
  gap: 8px;
}
.approve-btn {
  border: none;
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  background: #16a34a;
  color: #fff;
  cursor: pointer;
}
.reject-btn {
  border: 1px solid #fca5a5;
  background: #fef2f2;
  color: #b91c1c;
  border-radius: 8px;
  padding: 5px 12px;
  font-size: 12px;
  cursor: pointer;
}
.approve-btn:disabled,
.reject-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
</style>