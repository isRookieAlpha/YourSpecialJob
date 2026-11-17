// fetch POST + ReadableStream 手写 SSE 解析（EventSource 不支持 POST）
// 后端事件协议：meta / token / todos / tool / progress / hitl / done / error

// 公共读取：按空行分帧 + \r\n 归一化 + 45 秒断线看门狗
async function readSSE(resp, onEvent) {
  const reader = resp.body.getReader()
  const decoder = new TextDecoder('utf-8')
  let buffer = ''

  // 断线看门狗：后端每 15s 发 ping 注释，45s 没有任何字节说明连接已死
  let lastByteAt = Date.now()
  const watchdog = setInterval(() => {
    if (Date.now() - lastByteAt > 45000) {
      clearInterval(watchdog)
      onEvent('error', { message: '连接已中断（45 秒无数据），请重试或点击"新会话"。' })
      reader.cancel().catch(() => {})
    }
  }, 5000)

  try {
    while (true) {
      const { value, done } = await reader.read()
      if (done) break
      lastByteAt = Date.now()
      buffer += decoder.decode(value, { stream: true })
      // sse-starlette 默认用 \r\n 行尾，统一归一化后再按空行分帧（否则 \n\n 永远匹配不到）
      buffer = buffer.replace(/\r\n/g, '\n')

      // 按空行分帧
      let idx
      while ((idx = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, idx)
        buffer = buffer.slice(idx + 2)

        let event = 'message'
        const dataLines = []
        for (const line of frame.split('\n')) {
          if (line.startsWith(':')) continue // sse-starlette 心跳注释行
          if (line.startsWith('event:')) event = line.slice(6).trim()
          else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
        }
        if (dataLines.length) {
          try {
            onEvent(event, JSON.parse(dataLines.join('\n')))
          } catch { /* 非 JSON data 忽略，流不断 */ }
        }
      }
    }
  } finally {
    clearInterval(watchdog)
  }
}

// 非 2xx 时解析 detail
async function ensureOk(resp) {
  if (!resp.ok || !resp.body) {
    let detail = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      if (data.detail) detail = data.detail
    } catch { /* 非 JSON 忽略 */ }
    throw new Error(detail)
  }
}

export async function streamChat({ threadId, message, onEvent, signal }) {
  const resp = await fetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ thread_id: threadId || null, message }),
    signal, // AbortController 用于"停止生成"
  })
  await ensureOk(resp)
  await readSSE(resp, onEvent)
}

// HITL 恢复：审批决策后继续流式输出（事件协议与 streamChat 相同）
export async function resumeChat({ threadId, decisions, onEvent, signal }) {
  const resp = await fetch('/api/chat/resume', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
    body: JSON.stringify({ thread_id: threadId, decisions }),
    signal,
  })
  await ensureOk(resp)
  await readSSE(resp, onEvent)
}