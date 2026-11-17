// 普通 JSON 请求（非流式接口）

async function request(url, options = {}) {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`)
  return data
}

export async function ingestGroup(groupName, dateIso) {
  return request('/api/wechat/ingest', {
    method: 'POST',
    body: JSON.stringify({ group_name: groupName, target_date: dateIso }),
  })
}

// 逐日读取范围（每天一次请求，最多 30 天；某天失败立即停止并抛出）
export async function ingestGroupRange(groupName, startIso, endIso, onProgress) {
  const days = enumerateDays(startIso, endIso)
  if (days.length > 30) throw new Error('一次最多读取 30 天，请缩小日期范围')
  const total = {
    total_messages: 0, noise_filtered: 0, dup_raw: 0, batches: 0, llm_valid: 0,
    new_inserted: 0, dup_in_run: 0, dup_jsonl: 0, dup_db: 0, commute_failed: 0,
    elapsed_sec: 0, days: [],
  }
  for (let i = 0; i < days.length; i += 1) {
    onProgress?.({ index: i + 1, total: days.length, date: days[i] })
    const stats = await ingestGroup(groupName, days[i])
    total.days.push({ date: days[i], stats })
    for (const key of Object.keys(total)) {
      if (key !== 'days') total[key] += stats[key] || 0
    }
  }
  return total
}

// 逐群逐日读取（群列表来自设置页「默认读取群名」）
export async function ingestGroupsRange(groupNames, startIso, endIso, onProgress) {
  if (!groupNames.length) throw new Error('未配置默认读取群')
  const days = enumerateDays(startIso, endIso)
  if (days.length > 30) throw new Error('一次最多读取 30 天，请缩小日期范围')
  const total = {
    total_messages: 0, noise_filtered: 0, dup_raw: 0, batches: 0, llm_valid: 0,
    new_inserted: 0, dup_in_run: 0, dup_jsonl: 0, dup_db: 0, commute_failed: 0,
    elapsed_sec: 0, days: [], groups: [],
  }
  for (let g = 0; g < groupNames.length; g += 1) {
    const name = groupNames[g]
    const stats = await ingestGroupRange(name, startIso, endIso, ({ index, total: dayTotal, date }) => {
      onProgress?.({
        group: name,
        groupIndex: g + 1,
        groupTotal: groupNames.length,
        index,
        total: dayTotal,
        date,
      })
    })
    total.groups.push({ group: name, stats })
    total.days.push(...stats.days)
    for (const key of Object.keys(total)) {
      if (key !== 'days' && key !== 'groups') total[key] += stats[key] || 0
    }
  }
  return total
}

// 纯字符串日期枚举（含首尾，避免时区问题）
function enumerateDays(startIso, endIso) {
  const result = []
  const [sy, sm, sd] = startIso.split('-').map(Number)
  const [ey, em, ed] = endIso.split('-').map(Number)
  const cursor = new Date(Date.UTC(sy, sm - 1, sd))
  const end = new Date(Date.UTC(ey, em - 1, ed))
  while (cursor <= end && result.length < 31) {
    const y = cursor.getUTCFullYear()
    const m = String(cursor.getUTCMonth() + 1).padStart(2, '0')
    const d = String(cursor.getUTCDate()).padStart(2, '0')
    result.push(`${y}-${m}-${d}`)
    cursor.setUTCDate(cursor.getUTCDate() + 1)
  }
  return result
}

// 截图上传（multipart，不能复用 JSON request 封装）
export async function uploadScreenshot(groupName, dateIso, file) {
  const form = new FormData()
  form.append('group_name', groupName)
  form.append('target_date', dateIso)
  form.append('file', file)
  const resp = await fetch('/api/upload/screenshot', { method: 'POST', body: form })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`)
  return data
}

export async function fetchHealth() {
  return request('/api/health')
}

// 设置
export async function getSettings() {
  return request('/api/settings')
}

export async function saveSettings(patch) {
  return request('/api/settings', { method: 'PUT', body: JSON.stringify(patch) })
}

export async function testModel(kind) {
  return request('/api/settings/test', { method: 'POST', body: JSON.stringify({ kind }) })
}

// 通勤起点：省/市/区三级 + 全库重算
export async function getDistricts(adcode = '100000') {
  return request(`/api/system/districts?adcode=${encodeURIComponent(adcode)}`)
}

export async function pickDirectory() {
  return request('/api/system/pick-directory', { method: 'POST' })
}

export async function getCommuteStatus() {
  return request('/api/commute/refresh/status')
}

export async function runCommuteRefresh() {
  return request('/api/commute/refresh', { method: 'POST' })
}

// 简历 RAG
export async function uploadResume(file) {
  const form = new FormData()
  form.append('file', file)
  const resp = await fetch('/api/resume/upload', { method: 'POST', body: form })
  const data = await resp.json().catch(() => ({}))
  if (!resp.ok) throw new Error(data.detail || `HTTP ${resp.status}`)
  return data
}

export async function listResumeFiles() {
  return request('/api/resume/files')
}

export async function deleteResumeFile(id) {
  return request(`/api/resume/files/${id}`, { method: 'DELETE' })
}

export async function rebuildResumeIndex() {
  return request('/api/resume/rebuild', { method: 'POST' })
}

// 会话
export async function listSessions() {
  return request('/api/sessions')
}

export async function createSession(title = null) {
  return request('/api/sessions', { method: 'POST', body: JSON.stringify({ title }) })
}

export async function fetchMessages(id) {
  return request(`/api/sessions/${id}/messages`)
}

// 心动的offer
export async function listOffers(status = 'favorite,todo') {
  return request(`/api/offers?status=${encodeURIComponent(status)}`)
}

export async function createManualOffer(data) {
  return request('/api/offers', { method: 'POST', body: JSON.stringify(data) })
}

export async function updateOffer(id, data) {
  return request(`/api/offers/${id}`, { method: 'PUT', body: JSON.stringify(data) })
}

export async function patchOfferStatus(jobIds, status, mode = 'set') {
  return request('/api/offers/status', {
    method: 'PATCH',
    body: JSON.stringify({ job_ids: jobIds, status, mode }),
  })
}

export async function deleteOffer(id) {
  return request(`/api/offers/${id}`, { method: 'DELETE' })
}

export async function exportOffers(jobIds = null) {
  return request('/api/offers/export', {
    method: 'POST',
    body: JSON.stringify({ job_ids: jobIds }),
  })
}

// 长期跟踪
export async function getTrackingStatus() {
  return request('/api/tracking/status')
}

export async function runTracking() {
  return request('/api/tracking/run', { method: 'POST' })
}

// 微信互动
export async function getInteractionStatus() {
  return request('/api/interaction/status')
}

export async function runInteraction() {
  return request('/api/interaction/run', { method: 'POST' })
}

// 中介互动（轮询中介会话 → 草拟回复 → 用户确认后发送）
export async function getContactInteractionStatus() {
  return request('/api/interaction/contact-status')
}

export async function runContactInteraction() {
  return request('/api/interaction/contact-run', { method: 'POST' })
}

export async function getContactPending() {
  return request('/api/interaction/contact-pending')
}

export async function resumeContactPending(threadId, decision) {
  return request('/api/interaction/contact-pending/resume', {
    method: 'POST',
    body: JSON.stringify({ thread_id: threadId, decision }),
  })
}

export async function deleteSession(id) {
  const resp = await fetch(`/api/sessions/${id}`, { method: 'DELETE' })
  if (!resp.ok && resp.status !== 404) {
    const data = await resp.json().catch(() => ({}))
    throw new Error(data.detail || `HTTP ${resp.status}`)
  }
  if (resp.status === 404) throw new Error('会话不存在')
}