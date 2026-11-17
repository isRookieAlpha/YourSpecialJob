<script setup>
// 会话选择器：列表 / 新建 / 删除 / 切换（M3 切换不加载历史，M2 补历史回放）
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { createSession, deleteSession, listSessions } from '../../api/http'
import { useSessionStore } from '../../stores/session'

const router = useRouter()
const session = useSessionStore()
const sessions = ref([])
const loading = ref(false)
const error = ref('')

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
  try {
    await createSession()
    await reload()
  } catch (e) {
    error.value = e.message
  }
}

async function remove(item) {
  if (!window.confirm(`删除会话「${item.title}」？该会话的聊天记录也会删除。`)) return
  try {
    await deleteSession(item.id)
    if (session.threadId === item.id) session.newSession()
    await reload()
  } catch (e) {
    error.value = e.message
  }
}

function switchTo(item) {
  session.switchTo(item.id)
  router.push('/chat')
}

function formatTime(iso) {
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(reload)
</script>

<template>
  <div class="card">
    <div class="card-header">
      <div class="card-title">会话</div>
      <button class="create-btn" :disabled="loading" @click="create">新建会话</button>
    </div>
    <div v-if="error" class="err">{{ error }}</div>
    <div v-if="!sessions.length && !loading" class="empty">暂无会话，发一条消息会自动创建</div>
    <ul v-else class="session-list">
      <li
        v-for="item in sessions"
        :key="item.id"
        :class="['session-item', { active: item.id === session.threadId }]"
      >
        <div class="session-main" @click="switchTo(item)">
          <div class="session-title">{{ item.title }}</div>
          <div class="session-time">{{ formatTime(item.updated_at) }}</div>
        </div>
        <button class="delete-btn" @click="remove(item)">删除</button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.card {
  background: #fff;
  border-radius: 16px;
  padding: 16px 18px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
}
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
}
.create-btn {
  border: none;
  border-radius: 10px;
  padding: 6px 12px;
  font-size: 12px;
  background: #eff6ff;
  color: #2563eb;
  cursor: pointer;
}
.session-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 6px;
  max-height: 260px;
  overflow-y: auto;
}
.session-item {
  display: flex;
  align-items: center;
  gap: 8px;
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 8px 10px;
}
.session-item.active {
  border-color: #bfdbfe;
  background: #f0f7ff;
}
.session-main {
  flex: 1;
  min-width: 0;
  cursor: pointer;
}
.session-title {
  font-size: 13px;
  color: #1e293b;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.session-time {
  font-size: 11px;
  color: #94a3b8;
  margin-top: 2px;
}
.delete-btn {
  border: 1px solid #fee2e2;
  background: #fef2f2;
  color: #b91c1c;
  border-radius: 8px;
  padding: 4px 8px;
  font-size: 11px;
  cursor: pointer;
}
.empty {
  font-size: 13px;
  color: #94a3b8;
  text-align: center;
  padding: 12px 0;
}
.err {
  font-size: 12px;
  color: #b91c1c;
  margin-bottom: 8px;
}
</style>