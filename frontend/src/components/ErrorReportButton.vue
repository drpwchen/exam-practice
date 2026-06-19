<script setup>
import { computed, ref } from 'vue'
import { showToast } from 'vant'
import api from '../api.js'

const props = defineProps({
  questionId: { type: Number, default: null }
})

const show = ref(false)
const body = ref('')
const category = ref('general')
const sending = ref(false)
const pathname = computed(() => (typeof window !== 'undefined' ? window.location.pathname : ''))

const CATEGORIES = [
  { v: 'general', t: '一般' },
  { v: 'parser', t: '題目/詳解內容錯誤' },
  { v: 'tag', t: '領域標籤錯誤' },
  { v: 'answer', t: '答案錯誤' },
  { v: 'ui', t: '介面 bug' },
  { v: 'other', t: '其他' }
]

async function submit () {
  if (body.value.trim().length < 2) { showToast('請輸入至少 2 字'); return }
  sending.value = true
  try {
    await api.post('/error-reports', {
      body: body.value,
      category: category.value,
      question_id: props.questionId,
      page_url: window.location.href
    })
    showToast('已回報，謝謝')
    body.value = ''
    show.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '送出失敗')
  } finally {
    sending.value = false
  }
}
</script>

<template>
  <span class="report-btn" @click="show = true" title="回報問題">⚠</span>
  <van-popup v-model:show="show" position="bottom" round :z-index="2100" :style="{ padding: '16px 16px 100px' }">
    <h3 style="margin: 0 0 12px;">回報問題</h3>
    <div style="font-size: 12px; color:#888; margin-bottom:8px;">
      {{ questionId ? `題目 #${questionId}` : '一般回報' }} | {{ pathname }}
    </div>
    <van-field label="分類">
      <template #input>
        <select v-model="category" style="width:100%; border:0; background:transparent; font-size:14px;">
          <option v-for="c in CATEGORIES" :key="c.v" :value="c.v">{{ c.t }}</option>
        </select>
      </template>
    </van-field>
    <van-field
      v-model="body"
      type="textarea" rows="4" autosize
      label="說明"
      placeholder="敘述遇到的問題、預期 vs 實際"
    />
    <div style="margin-top: 16px;">
      <van-button block type="primary" :loading="sending" @click="submit">送出</van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.report-btn {
  cursor: pointer; color: #ff976a; font-size: 16px;
  padding: 4px 8px; user-select: none;
}
.report-btn:hover { opacity: 0.7; }
</style>
