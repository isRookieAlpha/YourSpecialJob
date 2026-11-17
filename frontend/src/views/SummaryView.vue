<script setup>
// 统计页 = 心动的offer 工作台：统计卡 / 列表增删改查 / ToDo 升降级 / 静态地图 / 日程表
import { computed, onActivated, onMounted, ref } from 'vue'
import { deleteOffer, listOffers, patchOfferStatus } from '../api/http'
import OfferFormDialog from '../components/offers/OfferFormDialog.vue'
import OfferMap from '../components/offers/OfferMap.vue'
import ScheduleTable from '../components/offers/ScheduleTable.vue'

const items = ref([])
const stats = ref({
  favorite_count: 0, todo_count: 0, estimated_total_income: 0, income_unknown_count: 0,
})
const loading = ref(true)
const error = ref('')
const filter = ref('all') // all | favorite | todo
const formOpen = ref(false)
const editing = ref(null)
const mapVersion = ref(0)

const filtered = computed(() => {
  if (filter.value === 'all') return items.value
  return items.value.filter((i) => i.offer_status === filter.value)
})
const todoItems = computed(() => items.value.filter((i) => i.offer_status === 'todo'))

async function load() {
  loading.value = true
  error.value = ''
  try {
    const data = await listOffers('favorite,todo')
    items.value = data.items || []
    stats.value = data.stats || stats.value
    mapVersion.value += 1
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)
onActivated(load) // KeepAlive 下从聊天页返回时刷新

function commissionText(item) {
  const lo = item.commission_min
  const hi = item.commission_max
  const unit = item.commission_unit || ''
  if (lo == null && hi == null) return item.commission_text || '佣金未知'
  if (lo != null && hi != null && lo !== hi) return `${lo}~${hi}${unit}`
  return `${lo ?? hi}${unit}`
}

function openCreate() {
  editing.value = null
  formOpen.value = true
}

function openEdit(item) {
  editing.value = item
  formOpen.value = true
}

async function onSaved() {
  formOpen.value = false
  await load()
}

async function toggleStatus(item) {
  const target = item.offer_status === 'todo' ? 'favorite' : 'todo'
  try {
    await patchOfferStatus([item.id], target, 'set')
    await load()
  } catch (e) {
    error.value = e.message
  }
}

async function remove(item) {
  const isManual = item.source === 'manual_form'
  const tip = isManual
    ? `删除「${item.work_summary}」？手动录入的记录将被永久删除。`
    : `从列表移除「${item.work_summary}」？仅移出心动的offer，不影响消息血缘。`
  if (!window.confirm(tip)) return
  try {
    await deleteOffer(item.id)
    await load()
  } catch (e) {
    error.value = e.message
  }
}
</script>

<template>
  <div class="summary-view">
    <!-- 统计卡 -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-value">{{ stats.todo_count }}</div>
        <div class="stat-label">ToDo 待办offer</div>
      </div>
      <div class="stat-card">
        <div class="stat-value">¥{{ stats.estimated_total_income }}</div>
        <div class="stat-label">
          ToDo 预计总收入
          <span class="stat-tip" title="按佣金中值估算：元/时×工时（未知按8小时）、元/天与元/次直接、元/月÷30">估算口径</span>
        </div>
      </div>
      <div class="stat-card">
        <div class="stat-value">{{ stats.favorite_count }}</div>
        <div class="stat-label">心动的offer 数量</div>
      </div>
    </div>

    <div v-if="error" class="banner-error">{{ error }}</div>

    <!-- 列表 -->
    <div class="list-card">
      <div class="list-toolbar">
        <div class="tabs">
          <button :class="['tab', { active: filter === 'all' }]" @click="filter = 'all'">全部</button>
          <button :class="['tab', { active: filter === 'favorite' }]" @click="filter = 'favorite'">心动的offer</button>
          <button :class="['tab', { active: filter === 'todo' }]" @click="filter = 'todo'">ToDo</button>
        </div>
        <div class="toolbar-right">
          <button class="add-btn" @click="openCreate">+ 手动新增</button>
          <button class="refresh-btn" :disabled="loading" @click="load">刷新</button>
        </div>
      </div>

      <div v-if="loading" class="empty">加载中…</div>
      <div v-else-if="!filtered.length" class="empty">
        {{ filter === 'all' ? '还没有心动的offer，先去聊天页让助手推荐吧' : '当前筛选下暂无记录' }}
      </div>
      <div v-else class="offer-list">
        <div v-for="item in filtered" :key="item.id" class="offer-item">
          <div class="item-main">
            <div class="item-title">
              {{ item.work_summary || '未命名' }}
              <span v-if="item.offer_status === 'todo'" class="badge todo">ToDo</span>
              <span v-else class="badge favorite">心动的offer</span>
            </div>
            <div class="item-meta">
              {{ item.work_date || '日期未知' }} · {{ item.work_time || '时段未知' }} ·
              {{ commissionText(item) }} · {{ item.location || '地点未知' }}
              <span v-if="!item.income_unknown"> · 预计 ¥{{ item.estimated_income }}</span>
            </div>
            <div class="item-source">{{ item.source_label }}</div>
          </div>
          <div class="item-actions">
            <button class="action" @click="toggleStatus(item)">
              {{ item.offer_status === 'todo' ? '降级为心动' : '升级为 ToDo' }}
            </button>
            <button class="action" @click="openEdit(item)">编辑</button>
            <button class="action danger" @click="remove(item)">
              {{ item.source === 'manual_form' ? '删除' : '移除' }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <OfferMap :items="todoItems" :version="mapVersion" @refresh="load" />
    <ScheduleTable :items="todoItems" />

    <OfferFormDialog
      v-if="formOpen"
      :offer="editing"
      @close="formOpen = false"
      @saved="onSaved"
    />
  </div>
</template>

<style scoped>
.summary-view {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 16px;
  display: flex;
  flex-direction: column;
  gap: 14px;
}
.add-btn {
  border: none;
  border-radius: 10px;
  padding: 6px 14px;
  font-size: 12.5px;
  background: #3b82f6;
  color: #fff;
  cursor: pointer;
}
.toolbar-right {
  display: flex;
  align-items: center;
  gap: 8px;
}
.stats-row {
  display: flex;
  gap: 12px;
}
.stat-card {
  flex: 1;
  background: #fff;
  border-radius: 18px;
  padding: 16px 18px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
}
.stat-value {
  font-size: 24px;
  font-weight: 700;
  color: #0f172a;
}
.stat-label {
  margin-top: 4px;
  font-size: 12px;
  color: #64748b;
}
.stat-tip {
  margin-left: 6px;
  color: #94a3b8;
  text-decoration: underline dotted;
  cursor: help;
}
.banner-error {
  font-size: 13px;
  color: #b91c1c;
  background: #fef2f2;
  border-radius: 12px;
  padding: 10px 14px;
}
.list-card {
  background: #fff;
  border-radius: 18px;
  padding: 14px 16px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
}
.list-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  margin-bottom: 10px;
}
.tabs {
  display: flex;
  gap: 4px;
  background: #f1f5f9;
  border-radius: 10px;
  padding: 3px;
}
.tab {
  border: none;
  background: transparent;
  border-radius: 8px;
  padding: 6px 14px;
  font-size: 12px;
  color: #64748b;
  cursor: pointer;
}
.tab.active {
  background: #fff;
  color: #2563eb;
  font-weight: 600;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.08);
}
.refresh-btn {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 9px;
  padding: 5px 12px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.empty {
  text-align: center;
  font-size: 13px;
  color: #94a3b8;
  padding: 24px 0;
}
.offer-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.offer-item {
  display: flex;
  align-items: center;
  gap: 12px;
  border: 1px solid #f1f5f9;
  border-radius: 14px;
  padding: 10px 12px;
}
.item-main {
  flex: 1;
  min-width: 0;
}
.item-title {
  font-size: 14px;
  font-weight: 600;
  color: #1e293b;
}
.badge {
  margin-left: 6px;
  font-size: 10px;
  font-weight: 400;
  border-radius: 6px;
  padding: 1px 7px;
}
.badge.todo {
  color: #b45309;
  background: #fffbeb;
}
.badge.favorite {
  color: #2563eb;
  background: #eff6ff;
}
.item-meta {
  margin-top: 3px;
  font-size: 12px;
  color: #64748b;
}
.item-source {
  margin-top: 2px;
  font-size: 11px;
  color: #94a3b8;
}
.item-actions {
  display: flex;
  gap: 6px;
  flex: none;
}
.action {
  border: 1px solid #e2e8f0;
  background: #fff;
  border-radius: 9px;
  padding: 5px 10px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.action:hover {
  border-color: #93c5fd;
  color: #2563eb;
}
.action.danger:hover {
  border-color: #fecaca;
  color: #b91c1c;
  background: #fef2f2;
}
</style>