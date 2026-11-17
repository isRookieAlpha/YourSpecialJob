<script setup>
// 待办步骤条：后端每次发送全量快照，前端整包替换
defineProps({
  items: { type: Array, default: () => [] },
})

const statusText = { pending: '待办', in_progress: '进行中', completed: '已完成' }
</script>

<template>
  <div v-if="items.length" class="todo-card">
    <div class="todo-title">任务进度</div>
    <ol class="todo-list">
      <li v-for="(item, i) in items" :key="i" :class="['todo-item', item.status]">
        <span class="dot" />
        <span class="text">{{ item.content }}</span>
        <span class="status">{{ statusText[item.status] || item.status }}</span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.todo-card {
  background: #fff;
  border-radius: 16px;
  padding: 14px 18px;
  box-shadow: 0 2px 12px rgba(30, 41, 59, 0.06);
  margin-bottom: 12px;
}
.todo-title {
  font-size: 13px;
  color: #64748b;
  margin-bottom: 8px;
}
.todo-list {
  list-style: none;
  margin: 0;
  padding: 0;
}
.todo-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 0;
  font-size: 14px;
  color: #334155;
}
.dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: #cbd5e1;
  flex: none;
}
.todo-item.in_progress .dot {
  background: #3b82f6;
  animation: pulse 1.2s infinite;
}
.todo-item.completed .dot {
  background: #22c55e;
}
.todo-item.completed .text {
  color: #94a3b8;
  text-decoration: line-through;
}
.status {
  margin-left: auto;
  font-size: 12px;
  color: #94a3b8;
}
@keyframes pulse {
  0%, 100% { opacity: 1; }
  50% { opacity: 0.4; }
}
</style>