<script setup>
// 简历与个人资料卡片：上传简历 → 解析/切块/embedding 入库；列表/删除/重建索引
import { onMounted, ref } from 'vue'
import {
  deleteResumeFile,
  listResumeFiles,
  rebuildResumeIndex,
  uploadResume,
} from '../../api/http'
import ConfirmDialog from './ConfirmDialog.vue'

const MAX_SIZE = 10 * 1024 * 1024
const fileInput = ref(null)
const files = ref([])
const loading = ref(false)
const uploading = ref(false)
const rebuilding = ref(false)
const message = ref('')
const error = ref('')
const confirmOpen = ref(false)
const pendingDelete = ref(null)

async function loadFiles() {
  loading.value = true
  try {
    const data = await listResumeFiles()
    files.value = data.files || []
    error.value = ''
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function pickFile() {
  if (uploading.value) return
  fileInput.value?.click()
}

async function onPick(event) {
  const file = event.target.files?.[0]
  event.target.value = ''
  if (!file) return
  if (file.size > MAX_SIZE) {
    error.value = '文件超过 10MB 限制'
    return
  }
  uploading.value = true
  error.value = ''
  message.value = '正在解析并建立索引（约几秒到几十秒）…'
  try {
    const result = await uploadResume(file)
    message.value = `已索引「${result.filename}」（${result.chunk_count} 块）`
    await loadFiles()
  } catch (e) {
    message.value = ''
    error.value = e.message
  } finally {
    uploading.value = false
  }
}

function askDelete(item) {
  pendingDelete.value = item
  confirmOpen.value = true
}

async function confirmDelete() {
  const item = pendingDelete.value
  confirmOpen.value = false
  pendingDelete.value = null
  if (!item) return
  try {
    await deleteResumeFile(item.id)
    message.value = `已删除「${item.filename}」`
    await loadFiles()
  } catch (e) {
    error.value = e.message
  }
}

async function rebuild() {
  if (!files.value.length || rebuilding.value) return
  rebuilding.value = true
  error.value = ''
  message.value = '正在重建索引…'
  try {
    const result = await rebuildResumeIndex()
    message.value = `重建完成：${result.files} 个文件，共 ${result.chunks} 块`
    if (result.failed?.length) {
      error.value = `部分文件失败：${result.failed.map((f) => `#${f.id}`).join('、')}`
    }
    await loadFiles()
  } catch (e) {
    message.value = ''
    error.value = e.message
  } finally {
    rebuilding.value = false
  }
}

function formatTime(iso) {
  if (!iso) return ''
  const date = new Date(iso)
  return `${date.getMonth() + 1}/${date.getDate()} ${String(date.getHours()).padStart(2, '0')}:${String(
    date.getMinutes(),
  ).padStart(2, '0')}`
}

onMounted(loadFiles)
</script>

<template>
  <div class="card">
    <div class="card-title">简历与个人资料</div>
    <p class="hint">
      上传 PDF/DOCX/TXT/MD 简历（≤10MB，可多份）。agent 在中介询问自我介绍、经历、技能时会检索简历中的真实内容，
      不会编造。文件仅保存在本机。
    </p>

    <div class="actions">
      <button class="primary-btn" :disabled="uploading" @click="pickFile">
        {{ uploading ? '处理中…' : '上传简历' }}
      </button>
      <button
        class="plain-btn"
        :disabled="!files.length || rebuilding || uploading"
        @click="rebuild"
      >
        {{ rebuilding ? '重建中…' : '重建索引' }}
      </button>
    </div>

    <div v-if="loading" class="hint">加载中…</div>
    <div v-else-if="!files.length" class="empty">尚未上传简历</div>
    <div v-else class="file-list">
      <div v-for="item in files" :key="item.id" class="file-row">
        <div class="file-main">
          <div class="file-name">{{ item.filename }}</div>
          <div class="file-meta">
            {{ item.chunk_count }} 块 · {{ item.status === 'ready' ? '已索引' : '索引失败' }}
            · {{ formatTime(item.created_at) }}
          </div>
          <div v-if="item.error" class="err">{{ item.error }}</div>
        </div>
        <button class="delete-btn" title="删除" @click="askDelete(item)">删除</button>
      </div>
    </div>

    <div v-if="message" class="ok">{{ message }}</div>
    <div v-if="error" class="err">{{ error }}</div>

    <input
      ref="fileInput"
      type="file"
      accept=".pdf,.docx,.txt,.md"
      hidden
      @change="onPick"
    />

    <ConfirmDialog
      v-model:open="confirmOpen"
      title="删除简历"
      :message="`确定删除「${pendingDelete?.filename || ''}」及其索引吗？`"
      confirm-text="删除"
      @confirm="confirmDelete"
      @cancel="confirmOpen = false"
    />
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
.hint {
  font-size: 12px;
  color: #94a3b8;
  margin: 0;
  line-height: 1.6;
}
.actions {
  display: flex;
  gap: 10px;
}
.primary-btn {
  border: none;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  background: #3b82f6;
  color: #fff;
  cursor: pointer;
}
.primary-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.plain-btn {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  padding: 6px 12px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.plain-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.empty {
  font-size: 12px;
  color: #94a3b8;
}
.file-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.file-row {
  display: flex;
  align-items: center;
  gap: 10px;
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 8px 10px;
}
.file-main {
  flex: 1;
  min-width: 0;
}
.file-name {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
  word-break: break-all;
}
.file-meta {
  font-size: 11.5px;
  color: #64748b;
  margin-top: 2px;
}
.delete-btn {
  border: 1px solid #fecaca;
  background: #fef2f2;
  color: #b91c1c;
  border-radius: 8px;
  padding: 4px 10px;
  font-size: 12px;
  cursor: pointer;
}
.ok {
  font-size: 12px;
  color: #059669;
}
.err {
  font-size: 12px;
  color: #b91c1c;
}</style>