<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api, { imgUrl } from '../api.js'
import { useSessionStore } from '../stores/session.js'
import ErrorReportButton from '../components/ErrorReportButton.vue'
import HelpButton from '../components/HelpButton.vue'

const PRACTICE_HELP = '**怎麼作答？**\n讀題 → 選答案 → 選把握度（猜的／一般／有把握）→ 送出，接著看詳解。\n\n**右上計時器的顏色？**\n🟢 綠＝正常　🟠 橘＝接近平均　🔴 紅＝超時。只是參考，不會強制跳題。\n\n**遇到「複選」徽章？**\n紅色「複選」題，選項會變 checkbox，**全部選對才算對**。'

// Pace target for the timer colour (seconds/question), from the server config
// (settings.exam_seconds_per_q). Default 48 = a 200-question / 160-minute paper.
const avgSecondsPerQ = ref(48)
const warnSeconds = computed(() => avgSecondsPerQ.value * 0.75)
const overSeconds = computed(() => avgSecondsPerQ.value)
async function loadPace () {
  try {
    const r = await api.get('/meta/db-info')
    if (r.data?.exam_seconds_per_q) avgSecondsPerQ.value = r.data.exam_seconds_per_q
  } catch (_) { /* keep default */ }
}

const router = useRouter()
const session = useSessionStore()

if (!session.sessionParams) {
  router.replace('/setup')
}

const loading = ref(false)
const submitting = ref(false)
const emptyPool = ref(false)
const startedAt = ref(0)
const selected = ref('')          // single-select: 'A' / multi-select unused
const selectedMulti = ref([])      // multi-select: ['A','C','E']
const confidence = ref(session.sessionParams?.default_confidence || 3)
const elapsedSec = ref(0)
let timerHandle = null

const q = computed(() => session.pending?.question || null)

const navTitle = computed(() => {
  // Compact: "#N tag" — bare # number + first tag, no full 「題目」 prefix.
  // Keeps room for the right-side ⚠ + 結束 + timer without overlap.
  if (q.value) {
    const tag = q.value.tags?.[0] || ''
    const num = `#${session.answeredCount + 1}`
    return tag ? `${num} ${tag}` : num
  }
  return '練習'
})

function startTimer () {
  stopTimer()
  elapsedSec.value = 0
  timerHandle = setInterval(() => {
    elapsedSec.value = Math.floor((Date.now() - startedAt.value) / 1000)
  }, 250)
}
function stopTimer () { if (timerHandle) { clearInterval(timerHandle); timerHandle = null } }

const timerColor = computed(() => {
  if (elapsedSec.value >= overSeconds.value) return '#ee0a24'
  if (elapsedSec.value >= warnSeconds.value) return '#ff976a'
  return '#07c160'
})
const timerLabel = computed(() => {
  const m = Math.floor(elapsedSec.value / 60)
  const s = elapsedSec.value % 60
  return `${m}:${s.toString().padStart(2, '0')}`
})

async function loadNext () {
  if (session.sessionFinished) {
    // Session done, jump straight to the summary page.
    router.replace('/session-summary')
    return
  }
  if (session.pending) {
    // Resuming — pending preserved across navigation
    startedAt.value = Date.now()
    startTimer()
    selected.value = ''
    selectedMulti.value = []
    confidence.value = session.sessionParams?.default_confidence || 3
    return
  }
  loading.value = true
  emptyPool.value = false
  selected.value = ''
  confidence.value = session.sessionParams?.default_confidence || 3
  try {
    const params = { source_code: session.sessionParams.source }
    if (session.sessionParams.tag_ids?.length) params.tag_ids = session.sessionParams.tag_ids.join(',')
    if (session.sessionParams.mode) params.mode = session.sessionParams.mode
    if (session.sessionParams.mode === 'cohort_wrong') {
      params.cohort_level = session.sessionParams.cohort_level || 'common'
      if (session.sessionParams.cohort_exclude_recent_days) {
        params.cohort_exclude_recent_days = session.sessionParams.cohort_exclude_recent_days
      }
    }
    if (session.sessionParams.mode === 'wrong') {
      if (session.sessionParams.wrong_kinds?.length) {
        params.wrong_kinds = session.sessionParams.wrong_kinds.join(',')
      }
      if (session.sessionParams.wrong_exclude_recent_days) {
        params.wrong_exclude_recent_days = session.sessionParams.wrong_exclude_recent_days
      }
    }
    if (session.sessionParams.mode === 'starred' && session.sessionParams.starred_exclude_recent_days) {
      params.starred_exclude_recent_days = session.sessionParams.starred_exclude_recent_days
    }
    if (session.sessionParams.exclude_mastered) params.exclude_mastered = 'true'
    if (!session.sessionParams.shuffle) params.shuffle = 'false'
    if (session.sessionParams.use_paraphrase) params.use_paraphrase = 'true'
    if (session.sessionParams.years?.length) params.years = session.sessionParams.years.join(',')
    // In-session dedup: skip already-answered qids EXCEPT relearn-ready ones,
    // which we surface via priority_qids so they jump to the front of the queue.
    if (session.excludeQids.length) params.exclude_qids = session.excludeQids.join(',')
    if (session.priorityQids.length) params.priority_qids = session.priorityQids.join(',')
    const r = await api.get('/practice/next', { params })
    session.setPending(r.data)
    startedAt.value = Date.now()
    startTimer()
  } catch (e) {
    if (e.response?.status === 404) {
      emptyPool.value = true
    } else {
      showToast(e.response?.data?.detail || '載入失敗')
    }
  } finally {
    loading.value = false
  }
}

const emptyPoolMsg = computed(() => {
  const mode = session.sessionParams?.mode || 'smart'
  if (mode === 'new') return '沒有新題可練了 🎉 全題庫都至少答對過一次'
  if (mode === 'review') return '目前沒有到期需複習的題目'
  if (mode === 'wrong') return '這個失敗模式／排除條件下沒有常錯題，試試「全部」或放寬排除天數'
  if (mode === 'starred') return '你還沒有加星的題目，先去題目按 ★ 收藏吧'
  if (mode === 'cohort_wrong') return '這個難度下沒有符合的大家錯題，試試放寬難度'
  return '這個篩選條件下沒有題目'
})

async function submit () {
  const isMulti = !!q.value?.is_multi
  const sel = isMulti
    ? [...selectedMulti.value].sort().join('')
    : selected.value
  if (!sel) { showToast(isMulti ? '請至少選一個答案' : '請選擇答案'); return }
  if (!confidence.value) { showToast('請評估把握度'); return }
  submitting.value = true
  stopTimer()
  const time_ms = Date.now() - startedAt.value
  try {
    const r = await api.post('/practice/answer', {
      answer_token: q.value.answer_token,
      selected_display: sel,
      confidence: confidence.value,
      time_ms
    })
    // Stash the user's confidence onto the chain entry so SessionSummary
    // can flag low-confidence (= guessed) answers as review candidates.
    session.recordAnswer({ ...r.data, confidence: confidence.value }, time_ms)
    // Always show feedback first — even on the last question, user should see
    // 詳解 before reaching SessionSummary. Feedback.vue's sticky footer detects
    // sessionFinished + isAtChainEnd and surfaces the 「本次成果」 button.
    router.push('/feedback')
  } catch (e) {
    const detail = e.response?.data?.detail || ''
    if (e.response?.status === 400 && /answer token/i.test(detail)) {
      // Resumed after >30 min — server invalidated the answer_token. Drop
      // pending and re-fetch so the user can answer afresh.
      showToast('題目已過期，重新載入中…')
      session.clearPending()
      selected.value = ''
      await loadNext()
    } else {
      showToast(detail || '送出失敗')
    }
  } finally {
    submitting.value = false
  }
}

function toggleMulti (letter) {
  const i = selectedMulti.value.indexOf(letter)
  if (i >= 0) selectedMulti.value.splice(i, 1)
  else selectedMulti.value.push(letter)
}

function goPrev () {
  if (session.chain.length === 0) return
  session.showLastFeedback()
  router.push('/feedback')
}

function endSession () { router.push('/') }
function endPractice () {
  // No answers yet → just bail home. Otherwise show results.
  if (session.chain.length === 0) {
    session.clearSession()
    router.push('/')
  } else {
    router.push('/session-summary')
  }
}

onMounted(() => { loadPace(); loadNext() })
onBeforeUnmount(stopTimer)
</script>

<template>
  <div class="page">
  <van-nav-bar :title="navTitle" left-text="返回" left-arrow @click-left="endSession">
    <template #right>
      <span class="summary-link" @click="endPractice">結束</span>
      <ErrorReportButton v-if="q" :questionId="q.question_id" />
      <HelpButton title="作答頁說明" :content="PRACTICE_HELP" :size="18" style="margin: 0 8px;" />
      <span v-if="q" class="timer" :style="{ color: timerColor }">{{ timerLabel }}</span>
    </template>
  </van-nav-bar>

  <div class="scroll-area">
  <van-loading v-if="loading" style="margin: 32px auto; display:block;" />

  <div v-else-if="emptyPool" class="empty-state">
    <div class="empty-icon">🎯</div>
    <div class="empty-msg">{{ emptyPoolMsg }}</div>
    <div class="empty-actions">
      <van-button round type="primary" @click="$router.push('/')">回首頁</van-button>
      <van-button round plain type="primary" @click="$router.push('/setup')">換條件</van-button>
    </div>
  </div>

  <div v-else-if="q" class="practice">
    <div v-if="q.tags.length > 1 || q.is_starred" class="tags">
      <van-tag v-for="t in q.tags.slice(1)" :key="t" type="primary" round style="margin-right:6px;">{{ t }}</van-tag>
      <van-tag v-if="q.is_starred" type="warning" round>★ 已標記</van-tag>
    </div>
    <div v-if="q.source_code === 'past_paper'" class="ai-tag-hint">
      <em>分類為 AI 自動產生，可能有誤</em>
    </div>

    <div class="stem">
      <van-tag v-if="q.is_multi" type="danger" round style="margin-right:8px;">複選</van-tag>
      {{ q.stem }}
    </div>

    <div v-if="q.stem_image_urls?.length" class="images">
      <img v-for="(src, i) in q.stem_image_urls" :key="i" :src="imgUrl(src)" />
    </div>

    <van-checkbox-group v-if="q.is_multi" v-model="selectedMulti" class="opts">
      <van-cell
        v-for="o in q.options" :key="o.display"
        :title="`(${o.display}) ${o.text}`"
        clickable
        @click="toggleMulti(o.display)"
      >
        <template #right-icon>
          <van-checkbox :name="o.display" @click.stop />
        </template>
      </van-cell>
    </van-checkbox-group>
    <van-radio-group v-else v-model="selected" class="opts">
      <van-cell
        v-for="o in q.options" :key="o.display"
        :title="`(${o.display}) ${o.text}`"
        clickable
        @click="selected = o.display"
      >
        <template #right-icon>
          <van-radio :name="o.display" />
        </template>
      </van-cell>
    </van-radio-group>
  </div>
  </div>

  <div v-if="q && !loading" class="sticky-footer">
    <div class="confidence-row">
      <span class="ctrl-label">把握度</span>
      <van-button-group>
        <van-button size="small" :type="confidence === 1 ? 'danger' : 'default'" @click="confidence = 1">猜的</van-button>
        <van-button size="small" :type="confidence === 2 ? 'warning' : 'default'" @click="confidence = 2">普通</van-button>
        <van-button size="small" :type="confidence === 3 ? 'success' : 'default'" @click="confidence = 3">很確定</van-button>
      </van-button-group>
    </div>
    <div class="footer-row">
      <van-button round size="small" :disabled="session.chain.length === 0" @click="goPrev" style="flex: 1;">
        ← 上一題
      </van-button>
      <van-button round type="primary" :loading="submitting" @click="submit" style="flex: 3;">
        送出{{ session.sessionLimit > 0 ? ` (${session.answeredCount + 1}/${session.sessionLimit})` : '' }}
      </van-button>
    </div>
  </div>
  </div>
</template>

<style scoped>
.page { display: flex; flex-direction: column; height: 100vh; height: calc(100dvh - var(--demo-banner-h, 0px)); }
.page :deep(.van-nav-bar) { flex: none; }
.scroll-area { flex: 1 1 auto; min-height: 0; overflow-y: auto; -webkit-overflow-scrolling: touch; }
.practice { padding-bottom: 16px; }
.empty-state { padding: 64px 24px; text-align: center; color: #555; }
.empty-icon { font-size: 56px; margin-bottom: 12px; }
.empty-msg { font-size: 16px; margin-bottom: 24px; }
.empty-actions { display: flex; gap: 12px; justify-content: center; }
.tags { padding: 12px 16px 0; }
.ai-tag-hint { padding: 4px 16px 0; font-size: 12px; color: #999; }
.stem { padding: 16px; line-height: 1.7; font-size: 16px; white-space: pre-wrap; }
.images { padding: 0 16px; }
.images img {
  display: block;
  max-width: min(100%, 720px);
  margin: 0 auto 12px;
  border-radius: 4px;
}
.opts { margin: 8px 0; }
.sticky-footer {
  flex: none;
  background: white;
  padding: 12px 16px calc(12px + env(safe-area-inset-bottom));
  border-top: 1px solid #e5e5e5;
  box-shadow: 0 -2px 8px rgba(0,0,0,0.06);
  z-index: 100;
}
.confidence-row {
  display: flex; align-items: center; justify-content: center; gap: 12px;
}
.footer-row {
  display: flex; gap: 8px; margin-top: 8px;
}
.ctrl-label { font-size: 13px; color: #888; }
.timer { font-family: monospace; font-size: 18px; font-weight: bold; transition: color 0.3s; }
.summary-link {
  color: #1989fa;
  font-size: 13px;
  margin-right: 12px;
  cursor: pointer;
}
</style>
