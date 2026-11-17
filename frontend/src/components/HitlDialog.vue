<script setup>
// HITL 审批弹窗：展示待确认的工具调用，批准/拒绝后继续流式输出
import { ref } from 'vue'

const props = defineProps({
  hitl: { type: Object, required: true }, // { actions: [{name,args,description}], allowed: [[...]] }
})
const emit = defineEmits(['decision'])

const rejecting = ref(false)
const rejectReason = ref('')

function approve() {
  emit('decision', props.hitl.actions.map(() => ({ type: 'approve' })))
}

function reject() {
  const decision = { type: 'reject' }
  if (rejectReason.value.trim()) decision.message = rejectReason.value.trim()
  emit('decision', props.hitl.actions.map(() => decision))
}

function formatArgs(args) {
  try {
    return JSON.stringify(args || {}, null, 2)
  } catch {
    return String(args)
  }
}
</script>

<template>
  <div class="overlay">
    <div class="dialog">
      <div class="dialog-title">需要确认</div>
      <p class="dialog-desc">以下操作需要你确认后才会执行：</p>

      <div class="action-list">
        <div v-for="(action, i) in hitl.actions" :key="i" class="action-card">
          <div class="action-name">
            {{ action.name === 'send_wechat_message_to_contact' ? '向微信联系人发送消息' : action.name }}
          </div>
          <div v-if="action.name === 'send_wechat_message_to_contact'" class="send-preview">
            <div class="send-row">
              <span class="send-label">联系人</span>
              <span>{{ action.args?.contact_name || '未填写' }}</span>
            </div>
            <div class="send-row">
              <span class="send-label">消息内容</span>
            </div>
            <pre class="send-message">{{ action.args?.message || '' }}</pre>
          </div>
          <pre v-else class="action-args">{{ formatArgs(action.args) }}</pre>
          <div v-if="action.description" class="action-desc">{{ action.description }}</div>
        </div>
      </div>

      <div v-if="rejecting" class="reject-box">
        <input v-model="rejectReason" placeholder="拒绝理由（可选，会告知助手）" />
      </div>

      <div class="actions">
        <template v-if="!rejecting">
          <button class="approve-btn" @click="approve">批准执行</button>
          <button class="reject-btn" @click="rejecting = true">拒绝</button>
        </template>
        <template v-else>
          <button class="reject-btn" @click="reject">确认拒绝</button>
          <button class="cancel-btn" @click="rejecting = false">返回</button>
        </template>
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
  max-width: 460px;
  background: #fff;
  border-radius: 18px;
  padding: 20px 22px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.25);
}
.dialog-title {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}
.dialog-desc {
  margin: 6px 0 12px;
  font-size: 13px;
  color: #64748b;
}
.action-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 40vh;
  overflow-y: auto;
}
.action-card {
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  padding: 10px 12px;
  background: #f8fafc;
}
.action-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}
.action-args {
  margin: 6px 0 0;
  font-size: 12px;
  color: #475569;
  background: #fff;
  border-radius: 8px;
  padding: 8px 10px;
  overflow-x: auto;
  font-family: 'Cascadia Code', Consolas, monospace;
  white-space: pre-wrap;
  word-break: break-all;
}
.action-desc {
  margin-top: 6px;
  font-size: 11px;
  color: #94a3b8;
  white-space: pre-wrap;
}
.send-preview {
  margin-top: 6px;
  background: #fff;
  border-radius: 8px;
  padding: 8px 10px;
  font-size: 12px;
  color: #334155;
}
.send-row {
  display: flex;
  gap: 8px;
  margin-bottom: 6px;
}
.send-label {
  color: #94a3b8;
  flex: none;
}
.send-message {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: inherit;
  color: #0f172a;
  max-height: 240px;
  overflow-y: auto;
}
.reject-box {
  margin-top: 12px;
}
.reject-box input {
  width: 100%;
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  outline: none;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
button {
  border: none;
  border-radius: 10px;
  padding: 9px 18px;
  font-size: 13px;
  cursor: pointer;
}
.approve-btn {
  background: #3b82f6;
  color: #fff;
}
.reject-btn {
  background: #fef2f2;
  color: #b91c1c;
}
.cancel-btn {
  background: #f1f5f9;
  color: #475569;
}
button:hover {
  filter: brightness(0.96);
}
</style>