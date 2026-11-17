<script setup>
// 消息列表：user 右侧气泡 / assistant 左侧气泡（Markdown 渲染）/ tool 灰色信息行 / error 红色提示
import { nextTick, ref, watch } from 'vue'
import { marked } from 'marked'

// GFM + 换行即 <br>（模型回复常按行排版）
marked.setOptions({ gfm: true, breaks: true })

const props = defineProps({
  messages: { type: Array, default: () => [] },
})

const listEl = ref(null)

function renderMarkdown(content) {
  return content ? marked.parse(content) : ''
}

// 新消息或流式增量时自动滚动到底部
watch(
  () => props.messages.map((m) => m.content).join('\n').length,
  async () => {
    await nextTick()
    if (listEl.value) listEl.value.scrollTop = listEl.value.scrollHeight
  }
)
</script>

<template>
  <div ref="listEl" class="message-list">
    <template v-for="(m, i) in messages" :key="i">
      <div v-if="m.role === 'tool'" class="tool-line">
        <span class="tool-name">{{ m.name }}</span>
        <span class="tool-preview">{{ m.content }}</span>
      </div>
      <div v-else-if="m.role === 'error'" class="error-line">{{ m.content }}</div>
      <div v-else-if="m.role === 'system_note'" class="system-note">{{ m.content }}</div>
      <div v-else :class="['bubble-row', m.role]">
        <div class="bubble">
          <template v-if="m.role === 'user'">{{ m.content }}</template>
          <span v-else class="md-body" v-html="renderMarkdown(m.content)" />
          <span v-if="m.streaming && i === messages.length - 1" class="cursor" />
        </div>
      </div>
    </template>
    <div v-if="!messages.length" class="empty">
      先读取微信群消息，或直接提问：帮我找 11 月 5 日佣金 100 左右的轻体力活
    </div>
  </div>
</template>

<style scoped>
.message-list {
  flex: 1;
  overflow-y: auto;
  padding: 16px 20px;
  display: flex;
  flex-direction: column;
  gap: 10px;
}
.bubble-row {
  display: flex;
}
.bubble-row.user {
  justify-content: flex-end;
}
.bubble {
  max-width: 78%;
  padding: 10px 14px;
  border-radius: 16px;
  font-size: 14px;
  line-height: 1.65;
  white-space: pre-wrap;
  word-break: break-word;
}
.bubble-row.user .bubble {
  background: #3b82f6;
  color: #fff;
  border-bottom-right-radius: 4px;
}
.bubble-row.assistant .bubble {
  background: #fff;
  color: #1e293b;
  box-shadow: 0 2px 10px rgba(30, 41, 59, 0.06);
  border-bottom-left-radius: 4px;
  white-space: normal;
  overflow-x: auto;
}
/* Markdown 内容样式（v-html 需要 :deep 穿透 scoped） */
.md-body :deep(p) {
  margin: 0 0 8px;
}
.md-body :deep(p:last-child) {
  margin-bottom: 0;
}
.md-body :deep(h1),
.md-body :deep(h2),
.md-body :deep(h3),
.md-body :deep(h4) {
  margin: 12px 0 6px;
  line-height: 1.4;
  font-weight: 700;
}
.md-body :deep(h1) { font-size: 18px; }
.md-body :deep(h2) { font-size: 16px; }
.md-body :deep(h3) { font-size: 15px; }
.md-body :deep(h4) { font-size: 14px; }
.md-body :deep(ul),
.md-body :deep(ol) {
  margin: 4px 0 8px;
  padding-left: 20px;
}
.md-body :deep(li) {
  margin: 2px 0;
}
.md-body :deep(strong) {
  font-weight: 700;
  color: #0f172a;
}
.md-body :deep(code) {
  background: #f1f5f9;
  border-radius: 4px;
  padding: 1px 5px;
  font-size: 12.5px;
  font-family: 'Cascadia Code', Consolas, monospace;
}
.md-body :deep(pre) {
  background: #f1f5f9;
  border-radius: 10px;
  padding: 10px 12px;
  overflow-x: auto;
  margin: 6px 0;
}
.md-body :deep(pre code) {
  background: none;
  padding: 0;
}
.md-body :deep(blockquote) {
  margin: 6px 0;
  padding-left: 10px;
  border-left: 3px solid #cbd5e1;
  color: #64748b;
}
.md-body :deep(hr) {
  border: none;
  border-top: 1px solid #e2e8f0;
  margin: 10px 0;
}
.md-body :deep(table) {
  border-collapse: collapse;
  margin: 6px 0;
  font-size: 13px;
}
.md-body :deep(th),
.md-body :deep(td) {
  border: 1px solid #e2e8f0;
  padding: 4px 8px;
  text-align: left;
}
.md-body :deep(th) {
  background: #f8fafc;
}
.md-body :deep(a) {
  color: #2563eb;
}
.cursor {
  display: inline-block;
  width: 7px;
  height: 15px;
  background: #94a3b8;
  margin-left: 2px;
  vertical-align: text-bottom;
  animation: blink 0.9s infinite;
}
@keyframes blink {
  50% { opacity: 0; }
}
.tool-line {
  align-self: flex-start;
  font-size: 12px;
  color: #64748b;
  background: #f1f5f9;
  border-radius: 10px;
  padding: 6px 10px;
  max-width: 88%;
}
.tool-name {
  font-weight: 600;
  margin-right: 6px;
  color: #475569;
}
.tool-preview {
  color: #94a3b8;
}
.error-line {
  align-self: center;
  font-size: 13px;
  color: #b91c1c;
  background: #fef2f2;
  border-radius: 12px;
  padding: 8px 14px;
}
/* 历史回放中的摘要提示行（SummarizationMiddleware 压缩旧消息） */
.system-note {
  align-self: center;
  font-size: 12px;
  color: #94a3b8;
  padding: 2px 12px;
  border-top: 1px dashed #e2e8f0;
  border-bottom: 1px dashed #e2e8f0;
}
.empty {
  margin: auto;
  color: #94a3b8;
  font-size: 14px;
  text-align: center;
  line-height: 1.8;
}
</style>