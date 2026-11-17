<script setup>
// 截图上传区：点击 / 拖拽 / 粘贴图片 → 弱模型多模态识别入库（无 OCR 兜底）
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { uploadScreenshot } from '../api/http'

const props = defineProps({
  groupName: { type: String, required: true },
  targetDate: { type: String, required: true },
})
const emit = defineEmits(['ingested'])

const MAX_FILES = 3
const files = ref([]) // { file, url }
const uploading = ref(false)
const error = ref('')
const doneCount = ref(0)

function addFiles(list) {
  error.value = ''
  for (const file of list) {
    if (!file.type.startsWith('image/')) continue
    if (files.value.length >= MAX_FILES) {
      error.value = `一次最多 ${MAX_FILES} 张截图`
      break
    }
    files.value.push({ file, url: URL.createObjectURL(file) })
  }
}

function onDrop(e) {
  e.preventDefault()
  addFiles(e.dataTransfer.files)
}

function onPick(e) {
  addFiles(e.target.files)
  e.target.value = ''
}

// 粘贴板图片（全局监听，仅在有图片文件时拦截，不影响文本粘贴）
function onPaste(e) {
  const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'))
  if (imgs.length) {
    e.preventDefault()
    addFiles(imgs)
  }
}

function remove(i) {
  URL.revokeObjectURL(files.value[i].url)
  files.value.splice(i, 1)
}

async function run() {
  if (uploading.value || !files.value.length) return
  if (!props.groupName.trim()) {
    error.value = '请先填写群名'
    return
  }
  uploading.value = true
  error.value = ''
  doneCount.value = 0
  try {
    for (const item of files.value) {
      const stats = await uploadScreenshot(props.groupName.trim(), props.targetDate, item.file)
      doneCount.value += 1
      emit('ingested', stats)
    }
    files.value.forEach((f) => URL.revokeObjectURL(f.url))
    files.value = []
  } catch (e) {
    error.value = e.message
  } finally {
    uploading.value = false
  }
}

onMounted(() => window.addEventListener('paste', onPaste))
onBeforeUnmount(() => window.removeEventListener('paste', onPaste))
</script>

<template>
  <div class="upload-zone" @dragover.prevent @drop="onDrop">
    <div class="upload-row">
      <label class="pick-btn">
        选择截图
        <input type="file" accept="image/png,image/jpeg,image/webp" multiple hidden @change="onPick" />
      </label>
      <button v-if="files.length" class="run-btn" :disabled="uploading" @click="run">
        {{ uploading ? `识别中 ${doneCount}/${files.length}…` : '识别并入库' }}
      </button>
      <span v-if="!files.length" class="hint">
        可点击选择、拖拽或直接粘贴截图 · 需弱模型支持图片输入（如 qwen3.5-omni-plus）
      </span>
      <span v-else-if="uploading" class="hint">正在用多模态模型识别，请稍候…</span>
    </div>

    <div v-if="files.length" class="thumbs">
      <div v-for="(item, i) in files" :key="item.url" class="thumb">
        <img :src="item.url" alt="截图预览" />
        <button class="remove" :disabled="uploading" @click="remove(i)">×</button>
      </div>
    </div>
    <div v-if="error" class="err">{{ error }}</div>
  </div>
</template>

<style scoped>
.upload-zone {
  display: flex;
  flex-direction: column;
  gap: 8px;
  padding: 6px 0 0;
  border-top: 1px dashed #e2e8f0;
}
.upload-row {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.pick-btn {
  border: 1px dashed #cbd5e1;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
  background: #f8fafc;
}
.pick-btn:hover {
  border-color: #93c5fd;
  color: #2563eb;
}
.run-btn {
  border: none;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  background: #eff6ff;
  color: #2563eb;
  cursor: pointer;
}
.run-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.hint {
  font-size: 12px;
  color: #94a3b8;
}
.thumbs {
  display: flex;
  gap: 8px;
}
.thumb {
  position: relative;
  width: 64px;
  height: 48px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.remove {
  position: absolute;
  top: 2px;
  right: 2px;
  width: 16px;
  height: 16px;
  border: none;
  border-radius: 50%;
  background: rgba(15, 23, 42, 0.65);
  color: #fff;
  font-size: 11px;
  line-height: 1;
  cursor: pointer;
  padding: 0;
}
.err {
  font-size: 12px;
  color: #b91c1c;
}
</style>