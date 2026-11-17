<script setup>
// 默认读取日期范围弹窗（起止日期，最多 30 天）
import { ref, watch } from 'vue'

const props = defineProps({
  maxDays: { type: Number, default: 30 },
})
const emit = defineEmits(['confirm'])
const open = defineModel('open', { type: Boolean, default: false })
const start = defineModel('start', { type: String, default: '' })
const end = defineModel('end', { type: String, default: '' })

const localStart = ref('')
const localEnd = ref('')
const error = ref('')

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function shiftDays(iso, days) {
  const [y, m, d] = iso.split('-').map(Number)
  const dt = new Date(Date.UTC(y, m - 1, d + days))
  return `${dt.getUTCFullYear()}-${String(dt.getUTCMonth() + 1).padStart(2, '0')}-${String(dt.getUTCDate()).padStart(2, '0')}`
}

watch(open, (value) => {
  if (!value) return
  localStart.value = start.value || todayIso()
  localEnd.value = end.value || localStart.value
  error.value = ''
})

function quick(days) {
  localEnd.value = todayIso()
  localStart.value = days === 1 ? todayIso() : shiftDays(todayIso(), -(days - 1))
  error.value = ''
}

function confirm() {
  if (!localStart.value || !localEnd.value) {
    error.value = '请选择起始日和结束日'
    return
  }
  if (localEnd.value < localStart.value) {
    error.value = '结束日不能早于起始日'
    return
  }
  const [sy, sm, sd] = localStart.value.split('-').map(Number)
  const [ey, em, ed] = localEnd.value.split('-').map(Number)
  const days = Math.round((Date.UTC(ey, em - 1, ed) - Date.UTC(sy, sm - 1, sd)) / 86400000) + 1
  if (days > props.maxDays) {
    error.value = `一次最多读取 ${props.maxDays} 天`
    return
  }
  start.value = localStart.value
  end.value = localEnd.value
  open.value = false
  emit('confirm', { start: localStart.value, end: localEnd.value })
}
</script>

<template>
  <div v-if="open" class="overlay" @click.self="open = false">
    <div class="dialog">
      <div class="dialog-title">默认读取日期</div>
      <p class="dialog-hint">一次最多读取 {{ maxDays }} 天（每天约 14 秒，逐天打开微信聊天记录）。</p>
      <div class="quick-row">
        <button class="quick" @click="quick(1)">今天</button>
        <button class="quick" @click="quick(7)">近 7 天</button>
        <button class="quick" @click="quick(30)">近 30 天</button>
      </div>
      <div class="date-row">
        <label>
          <span>起始日</span>
          <input v-model="localStart" type="date" />
        </label>
        <label>
          <span>结束日</span>
          <input v-model="localEnd" type="date" />
        </label>
      </div>
      <div v-if="error" class="err">{{ error }}</div>
      <div class="dialog-actions">
        <button class="btn-cancel" @click="open = false">取消</button>
        <button class="btn-confirm" @click="confirm">确定</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.overlay {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.35);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}
.dialog {
  width: 460px;
  max-width: calc(100vw - 40px);
  background: #fff;
  border-radius: 18px;
  padding: 20px 22px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18);
}
.dialog-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
  margin-bottom: 6px;
}
.dialog-hint {
  font-size: 12px;
  color: #94a3b8;
  margin: 0 0 12px;
}
.quick-row {
  display: flex;
  gap: 8px;
  margin-bottom: 12px;
}
.quick {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 8px;
  padding: 5px 10px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.date-row {
  display: flex;
  gap: 12px;
}
.date-row label {
  flex: 1;
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
}
input {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
}
.err {
  color: #b91c1c;
  font-size: 12px;
  margin-top: 8px;
}
.dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 18px;
}
.btn-cancel {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
}
.btn-confirm {
  border: none;
  background: #3b82f6;
  color: #fff;
  border-radius: 10px;
  padding: 8px 16px;
  font-size: 12px;
  cursor: pointer;
}</style>