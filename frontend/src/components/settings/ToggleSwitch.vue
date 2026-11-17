<script setup>
// iOS 风格拨动开关（v-model Boolean）
const model = defineModel({ type: Boolean, default: false })
defineProps({ disabled: { type: Boolean, default: false } })
</script>

<template>
  <button
    type="button"
    class="toggle"
    :class="{ on: model }"
    role="switch"
    :aria-checked="model"
    :disabled="disabled"
    @click="model = !model"
  >
    <span class="knob" />
  </button>
</template>

<style scoped>
.toggle {
  width: 46px;
  height: 26px;
  padding: 0;
  border: none;
  border-radius: 13px;
  background: #cbd5e1;
  position: relative;
  cursor: pointer;
  transition: background 0.2s;
  flex-shrink: 0;
}
.toggle.on {
  background: #3b82f6;
}
.toggle:disabled {
  opacity: 0.55;
  cursor: default;
}
.knob {
  position: absolute;
  top: 2px;
  left: 2px;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.25);
  transition: transform 0.2s;
}
.toggle.on .knob {
  transform: translateX(20px);
}
</style>
