<script setup>
// 微信设置卡片：默认读取群/日期范围、定时跟踪、主动推送、微信互动、中介名单与中介互动
import { computed, onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'
import {
  getContactInteractionStatus,
  getInteractionStatus,
  getTrackingStatus,
  runContactInteraction,
  runInteraction,
  runTracking,
} from '../../api/http'
import DateRangeDialog from './DateRangeDialog.vue'
import ToggleSwitch from './ToggleSwitch.vue'

defineProps({
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
})
const emit = defineEmits(['save', 'contacts-touched'])

const groups = defineModel('groups', { type: Array, default: () => [] })
const dateStart = defineModel('dateStart', { type: String, default: '' })
const dateEnd = defineModel('dateEnd', { type: String, default: '' })
const trackingEnabled = defineModel('trackingEnabled', { type: Boolean, default: false })
const trackingIntervalMin = defineModel('trackingIntervalMin', { type: Number, default: 15 })
const trackingPushEnabled = defineModel('trackingPushEnabled', { type: Boolean, default: true })
const trackingPushLimit = defineModel('trackingPushLimit', { type: Number, default: 5 })
const trackingLedgerDays = defineModel('trackingLedgerDays', { type: Number, default: 7 })
const wechatTarget = defineModel('wechatTarget', { type: String, default: '' })
const interactionEnabled = defineModel('interactionEnabled', { type: Boolean, default: false })
const interactionIntervalSec = defineModel('interactionIntervalSec', { type: Number, default: 60 })
const contacts = defineModel('contacts', { type: Array, default: () => [] })
const contactInteractionEnabled = defineModel('contactInteractionEnabled', {
  type: Boolean,
  default: false,
})
const contactInteractionIntervalSec = defineModel('contactInteractionIntervalSec', {
  type: Number,
  default: 60,
})

const status = ref(null)
const interactionStatus = ref(null)
const contactStatus = ref(null)
const contactStatusError = ref('')
const contactRunning = ref(false)
const contactPolling = ref(false)
const statusError = ref('')
const running = ref(false)
const polling = ref(false)
const interactionRunning = ref(false)
const dateDialogOpen = ref(false)
const trackingRefreshing = ref(false)
const trackingRefreshed = ref(false)
const interactionRefreshing = ref(false)
const interactionRefreshed = ref(false)
const contactRefreshing = ref(false)
const contactRefreshed = ref(false)
let pollTimer = null
let contactTimer = null

//1.本轮进度（每个群一个单位；running=false 后不再显示）
const progressPercent = computed(() => {
  const progress = status.value?.progress
  if (!progress || !progress.total) return 0
  return Math.min(100, Math.round((progress.current / progress.total) * 100))
})

function addGroup() {
  if (groups.value.length >= 10) {
    statusError.value = '最多 10 个群'
    return
  }
  groups.value.push('')
}

function removeGroup(index) {
  groups.value.splice(index, 1)
  if (!groups.value.length) groups.value.push('')
}

async function loadStatus(silent = false) {
  try {
    status.value = await getTrackingStatus()
    interactionStatus.value = await getInteractionStatus()
    statusError.value = ''
  } catch (e) {
    if (!silent) statusError.value = e.message
  }
  await loadContactStatus(silent)
}

async function loadContactStatus(silent = false) {
  try {
    contactStatus.value = await getContactInteractionStatus()
    contactStatusError.value = ''
    return true
  } catch (e) {
    if (!silent) contactStatusError.value = e.message
    return false
  }
}

//1.中介名单：全部参与轮询，发送并确认后自动加入
function addContact() {
  if (contacts.value.length >= 10) {
    contactStatusError.value = '最多 10 个中介'
    return
  }
  contacts.value.push('')
  emit('contacts-touched')
}

function removeContact(index) {
  contacts.value.splice(index, 1)
  if (!contacts.value.length) contacts.value.push('')
  emit('contacts-touched')
}

function touchContacts() {
  emit('contacts-touched')
}

function stopPolling() {
  polling.value = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// 一轮包含微信读取+弱模型提取，最长按 5 分钟轮询
function startPolling(beforeAt) {
  stopPolling()
  polling.value = true
  let attempts = 0
  pollTimer = setInterval(async () => {
    attempts += 1
    await loadStatus(true)
    const at = status.value?.last_round?.at
    const failed = Boolean(status.value?.last_round?.error)
    // running 在轮次开始时就会置 True；必须等 running 结束再判断时间变化，
    // 否则第一次 tick 看到 last_run_at 已更新就误判为轮次结束
    if (!status.value?.running && ((at && at !== beforeAt) || failed)) stopPolling()
    else if (attempts >= 150) stopPolling()
  }, 2000)
}

async function triggerRun() {
  if (running.value || polling.value) return
  running.value = true
  statusError.value = ''
  try {
    const beforeAt = status.value?.last_round?.at || null
    await runTracking()
    startPolling(beforeAt)
  } catch (e) {
    statusError.value = e.message
  } finally {
    running.value = false
  }
}

async function triggerInteraction() {
  if (interactionRunning.value) return
  interactionRunning.value = true
  try {
    await runInteraction()
    setTimeout(loadStatus, 15000)
  } catch (e) {
    statusError.value = e.message
  } finally {
    interactionRunning.value = false
  }
}

function stopContactPolling() {
  contactPolling.value = false
  if (contactTimer) {
    clearInterval(contactTimer)
    contactTimer = null
  }
}

// 一次检查包含微信读取（10~15 秒），2 秒轮询最多 5 分钟
function startContactPolling(beforeAt) {
  stopContactPolling()
  contactPolling.value = true
  let attempts = 0
  contactTimer = setInterval(async () => {
    attempts += 1
    await loadContactStatus(true)
    const at = contactStatus.value?.last_poll?.last_poll_at
    const failed = Boolean(contactStatus.value?.last_poll?.last_error)
    // 同跟踪：last_poll_at 在检查开始时就更新，必须等 running 结束再判断
    if (!contactStatus.value?.running && ((at && at !== beforeAt) || failed)) stopContactPolling()
    else if (attempts >= 150) stopContactPolling()
  }, 2000)
}

async function triggerContactCheck() {
  if (contactRunning.value || contactPolling.value) return
  contactRunning.value = true
  contactStatusError.value = ''
  try {
    const beforeAt = contactStatus.value?.last_poll?.last_poll_at || null
    await runContactInteraction()
    startContactPolling(beforeAt)
  } catch (e) {
    contactStatusError.value = e.message
  } finally {
    contactRunning.value = false
  }
}

//1.手动刷新状态显示（不触发任务），按钮短暂显示"已刷新"
function flashRefreshed(flag) {
  flag.value = true
  setTimeout(() => (flag.value = false), 1500)
}

async function refreshTracking() {
  if (trackingRefreshing.value) return
  trackingRefreshing.value = true
  let ok = false
  try {
    status.value = await getTrackingStatus()
    statusError.value = ''
    ok = true
  } catch (e) {
    statusError.value = e.message
  } finally {
    trackingRefreshing.value = false
  }
  if (ok) flashRefreshed(trackingRefreshed)  // 失败时不显示"已刷新"
}

async function refreshInteraction() {
  if (interactionRefreshing.value) return
  interactionRefreshing.value = true
  let ok = false
  try {
    interactionStatus.value = await getInteractionStatus()
    statusError.value = ''
    ok = true
  } catch (e) {
    statusError.value = e.message
  } finally {
    interactionRefreshing.value = false
  }
  if (ok) flashRefreshed(interactionRefreshed)
}

async function refreshContact() {
  if (contactRefreshing.value) return
  contactRefreshing.value = true
  let ok = false
  try {
    ok = await loadContactStatus(false)
  } finally {
    contactRefreshing.value = false
  }
  if (ok) flashRefreshed(contactRefreshed)
}

function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(loadStatus)
onActivated(loadStatus)
onDeactivated(stopContactPolling)
onBeforeUnmount(() => {
  stopPolling()
  stopContactPolling()
})
</script>

<template>
  <div class="card">
    <div class="card-title">微信设置</div>
    <p class="hint">推送与互动共用下方「微信目标会话」；关闭上方微信访问开关后本卡片隐藏且功能停用。</p>

    <div class="section-title">默认读取群名（第一行为「+」菜单默认读取群，全部用于定时跟踪）</div>
    <div class="group-list">
      <div v-for="(_, index) in groups" :key="index" class="group-row">
        <button class="icon-btn" title="删除" @click="removeGroup(index)">−</button>
        <input v-model="groups[index]" :placeholder="index === 0 ? '默认读取群（如 兼职1群）' : '群名'" />
        <span v-if="index === 0" class="badge">默认</span>
      </div>
      <button class="plain-btn" @click="addGroup">+ 添加群</button>
    </div>

    <div class="section-title">默认读取日期</div>
    <div class="date-row">
      <button class="plain-btn" @click="dateDialogOpen = true">
        {{ dateStart || '选择起始日' }} ~ {{ dateEnd || dateStart || '选择结束日' }}
      </button>
      <span class="hint">最多 30 天，逐天读取（约 14 秒/天）</span>
    </div>

    <label class="switch-row">
      <span>启用定时跟踪</span>
      <ToggleSwitch v-model="trackingEnabled" />
    </label>
    <label>
      <span>微信目标会话（留空使用 .env 的 WECHAT_SENDER_ID）</span>
      <input v-model="wechatTarget" placeholder="如 文件传输助手 / 你的微信备注" />
    </label>

    <div class="row">
      <label>
        <span>跟踪间隔（分钟，5~180）</span>
        <input v-model.number="trackingIntervalMin" type="number" min="5" max="180" />
      </label>
      <label>
        <span>单条摘要最多展示条数（1~10）</span>
        <input v-model.number="trackingPushLimit" type="number" min="1" max="10" />
      </label>
      <label>
        <span>账本保留天数（1~30）</span>
        <input v-model.number="trackingLedgerDays" type="number" min="1" max="30" />
      </label>
    </div>

    <div class="status-box">
      <div class="status-line">
        跟踪：{{ status?.enabled ? '已启用' : '未启用' }} ·
        {{ status?.running ? '运行中' : '空闲' }} ·
        群 {{ status?.groups_configured?.length || 0 }} 个
        <span v-if="status?.paused_reason" class="paused">（{{ status.paused_reason }}）</span>
      </div>
      <div class="status-line">
        上次运行：{{ formatTime(status?.last_round?.at) }} ·
        新入库 {{ status?.last_round?.stats?.new_inserted ?? 0 }} ·
        推送 {{ status?.last_round?.stats?.pushed ?? 0 }}
      </div>
      <div class="status-line">
        24h 推送：成功 {{ status?.push_24h?.sent ?? 0 }} · 失败 {{ status?.push_24h?.failed ?? 0 }} ·
        跳过 {{ status?.push_24h?.skipped ?? 0 }}
      </div>
      <div v-if="status?.running" class="progress-box">
        <div class="progress-line">
          {{ status.progress?.label ? `正在处理：${status.progress.label}` : '准备中…' }}
          （{{ status.progress?.current || 0 }}/{{ status.progress?.total || 0 }}）
        </div>
        <div class="progress-track">
          <div class="progress-fill" :style="{ width: `${progressPercent}%` }" />
        </div>
      </div>
      <div v-if="status?.last_round?.error" class="err">{{ status.last_round.error }}</div>
      <div class="btn-row">
        <button class="plain-btn" :disabled="running || polling" @click="triggerRun">
          {{ running || polling ? '运行中…' : '立即执行一轮' }}
        </button>
        <button class="plain-btn" :disabled="trackingRefreshing" @click="refreshTracking">
          {{ trackingRefreshed ? '已刷新' : '刷新' }}
        </button>
      </div>
    </div>

    <label class="switch-row">
      <span>允许主动推荐至微信</span>
      <ToggleSwitch v-model="trackingPushEnabled" />
    </label>

    <div class="section-title">微信互动（轮询目标会话，收到你的消息后由 agent 回复）</div>
    <label class="switch-row">
      <span>启用微信互动</span>
      <ToggleSwitch v-model="interactionEnabled" />
    </label>
    <label>
      <span>互动轮询间隔（秒，30~300；每次检查需操作微信窗口约 10~15 秒）</span>
      <input v-model.number="interactionIntervalSec" type="number" min="30" max="300" />
    </label>
    <div class="status-box">
      <div class="status-line">
        互动：{{ interactionStatus?.enabled ? '已启用' : '未启用' }} ·
        {{ interactionStatus?.running ? '检查中' : '空闲' }} ·
        目标：{{ interactionStatus?.target_configured ? interactionStatus.target_masked : '未配置' }}
        <span v-if="interactionStatus?.paused_reason" class="paused">（{{ interactionStatus.paused_reason }}）</span>
      </div>
      <div class="status-line">
        待审批操作：{{ interactionStatus?.pending_count ?? 0 }} 条（危险操作会发微信请求确认，回复「同意」或「拒绝」）
      </div>
      <div v-if="interactionStatus?.last_poll?.last_error" class="err">
        {{ interactionStatus.last_poll.last_error }}
      </div>
      <div class="btn-row">
        <button class="plain-btn" :disabled="interactionRunning" @click="triggerInteraction">
          {{ interactionRunning ? '已触发…' : '立即检查一次' }}
        </button>
        <button class="plain-btn" :disabled="interactionRefreshing" @click="refreshInteraction">
          {{ interactionRefreshed ? '已刷新' : '刷新' }}
        </button>
      </div>
    </div>

    <div class="section-title">中介微信名（全部参与定时轮询；发送消息并确认后会自动加入）</div>
    <div class="group-list">
      <div v-for="(_, index) in contacts" :key="index" class="group-row">
        <button class="icon-btn" title="删除" @click="removeContact(index)">−</button>
        <input
          v-model="contacts[index]"
          placeholder="中介微信备注/昵称（如 中介小王）"
          @input="touchContacts"
        />
      </div>
      <button class="plain-btn" @click="addContact">+ 添加中介</button>
    </div>

    <div class="section-title">中介互动（轮询中介会话，草拟回复经你确认后发送）</div>
    <label class="switch-row">
      <span>启用中介互动</span>
      <ToggleSwitch v-model="contactInteractionEnabled" />
    </label>
    <label>
      <span>轮询间隔（秒，30~300；每次检查一个中介，需操作微信窗口约 10~15 秒）</span>
      <input v-model.number="contactInteractionIntervalSec" type="number" min="30" max="300" />
    </label>
    <div class="status-box">
      <div class="status-line">
        中介互动：{{ contactStatus?.enabled ? '已启用' : '未启用' }} ·
        {{ contactStatus?.running ? '检查中' : '空闲' }} ·
        名单 {{ contactStatus?.contacts?.length || 0 }} 个
        <span v-if="contactStatus?.paused_reason" class="paused">（{{ contactStatus.paused_reason }}）</span>
      </div>
      <div class="status-line">
        待确认回复：{{ contactStatus?.pending_count ?? 0 }} 条（微信会收到审批通知，也可在网页聊天页确认）
      </div>
      <div class="status-line">
        上次检查：{{ formatTime(contactStatus?.last_poll?.last_poll_at) }}
        <span v-if="contactStatus?.last_poll?.last_contact">
          · 联系人：{{ contactStatus.last_poll.last_contact }}
        </span>
      </div>
      <div v-if="contactStatus?.last_poll?.last_error" class="err">
        {{ contactStatus.last_poll.last_error }}
      </div>
      <div class="btn-row">
        <button
          class="plain-btn"
          :disabled="contactRunning || contactPolling"
          @click="triggerContactCheck"
        >
          {{ contactRunning || contactPolling ? '检查中…' : '立即检查一次' }}
        </button>
        <button class="plain-btn" :disabled="contactRefreshing" @click="refreshContact">
          {{ contactRefreshed ? '已刷新' : '刷新' }}
        </button>
      </div>
    </div>
    <div v-if="contactStatusError" class="err">{{ contactStatusError }}</div>

    <div v-if="statusError" class="err">{{ statusError }}</div>
    <div class="save-row">
      <span v-if="message" class="ok">{{ message }}</span>
      <span v-if="error" class="err">{{ error }}</span>
      <span class="spacer" />
      <button class="save-btn" :disabled="saving" @click="emit('save')">
        {{ saving ? '保存中…' : '保存本卡片' }}
      </button>
    </div>

    <DateRangeDialog
      v-model:open="dateDialogOpen"
      v-model:start="dateStart"
      v-model:end="dateEnd"
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
.section-title {
  font-size: 12px;
  font-weight: 600;
  color: #334155;
  padding-top: 4px;
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
input {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  color: #0f172a;
  outline: none;
}
input:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(147, 197, 253, 0.25);
}
.hint {
  font-size: 12px;
  color: #94a3b8;
  margin: 0;
  line-height: 1.6;
}
.group-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.group-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.group-row input {
  flex: 1;
}
.icon-btn {
  width: 28px;
  height: 28px;
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 8px;
  font-size: 16px;
  color: #b91c1c;
  cursor: pointer;
}
.date-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}
.plain-btn {
  border: 1px solid #e2e8f0;
  background: #f8fafc;
  border-radius: 10px;
  padding: 6px 12px;
  font-size: 12px;
  color: #475569;
  cursor: pointer;
  align-self: flex-start;
}
.plain-btn:disabled {
  opacity: 0.6;
  cursor: default;
}
.status-box {
  background: #f8fafc;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.status-line {
  font-size: 12px;
  color: #475569;
}
.btn-row {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.progress-box {
  background: #eff6ff;
  border-radius: 10px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.progress-line {
  font-size: 12px;
  color: #1e40af;
}
.progress-track {
  height: 6px;
  background: #dbeafe;
  border-radius: 4px;
  overflow: hidden;
}
.progress-fill {
  height: 100%;
  background: #3b82f6;
  transition: width 0.3s;
}
.paused {
  color: #b45309;
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