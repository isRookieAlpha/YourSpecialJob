<script setup>
// 输入框：Enter 发送（Shift+Enter 换行），生成中可停止；
// 左侧"+"菜单：选择图片（系统文件选择器）/ 读取微信；图片也可直接拖拽/粘贴到聊天框，
// 点击"发送"后由 ChatView 统一识别入库并触发推荐。
import { onBeforeUnmount, ref } from 'vue'
import { getSettings, ingestGroupsRange } from '../api/http'

defineProps({
  streaming: { type: Boolean, default: false },
})
const emit = defineEmits(['send', 'stop', 'ingested'])

const MAX_IMAGES = 3

const text = ref('')
const images = ref([]) // { file, url }
const menuOpen = ref(false)
const loadingDefaults = ref(false)
const ingesting = ref(false)
const ingestProgress = ref('')
const defaults = ref({ groups: [], dateStart: '', dateEnd: '', accessEnabled: true })
const menuError = ref('')
const fileInput = ref(null)

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function rangeLabel(start, end) {
  if (!start) return ''
  return start === end ? start : `${start}~${end}`
}

async function toggleMenu() {
  menuOpen.value = !menuOpen.value
  menuError.value = ''
  if (!menuOpen.value) return
  //1.每次打开都拉取最新设置（默认读取群 / 日期范围 / 微信访问开关）
  loadingDefaults.value = true
  try {
    const s = await getSettings()
    const p = s.preferences || {}
    const groups = Array.isArray(p.wechat_groups) ? p.wechat_groups.filter(Boolean) : []
    const start = p.default_date_start || p.default_date || todayIso()
    defaults.value = {
      //1.读取设置页「默认读取群名」全部群（旧配置回退 default_group）
      groups: groups.length ? groups : (p.default_group ? [p.default_group] : []),
      dateStart: start,
      dateEnd: p.default_date_end || start,
      accessEnabled: p.wechat_access_enabled !== false,
    }
  } catch (e) {
    menuError.value = e.message
  } finally {
    loadingDefaults.value = false
  }
}

//2.选择图片：直接打开系统文件选择器（来源不限群，识别入库时统一记为"图片上传"）
function pickImages() {
  menuOpen.value = false
  fileInput.value?.click()
}

function addImages(list) {
  menuError.value = ''
  for (const file of list) {
    if (!file.type || !file.type.startsWith('image/')) continue
    if (images.value.length >= MAX_IMAGES) {
      menuError.value = `一次最多 ${MAX_IMAGES} 张图片`
      break
    }
    images.value.push({ file, url: URL.createObjectURL(file) })
  }
}

function onPick(e) {
  addImages([...e.target.files])
  e.target.value = ''
}

//3.拖拽 / 粘贴图片进聊天框
function onDrop(e) {
  e.preventDefault()
  addImages([...e.dataTransfer.files])
}

function onPaste(e) {
  const imgs = [...(e.clipboardData?.files || [])].filter((f) => f.type.startsWith('image/'))
  if (imgs.length) {
    e.preventDefault()
    addImages(imgs)
  }
}

function removeImage(i) {
  URL.revokeObjectURL(images.value[i].url)
  images.value.splice(i, 1)
}

async function readWechat() {
  if (ingesting.value) return
  if (!defaults.value.accessEnabled) {
    menuError.value = '微信访问已在设置页关闭'
    return
  }
  if (!defaults.value.groups.length) {
    menuError.value = '请先在「设置 → 微信设置」配置默认读取群名'
    return
  }
  menuOpen.value = false
  ingesting.value = true
  ingestProgress.value = ''
  try {
    const start = defaults.value.dateStart || todayIso()
    const end = defaults.value.dateEnd || start
    const stats = await ingestGroupsRange(
      defaults.value.groups,
      start,
      end,
      ({ group, groupIndex, groupTotal, index, total, date }) => {
        const groupText = groupTotal > 1 ? `${group}（${groupIndex}/${groupTotal}）· ` : ''
        ingestProgress.value = `读取中：${groupText}${date}（${index}/${total}）…`
      },
    )
    emit('ingested', stats)
  } catch (e) {
    menuError.value = e.message
    menuOpen.value = true
  } finally {
    ingesting.value = false
    ingestProgress.value = ''
  }
}

function submit() {
  const value = text.value.trim()
  if (!value && !images.value.length) return
  emit('send', { text: value, images: images.value.map((i) => i.file) })
  images.value.forEach((i) => URL.revokeObjectURL(i.url))
  images.value = []
  text.value = ''
}

function onKeydown(e) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    submit()
  }
}

function onOutsideClick(e) {
  if (!e.target.closest('.plus-wrap')) menuOpen.value = false
}

document.addEventListener('click', onOutsideClick)
document.addEventListener('paste', onPaste)
onBeforeUnmount(() => {
  document.removeEventListener('click', onOutsideClick)
  document.removeEventListener('paste', onPaste)
  images.value.forEach((i) => URL.revokeObjectURL(i.url))
})
</script>

<template>
  <div class="input-wrap">
    <div v-if="images.length" class="thumbs">
      <div v-for="(img, i) in images" :key="img.url" class="thumb">
        <img :src="img.url" alt="待发送图片" />
        <button class="rm" title="移除" @click="removeImage(i)">×</button>
      </div>
      <span class="thumbs-hint">发送后将自动识别并入库</span>
    </div>

    <div class="input-bar" @dragover.prevent @drop="onDrop">
      <div class="plus-wrap">
        <button class="icon-btn plus" :disabled="streaming || ingesting" title="选择图片 / 读取微信" @click="toggleMenu">
          +
        </button>

        <div v-if="menuOpen" class="menu">
          <button class="menu-item" @click="pickImages">
            <span class="menu-icon">🖼</span> 选择图片
          </button>
          <div class="menu-hint">也可直接将图片拖拽或复制在聊天框中</div>
          <div class="menu-divider" />
          <button
            v-if="defaults.accessEnabled"
            class="menu-item"
            :disabled="ingesting"
            @click="readWechat"
          >
            <span class="menu-icon">💬</span>
            {{ ingesting ? '读取中…' : `读取微信${defaults.groups.length ? `（${defaults.groups.length} 个群 · ${rangeLabel(defaults.dateStart, defaults.dateEnd)}）` : ''}` }}
          </button>
          <div v-else class="menu-hint">微信访问已在设置页关闭（图片上传/手动粘贴仍可用）</div>
          <div v-if="loadingDefaults" class="menu-hint">正在读取设置…</div>
          <div v-if="menuError" class="menu-error">{{ menuError }}</div>
          <div class="menu-hint">图片识别需弱模型支持图片输入（如 qwen3.5-omni-plus）</div>
        </div>
      </div>

      <textarea
        v-model="text"
        rows="1"
        placeholder="例如：帮我找佣金 100 元以上的轻体力活；也可直接粘贴/拖拽兼职截图"
        @keydown="onKeydown"
      />
      <button v-if="!streaming" class="send" @click="submit">发送</button>
      <button v-else class="stop" @click="emit('stop')">停止</button>
    </div>

    <div v-if="ingestProgress" class="hint-progress">{{ ingestProgress }}</div>
    <div v-if="menuError && !menuOpen" class="hint-error">{{ menuError }}</div>

    <input
      ref="fileInput"
      type="file"
      accept="image/png,image/jpeg,image/webp"
      multiple
      hidden
      @change="onPick"
    />
  </div>
</template>

<style scoped>
.input-wrap {
  display: flex;
  flex-direction: column;
}
.thumbs {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 20px 0;
}
.thumb {
  position: relative;
  width: 64px;
  height: 48px;
  border-radius: 8px;
  overflow: hidden;
  border: 1px solid #e2e8f0;
  flex: none;
}
.thumb img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.rm {
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
.thumbs-hint {
  font-size: 11px;
  color: #94a3b8;
}
.input-bar {
  display: flex;
  gap: 10px;
  padding: 12px 20px 18px;
  align-items: flex-end;
}
.plus-wrap {
  position: relative;
  flex: none;
}
.icon-btn {
  width: 40px;
  height: 40px;
  border: 1px solid #e2e8f0;
  border-radius: 12px;
  background: #f8fafc;
  color: #475569;
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s;
}
.icon-btn:hover {
  border-color: #93c5fd;
  color: #2563eb;
}
.icon-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.menu {
  position: absolute;
  bottom: calc(100% + 8px);
  left: 0;
  width: 270px;
  background: #fff;
  border-radius: 14px;
  box-shadow: 0 8px 30px rgba(15, 23, 42, 0.18);
  padding: 6px;
  z-index: 50;
}
.menu-item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  border: none;
  background: transparent;
  border-radius: 10px;
  padding: 9px 10px;
  font-size: 13px;
  color: #334155;
  text-align: left;
  cursor: pointer;
}
.menu-item:hover {
  background: #f8fafc;
}
.menu-item:disabled {
  opacity: 0.6;
  cursor: default;
}
.menu-icon {
  font-size: 15px;
}
.menu-divider {
  height: 1px;
  background: #f1f5f9;
  margin: 4px 8px;
}
.menu-hint {
  padding: 2px 10px 4px;
  font-size: 11px;
  color: #94a3b8;
}
.menu-error {
  padding: 4px 10px;
  font-size: 12px;
  color: #b91c1c;
}
.hint-error {
  padding: 0 20px 8px;
  font-size: 12px;
  color: #b91c1c;
}
.hint-progress {
  padding: 0 20px 8px;
  font-size: 12px;
  color: #2563eb;
}
textarea {
  flex: 1;
  resize: none;
  border: 1px solid #e2e8f0;
  border-radius: 14px;
  padding: 12px 14px;
  font-size: 14px;
  font-family: inherit;
  line-height: 1.5;
  outline: none;
  max-height: 120px;
  background: #fff;
}
textarea:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(147, 197, 253, 0.25);
}
button.send,
button.stop {
  border: none;
  border-radius: 12px;
  padding: 11px 20px;
  font-size: 14px;
  cursor: pointer;
  flex: none;
  transition: filter 0.15s;
}
button:hover {
  filter: brightness(0.95);
}
.send {
  background: #3b82f6;
  color: #fff;
}
.stop {
  background: #f1f5f9;
  color: #475569;
}
</style>