<script setup>
// 日程表：按 work_date 分组展示 ToDo 的时段（无精确时段默认 08:00-22:00 并标注）
import { computed } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] }, // ToDo 列表（后端已附 work_start/work_end/work_time_defaulted）
})

//1.按日期分组，组内按开始时间排序；无日期归"日期未知"
const groups = computed(() => {
  const map = new Map()
  for (const item of props.items) {
    const key = item.work_date || '日期未知'
    if (!map.has(key)) map.set(key, [])
    map.get(key).push(item)
  }
  const keys = [...map.keys()].sort((a, b) => (a === '日期未知' ? 1 : b === '日期未知' ? -1 : a.localeCompare(b)))
  return keys.map((key) => ({
    date: key,
    rows: map.get(key).sort((a, b) => (a.work_start || '').localeCompare(b.work_start || '')),
  }))
})

function formatDate(iso) {
  if (iso === '日期未知') return iso
  const d = new Date(iso + 'T00:00:00')
  return `${d.getMonth() + 1}月${d.getDate()}日（周${'日一二三四五六'[d.getDay()]}）`
}
</script>

<template>
  <div class="card">
    <div class="card-title">ToDo 日程表</div>
    <div v-if="!items.length" class="empty">还没有 ToDo 待办offer</div>
    <div v-else class="schedule">
      <div v-for="group in groups" :key="group.date" class="day-group">
        <div class="day-title">{{ formatDate(group.date) }}</div>
        <div v-for="row in group.rows" :key="row.id" class="event-row">
          <span class="event-time">
            {{ row.work_start }} - {{ row.work_end }}
            <span v-if="row.work_time_defaulted" class="default-badge">默认时段</span>
          </span>
          <span class="event-title">{{ row.work_summary || '未命名' }}</span>
          <span class="event-loc">{{ row.location || '' }}</span>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.card {
  background: #fff;
  border-radius: 18px;
  padding: 16px 18px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 10px;
}
.schedule {
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.day-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.day-title {
  font-size: 12px;
  font-weight: 600;
  color: #2563eb;
  border-left: 3px solid #93c5fd;
  padding-left: 8px;
}
.event-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 13px;
  color: #334155;
  padding: 6px 10px;
  background: #f8fafc;
  border-radius: 10px;
}
.event-time {
  flex: none;
  font-family: 'Cascadia Code', Consolas, monospace;
  font-size: 12px;
  color: #475569;
}
.default-badge {
  margin-left: 4px;
  font-size: 10px;
  color: #94a3b8;
  background: #eef2f7;
  border-radius: 6px;
  padding: 1px 5px;
  font-family: inherit;
}
.event-title {
  flex: 1;
  min-width: 0;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
.event-loc {
  flex: none;
  font-size: 12px;
  color: #94a3b8;
}
.empty {
  font-size: 13px;
  color: #94a3b8;
  text-align: center;
  padding: 16px 0;
}
</style>