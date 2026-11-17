import { defineStore } from 'pinia'
import { ref } from 'vue'

// 会话状态：thread_id 跨页面共享；resetToken 通知 ChatView 清空/重载；
// sessionsVersion 通知侧边栏会话列表刷新（标题/时间变化）
export const useSessionStore = defineStore('session', () => {
  const threadId = ref(localStorage.getItem('last_thread_id') || null)
  const resetToken = ref(0)
  const sessionsVersion = ref(0)

  function setThreadId(id) {
    threadId.value = id
    if (id) localStorage.setItem('last_thread_id', id)
    else localStorage.removeItem('last_thread_id')
    sessionsVersion.value += 1
  }

  function newSession() {
    setThreadId(null)
    resetToken.value += 1
  }

  function switchTo(id) {
    setThreadId(id)
    resetToken.value += 1
  }

  // 聊天完成/删除会话后手动触发列表刷新
  function touchSessions() {
    sessionsVersion.value += 1
  }

  return { threadId, resetToken, sessionsVersion, setThreadId, newSession, switchTo, touchSessions }
})