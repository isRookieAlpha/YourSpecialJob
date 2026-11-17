<script setup>
// 长期跟踪设置：定时增量读取群消息、偏好命中推送微信；附运行状态与手动触发
import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  getInteractionStatus,
  getTrackingStatus,
  runInteraction,
  runTracking,
} from '../../api/http'

defineProps({
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
})
const emit = defineEmits(['save'])

const enabled = defineModel('enabled', { type: Boolean, default: false })
const intervalMin = defineModel('intervalMin', { type: Number, default: 15 })
const groupsText = defineModel('groupsText', { type: String, default: '' })
const pushEnabled = defineModel('pushEnabled', { type: Boolean, default: true })
const pushLimit = defineModel('pushLimit', { type: Number, default: 5 })
const ledgerDays = defineModel('ledgerDays', { type: Number, default: 7 })
// 微信互动（推送与互动共用目标会话）
const interactionEnabled = defineModel('interactionEnabled', { type: Boolean, default: false })
const interactionIntervalSec = defineModel('interactionIntervalSec', { type: Number, default: 60 })
const wechatTarget = defineModel('wechatTarget', { type: String, default: '' })

const status = ref(null)
const interactionStatus = ref(null)
const statusError = ref('')
const running = ref(false)
const polling = ref(false)
const interactionRunning = ref(false)
let pollTimer = null

async function loadStatus(silent = false) {
  try {
    status.value = await getTrackingStatus()
    interactionStatus.value = await getInteractionStatus()
    statusError.value = ''
  } catch (e) {
    if (!silent) statusError.value = e.message
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

// 触发后轮询状态：一轮包含微信读取+弱模型提取，通常几十秒到几分钟
function startPolling(beforeAt) {
  stopPolling()
  polling.value = true
  let attempts = 0
  pollTimer = setInterval(async () => {
    attempts += 1
    await loadStatus(true)
    const at = status.value?.last_round?.at
    const failed = Boolean(status.value?.last_round?.error)
    if ((at && at !== beforeAt) || failed || attempts >= 30) stopPolling()
  }, 2000)
}

function stopPolling() {
  polling.value = false
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
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

function formatTime(iso) {
  if (!iso) return '-'
  const d = new Date(iso)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

onMounted(loadStatus)
onBeforeUnmount(stopPolling)
</script>

<template>
  <div class="card">
    <div class="card-title">长期跟踪</div>
    <p class="hint-top">
      定时后台读取群消息（增量入库，不重复调用大模型）；命中偏好时推送到微信（WECHAT_SENDER_ID 会话）。
      首次启用为基线：只入库不推送。
    </p>

    <label class="checkbox-row">
      <input v-model="enabled" type="checkbox" />
      <span>启用定时跟踪</span>
    </label>

    <div class="row">
      <label>
        <span>跟踪间隔（分钟，5~180）</span>
        <input v-model.number="intervalMin" type="number" min="5" max="180" />
      </label>
      <label>
        <span>单条摘要最多展示条数（1~10）</span>
        <input v-model.number="pushLimit" type="number" min="1" max="10" />
      </label>
      <label>
        <span>账本保留天数（1~30）</span>
        <input v-model.number="ledgerDays" type="number" min="1" max="30" />
      </label>
    </div>

    <label>
      <span>跟踪群（每行一个，或用逗号分隔）</span>
      <textarea v-model="groupsText" rows="3" placeholder="兼职1群&#10;兼职2群" />
    </label>

    <label class="checkbox-row">
      <input v-model="pushEnabled" type="checkbox" />
      <span>命中偏好时推送到微信</span>
    </label>

    <div class="status-box">
      <div class="status-title">
        运行状态
        <button class="refresh" @click="loadStatus">刷新</button>
        <button class="run-now" :disabled="running || polling" @click="triggerRun">
          {{ running || polling ? '运行中…' : '立即执行一轮' }}
        </button>
      </div>
      <div v-if="statusError" class="status-err">{{ statusError }}</div>
      <template v-if="status">
        <div class="status-line">
          {{ status.enabled ? '已启用' : '未启用' }} · {{ status.running ? '正在运行' : '空闲' }}
          · 间隔 {{ status.interval_min }} 分钟
          <span v-if="status.paused_reason" class="paused">（{{ status.paused_reason }}）</span>
        </div>
        <div v-if="status.last_round" class="status-line">
          上次运行：{{ formatTime(status.last_round.at) }}
          <span :class="status.last_round.ok ? 'ok' : 'err'">{{ status.last_round.ok ? '成功' : '失败' }}</span>
          <span v-if="status.last_round.stats">· 新入库 {{ status.last_round.stats.new_inserted ?? 0 }} · 推送 {{ status.last_round.stats.pushed ?? 0 }}</span>
        </div>
        <div v-if="status.last_round?.error" class="status-line err">
          错误：{{ status.last_round.error }}
        </div>
        <div class="status-line">
          推送目标：{{ status.push_target_configured ? '已配置' : '未配置（WECHAT_SENDER_ID）' }}
          · 高德：{{ status.amap_configured ? '已配置' : '未配置' }}
          · 通勤起点：{{ status.commute_origin_configured ? '已配置' : '未配置' }}
        </div>
        <div class="status-line">
          近 24h 推送：成功 {{ status.push_24h.sent }} · 失败 {{ status.push_24h.failed }} · 待重试 {{ status.push_24h.retry_pending }}
        </div>
        <div v-if="status.groups?.length" class="group-list">
          <div v-for="g in status.groups" :key="g.group_name" class="group-line">
            {{ g.group_name }}：{{ g.initialized_at ? '已初始化' : '待首轮基线' }}
            · 上次 {{ formatTime(g.last_round_at) }}
            <span v-if="g.last_error" class="err">{{ g.last_error }}</span>
          </div>
        </div>
      </template>
    </div>

    <div class="section-divider" />
    <div class="section-title">微信互动（推送与互动共用目标会话）</div>
    <label class="checkbox-row">
      <input v-model="interactionEnabled" type="checkbox" />
      <span>启用微信互动（轮询目标会话，收到你的消息后由 agent 回复）</span>
    </label>
    <label>
      <span>微信目标会话（留空使用 .env 的 WECHAT_SENDER_ID）</span>
      <input v-model="wechatTarget" placeholder="如 文件传输助手 / 你的微信备注" />
    </label>
    <label>
      <span>互动轮询间隔（秒，30~300；每次检查需操作微信窗口约 10~15 秒）</span>
      <input v-model.number="interactionIntervalSec" type="number" min="30" max="300" />
    </label>
    <div class="interaction-box">
      <div class="status-line">
        互动：{{ interactionStatus?.enabled ? '已启用' : '未启用' }} ·
        {{ interactionStatus?.running ? '检查中' : '空闲' }} ·
        目标：{{ interactionStatus?.target_configured ? interactionStatus.target_masked : '未配置' }}
        <span v-if="interactionStatus?.paused_reason" class="paused">（{{ interactionStatus.paused_reason }}）</span>
      </div>
      <div class="status-line">
        待审批操作：{{ interactionStatus?.pending_count ?? 0 }} 条
        （危险操作会发微信请求确认，回复「同意」或「拒绝」）
      </div>
      <button class="run-now" :disabled="interactionRunning" @click="triggerInteraction">
        {{ interactionRunning ? '已触发…' : '立即检查一次' }}
      </button>
    </div>

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
.hint-top {
  margin: 0;
  font-size: 12px;
  color: #94a3b8;
  line-height: 1.6;
}
.row {
  display: flex;
  gap: 12px;
  flex-wrap: wrap;
}
.row label {
  flex: 1;
  min-width: 140px;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
}
input,
textarea {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  outline: none;
  color: #0f172a;
  font-family: inherit;
  resize: vertical;
}
input:focus,
textarea:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(147, 197, 253, 0.25);
}
.checkbox-row {
  flex-direction: row;
  align-items: center;
  gap: 8px;
  font-size: 13px;
  color: #334155;
}
.status-box {
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 5px;
  background: #f8fafc;
}
.status-title {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  font-weight: 600;
  color: #475569;
}
.refresh,
.run-now {
  border: 1px solid #e2e8f0;
  background: #fff;
  border-radius: 8px;
  padding: 3px 10px;
  font-size: 11px;
  color: #475569;
  cursor: pointer;
}
.run-now {
  margin-left: auto;
  border-color: #bfdbfe;
  color: #2563eb;
}
.run-now:disabled {
  opacity: 0.6;
  cursor: default;
}
.status-line {
  font-size: 12px;
  color: #64748b;
}
.paused {
  color: #b45309;
}
.group-list {
  display: flex;
  flex-direction: column;
  gap: 2px;
  margin-top: 2px;
}
.group-line {
  font-size: 11.5px;
  color: #94a3b8;
}
.section-divider {
  height: 1px;
  background: #f1f5f9;
  margin: 4px 0;
}
.section-title {
  font-size: 13px;
  font-weight: 600;
  color: #475569;
}
.interaction-box {
  border: 1px solid #f1f5f9;
  border-radius: 12px;
  padding: 10px 12px;
  display: flex;
  flex-direction: column;
  gap: 6px;
  background: #f8fafc;
}
.save-row {
  display: flex;
  align-items: center;
  gap: 8px;
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
}
.status-err {
  font-size: 12px;
  color: #b91c1c;
}
</style>