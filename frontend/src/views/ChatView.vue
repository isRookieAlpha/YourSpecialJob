<script setup>
// 聊天页：消息流 + todo 步骤条 + 输入框（+菜单/截图/读微信）+ 右侧心动的offer面板
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref, watch } from 'vue'
import ChatInput from '../components/ChatInput.vue'
import ContactPendingCard from '../components/ContactPendingCard.vue'
import HitlDialog from '../components/HitlDialog.vue'
import MessageList from '../components/MessageList.vue'
import OfferPanel from '../components/OfferPanel.vue'
import TodoProgress from '../components/TodoProgress.vue'
import {
  fetchMessages,
  getContactPending,
  resumeContactPending,
  uploadScreenshot,
} from '../api/http'
import { resumeChat, streamChat } from '../api/sse'
import { useSessionStore } from '../stores/session'

const session = useSessionStore()
const messages = ref([])
const todos = ref([])
const progressText = ref('')
const streaming = ref(false)
const pendingHitl = ref(null) // 待审批的 HITL 请求 { actions, allowed }
const offerCandidates = ref([]) // 本轮 query_jobs 推送的结构化候选
const offerOpen = ref(false)
const contactPending = ref([]) // 后台轮询生成的中介待确认回复
const contactPendingBusy = ref(false)
let controller = null
let aborted = false
let contactTimer = null

// 加载会话历史（threadId 为空时清空；切换/新建会话与刷新页面共用）
async function loadHistory() {
  messages.value = []
  todos.value = []
  progressText.value = ''
  if (!session.threadId) return
  try {
    const data = await fetchMessages(session.threadId)
    messages.value = data.messages || []
    todos.value = data.todos || []
  } catch {
    // 历史加载失败不影响继续聊天（静默降级）
  }
}

// 会话切换/新建时：先中止在途流（修复旧流污染新会话），再清空并加载历史
watch(() => session.resetToken, async () => {
  stop()
  pendingHitl.value = null
  offerOpen.value = false
  offerCandidates.value = []
  await loadHistory()
})

// 首次挂载：恢复 localStorage 中上次会话的历史
onMounted(loadHistory)

// 中介待确认：每 10 秒刷新（后台轮询生成，网页/微信双通道确认）
async function loadContactPending() {
  try {
    const data = await getContactPending()
    contactPending.value = data.items || []
  } catch {
    // 静默：服务降级时不打扰聊天
  }
}

function stopContactPolling() {
  if (contactTimer) {
    clearInterval(contactTimer)
    contactTimer = null
  }
}

function startContactPolling() {
  stopContactPolling()
  loadContactPending()
  contactTimer = setInterval(loadContactPending, 10000)
}

async function onContactDecision(item, decision) {
  if (contactPendingBusy.value) return
  contactPendingBusy.value = true
  try {
    const result = await resumeContactPending(item.thread_id, decision)
    messages.value.push({
      role: 'system_note',
      content: result.contact_name
        ? `中介「${result.contact_name}」：${result.message}`
        : result.message,
    })
  } catch (e) {
    messages.value.push({ role: 'error', content: e.message })
  } finally {
    contactPendingBusy.value = false
    await loadContactPending()
  }
}

onMounted(startContactPolling)
onActivated(startContactPolling)
onDeactivated(stopContactPolling)
onBeforeUnmount(stopContactPolling)

function pushAssistantStreaming() {
  const last = messages.value[messages.value.length - 1]
  if (last && last.role === 'assistant' && last.streaming) return last
  // 1.新段开始：先复位上一段的流式标记（光标只应出现在当前段）
  messages.value.forEach((m) => {
    if (m.streaming) m.streaming = false
  })
  messages.value.push({ role: 'assistant', content: '', streaming: true })
  // 必须返回数组里的响应式代理对象：直接改 push 前的原始对象不会触发 Vue 重渲染
  return messages.value[messages.value.length - 1]
}

function handleEvent(event, data) {
  switch (event) {
    case 'meta':
      session.setThreadId(data.thread_id)
      break
    case 'token': {
      const msg = pushAssistantStreaming()
      msg.content += data.content
      break
    }
    case 'todos':
      todos.value = data.items
      break
    case 'progress':
      progressText.value = data.message
      break
    case 'tool': {
      // 2.首轮模型只返回 tool_calls 时占位气泡为空：移除，避免留下空白+常驻光标
      const last = messages.value[messages.value.length - 1]
      if (last && last.role === 'assistant' && !last.content && last.streaming) {
        messages.value.pop()
      }
      messages.value.push({ role: 'tool', name: data.name, content: data.preview })
      break
    }
    case 'offers': {
      if (data.replace) {
        // select_offers 声明本轮最终推荐：覆盖候选池，保证右侧与左侧推荐一致
        offerCandidates.value = data.items || []
      } else {
        // query_jobs 候选池：两段式推荐会触发多次查询，按 id 合并
        const merged = new Map(offerCandidates.value.map((i) => [i.id, i]))
        for (const item of data.items || []) merged.set(item.id, item)
        offerCandidates.value = [...merged.values()]
      }
      break
    }
    case 'error':
      finalizeStreaming()
      messages.value.push({ role: 'error', content: data.message })
      break
    case 'hitl':
      pendingHitl.value = data
      break
    case 'done':
      finalizeStreaming()
      progressText.value = ''
      session.touchSessions() // 刷新侧边栏标题/时间
      // 正常结束（非 HITL 中断、非用户中止）：任务收尾 + 弹心动的offer面板
      if (!pendingHitl.value && !aborted) {
        // 模型通常在最终回复前不再调用 write_todos，界面统一把剩余项标记完成
        todos.value = todos.value.map((t) => ({ ...t, status: 'completed' }))
        if (offerCandidates.value.length) {
          offerOpen.value = true
        }
      }
      break
  }
}

// 统一复位所有"流式中"占位（tool 消息插入后 token 会新建占位，只复位最初那个会留下闪烁光标）
function finalizeStreaming() {
  messages.value.forEach((m) => {
    if (m.streaming) m.streaming = false
  })
}

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

// 本次截图批次标识（group_name）：同批多张图共用，供 agent 用 query_jobs 精确过滤
function imageBatchGroup() {
  const d = new Date()
  const pad = (n) => String(n).padStart(2, '0')
  return `图片上传_${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`
}

async function send({ text, images }) {
  if (streaming.value) return
  offerCandidates.value = []
  offerOpen.value = false
  aborted = false
  const hasImages = images && images.length > 0
  messages.value.push({
    role: 'user',
    content: text || (hasImages ? `[图片 × ${images.length}]` : ''),
  })

  streaming.value = true
  controller = new AbortController()
  try {
    //1.图片逐张识别入库（本次批次共用一个 group_name + 入库起始时间标识），累计统计
    let totalValid = 0
    let totalInserted = 0
    const batchGroup = hasImages ? imageBatchGroup() : ''
    const batchStartedAt = hasImages ? new Date().toISOString() : ''
    if (hasImages) {
      for (let i = 0; i < images.length; i++) {
        progressText.value = `正在识别图片 ${i + 1}/${images.length}…`
        const stats = await uploadScreenshot(batchGroup, todayIso(), images[i])
        totalValid += stats.llm_valid
        totalInserted += stats.new_inserted
        messages.value.push({
          role: 'system_note',
          content: `图片 ${i + 1} 已识别入库：有效兼职 ${stats.llm_valid} 条，新入库 ${stats.new_inserted} 条`,
        })
      }
    }
    progressText.value = ''

    //2.发送消息给 agent：告知图片已入库 + 本轮入库起始时间，要求“先新数据、再历史数据”两段式推荐
    const message = hasImages
      ? `[图片识别完成] 已从 ${images.length} 张截图中识别并入库：有效兼职 ${totalValid} 条` +
        `（group_name='${batchGroup}'，ingested_after='${batchStartedAt}'）。\n` +
        `用户原始指令：${text || '请帮我推荐合适的兼职'}\n` +
        `请先调用 query_jobs(ingested_after='${batchStartedAt}') 查询本次新入库的截图数据，作为主要推荐；` +
        `再调用 query_jobs(ingested_before='${batchStartedAt}', limit=3) 从数据库历史数据中补充 1~3 条合适的兼职，` +
        `并说明“您的历史兼职信息数据库中也有这些向您推荐，若您想了解更多来自历史兼职信息数据库的推荐请和我说”。`
      : text
    pushAssistantStreaming()
    await streamChat({
      threadId: session.threadId,
      message,
      onEvent: handleEvent,
      signal: controller.signal,
    })
  } catch (e) {
    if (e.name !== 'AbortError') {
      messages.value.push({ role: 'error', content: e.message })
    }
  } finally {
    finalizeStreaming()
    streaming.value = false
    progressText.value = ''
    controller = null
  }
}

function stop() {
  aborted = true
  if (controller) controller.abort()
}

// HITL 决策：关闭弹窗后带 decisions 恢复流式输出
async function onHitlDecision(decisions) {
  const threadId = session.threadId
  pendingHitl.value = null
  streaming.value = true
  controller = new AbortController()
  try {
    await resumeChat({
      threadId,
      decisions,
      onEvent: handleEvent,
      signal: controller.signal,
    })
  } catch (e) {
    if (e.name !== 'AbortError') {
      messages.value.push({ role: 'error', content: e.message })
    }
  } finally {
    finalizeStreaming()
    streaming.value = false
    progressText.value = ''
    controller = null
  }
}

// 截图/读微信入库完成 → 以系统提示行展示统计
function onIngested(stats) {
  const groups = Array.isArray(stats.groups) ? Math.max(1, stats.groups.length) : 1
  const days = Array.isArray(stats.days)
    ? Math.max(1, Math.round(stats.days.length / groups))
    : 1
  messages.value.push({
    role: 'system_note',
    content: `已入库（${groups} 个群 · 每群 ${days} 天）：原始 ${stats.total_messages} 条 · 有效兼职 ${stats.llm_valid} 条 · 新入库 ${stats.new_inserted} 条。可以直接询问推荐。`,
  })
}

function onNotice(text) {
  messages.value.push({ role: 'system_note', content: text })
}
</script>

<template>
  <div class="chat-view">
    <TodoProgress :items="todos" />
    <ContactPendingCard
      :items="contactPending"
      :busy="contactPendingBusy"
      @approve="(item) => onContactDecision(item, 'approve')"
      @reject="(item) => onContactDecision(item, 'reject')"
    />

    <div class="chat-layout">
      <div class="chat-card">
        <MessageList :messages="messages" />
        <div v-if="progressText" class="progress-line">
          <span class="spinner" />{{ progressText }}
        </div>
        <ChatInput
          :streaming="streaming"
          @send="send"
          @stop="stop"
          @ingested="onIngested"
        />
      </div>

      <OfferPanel
        v-if="offerOpen"
        :items="offerCandidates"
        @close="offerOpen = false"
        @notice="onNotice"
      />
    </div>

    <HitlDialog v-if="pendingHitl" :hitl="pendingHitl" @decision="onHitlDecision" />
  </div>
</template>

<style scoped>
.chat-view {
  flex: 1;
  display: flex;
  flex-direction: column;
  min-height: 0;
  height: 100%;
}
.chat-layout {
  flex: 1;
  min-height: 0;
  display: flex;
  gap: 12px;
}
.chat-card {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  min-height: 0;
  background: #fff;
  border-radius: 20px;
  box-shadow: 0 6px 20px rgba(30, 41, 59, 0.09);
  /* 裁掉内部滚动内容，保持圆角干净 */
  overflow: hidden;
}
.progress-line {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 22px;
  font-size: 13px;
  color: #d97706;
}
.spinner {
  width: 12px;
  height: 12px;
  border: 2px solid #fcd34d;
  border-top-color: #d97706;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex: none;
}
@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>