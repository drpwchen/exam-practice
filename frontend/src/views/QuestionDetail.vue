<script setup>
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import api, { imgUrl } from '../api.js'
import { useKeyboardSafePopup } from '../composables/useKeyboardSafePopup.js'

const route = useRoute()
const { popupStyle: notePopupStyle } = useKeyboardSafePopup()
const q = ref(null)
const loading = ref(true)

async function load () {
  loading.value = true
  try {
    const r = await api.get(`/questions/${route.params.id}`)
    q.value = r.data
  } catch (e) {
    showToast('載入失敗')
  } finally {
    loading.value = false
  }
}

async function toggleStar () {
  await api.put(`/overrides/${q.value.id}`, { is_starred: !q.value.is_starred })
  q.value.is_starred = !q.value.is_starred
}

const noteShow = ref(false)
const noteBody = ref('')
const noteSaving = ref(false)
function openNote () {
  noteBody.value = q.value?.user_note || ''
  noteShow.value = true
}
async function saveNote () {
  noteSaving.value = true
  try {
    await api.put(`/notes/${q.value.id}`, { body: noteBody.value })
    q.value.user_note = noteBody.value
    showToast('已存')
    noteShow.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '存檔失敗')
  } finally { noteSaving.value = false }
}
async function deleteNote () {
  try {
    await showConfirmDialog({
      title: '刪除心得',
      message: '這題的心得會永久刪除，確定？',
      confirmButtonColor: '#ee0a24'
    })
  } catch (_) { return }
  noteSaving.value = true
  try {
    await api.delete(`/notes/${q.value.id}`)
    q.value.user_note = null
    noteBody.value = ''
    showToast('已刪除')
    noteShow.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '刪除失敗')
  } finally { noteSaving.value = false }
}

onMounted(load)
</script>

<template>
  <van-nav-bar :title="`題目 #${route.params.id}`" left-text="上一頁" left-arrow @click-left="$router.back()" />
  <van-loading v-if="loading" style="margin: 32px auto; display:block;" />
  <div v-else-if="q">
    <van-cell-group inset title="標籤">
      <div style="padding: 12px 16px;">
        <van-tag v-for="t in q.tags" :key="t" type="primary" round style="margin-right: 6px;">{{ t }}</van-tag>
        <van-tag v-if="q.question_label" type="success" round style="margin-right:6px;">{{ q.question_label }}</van-tag>
        <van-tag v-if="q.is_starred" type="warning" round>★ 已標記</van-tag>
      </div>
      <van-cell v-if="q.source_info" title="出處" :value="q.source_info" />
    </van-cell-group>

    <van-cell-group inset title="題目">
      <div style="padding: 12px 16px; line-height: 1.7; white-space: pre-wrap;">{{ q.stem }}</div>
      <div v-if="q.stem_image_urls?.length" style="padding: 0 16px;">
        <img v-for="(src, i) in q.stem_image_urls" :key="i" :src="imgUrl(src)" style="display:block; max-width: min(100%, 720px); margin: 0 auto 8px;" />
      </div>
    </van-cell-group>

    <van-cell-group inset title="選項 (正解標綠)">
      <van-cell
        v-for="(text, letter) in q.options" :key="letter"
        :title="`(${letter}) ${text}`"
        :class="{ 'opt-correct': (q.correct_letter || '').includes(letter) }"
      />
    </van-cell-group>

    <div v-if="q.user_note" class="note-card">
      <div class="note-header">
        <span class="note-title">📝 我的心得</span>
        <span class="note-edit" @click="openNote">編輯</span>
      </div>
      <div class="note-body">{{ q.user_note }}</div>
    </div>

    <div v-if="q?.dispute_note" class="dispute-note">⚠️ {{ q.dispute_note }}</div>

    <van-cell-group inset title="你的紀錄" style="margin-top: 16px;">
      <van-cell title="答過次數" :value="q.user_answer_count" />
      <van-cell v-if="q.last_selected_letter" title="上次答" :value="`(${q.last_selected_letter}) ${q.last_was_correct ? '✓' : '✗'}`" />
    </van-cell-group>

    <div style="padding: 16px; display: flex; gap: 8px; flex-wrap: wrap;">
      <van-button @click="toggleStar" style="flex:1;">{{ q.is_starred ? '取消星' : '★ 加星' }}</van-button>
      <van-button @click="openNote" style="flex:1;">📝 {{ q.user_note ? '編輯心得' : '寫心得' }}</van-button>
    </div>
  </div>

  <van-popup v-model:show="noteShow" position="bottom" round :style="notePopupStyle">
    <h3 style="margin: 0 0 12px;">這題的心得 / 筆記</h3>
    <van-field
      v-model="noteBody" rows="6" autosize type="textarea"
      placeholder="例：詳解寫得好、概念常錯、容易記錯..."
    />
    <div style="margin-top: 16px; display: flex; gap: 8px;">
      <van-button
        v-if="q?.user_note"
        type="danger" plain :loading="noteSaving" @click="deleteNote"
        style="flex: 1;"
      >刪除</van-button>
      <van-button type="primary" :loading="noteSaving" @click="saveNote" style="flex: 2;">
        儲存
      </van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.pdf-title { padding: 16px 16px 8px; font-size: 14px; color: #555; font-weight: bold; }
:deep(.opt-correct) { background: #e8f9e8; }
.note-card {
  margin: 12px 16px;
  padding: 12px 14px;
  background: #fff8e1;
  border-left: 4px solid #ffd21e;
  border-radius: 6px;
}
.note-header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 6px;
}
.note-title { font-size: 13px; font-weight: bold; color: #5f4400; }
.note-edit {
  font-size: 12px; color: #1989fa; cursor: pointer;
  padding: 2px 8px; border: 1px solid #1989fa; border-radius: 4px;
}
.note-body {
  font-size: 14px; line-height: 1.6; color: #5f4400;
  white-space: pre-wrap;
}
.dispute-note {
  margin: 12px 16px 4px;
  padding: 10px 14px;
  background: #fff3cd;
  border-left: 4px solid #e6a817;
  border-radius: 6px;
  font-size: 13px;
  color: #7d4e00;
  line-height: 1.5;
}
</style>
