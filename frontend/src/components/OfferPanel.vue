<script setup>
// HITL 侧栏：三步向导——添加至心动的offer → 添加为 ToDo 待办 → 询问导出 PDF
import { computed, ref, watch } from 'vue'
import { exportOffers, listOffers, patchOfferStatus } from '../api/http'

const props = defineProps({
  items: { type: Array, default: () => [] }, // SSE offers 候选
})
const emit = defineEmits(['close', 'notice'])

const step = ref('favorite') // favorite | todo | export | exported
const selected = ref(new Set())
const favoriteItems = ref([])
const todoItems = ref([])
const busy = ref(false)
const error = ref('')
const exportInfo = ref(null)

//1.展示全部推荐（与左侧推荐文本一致）；已收藏/已 ToDo 的仅展示状态、不可勾选
const candidates = computed(() => props.items)

watch(() => props.items, reset)

function reset() {
  step.value = 'favorite'
  selected.value = new Set()
  favoriteItems.value = []
  todoItems.value = []
  exportInfo.value = null
  error.value = ''
}

function toggle(id) {
  const item = props.items.find((i) => i.id === id)
  if (item && item.offer_status !== 'none') return
  const next = new Set(selected.value)
  next.has(id) ? next.delete(id) : next.add(id)
  selected.value = next
}

function isSelected(id) {
  return selected.value.has(id)
}

function commissionText(item) {
  const lo = item.commission_min
  const hi = item.commission_max
  const unit = item.commission_unit || ''
  if (lo == null && hi == null) return item.commission_text || '佣金未知'
  if (lo != null && hi != null && lo !== hi) return `${lo}~${hi}${unit}`
  return `${lo ?? hi}${unit}`
}

function commuteText(item) {
  const parts = []
  if (item.commute_bike_min != null) parts.push(`骑行${item.commute_bike_min}分钟`)
  if (item.commute_transit_min != null) parts.push(`公交/地铁${item.commute_transit_min}分钟`)
  if (item.commute_drive_min != null) parts.push(`驾车${item.commute_drive_min}分钟`)
  return parts.length ? parts.join(' / ') : '通勤未计算'
}

function selectAll(rows) {
  selected.value = new Set(rows.filter((r) => r.offer_status === 'none').map((r) => r.id))
}

//2.第一步：选中候选 → 加入心动的offer（promote，不会降级已在 ToDo 的行）
async function addFavorite() {
  const ids = [...selected.value]
  if (!ids.length || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await patchOfferStatus(ids, 'favorite', 'promote')
    const data = await listOffers('favorite,todo')
    favoriteItems.value = data.items.filter((i) => ids.includes(i.id) && i.offer_status !== 'none')
    if (!favoriteItems.value.length) {
      error.value = '选中项均已在收藏或 ToDo 中'
      busy.value = false
      return
    }
    selectAll(favoriteItems.value) // 默认全选，进入第二步
    step.value = 'todo'
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

//3.第二步：选中收藏行 → 升级为 ToDo
async function addTodo() {
  const ids = [...selected.value]
  if (!ids.length || busy.value) return
  busy.value = true
  error.value = ''
  try {
    await patchOfferStatus(ids, 'todo', 'promote')
    const data = await listOffers('todo')
    todoItems.value = data.items.filter((i) => ids.includes(i.id))
    step.value = 'export'
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}

//4.第三步：导出选中 ToDo 为 PDF（输出到设置目录）
async function doExport() {
  if (busy.value) return
  busy.value = true
  error.value = ''
  try {
    const r = await exportOffers(todoItems.value.map((i) => i.id))
    exportInfo.value = r
    step.value = 'exported'
    emit('notice', `PDF 已导出至：${r.path}`)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <aside :class="['offer-panel', { compact: step === 'export' || step === 'exported' }]">
    <div class="panel-header">
      <span class="panel-title">
        <template v-if="step === 'favorite'">添加至心动的offer</template>
        <template v-else-if="step === 'todo'">添加为 ToDo 待办offer</template>
        <template v-else>导出 PDF 报告</template>
      </span>
      <button class="close-x" title="关闭" @click="emit('close')">×</button>
    </div>

    <div v-if="error" class="panel-error">{{ error }}</div>

    <!-- 第一步：候选列表 -->
    <div v-if="step === 'favorite'" class="panel-body">
      <div v-if="!candidates.length" class="panel-empty">本轮没有推荐候选</div>
      <div
        v-for="item in candidates"
        :key="item.id"
        :class="['offer-row', {
          active: isSelected(item.id),
          disabled: item.offer_status !== 'none',
        }]"
        @click="toggle(item.id)"
      >
        <span class="check">{{ isSelected(item.id) ? '✓' : '' }}</span>
        <div class="row-main">
          <div class="row-title">
            {{ item.work_summary || '未命名' }}
            <span v-if="item.offer_status === 'favorite'" class="status-badge">已收藏</span>
            <span v-else-if="item.offer_status === 'todo'" class="status-badge">已是ToDo</span>
          </div>
          <div class="row-meta">
            {{ item.work_date || '日期未知' }} · {{ item.work_time || '时段未知' }} ·
            {{ commissionText(item) }} · {{ item.location || '地点未知' }}
          </div>
          <div class="row-meta">{{ commuteText(item) }}</div>
          <div class="row-source">{{ item.source_label }}</div>
        </div>
      </div>
    </div>

    <!-- 第二步：刚加入收藏的行 -->
    <div v-else-if="step === 'todo'" class="panel-body">
      <div v-for="item in favoriteItems" :key="item.id"
           :class="['offer-row', { active: isSelected(item.id) }]"
           @click="toggle(item.id)">
        <span class="check">{{ isSelected(item.id) ? '✓' : '' }}</span>
        <div class="row-main">
          <div class="row-title">{{ item.work_summary || '未命名' }}</div>
          <div class="row-meta">
            {{ item.work_date || '日期未知' }} · {{ item.work_time || '时段未知' }} ·
            {{ commissionText(item) }} · {{ item.location || '地点未知' }}
          </div>
          <div class="row-meta">{{ commuteText(item) }}</div>
          <div class="row-source">{{ item.source_label }}</div>
        </div>
      </div>
    </div>

    <!-- 第三步：询问导出（面板缩小） -->
    <div v-else-if="step === 'export'" class="panel-body compact-body">
      <p class="ask">是否将选中的 {{ todoItems.length }} 条 ToDo 待办offer 导出为 PDF 报告？</p>
      <ul class="compact-list">
        <li v-for="item in todoItems" :key="item.id">
          {{ item.work_summary || '未命名' }}（{{ commissionText(item) }}）
        </li>
      </ul>
    </div>

    <!-- 已导出 -->
    <div v-else class="panel-body compact-body">
      <p class="done-title">已导出 PDF</p>
      <p class="done-path">{{ exportInfo?.path }}</p>
    </div>

    <div class="panel-footer">
      <template v-if="step === 'favorite'">
        <button class="btn-cancel" @click="emit('close')">取消</button>
        <button class="btn-primary" :disabled="busy || !selected.size" @click="addFavorite">
          {{ busy ? '添加中…' : '添加' }}
        </button>
      </template>
      <template v-else-if="step === 'todo'">
        <button class="btn-cancel" @click="emit('close')">取消</button>
        <button class="btn-primary" :disabled="busy || !selected.size" @click="addTodo">
          {{ busy ? '添加中…' : '添加' }}
        </button>
      </template>
      <template v-else-if="step === 'export'">
        <button class="btn-cancel" @click="emit('close')">否</button>
        <button class="btn-primary" :disabled="busy" @click="doExport">
          {{ busy ? '导出中…' : '是，导出' }}
        </button>
      </template>
      <template v-else>
        <button class="btn-primary" @click="emit('close')">完成</button>
      </template>
    </div>
  </aside>
</template>

<style scoped>
.offer-panel {
  width: 360px;
  flex: none;
  display: flex;
  flex-direction: column;
  background: #fff;
  border-radius: 20px;
  box-shadow: 0 6px 20px rgba(30, 41, 59, 0.09);
  overflow: hidden;
  transition: width 0.2s ease;
}
.offer-panel.compact {
  width: 300px;
}
.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 16px 10px;
  border-bottom: 1px solid #f1f5f9;
}
.panel-title {
  font-size: 14px;
  font-weight: 700;
  color: #0f172a;
}
.close-x {
  border: none;
  background: transparent;
  font-size: 18px;
  color: #94a3b8;
  cursor: pointer;
  line-height: 1;
}
.panel-body {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.compact-body {
  overflow: visible;
}
.offer-row {
  display: flex;
  gap: 8px;
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 8px 10px;
  cursor: pointer;
  background: #fff;
  transition: border-color 0.12s, background 0.12s;
}
.offer-row:hover {
  border-color: #bfdbfe;
}
.offer-row.active {
  border-color: #93c5fd;
  background: #eff6ff;
}
.offer-row.disabled {
  cursor: default;
  opacity: 0.65;
  background: #f8fafc;
}
.offer-row.disabled:hover {
  border-color: #f1f5f9;
}
.status-badge {
  margin-left: 6px;
  font-size: 10.5px;
  font-weight: 400;
  color: #64748b;
  background: #f1f5f9;
  border-radius: 6px;
  padding: 1px 6px;
}
.check {
  flex: none;
  width: 16px;
  height: 16px;
  border: 1px solid #cbd5e1;
  border-radius: 5px;
  font-size: 11px;
  color: #2563eb;
  display: flex;
  align-items: center;
  justify-content: center;
  margin-top: 2px;
}
.offer-row.active .check {
  border-color: #2563eb;
  background: #dbeafe;
}
.row-main {
  min-width: 0;
}
.row-title {
  font-size: 13px;
  font-weight: 600;
  color: #1e293b;
}
.row-meta {
  margin-top: 2px;
  font-size: 11.5px;
  color: #64748b;
}
.row-source {
  margin-top: 2px;
  font-size: 10.5px;
  color: #94a3b8;
}
.panel-empty {
  text-align: center;
  color: #94a3b8;
  font-size: 13px;
  padding: 24px 0;
}
.panel-error {
  margin: 8px 12px 0;
  font-size: 12px;
  color: #b91c1c;
  background: #fef2f2;
  border-radius: 8px;
  padding: 6px 10px;
}
.ask {
  margin: 4px 2px 8px;
  font-size: 13px;
  color: #334155;
  line-height: 1.6;
}
.compact-list {
  margin: 0;
  padding-left: 18px;
  font-size: 12px;
  color: #64748b;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.done-title {
  margin: 4px 2px;
  font-size: 14px;
  font-weight: 600;
  color: #059669;
}
.done-path {
  font-size: 12px;
  color: #475569;
  word-break: break-all;
  background: #f8fafc;
  border-radius: 8px;
  padding: 8px 10px;
}
.panel-footer {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  padding: 10px 16px 14px;
  border-top: 1px solid #f1f5f9;
}
.btn-cancel,
.btn-primary {
  border: none;
  border-radius: 10px;
  padding: 8px 18px;
  font-size: 13px;
  cursor: pointer;
}
.btn-cancel {
  background: #f1f5f9;
  color: #475569;
}
.btn-primary {
  background: #3b82f6;
  color: #fff;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>