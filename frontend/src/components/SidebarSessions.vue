<script setup>
// 侧边栏历史会话：顶部"+"新建会话框 + 会话列表（切换/删除/active 高亮）
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { createSession, deleteSession, listSessions } from '../api/http'
import { useSessionStore } from '../stores/session'

const router = useRouter()
const session = useSessionStore()
const sessions = ref([])
const loading = ref(false)
const creating = ref(false)
const error = ref('')

const activeId = computed(() => session.threadId)

async function reload() {
  loading.value = true
  error.value = ''
  try {
    sessions.value = await listSessions()
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

async function create() {
  if (creating.value) return
  creating.value = true
  try {
    const created = await createSession()
    session.switchTo(created.id)
    router.push('/chat')
    await reload()
  } catch (e) {
    error.value = e.message
  } finally {
    creating.value = false
  }
}

function switchTo(item) {
  if (item.id === activeId.value) {
    router.push('/chat')
    return
  }
  session.switchTo(item.id)
  router.push('/chat')
}

async function remove(item, event) {
  event.stopPropagation()
  if (!window.confirm(`删除会话「${item.title}」？该会话的聊天记录也会删除。`)) return
  try {
    await deleteSession(item.id)
  } catch (e) {
    error.value = e.message
  } finally {
    if (activeId.value === item.id) session.newSession()
    await reload()
  }
}

function formatTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  const now = new Date()
  if (d.toDateString() === now.toDateString()) {
    return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  }
  return `${d.getMonth() + 1}/${d.getDate()}`
}

onMounted(reload)
watch(
  () => [session.threadId, session.sessionsVersion, session.resetToken],
  reload
)
</script>

<template>
  <div class="sessions">
    <button class="new-btn" :disabled="creating" @click="create">
      <span class="plus">+</span>
      新建会话
    </button>

    <div v-if="error" class="err">{{ error }}</div>

    <div class="session-list">
      <div v-if="!sessions.length && !loading" class="empty">暂无会话</div>
      <div
        v-for="item in sessions"
        :key="item.id"
        :class="['session-item', { active: item.id === activeId }]"
        @click="switchTo(item)"
      >
        <span class="session-title">{{ item.title }}</span>
        <span class="session-time">{{ formatTime(item.updated_at) }}</span>
        <button class="delete-btn" title="删除会话" @click="remove(item, $event)">×</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.sessions {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow: hidden;
}
.new-btn {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: 1px dashed #cbd5e1;
  background: #f8fafc;
  border-radius: 12px;
  padding: 9px 12px;
  font-size: 13px;
  color: #334155;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.new-btn:hover {
  border-color: #93c5fd;
  color: #2563eb;
}
.new-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.plus {
  font-size: 16px;
  line-height: 1;
}
.session-list {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding-right: 2px;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 6px;
  border-radius: 10px;
  padding: 8px 10px;
  cursor: pointer;
  border: 1px solid transparent;
}
.session-item:hover {
  background: #f8fafc;
}
.session-item.active {
  background: #eff6ff;
  border-color: #bfdbfe;
}
.session-title {
  flex: 1;
  min-width: 0;
  font-size: 13px;
  color: #334155;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-item.active .session-title {
  color: #2563eb;
  font-weight: 600;
}
.session-time {
  flex: none;
  font-size: 11px;
  color: #94a3b8;
}
.delete-btn {
  flex: none;
  width: 18px;
  height: 18px;
  border: none;
  border-radius: 6px;
  background: transparent;
  color: #94a3b8;
  font-size: 14px;
  line-height: 1;
  cursor: pointer;
  opacity: 0;
  transition: opacity 0.15s, background 0.15s;
  padding: 0;
}
.session-item:hover .delete-btn {
  opacity: 1;
}
.delete-btn:hover {
  background: #fef2f2;
  color: #b91c1c;
}
.empty {
  text-align: center;
  font-size: 12px;
  color: #94a3b8;
  padding: 12px 0;
}
.err {
  font-size: 12px;
  color: #b91c1c;
}
</style>