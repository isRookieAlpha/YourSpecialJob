<script setup>
// 偏好与通用设置卡片：微信访问开关 / 导出目录 / 求职偏好（存入 app_settings.preferences）
import { computed, ref } from 'vue'
import { pickDirectory } from '../../api/http'
import ToggleSwitch from './ToggleSwitch.vue'

defineProps({
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
})
const emit = defineEmits(['save'])

const accessEnabled = defineModel('accessEnabled', { type: Boolean, default: true })
const exportDir = defineModel('exportDir', { type: String, default: '' })
const gender = defineModel('gender', { type: String, default: 'any' })
const age = defineModel('age', { type: [Number, String], default: '' })
const commissionMin = defineModel('commissionMin', { type: [Number, String], default: 100 })
const dailyHoursMax = defineModel('dailyHoursMax', { type: [Number, String], default: '' })
const workTimeStart = defineModel('workTimeStart', { type: String, default: '08:00' })
const workTimeEnd = defineModel('workTimeEnd', { type: String, default: '22:00' })
const maxCommuteMin = defineModel('maxCommuteMin', { type: [Number, String], default: '' })
const maxCommuteTransitMin = defineModel('maxCommuteTransitMin', { type: [Number, String], default: '' })
const maxCommuteDriveMin = defineModel('maxCommuteDriveMin', { type: [Number, String], default: '' })
const laborLevel = defineModel('laborLevel', { type: String, default: 'any' })
const note = defineModel('note', { type: String, default: '' })

const picking = ref(false)
const pickError = ref('')

async function chooseDirectory() {
  if (picking.value) return
  picking.value = true
  pickError.value = ''
  try {
    const r = await pickDirectory()
    if (!r.cancelled && r.path) exportDir.value = r.path
  } catch (e) {
    pickError.value = e.message
  } finally {
    picking.value = false
  }
}

function toMinutes(hhmm) {
  const [h, m] = String(hhmm || '00:00').split(':').map(Number)
  return (h || 0) * 60 + (m || 0)
}

function toHHMM(minutes) {
  // 1440（24:00）后端不认，右端统一表示为 23:59
  const value = Math.max(0, Math.min(1439, Math.round(minutes)))
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
}

const startMinutes = computed({
  get: () => toMinutes(workTimeStart.value || '08:00'),
  set: (value) => {
    workTimeStart.value = toHHMM(Math.min(value, toMinutes(workTimeEnd.value || '22:00') - 30))
  },
})
const endMinutes = computed({
  get: () => toMinutes(workTimeEnd.value || '22:00'),
  set: (value) => {
    workTimeEnd.value = toHHMM(Math.max(value, toMinutes(workTimeStart.value || '08:00') + 30))
  },
})

//2.拖动时手动钳制并立即写回 DOM：模型值到边界后不再变化，Vue 不会重写 input，滑柄会与标签脱节
function onStartInput(event) {
  const raw = Number(event.target.value)
  const end = toMinutes(workTimeEnd.value || '22:00')
  const value = Math.max(0, Math.min(raw, end - 30))
  workTimeStart.value = toHHMM(value)
  event.target.value = String(value)
}

function onEndInput(event) {
  const raw = Number(event.target.value)
  const start = toMinutes(workTimeStart.value || '08:00')
  const value = Math.min(1439, Math.max(raw, start + 30))
  workTimeEnd.value = toHHMM(value)
  event.target.value = String(value)
}

//1.双柄滑块：选中区段覆盖在两个叠加的 range 轨道之间
const fillStyle = computed(() => {
  const start = Math.min(startMinutes.value, endMinutes.value)
  const end = Math.max(startMinutes.value, endMinutes.value)
  return {
    left: `${(start / 1440) * 100}%`,
    width: `${((end - start) / 1440) * 100}%`,
  }
})
</script>

<template>
  <div class="card">
    <div class="card-title">偏好与通用设置</div>

    <label class="switch-row">
      <span>允许 YourSpecialJob 智能体访问您的微信</span>
      <ToggleSwitch v-model="accessEnabled" />
    </label>
    <p class="hint">关闭后仅支持图片上传、手动复制粘贴与历史库推荐，微信读取/跟踪/互动将停用。</p>

    <label>
      <span>兼职摘要 PDF 报告导出位置（默认为桌面）</span>
      <div class="dir-row">
        <input v-model="exportDir" placeholder="例如 C:\Users\你\Desktop" />
        <button class="plain-btn" :disabled="picking" @click="chooseDirectory">
          {{ picking ? '选择中…' : '选择目录' }}
        </button>
      </div>
      <span v-if="pickError" class="err">{{ pickError }}</span>
    </label>

    <div class="row">
      <label>
        <span>性别</span>
        <select v-model="gender">
          <option value="any">不限制</option>
          <option value="male">男</option>
          <option value="female">女</option>
        </select>
      </label>
      <label>
        <span>年龄（岁，留空=不限）</span>
        <input v-model.number="age" type="number" min="16" max="80" placeholder="不限制" />
      </label>
    </div>

    <div class="row">
      <label>
        <span>期望最低佣金（元）</span>
        <input v-model.number="commissionMin" type="number" min="0" />
      </label>
      <label>
        <span>期望每日工作时长（小时/天，留空=不限）</span>
        <input v-model.number="dailyHoursMax" type="number" min="0" step="0.5" placeholder="不限制" />
      </label>
    </div>

    <label>
      <span>期望工作时段（默认 08:00-22:00）</span>
      <div class="time-range">
        <div class="time-track">
          <div class="time-fill" :style="fillStyle" />
        </div>
        <input
          :value="startMinutes"
          class="time-slider"
          type="range"
          min="0"
          max="1440"
          step="15"
          aria-label="开始时间"
          @input="onStartInput"
        />
        <input
          :value="endMinutes"
          class="time-slider"
          type="range"
          min="0"
          max="1440"
          step="15"
          aria-label="结束时间"
          @input="onEndInput"
        />
      </div>
      <span class="range-label">{{ workTimeStart }} — {{ workTimeEnd }}</span>
    </label>

    <div class="row">
      <label>
        <span>期望骑行通勤时间上限（分钟）</span>
        <input v-model.number="maxCommuteMin" type="number" min="0" placeholder="不限制" />
      </label>
      <label>
        <span>期望公共交通通勤时间上限（分钟）</span>
        <input v-model.number="maxCommuteTransitMin" type="number" min="0" placeholder="不限制" />
      </label>
      <label>
        <span>期望驾车通勤时间上限（分钟）</span>
        <input v-model.number="maxCommuteDriveMin" type="number" min="0" placeholder="不限制" />
      </label>
    </div>

    <label>
      <span>期望兼职体力劳动量</span>
      <select v-model="laborLevel">
        <option value="light">轻度体力</option>
        <option value="any">不限制</option>
        <option value="heavy">重度体力</option>
      </select>
    </label>

    <label>
      <span>其他偏好（自由文本）</span>
      <textarea
        v-model="note"
        rows="2"
        placeholder="如英语需求优先、最好是展览展会、希望可以丰富个人能力和视野等等"
      />
    </label>

    <div class="save-row">
      <span v-if="message" class="ok">{{ message }}</span>
      <span v-if="error" class="err">{{ error }}</span>
      <span class="spacer" />
      <button class="save-btn" :disabled="saving" @click="emit('save')">
        {{ saving ? '保存中…' : '保存本卡片' }}
      </button>
    </div>
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
.row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
  flex: 1;
  min-width: 160px;
}
.switch-row {
  flex-direction: row;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  color: #fff;
  font-size: 15px;
  min-width: 0;
  background: #07c160;
  border-radius: 12px;
  padding: 10px 12px;
}
input,
select,
textarea {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  color: #0f172a;
  background: #fff;
  outline: none;
  font-family: inherit;
}
input:focus,
select:focus,
textarea:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(147, 197, 253, 0.25);
}
.dir-row {
  display: flex;
  gap: 8px;
}
.dir-row input {
  flex: 1;
}
.plain-btn {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  padding: 6px 12px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
  white-space: nowrap;
}
.plain-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.time-range {
  position: relative;
  height: 30px;
  margin-top: 4px;
}
.time-track {
  position: absolute;
  left: 0;
  right: 0;
  top: 12px;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
}
.time-fill {
  position: absolute;
  top: 0;
  bottom: 0;
  background: #f59e0b;
  border-radius: 3px;
}
.time-slider {
  position: absolute;
  left: 0;
  top: 0;
  width: 100%;
  height: 30px;
  margin: 0;
  padding: 0;
  border: none;
  box-shadow: none;
  background: transparent;
  appearance: none;
  -webkit-appearance: none;
  pointer-events: none;
}
.time-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  border: 3px solid #f59e0b;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.2);
  pointer-events: auto;
  cursor: pointer;
}
.time-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  border: 3px solid #f59e0b;
  box-shadow: 0 1px 4px rgba(15, 23, 42, 0.2);
  pointer-events: auto;
  cursor: pointer;
}
.range-label {
  font-size: 12px;
  color: #2563eb;
}
.hint {
  font-size: 12px;
  color: #94a3b8;
  margin: 0;
}
.save-row {
  display: flex;
  align-items: center;
  gap: 10px;
}
.spacer {
  flex: 1;
}
.save-btn {
  border: none;
  border-radius: 10px;
  padding: 7px 14px;
  font-size: 12px;
  background: #3b82f6;
  color: #fff;
  cursor: pointer;
}
.save-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.ok {
  font-size: 12px;
  color: #059669;
}
.err {
  font-size: 12px;
  color: #b91c1c;
}</style>