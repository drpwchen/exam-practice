<script setup>
import { showDialog } from 'vant'
import { useHelpMode } from '../composables/useHelpMode.js'
import { mk } from '../markup.js'

// Per-page help icon — the single "?" in each page title.
// Click → (1) show this page's overview dialog, (2) turn on 說明模式 so each
// field's inline "?" (InfoTooltip) appears. content may contain \n.
const props = defineProps({
  title: { type: String, default: '說明' },
  content: { type: String, required: true },
  size: { type: [String, Number], default: 18 },
  color: { type: String, default: '#888' }
})

const { helpMode, enableHelpMode } = useHelpMode()

function open () {
  enableHelpMode()
  showDialog({
    title: props.title,
    message: mk(props.content),
    messageAlign: 'left',
    confirmButtonText: '了解',
    allowHtml: true
  })
}
</script>

<template>
  <van-icon
    name="question-o"
    :size="size"
    :color="helpMode ? '#1989fa' : color"
    class="help-btn"
    @click="open"
  />
</template>

<style scoped>
.help-btn { cursor: pointer; }
</style>
