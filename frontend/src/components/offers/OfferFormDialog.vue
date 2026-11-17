<script setup>
// 手动录入/编辑兼职表单（新增写入 source=manual_form；编辑仅改白名单字段）
import { reactive, ref } from 'vue'
import { createManualOffer, updateOffer } from '../../api/http'

const props = defineProps({
  offer: { type: Object, default: null }, // null=新增
})
const emit = defineEmits(['close', 'saved'])

const saving = ref(false)
const error = ref('')

const form = reactive({
  work_summary: props.offer?.work_summary || '',
  work_date: props.offer?.work_date || new Date().toISOString().slice(0, 10),
  work_time: props.offer?.work_time || '',
  commission_min: props.offer?.commission_min ?? '',
  commission_max: props.offer?.commission_max ?? '',
  commission_unit: props.offer?.commission_unit || '元/天',
  location: props.offer?.location || '',
  contact: props.offer?.contact || '',
  notes: props.offer?.notes || '',
  is_todo: false,
})

function payload() {
  const data = {
    work_summary: form.work_summary.trim(),
    work_date: form.work_date,
    work_time: form.work_time.trim() || null,
    location: form.location.trim() || null,
    contact: form.contact.trim() || null,
    notes: form.notes.trim() || null,
  }
  if (form.commission_min !== '' && form.commission_min != null) data.commission_min = Number(form.commission_min)
  if (form.commission_max !== '' && form.commission_max != null) data.commission_max = Number(form.commission_max)
  if (data.commission_min != null || data.commission_max != null) data.commission_unit = form.commission_unit
  return data
}

async function submit() {
  if (!form.work_summary.trim()) {
    error.value = '工作内容摘要不能为空'
    return
  }
  saving.value = true
  error.value = ''
  try {
    let saved
    if (props.offer) {
      saved = await updateOffer(props.offer.id, payload())
    } else {
      saved = await createManualOffer({ ...payload(), is_todo: form.is_todo })
    }
    emit('saved', saved)
  } catch (e) {
    error.value = e.message
  } finally {
    saving.value = false
  }
}
</script>

<template>
  <div class="overlay" @click.self="emit('close')">
    <div class="dialog">
      <div class="dialog-title">{{ offer ? '编辑兼职' : '手动新增兼职' }}</div>
      <p v-if="offer" class="dialog-sub">仅修改本地记录，不回写原始群消息。</p>

      <div class="grid">
        <label class="full">
          <span>工作内容 *</span>
          <input v-model="form.work_summary" placeholder="如 酒店宴会传菜" />
        </label>
        <label>
          <span>工作日期 *</span>
          <input v-model="form.work_date" type="date" />
        </label>
        <label>
          <span>工作时段</span>
          <input v-model="form.work_time" placeholder="如 7:30-21:00" />
        </label>
        <label>
          <span>佣金下限（元）</span>
          <input v-model="form.commission_min" type="number" min="0" />
        </label>
        <label>
          <span>佣金上限（元）</span>
          <input v-model="form.commission_max" type="number" min="0" />
        </label>
        <label>
          <span>佣金单位</span>
          <select v-model="form.commission_unit">
            <option value="元/时">元/时</option>
            <option value="元/天">元/天</option>
            <option value="元/次">元/次</option>
            <option value="元/月">元/月</option>
          </select>
        </label>
        <label>
          <span>工作地点</span>
          <input v-model="form.location" placeholder="如 静安寺" />
        </label>
        <label>
          <span>联系方式</span>
          <input v-model="form.contact" placeholder="如 微信abc" />
        </label>
        <label class="full">
          <span>备注</span>
          <input v-model="form.notes" placeholder="其他要求（可选）" />
        </label>
        <label v-if="!offer" class="checkbox-row full">
          <input v-model="form.is_todo" type="checkbox" />
          <span>同时设为 ToDo 待办offer</span>
        </label>
      </div>

      <div v-if="error" class="err">{{ error }}</div>

      <div class="actions">
        <button class="btn-cancel" @click="emit('close')">取消</button>
        <button class="btn-primary" :disabled="saving" @click="submit">
          {{ saving ? '保存中…' : '保存' }}
        </button>
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
  padding: 20px;
}
.dialog {
  width: 100%;
  max-width: 520px;
  max-height: 88vh;
  overflow-y: auto;
  background: #fff;
  border-radius: 18px;
  padding: 20px 22px;
  box-shadow: 0 12px 40px rgba(15, 23, 42, 0.25);
}
.dialog-title {
  font-size: 16px;
  font-weight: 700;
  color: #0f172a;
}
.dialog-sub {
  margin: 4px 0 0;
  font-size: 12px;
  color: #94a3b8;
}
.grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 10px 12px;
  margin-top: 14px;
}
.full {
  grid-column: 1 / -1;
}
label {
  display: flex;
  flex-direction: column;
  gap: 4px;
  font-size: 12px;
  color: #64748b;
}
input,
select {
  border: 1px solid #e2e8f0;
  border-radius: 10px;
  padding: 8px 10px;
  font-size: 13px;
  outline: none;
  color: #0f172a;
  font-family: inherit;
  background: #fff;
}
input:focus,
select:focus {
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
.err {
  margin-top: 10px;
  font-size: 12px;
  color: #b91c1c;
}
.actions {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  margin-top: 16px;
}
.btn-cancel,
.btn-primary {
  border: none;
  border-radius: 10px;
  padding: 9px 18px;
  font-size: 13px;
  cursor: pointer;
}
.btn-cancel {
  background: #f1f5f9;
  color: #475569;
}
.btn-primary {
  background: #3b82f6;
  color: #fff;
}
.btn-primary:disabled {
  opacity: 0.5;
  cursor: default;
}
</style>