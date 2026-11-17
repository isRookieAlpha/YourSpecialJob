<script setup>
// 设置页：通勤起始位置 / 偏好与通用 / 微信设置 / 大模型设置（每卡片独立保存）
// preferences 保存采用"原始全量 + 本页字段覆盖"合并，避免整体替换丢键
import { onBeforeUnmount, onMounted, reactive, ref } from 'vue'
import {
  getCommuteStatus,
  getSettings,
  runCommuteRefresh,
  saveSettings,
} from '../api/http'
import CommuteCard from '../components/settings/CommuteCard.vue'
import LargeModelCard from '../components/settings/LargeModelCard.vue'
import PreferenceCard from '../components/settings/PreferenceCard.vue'
import ResumeCard from '../components/settings/ResumeCard.vue'
import WechatCard from '../components/settings/WechatCard.vue'

const loading = ref(true)
const pageError = ref('')
const preferencesRaw = ref({}) // GET 返回的完整 preferences（合并保存用）
const contactsTouched = ref(false) // 用户是否手动编辑过中介名单（未编辑时用最新 raw，避免覆盖自动追加）

const form = reactive({
  strong: { model: '', baseUrl: '', apiKey: '' },
  weak: { model: '', baseUrl: '', apiKey: '' },
  backup: { model: '', baseUrl: '', apiKey: '' },
  embedding: { model: '', baseUrl: '', apiKey: '', dims: 1024 },
  amap: { apiKey: '', url: '' },
  preferences: {
    wechatAccessEnabled: true,
    exportDir: '',
    gender: 'any',
    age: '',
    commissionMin: 100,
    dailyHoursMax: '',
    workTimeStart: '08:00',
    workTimeEnd: '22:00',
    maxCommuteMin: '',
    maxCommuteTransitMin: '',
    maxCommuteDriveMin: '',
    laborLevel: 'any',
    note: '',
    wechatGroups: [''],
    dateStart: '',
    dateEnd: '',
    trackingEnabled: false,
    trackingIntervalMin: 15,
    trackingPushEnabled: true,
    trackingPushLimit: 5,
    trackingLedgerDays: 7,
    wechatTarget: '',
    interactionEnabled: false,
    interactionIntervalSec: 60,
    contacts: [''],
    contactInteractionEnabled: false,
    contactInteractionIntervalSec: 60,
    amapProvince: '',
    amapCity: '',
    amapDistrict: '',
    commuteDetail: '',
  },
  configured: { strong: false, weak: false, backup: false, embedding: false, amap: false },
})

const saveState = reactive({
  commute: { saving: false, message: '', error: '' },
  preferences: { saving: false, message: '', error: '' },
  wechat: { saving: false, message: '', error: '' },
  models: { saving: false, message: '', error: '' },
})

const commuteStatus = ref(null)
const commuteError = ref('')
let commuteTimer = null

function todayIso() {
  return new Date().toISOString().slice(0, 10)
}

function normalizeCityName(province, city) {
  //1.直辖市的"市"级区划名是"上海城区/北京城区"，存库时规范为省级名"上海市"
  if (!city) return ''
  if (city.endsWith('城区') || city === province) return province
  return city
}

function composeOrigin() {
  const p = form.preferences
  const parts = []
  //2."XX城区"不是地名，拼接地址时跳过
  const cityPart = p.amapCity && !p.amapCity.endsWith('城区') ? p.amapCity : ''
  for (const part of [p.amapProvince, cityPart, p.amapDistrict, p.commuteDetail].map((s) => (s || '').trim())) {
    if (!part) continue
    const last = parts[parts.length - 1]
    if (last === part || (last && last.endsWith(part))) continue
    parts.push(part)
  }
  return parts.join('')
}

function numOrNull(value) {
  if (value === '' || value === null || value === undefined) return null
  const num = Number(value)
  return Number.isFinite(num) && num > 0 ? num : null
}

function applySettings(s, { clearApiKeys = false } = {}) {
  for (const kind of ['strong', 'weak', 'backup']) {
    form[kind].model = s[kind].model
    form[kind].baseUrl = s[kind].base_url
    if (clearApiKeys) form[kind].apiKey = ''
    form.configured[kind] = s[kind].api_key_configured
  }
  form.embedding.model = s.embedding.model
  form.embedding.baseUrl = s.embedding.base_url
  form.embedding.dims = s.embedding.dims
  if (clearApiKeys) form.embedding.apiKey = ''
  form.configured.embedding = s.embedding.api_key_configured

  //2.高德 MCP：key 只回显是否已配置，URL 明文回显
  form.amap.url = s.amap?.url || ''
  if (clearApiKeys) form.amap.apiKey = ''
  form.configured.amap = Boolean(s.amap?.api_key_configured)

  const p = s.preferences || {}
  preferencesRaw.value = { ...p }

  //1.旧键迁移（新键优先）
  const groups = Array.isArray(p.wechat_groups) && p.wechat_groups.filter(Boolean).length
    ? [...p.wechat_groups]
    : Array.isArray(p.tracking_groups) && p.tracking_groups.filter(Boolean).length
      ? [...p.tracking_groups]
      : p.default_group
        ? [p.default_group]
        : ['']
  const start = p.default_date_start || p.default_date || todayIso()

  form.preferences.wechatAccessEnabled = p.wechat_access_enabled !== false
  form.preferences.exportDir = p.export_dir ?? ''
  form.preferences.gender = p.gender ?? 'any'
  form.preferences.age = p.age ?? ''
  form.preferences.commissionMin = p.commission_min ?? 100
  form.preferences.dailyHoursMax = p.daily_hours_max ?? ''
  form.preferences.workTimeStart = p.work_time_start || '08:00'
  form.preferences.workTimeEnd = p.work_time_end || '22:00'
  form.preferences.maxCommuteMin = p.max_commute_min ?? ''
  form.preferences.maxCommuteTransitMin = p.max_commute_transit_min ?? ''
  form.preferences.maxCommuteDriveMin = p.max_commute_drive_min ?? ''
  form.preferences.laborLevel = p.labor_level ?? (p.prefer_light === true ? 'light' : 'any')
  form.preferences.note = p.note ?? ''
  form.preferences.wechatGroups = groups
  form.preferences.dateStart = start
  form.preferences.dateEnd = p.default_date_end || start
  form.preferences.trackingEnabled = p.tracking_enabled ?? false
  form.preferences.trackingIntervalMin = p.tracking_interval_min ?? 15
  form.preferences.trackingPushEnabled = p.tracking_push_enabled ?? true
  form.preferences.trackingPushLimit = p.tracking_push_limit ?? 5
  form.preferences.trackingLedgerDays = p.tracking_ledger_days ?? 7
  form.preferences.wechatTarget = p.wechat_target ?? ''
  form.preferences.interactionEnabled = p.interaction_enabled ?? false
  form.preferences.interactionIntervalSec = p.interaction_interval_sec ?? 60
  const contacts = Array.isArray(p.wechat_contacts) ? p.wechat_contacts.filter(Boolean) : []
  form.preferences.contacts = contacts.length ? [...contacts] : ['']
  form.preferences.contactInteractionEnabled = p.contact_interaction_enabled ?? false
  form.preferences.contactInteractionIntervalSec = p.contact_interaction_interval_sec ?? 60
  contactsTouched.value = false
  form.preferences.amapProvince = p.amap_province ?? ''
  form.preferences.amapCity = p.amap_city ?? ''
  form.preferences.amapDistrict = p.amap_district ?? ''
  form.preferences.commuteDetail = p.commute_detail ?? ''
}

async function load() {
  loading.value = true
  try {
    applySettings(await getSettings())
    pageError.value = ''
  } catch (e) {
    pageError.value = e.message
  } finally {
    loading.value = false
  }
}

//2.通用保存流程：成功后用服务端结果回填；仅模型块保存时清空 API Key 输入
async function runSave(key, patch, successText, options = {}) {
  const st = saveState[key]
  if (st.saving) return false
  st.saving = true
  st.message = ''
  st.error = ''
  try {
    const r = await saveSettings(patch)
    st.message = r.agent_rebuilt ? `${successText}，并已重建模型与 agent` : successText
    applySettings(r.settings, options)
    setTimeout(() => (st.message = ''), 4000)
    return true
  } catch (e) {
    st.error = e.message
    return false
  } finally {
    st.saving = false
  }
}

async function refreshPreferencesRaw() {
  //5.保存前刷新最新 preferences：避免页面旧快照覆盖后台自动追加的 wechat_contacts
  try {
    const fresh = await getSettings()
    preferencesRaw.value = { ...(fresh.preferences || {}) }
  } catch (e) {
    // 刷新失败不阻塞保存（沿用旧快照）
  }
}

function normalizeList(list) {
  return (list || []).map((s) => (s || '').trim()).filter(Boolean)
}

function markContactsTouched() {
  contactsTouched.value = true
}

function preferencesPatch() {
  const p = form.preferences
  const groups = p.wechatGroups.map((s) => (s || '').trim()).filter(Boolean)
  const editedContacts = normalizeList(p.contacts)
  const rawContacts = Array.isArray(preferencesRaw.value.wechat_contacts)
    ? normalizeList(preferencesRaw.value.wechat_contacts)
    : []
  const contacts = contactsTouched.value
    ? editedContacts
    : (rawContacts.length ? rawContacts : editedContacts)
  return {
    ...preferencesRaw.value,
    wechat_access_enabled: p.wechatAccessEnabled,
    export_dir: p.exportDir,
    gender: p.gender,
    age: numOrNull(p.age),
    commission_min: Number(p.commissionMin) || 0,
    daily_hours_max: numOrNull(p.dailyHoursMax),
    work_time_start: p.workTimeStart,
    work_time_end: p.workTimeEnd,
    max_commute_min: numOrNull(p.maxCommuteMin),
    max_commute_transit_min: numOrNull(p.maxCommuteTransitMin),
    max_commute_drive_min: numOrNull(p.maxCommuteDriveMin),
    labor_level: p.laborLevel,
    prefer_light: p.laborLevel === 'light',
    note: p.note,
    wechat_groups: groups,
    default_group: groups[0] || '',
    tracking_groups: groups,
    default_date_start: p.dateStart,
    default_date_end: p.dateEnd,
    default_date: p.dateStart,
    tracking_enabled: p.trackingEnabled,
    tracking_interval_min: p.trackingIntervalMin,
    tracking_push_enabled: p.trackingPushEnabled,
    tracking_push_limit: p.trackingPushLimit,
    tracking_ledger_days: p.trackingLedgerDays,
    wechat_target: p.wechatTarget,
    interaction_enabled: p.interactionEnabled,
    interaction_interval_sec: p.interactionIntervalSec,
    wechat_contacts: contacts,
    contact_interaction_enabled: p.contactInteractionEnabled,
    contact_interaction_interval_sec: p.contactInteractionIntervalSec,
    amap_province: p.amapProvince,
    amap_city: normalizeCityName(p.amapProvince, p.amapCity),
    amap_district: p.amapDistrict,
    commute_detail: p.commuteDetail,
    commute_origin: composeOrigin(),
  }
}

async function saveCommute({ changed } = { changed: false }) {
  await refreshPreferencesRaw()
  const patch = {
    preferences: {
      ...preferencesRaw.value,
      amap_province: form.preferences.amapProvince,
      amap_city: normalizeCityName(form.preferences.amapProvince, form.preferences.amapCity),
      amap_district: form.preferences.amapDistrict,
      commute_detail: form.preferences.commuteDetail,
      commute_origin: composeOrigin(),
    },
  }
  const ok = await runSave('commute', patch, changed ? '已保存，正在重算通勤…' : '已保存')
  if (ok && changed) await triggerCommuteRefresh()
}

async function savePreferences() {
  await refreshPreferencesRaw()
  return runSave('preferences', { preferences: preferencesPatch() }, '已保存')
}

async function saveWechat() {
  await refreshPreferencesRaw()
  return runSave(
    'wechat',
    { preferences: preferencesPatch() },
    '已保存，跟踪器/互动/中介互动将在下一轮生效',
  )
}

function saveModels() {
  return runSave('models', {
    strong: { model: form.strong.model, base_url: form.strong.baseUrl, api_key: form.strong.apiKey },
    weak: { model: form.weak.model, base_url: form.weak.baseUrl, api_key: form.weak.apiKey },
    backup: { model: form.backup.model, base_url: form.backup.baseUrl, api_key: form.backup.apiKey },
    embedding: {
      model: form.embedding.model,
      base_url: form.embedding.baseUrl,
      api_key: form.embedding.apiKey,
      dims: form.embedding.dims,
    },
    amap: {
      api_key: form.amap.apiKey,
      // URL 明文非密钥：留空表示清除（回退本地 npx），空串在 PUT 语义里是"不修改"
      url: form.amap.url.trim() ? form.amap.url.trim() : null,
    },
  }, '已保存（API Key 输入已清空，未修改的 key 保持原值）', { clearApiKeys: true })
}

//3.通勤重算状态轮询（3~5 分钟，2 秒一次，最多 5 分钟）
function stopCommutePolling() {
  if (commuteTimer) {
    clearInterval(commuteTimer)
    commuteTimer = null
  }
}

function startCommutePolling() {
  stopCommutePolling()
  let attempts = 0
  commuteTimer = setInterval(async () => {
    attempts += 1
    await loadCommuteStatus()
    if (!commuteStatus.value?.running || attempts >= 150) stopCommutePolling()
  }, 2000)
}

async function loadCommuteStatus() {
  try {
    commuteStatus.value = await getCommuteStatus()
    commuteError.value = ''
  } catch (e) {
    commuteError.value = e.message
  }
}

async function triggerCommuteRefresh() {
  commuteError.value = ''
  try {
    await runCommuteRefresh()
    await loadCommuteStatus()
    startCommutePolling()
  } catch (e) {
    commuteError.value = e.message
  }
}

onMounted(async () => {
  await load()
  await loadCommuteStatus()
  if (commuteStatus.value?.running) startCommutePolling()
})
onBeforeUnmount(stopCommutePolling)
</script>

<template>
  <div class="settings-view">
    <div v-if="loading" class="state">加载中…</div>
    <div v-else class="grid">
      <div v-if="pageError" class="page-error">{{ pageError }}</div>

      <CommuteCard
        :saving="saveState.commute.saving"
        :message="saveState.commute.message"
        :error="saveState.commute.error"
        :refresh-status="commuteStatus"
        :refresh-error="commuteError"
        :original-origin="preferencesRaw.commute_origin || ''"
        :original-city="preferencesRaw.amap_city || ''"
        v-model:province="form.preferences.amapProvince"
        v-model:city="form.preferences.amapCity"
        v-model:district="form.preferences.amapDistrict"
        v-model:detail="form.preferences.commuteDetail"
        @save="saveCommute"
      />

      <PreferenceCard
        :saving="saveState.preferences.saving"
        :message="saveState.preferences.message"
        :error="saveState.preferences.error"
        v-model:access-enabled="form.preferences.wechatAccessEnabled"
        v-model:export-dir="form.preferences.exportDir"
        v-model:gender="form.preferences.gender"
        v-model:age="form.preferences.age"
        v-model:commission-min="form.preferences.commissionMin"
        v-model:daily-hours-max="form.preferences.dailyHoursMax"
        v-model:work-time-start="form.preferences.workTimeStart"
        v-model:work-time-end="form.preferences.workTimeEnd"
        v-model:max-commute-min="form.preferences.maxCommuteMin"
        v-model:max-commute-transit-min="form.preferences.maxCommuteTransitMin"
        v-model:max-commute-drive-min="form.preferences.maxCommuteDriveMin"
        v-model:labor-level="form.preferences.laborLevel"
        v-model:note="form.preferences.note"
        @save="savePreferences"
      />

      <WechatCard
        v-if="form.preferences.wechatAccessEnabled"
        :saving="saveState.wechat.saving"
        :message="saveState.wechat.message"
        :error="saveState.wechat.error"
        v-model:groups="form.preferences.wechatGroups"
        v-model:date-start="form.preferences.dateStart"
        v-model:date-end="form.preferences.dateEnd"
        v-model:tracking-enabled="form.preferences.trackingEnabled"
        v-model:tracking-interval-min="form.preferences.trackingIntervalMin"
        v-model:tracking-push-enabled="form.preferences.trackingPushEnabled"
        v-model:tracking-push-limit="form.preferences.trackingPushLimit"
        v-model:tracking-ledger-days="form.preferences.trackingLedgerDays"
        v-model:wechat-target="form.preferences.wechatTarget"
        v-model:interaction-enabled="form.preferences.interactionEnabled"
        v-model:interaction-interval-sec="form.preferences.interactionIntervalSec"
        v-model:contacts="form.preferences.contacts"
        v-model:contact-interaction-enabled="form.preferences.contactInteractionEnabled"
        v-model:contact-interaction-interval-sec="form.preferences.contactInteractionIntervalSec"
        @save="saveWechat"
        @contacts-touched="markContactsTouched"
      />

      <ResumeCard />

      <LargeModelCard
        :saving="saveState.models.saving"
        :message="saveState.models.message"
        :error="saveState.models.error"
        :configured="form.configured"
        v-model:strong="form.strong"
        v-model:weak="form.weak"
        v-model:backup="form.backup"
        v-model:embedding="form.embedding"
        v-model:amap="form.amap"
        @save="saveModels"
      />
    </div>
  </div>
</template>

<style scoped>
.settings-view {
  flex: 1;
  overflow-y: auto;
  padding-bottom: 24px;
}
.grid {
  display: flex;
  flex-direction: column;
  gap: 14px;
  max-width: 760px;
  margin: 0 auto;
}
.state {
  text-align: center;
  color: #94a3b8;
  padding: 40px 0;
}
.page-error {
  background: #fef2f2;
  color: #b91c1c;
  border-radius: 12px;
  padding: 10px 14px;
  font-size: 12px;
}</style>