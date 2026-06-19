<script setup>
import { computed, ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import api, { imgUrl } from '../api.js'
import { useSessionStore } from '../stores/session.js'
import ErrorReportButton from '../components/ErrorReportButton.vue'
import PdfViewer from '../components/PdfViewer.vue'
import HelpButton from '../components/HelpButton.vue'

const FEEDBACK_HELP = '**這頁是什麼？**\n答完一題後的結果頁：顯示你選的答案、正確答案與**詳解**。\n\n**可以做什麼？**\n• ⭐ 加星收進錯題本\n• 📝 寫下自己的心得\n• 調整這題之後的**出現頻率**\n\n用下方「上一題／下一題」在本次練習的題目間切換。'

const router = useRouter()
const session = useSessionStore()

const entry = computed(() => session.currentEntry)
const q = computed(() => entry.value?.question)
const a = computed(() => entry.value?.answer)

const existingNote = ref('')

async function loadExistingNote () {
  if (!q.value) { existingNote.value = ''; return }
  try {
    const r = await api.get(`/notes/${q.value.question_id}`)
    existingNote.value = r.data?.body || ''
  } catch (_) { existingNote.value = '' }
}

onMounted(() => {
  if (!entry.value) {
    router.replace('/')
    return
  }
  loadExistingNote()
})

// When user navigates between chain entries (上一題/下一題 inside Feedback),
// q changes but onMounted doesn't re-run — re-fetch the note.
watch(() => q.value?.question_id, loadExistingNote)

const originalOptions = computed(() => a.value?.original_options || {})

const showOriginal = ref([])
const showNote = ref(false)
const noteBody = ref('')
const noteSaving = ref(false)
const showOverride = ref(false)
const boost = ref(1.0)

function openNote () {
  // Pre-fill with whatever we already loaded so user doesn't lose their note.
  noteBody.value = existingNote.value
  showNote.value = true
}

async function saveNote () {
  noteSaving.value = true
  try {
    await api.put(`/notes/${q.value.question_id}`, { body: noteBody.value })
    existingNote.value = noteBody.value
    showToast('已存')
    showNote.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '存檔失敗')
  } finally {
    noteSaving.value = false
  }
}

async function deleteNote () {
  try {
    await showConfirmDialog({
      title: '刪除心得',
      message: '這題的心得會永久刪除，確定？',
      confirmButtonColor: '#ee0a24'
    })
  } catch (_) { return /* user cancelled */ }
  noteSaving.value = true
  try {
    await api.delete(`/notes/${q.value.question_id}`)
    existingNote.value = ''
    noteBody.value = ''
    showToast('已刪除')
    showNote.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '刪除失敗')
  } finally {
    noteSaving.value = false
  }
}

async function toggleStar () {
  try {
    const newStarred = !q.value.is_starred
    await api.put(`/overrides/${q.value.question_id}`, { is_starred: newStarred })
    session.patchChainQuestion(session.feedbackIndex, { is_starred: newStarred })
    showToast(newStarred ? '已加星' : '已取消星')
  } catch (e) { showToast('失敗') }
}

async function applyBoost () {
  try {
    await api.put(`/overrides/${q.value.question_id}`, { priority_boost: boost.value })
    session.patchChainQuestion(session.feedbackIndex, { priority_boost: boost.value })
    showToast(`頻率倍數設為 ${boost.value}x`)
    showOverride.value = false
  } catch (e) { showToast('失敗') }
}

function goPrev () { session.goPrevFeedback() }
function goNext () {
  if (session.feedbackIndex < session.chain.length - 1) {
    session.goNextFeedback()
    return
  }
  // At chain end. If session limit reached, stay (show 結束 buttons).
  if (session.sessionFinished) return
  // Otherwise navigate to /practice — will resume pending or fetch next
  router.push('/practice')
}

function goHome () { router.push('/') }
function goSummary () { router.push('/session-summary') }
function endPractice () {
  // session.chain is always >= 1 here (Feedback only renders with an entry),
  // but keep the same shape as Practice.vue for consistency.
  if (session.chain.length === 0) {
    session.clearSession()
    router.push('/')
  } else {
    router.push('/session-summary')
  }
}
function newSession () {
  session.clearSession()
  router.push('/setup')
}
</script>

<template>
  <div class="page">
  <van-nav-bar :title="a?.is_correct ? '✓ 答對' : '✗ 答錯'" left-text="上一頁" left-arrow @click-left="$router.back()">
    <template #right>
      <span class="summary-link" @click="endPractice">結束</span>
      <ErrorReportButton v-if="q" :questionId="q.question_id" />
      <HelpButton title="詳解頁說明" :content="FEEDBACK_HELP" :size="18" style="margin-left: 8px;" />
    </template>
  </van-nav-bar>

  <div class="scroll-area">
  <div v-if="q && a" class="fb">
    <div class="result" :class="{ ok: a.is_correct, wrong: !a.is_correct }">
      <div class="big">{{ a.is_correct ? '答對 ✓' : '答錯 ✗' }}</div>
      <div class="meta">
        你選 (<b>{{ a.selected_letter }}</b>) ；正確答案 (<b>{{ a.correct_letter }}</b>)
      </div>
      <div v-if="a.next_due" class="meta">下次出現：{{ new Date(a.next_due).toLocaleString('zh-TW') }}</div>
    </div>

    <van-cell-group inset :title="q.paraphrase_id ? '題目（改寫版）' : '題目'">
      <div v-if="q.paraphrase_id" class="paraphrase-tag">
        ✨ 改寫版本 #{{ q.paraphrase_variant_idx }}（Claude 產生，意義與原題相同）
      </div>
      <div style="padding: 12px 16px; line-height: 1.7; white-space: pre-wrap;">{{ q.stem }}</div>
      <div v-if="q.stem_image_urls?.length" style="padding: 0 16px;">
        <img v-for="(src,i) in q.stem_image_urls" :key="i" :src="imgUrl(src)" style="display:block; max-width:min(100%, 720px); margin:0 auto 8px;" />
      </div>
      <van-cell v-if="q.question_label" title="題號" :value="q.question_label" />
      <van-cell v-if="a.source_info" title="出處" :value="a.source_info" />
    </van-cell-group>

    <van-collapse v-if="q.paraphrase_id" v-model="showOriginal" style="margin: 16px;">
      <van-collapse-item title="📖 對照原題" name="orig">
        <div class="orig-stem">{{ q.original_stem }}</div>
        <div v-if="q.original_options_text" class="orig-opts">
          <div v-for="(text, letter) in q.original_options_text" :key="letter" class="orig-opt">
            <b>({{ letter }})</b> {{ text }}
          </div>
        </div>
      </van-collapse-item>
    </van-collapse>

    <van-cell-group inset title="選項（原始順序）">
      <van-cell
        v-for="(text, letter) in originalOptions" :key="letter"
        :title="`(${letter}) ${text}`"
        :class="{
          'opt-correct': a.correct_letter.includes(letter),
          'opt-selected-wrong': a.selected_letter.includes(letter) && !a.correct_letter.includes(letter)
        }" />
    </van-cell-group>

    <van-cell-group
      v-if="q.explanation_image_urls?.length || a.explanation"
      inset title="詳解"
    >
      <div v-if="q.explanation_image_urls?.length" style="padding: 12px 16px;">
        <PdfViewer :images="q.explanation_image_urls" />
      </div>
      <div v-else class="explanation-text">{{ a.explanation }}</div>
    </van-cell-group>

    <div v-if="existingNote" class="note-card">
      <div class="note-header">
        <span class="note-title">📝 我的心得</span>
        <span class="note-edit" @click="openNote">編輯</span>
      </div>
      <div class="note-body">{{ existingNote }}</div>
    </div>

    <div v-if="a?.dispute_note" class="dispute-note">⚠️ {{ a.dispute_note }}</div>

    <div class="actions">
      <van-button :type="q.is_starred ? 'warning' : 'default'" size="small" @click="toggleStar">
        ★ {{ q.is_starred ? '取消星' : '加星' }}
      </van-button>
      <van-button size="small" @click="openNote">📝 {{ existingNote ? '編輯心得' : '寫心得' }}</van-button>
      <van-button size="small" @click="boost = q.priority_boost; showOverride = true">⚙ 頻率</van-button>
    </div>
  </div>
  </div>

  <div v-if="entry" class="sticky-footer">
    <div style="display:flex; gap:8px; align-items:center;">
      <van-button round size="small" :disabled="!session.canGoPrev" @click="goPrev" style="flex:1;">
        ← 上一題
      </van-button>
      <span class="chain-pos">
        {{ session.feedbackIndex + 1 }} / {{ session.chain.length }}
        <span v-if="session.sessionLimit > 0" class="chain-limit">（共 {{ session.sessionLimit }}）</span>
      </span>
      <template v-if="session.sessionFinished && session.isAtChainEnd">
        <van-button round size="small" plain @click="goHome" style="flex:1;">回首頁</van-button>
        <van-button round size="small" type="primary" @click="goSummary" style="flex:2;">本次成果</van-button>
      </template>
      <template v-else>
        <van-button round type="primary" @click="goNext" style="flex:2;">
          {{ session.isAtChainEnd ? '下一題 →' : '下一題 →' }}
        </van-button>
      </template>
    </div>
  </div>
  </div>

  <div v-if="showNote" class="floating-note">
    <div class="floating-note-header">
      <span>📝 這題的心得 / 筆記</span>
      <span class="floating-note-close" @click="showNote = false">✕</span>
    </div>
    <div class="floating-note-body">
      <van-field
        v-model="noteBody" rows="4" autosize type="textarea"
        placeholder="例：詳解寫得好、概念常錯、容易記錯..."
      />
    </div>
    <div class="floating-note-actions">
      <van-button
        v-if="existingNote"
        type="danger" plain size="small" :loading="noteSaving" @click="deleteNote"
        style="flex: 1;"
      >刪除</van-button>
      <van-button type="primary" size="small" :loading="noteSaving" @click="saveNote" style="flex: 2;">
        儲存
      </van-button>
    </div>
  </div>

  <van-popup v-model:show="showOverride" position="bottom" round :style="{ padding: '16px' }">
    <h3 style="margin: 0 0 12px;">調整出題頻率</h3>
    <p style="color:#888; font-size:13px;">1x = 正常；3x = 比正常頻繁 3 倍；0.5x = 一半</p>
    <van-stepper v-model="boost" :step="0.5" :min="0.5" :max="5" decimal-length="1" />
    <div style="margin-top: 16px;">
      <van-button block type="primary" @click="applyBoost">套用</van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.page { display: flex; flex-direction: column; height: 100vh; height: calc(100dvh - var(--demo-banner-h, 0px)); }
.page :deep(.van-nav-bar) { flex: none; }
.scroll-area { flex: 1 1 auto; min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; }
.fb { padding-bottom: 16px; }
.result { padding: 24px 16px; text-align: center; color: white; }
.result.ok { background: #07c160; }
.result.wrong { background: #ee0a24; }
.result .big { font-size: 22px; font-weight: bold; }
.result .meta { margin-top: 6px; font-size: 13px; opacity: 0.9; }
.pdf-title { padding: 16px 16px 8px; font-size: 14px; color: #555; font-weight: bold; }
.actions { display: flex; gap: 8px; padding: 16px; flex-wrap: wrap; justify-content: center; }
.sticky-footer {
  flex: none;
  background: white;
  padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid #e5e5e5;
  box-shadow: 0 -2px 8px rgba(0,0,0,0.06);
  z-index: 100;
}
.chain-pos { font-size: 12px; color: #888; min-width: 80px; text-align: center; }
.chain-limit { display: block; font-size: 10px; }
.summary-link {
  color: #1989fa;
  font-size: 13px;
  margin-right: 12px;
  cursor: pointer;
}
:deep(.opt-correct) { background: #e8f9e8; }
:deep(.opt-selected-wrong) { background: #fde8e8; }
.paraphrase-tag {
  margin: 8px 16px 0;
  padding: 6px 10px;
  background: #fff7e6;
  border-left: 3px solid #fa8c16;
  border-radius: 4px;
  font-size: 12px;
  color: #874d00;
}
.orig-stem { line-height: 1.7; white-space: pre-wrap; padding: 8px 0; color: #444; }
.orig-opts { font-size: 13px; color: #444; }
.orig-opt { padding: 4px 0; line-height: 1.5; }
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
.floating-note {
  position: fixed;
  left: 0;
  right: 0;
  bottom: calc(64px + env(safe-area-inset-bottom));
  background: white;
  border-top: 1px solid #e5e5e5;
  box-shadow: 0 -4px 16px rgba(0,0,0,0.12);
  z-index: 99;
  max-height: 50vh;
  display: flex;
  flex-direction: column;
}
@media (min-width: 768px) {
  .floating-note {
    left: auto;
    right: 16px;
    bottom: calc(80px + env(safe-area-inset-bottom));
    width: 380px;
    border: 1px solid #e5e5e5;
    border-radius: 8px;
  }
}
.floating-note-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 10px 14px;
  background: #fff8e1;
  border-bottom: 1px solid #f0e0a0;
  font-size: 14px;
  font-weight: bold;
  color: #5f4400;
  flex-shrink: 0;
}
.floating-note-close {
  cursor: pointer;
  font-size: 16px;
  color: #888;
  padding: 0 6px;
}
.floating-note-body {
  padding: 12px 14px;
  flex: 1 1 auto;
  min-height: 0;
  overflow-y: auto;
}
.floating-note-actions {
  display: flex;
  gap: 8px;
  padding: 10px 14px;
  border-top: 1px solid #f0e0a0;
  background: white;
  flex-shrink: 0;
}
.explanation-text {
  padding: 12px 16px;
  line-height: 1.7;
  font-size: 14px;
  color: #333;
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
