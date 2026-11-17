<script setup>
// ToDo 地点静态地图（后端代理高德返回 PNG + X-Map-Legend 图例）
// 图例把 A/B/C 大头针映射到具体兼职，失败时降级为地点文字列表
import { computed, onBeforeUnmount, ref, watch } from 'vue'

const props = defineProps({
  items: { type: Array, default: () => [] }, // ToDo 列表（含 location/work_summary）
  version: { type: Number, default: 0 }, // 数据变化时触发刷新
})

const mapUrl = ref('')
const loading = ref(false)
const error = ref('')
const skipped = ref(0)
const legend = ref([])

const locations = computed(() =>
  [...new Set(props.items.map((i) => (i.location || '').trim()).filter(Boolean))]
)

async function load() {
  if (!locations.value.length) {
    revoke()
    legend.value = []
    error.value = ''
    return
  }
  loading.value = true
  error.value = ''
  revoke()
  try {
    const resp = await fetch('/api/offers/map.png', { cache: 'no-store' })
    if (!resp.ok) {
      let detail = `HTTP ${resp.status}`
      try {
        const data = await resp.json()
        if (data.detail) detail = data.detail
      } catch { /* 非 JSON 忽略 */ }
      throw new Error(detail)
    }
    skipped.value = Number(resp.headers.get('X-Map-Skipped') || 0)
    //1.图例：URL 编码 JSON（label → 地点 → 关联兼职标题）
    try {
      const raw = resp.headers.get('X-Map-Legend')
      legend.value = raw ? JSON.parse(decodeURIComponent(raw)) : []
    } catch {
      legend.value = []
    }
    mapUrl.value = URL.createObjectURL(await resp.blob())
  } catch (e) {
    error.value = e.message
  } finally {
    loading.value = false
  }
}

function revoke() {
  if (mapUrl.value) {
    URL.revokeObjectURL(mapUrl.value)
    mapUrl.value = ''
  }
}

watch(() => [props.version, locations.value.length], load, { immediate: true })
onBeforeUnmount(revoke)
</script>

<template>
  <div class="card">
    <div class="card-header">
      <div class="card-title">ToDo 工作地点</div>
      <button class="refresh-btn" :disabled="loading || !locations.length" @click="load">刷新地图</button>
    </div>

    <div v-if="!locations.length" class="empty">还没有带地点的 ToDo 待办offer</div>
    <div v-else-if="loading" class="empty">地图加载中…</div>
    <template v-else-if="mapUrl">
      <img :src="mapUrl" class="map-img" alt="ToDo 地点地图" />
      <ul v-if="legend.length" class="legend">
        <li v-for="item in legend" :key="item.label">
          <span class="pin">{{ item.label }}</span>
          <span class="legend-loc">{{ item.location }}</span>
          <span class="legend-titles">{{ item.titles.join('、') }}</span>
        </li>
      </ul>
      <div v-if="skipped > 0" class="skip-hint">
        另有 {{ skipped }} 个地点未在地图标出（无法定位或超过 10 个上限，见日程表）
      </div>
    </template>
    <div v-else class="fallback">
      <div class="err">{{ error || '地图不可用' }}</div>
      <ul class="location-list">
        <li v-for="(loc, i) in locations.slice(0, 10)" :key="i">{{ loc }}</li>
      </ul>
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
.card-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
}
.card-title {
  font-size: 15px;
  font-weight: 600;
  color: #0f172a;
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
.refresh-btn:disabled {
  opacity: 0.5;
  cursor: default;
}
.map-img {
  width: 100%;
  border-radius: 12px;
  display: block;
}
.legend {
  list-style: none;
  margin: 10px 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.legend li {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
  color: #475569;
}
.pin {
  flex: none;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #2563eb;
  color: #fff;
  font-size: 11px;
  font-weight: 700;
  display: flex;
  align-items: center;
  justify-content: center;
}
.legend-loc {
  font-weight: 600;
  color: #1e293b;
}
.legend-titles {
  color: #64748b;
}
.empty {
  font-size: 13px;
  color: #94a3b8;
  text-align: center;
  padding: 20px 0;
}
.err {
  font-size: 12px;
  color: #b45309;
  background: #fffbeb;
  border-radius: 10px;
  padding: 8px 12px;
}
.location-list {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
  color: #64748b;
}
.skip-hint {
  margin-top: 8px;
  font-size: 11px;
  color: #94a3b8;
}
</style>