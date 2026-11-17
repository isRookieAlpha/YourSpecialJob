<script setup>
// 通勤起始位置卡片：省/市/区三级联动 + 详细地址；修改后确认并触发全库重算
import { computed, onMounted, ref, watch } from 'vue'
import { getDistricts } from '../../api/http'
import ConfirmDialog from './ConfirmDialog.vue'

const props = defineProps({
  saving: { type: Boolean, default: false },
  message: { type: String, default: '' },
  error: { type: String, default: '' },
  refreshStatus: { type: Object, default: null },
  refreshError: { type: String, default: '' },
  originalOrigin: { type: String, default: '' },
  originalCity: { type: String, default: '' },
})
const emit = defineEmits(['save'])

const province = defineModel('province', { type: String, default: '' })
const city = defineModel('city', { type: String, default: '' })
const district = defineModel('district', { type: String, default: '' })
const detail = defineModel('detail', { type: String, default: '' })

const provinces = ref([])
const cities = ref([])
const districts = ref([])
const loadError = ref('')
const loading = ref(false)
const confirmOpen = ref(false)

function findAdcode(items, name) {
  return items.find((item) => item.name === name)?.adcode || ''
}

async function loadProvinces() {
  loading.value = true
  try {
    provinces.value = (await getDistricts('100000')).districts || []
    loadError.value = ''
  } catch (e) {
    loadError.value = e.message
  } finally {
    loading.value = false
  }
}

async function loadCities(provinceName) {
  cities.value = []
  districts.value = []
  const adcode = findAdcode(provinces.value, provinceName)
  if (!adcode) return
  try {
    cities.value = (await getDistricts(adcode)).districts || []
  } catch (e) {
    loadError.value = e.message
  }
}

async function loadDistricts(cityName) {
  districts.value = []
  const adcode = findAdcode(cities.value, cityName)
  if (!adcode) return
  try {
    districts.value = (await getDistricts(adcode)).districts || []
  } catch (e) {
    loadError.value = e.message
  }
}

//1.用户手动切换省份/城市：清空下级并加载新列表（@change 只在用户操作时触发）
function onProvinceChange() {
  city.value = ''
  district.value = ''
  loadCities(province.value)
}

function onCityChange() {
  district.value = ''
  loadDistricts(city.value)
}

//2.程序化回填（保存后服务端返回规范城市名"上海市"）：只映射回级联选项，不清空区县
watch(city, (value) => {
  if (!value || !cities.value.length) return
  if (cities.value.some((item) => item.name === value)) return
  const match = cities.value.find((item) => cityCore(item.name) === cityCore(value))
  if (match) city.value = match.name
})

function composeOrigin() {
  const parts = []
  //1.直辖市的"市"级区划名为"上海城区/北京城区"，不是地名，拼接时跳过
  const cityPart = city.value && !city.value.endsWith('城区') ? city.value : ''
  for (const part of [province.value, cityPart, district.value, detail.value].map((s) => (s || '').trim())) {
    if (!part) continue
    const last = parts[parts.length - 1]
    if (last === part || (last && last.endsWith(part))) continue
    parts.push(part)
  }
  return parts.join('')
}

function cityCore(value) {
  return String(value || '').replace(/城区$/, '').replace(/市$/, '')
}

//2.旧值 amap_city 可能是"上海市"，而级联选项是"上海城区"，需反向匹配
function restoreCityName() {
  //4.返回映射后的显示名：defineModel 的 set 只 emit 事件，同步回读仍是父级旧值，
  //   所以不能依赖 city.value 的即时更新来加载区县
  if (!city.value) return ''
  if (cities.value.some((item) => item.name === city.value)) return city.value
  const match = cities.value.find((item) => cityCore(item.name) === cityCore(city.value))
  if (match) {
    city.value = match.name
    return match.name
  }
  return city.value
}

const composedOrigin = computed(() => composeOrigin())

function handleSave() {
  if (!province.value || !city.value || !district.value) {
    loadError.value = '请选择省/市/区'
    return
  }
  //3.城市按规范名比较（"上海城区" 与存库值 "上海市" 视为相同）
  const normalizedCity = city.value.endsWith('城区') ? province.value : city.value
  const changed = composedOrigin.value !== (props.originalOrigin || '')
    || normalizedCity !== (props.originalCity || '')
  if (changed) {
    confirmOpen.value = true
    return
  }
  emit('save', { changed: false })
}

function confirmSave() {
  confirmOpen.value = false
  emit('save', { changed: true })
}

const progressPercent = computed(() => {
  const s = props.refreshStatus
  if (!s || !s.total) return 0
  return Math.min(100, Math.round((s.processed / s.total) * 100))
})

onMounted(async () => {
  await loadProvinces()
  //3.恢复已保存的选择（先加载上级，再逐级恢复；直辖市做"上海市→上海城区"反向映射）
  if (province.value) {
    await loadCities(province.value)
    const restoredCity = restoreCityName()
    if (restoredCity) await loadDistricts(restoredCity)
  }
})
</script>

<template>
  <div class="card">
    <div class="card-title">通勤起始位置</div>
    <p class="hint">
      用于计算每条兼职的距离、骑行/公共交通/驾车通勤时间。若修改该地址，则数据库中的所有通勤时间需重新计算，
      大约需要 3-5 分钟。
    </p>
    <div class="row">
      <label>
        <span>省/直辖市</span>
        <select v-model="province" :disabled="loading" @change="onProvinceChange">
          <option value="">{{ loading ? '加载中…' : '请选择' }}</option>
          <option v-for="item in provinces" :key="item.adcode" :value="item.name">{{ item.name }}</option>
        </select>
      </label>
      <label>
        <span>城市</span>
        <select v-model="city" :disabled="!province" @change="onCityChange">
          <option value="">请选择</option>
          <option v-for="item in cities" :key="item.adcode" :value="item.name">{{ item.name }}</option>
        </select>
      </label>
      <label>
        <span>区/县</span>
        <select v-model="district" :disabled="!city">
          <option value="">请选择</option>
          <option v-for="item in districts" :key="item.adcode" :value="item.name">{{ item.name }}</option>
        </select>
      </label>
    </div>
    <label>
      <span>详细地址/地标（可选，提高距离精度）</span>
      <input v-model="detail" placeholder="如 静安寺 / 张江地铁站" />
    </label>
    <div class="origin-preview">通勤起点：{{ composedOrigin || '未选择' }}</div>

    <div v-if="refreshStatus && (refreshStatus.running || refreshStatus.finished_at)" class="progress-box">
      <div class="progress-line">
        <template v-if="refreshStatus.running">
          正在重算 {{ refreshStatus.processed }}/{{ refreshStatus.total }}
          （更新 {{ refreshStatus.updated }}，失败 {{ refreshStatus.failed }}）
        </template>
        <template v-else>
          上次重算：更新 {{ refreshStatus.updated }}，失败 {{ refreshStatus.failed }}
          <span v-if="refreshStatus.finished_at">（{{ refreshStatus.finished_at.slice(11, 19) }}）</span>
        </template>
      </div>
      <div class="progress-track">
        <div class="progress-fill" :style="{ width: `${progressPercent}%` }" />
      </div>
      <div v-if="refreshStatus.last_error" class="err">{{ refreshStatus.last_error }}</div>
      <div v-if="refreshError" class="err">{{ refreshError }}</div>
      <div v-if="refreshStatus.failures && refreshStatus.failures.length" class="failure-list">
        <div class="failure-title">失败明细（{{ refreshStatus.failures.length }} 项）</div>
        <div v-for="(item, index) in refreshStatus.failures" :key="index" class="failure-item">
          <span class="failure-loc">{{ item.location }}</span>
          <span class="failure-reason">
            {{ item.reason }}<template v-if="item.count > 1">（{{ item.count }} 条兼职）</template>
          </span>
        </div>
      </div>
    </div>

    <div class="save-row">
      <span v-if="loadError" class="err">{{ loadError }}</span>
      <span v-if="message" class="ok">{{ message }}</span>
      <span v-if="error" class="err">{{ error }}</span>
      <span class="spacer" />
      <button class="save-btn" :disabled="saving" @click="handleSave">
        {{ saving ? '保存中…' : '保存本卡片' }}
      </button>
    </div>

    <ConfirmDialog
      v-model:open="confirmOpen"
      title="修改通勤起始位置"
      message="若修改该地址，则数据库中的所有通勤时间需重新计算，大约需要 3-5 分钟。是否继续？"
      confirm-text="保存并重算"
      @confirm="confirmSave"
      @cancel="confirmOpen = false"
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
.hint {
  font-size: 12px;
  color: #94a3b8;
  margin: 0;
  line-height: 1.6;
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
input,
select {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  color: #0f172a;
  background: #fff;
  outline: none;
}
input:focus,
select:focus {
  border-color: #93c5fd;
  box-shadow: 0 0 0 3px rgba(147, 197, 253, 0.25);
}
.origin-preview {
  font-size: 12px;
  color: #2563eb;
}
.progress-box {
  background: #eff6ff;
  border-radius: 12px;
  padding: 10px 12px;
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
.failure-list {
  background: #fef2f2;
  border-radius: 10px;
  padding: 8px 10px;
  display: flex;
  flex-direction: column;
  gap: 4px;
  max-height: 220px;
  overflow-y: auto;
}
.failure-title {
  font-size: 12px;
  font-weight: 600;
  color: #b91c1c;
}
.failure-item {
  display: flex;
  gap: 8px;
  font-size: 12px;
  line-height: 1.5;
}
.failure-loc {
  color: #7f1d1d;
  font-weight: 500;
  word-break: break-all;
}
.failure-reason {
  color: #b91c1c;
  flex: 1;
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