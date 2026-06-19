<script setup>
import { ref } from 'vue'
import { showDialog } from 'vant'
import { mk } from '../markup.js'

const props = defineProps({
  title: { type: String, required: true },
  body: { type: String, required: true },     // can include \n
  size: { type: String, default: '14px' }     // icon size override
})

const isOpen = ref(false)

async function open () {
  if (isOpen.value) return
  isOpen.value = true
  try {
    await showDialog({
      title: props.title,
      message: mk(props.body),
      messageAlign: 'left',
      confirmButtonText: '了解',
      allowHtml: true
    })
  } finally {
    isOpen.value = false
  }
}
</script>

<template>
  <span class="info-tooltip" @click.stop="open" :style="{ fontSize: size }">?</span>
</template>

<style scoped>
.info-tooltip {
  display: inline-flex;
  align-items: center; justify-content: center;
  width: 1.3em; height: 1.3em;
  border-radius: 50%;
  background: #e8eef7; color: #1989fa;
  font-weight: bold; line-height: 1; cursor: pointer;
  margin-left: 4px;
  user-select: none;
  border: 1px solid #c8d8ed;
  flex-shrink: 0;
}
.info-tooltip:hover { background: #1989fa; color: #fff; }
</style>
