<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api, { imgUrl } from '../api.js'
import { useAuthStore } from '../stores/auth.js'

const router = useRouter()
const auth = useAuthStore()

// ─────── Tab state ───────
const tab = ref('reports')   // 'reports' | 'review'

// ─────── Error reports (from users) ───────
const reports = ref([])
const reportsLoading = ref(true)
const onlyOpen = ref(true)

const CATEGORY_LABEL = {
  general: '一般',
  parser: '題目/詳解錯誤',
  tag: '領域標籤錯誤',
  answer: '答案錯誤',
  ui: '介面 bug',
  audit: '稽核',
  other: '其他',
  qqe: 'QQE 內部'
}
const CATEGORY_COLOR = {
  general: '#888',
  parser: '#ee0a24',
  tag: '#1989fa',
  answer: '#ee0a24',
  ui: '#ff976a',
  audit: '#9b59b6',
  other: '#888',
  qqe: '#999'
}

async function loadReports () {
  reportsLoading.value = true
  try {
    const params = onlyOpen.value ? '?only_open=true' : ''
    const r = await api.get(`/error-reports${params}`)
    reports.value = r.data
  } catch (e) {
    showToast(e.response?.data?.detail || '載入失敗')
  } finally {
    reportsLoading.value = false
  }
}

async function resolveReport (report) {
  try {
    await api.patch(`/error-reports/${report.id}/resolve`)
    showToast('已標記處理')
    await loadReports()
  } catch (e) {
    showToast(e.response?.data?.detail || '失敗')
  }
}

function viewQuestion (qid) {
  if (qid) router.push(`/question/${qid}`)
}

// Open a question on the 待審 tab via jumpToQ (used by report's "進入待審" button).
async function openOnReview (qid) {
  tab.value = 'review'
  jumpInput.value = String(qid)
  await jumpToQ()
}

function fmtTime (iso) {
  return new Date(iso).toLocaleString('zh-TW', { hour12: false })
}

const summary = computed(() => {
  const open = reports.value.filter(r => !r.is_resolved).length
  return { open, total: reports.value.length }
})

// ─────── Q&Q comms (admin → QQE agent via error_reports.category=qqe) ───────
const qqeBody = ref('')
const qqeSending = ref(false)
const qqeRecent = ref([])    // recent qqe-category reports for current question

async function loadQqeForCurrent () {
  qqeRecent.value = []
  if (!current.value) return
  try {
    const r = await api.get('/error-reports?only_open=false')
    qqeRecent.value = r.data.filter(x => x.question_id === current.value.id && x.category === 'qqe')
  } catch (_) { /* silent */ }
}

async function sendQqe () {
  if (!current.value) return
  const body = qqeBody.value.trim()
  if (body.length < 2) { showToast('內容太短'); return }
  qqeSending.value = true
  try {
    await api.post('/error-reports', {
      body, category: 'qqe', question_id: current.value.id
    })
    qqeBody.value = ''
    showToast('已送出給 QQE')
    await loadQqeForCurrent()
  } catch (e) {
    showToast(e.response?.data?.detail || '送出失敗')
  } finally { qqeSending.value = false }
}

// ─────── Review queue ───────
const queue = ref([])
const queueLoading = ref(false)
const cursor = ref(0)
const detail = ref(null)
const detailLoading = ref(false)
// 2026-04-28: default OFF — surface programmatic flags (e.g. QQE C2+B audit
// findings) by default, otherwise admin only sees manually-flagged rows.
const onlyFlagged = ref(false)
const adHocId = ref(null)
const previewMode = ref('detail') // 'detail' | 'practice'
const previewSelected = ref('')

async function loadQueue () {
  queueLoading.value = true
  try {
    const r = await api.get(`/admin/review-queue?needs_review_only=${onlyFlagged.value}`)
    queue.value = r.data
    cursor.value = 0
    adHocId.value = null
    await loadDetail()
  } catch (_) { showToast('載入待審失敗') }
  finally { queueLoading.value = false }
}

const current = computed(() => {
  if (adHocId.value != null) return { id: adHocId.value, source_code: '', source_ref: '' }
  return queue.value[cursor.value] || null
})

async function loadDetail () {
  if (!current.value) { detail.value = null; return }
  detailLoading.value = true
  previewSelected.value = ''
  try {
    const r = await api.get(`/questions/${current.value.id}`)
    detail.value = r.data
  } catch (_) { showToast('載入詳情失敗') }
  finally { detailLoading.value = false }
  await loadQqeForCurrent()
}

function nextQ () {
  if (adHocId.value != null) { adHocId.value = null; loadDetail(); return }
  if (cursor.value < queue.value.length - 1) {
    cursor.value++
    loadDetail()
  } else { showToast('已到最後一題') }
}
function prevQ () {
  if (adHocId.value != null) { adHocId.value = null; loadDetail(); return }
  if (cursor.value > 0) { cursor.value--; loadDetail() }
}

const jumpInput = ref('')
async function jumpToQ () {
  const id = parseInt(jumpInput.value)
  if (!id || id < 1) { showToast('請輸入 Q# 數字'); return }
  const idx = queue.value.findIndex(q => q.id === id)
  if (idx >= 0) {
    adHocId.value = null
    cursor.value = idx
  } else {
    adHocId.value = id
  }
  jumpInput.value = ''
  await loadDetail()
}

async function markReviewed () {
  if (!current.value) return
  try {
    await api.patch(`/admin/questions/${current.value.id}`, { needs_review: false, ocr_status: 'reviewed' })
    showToast('已標記為已審')
    queue.value.splice(cursor.value, 1)
    if (cursor.value >= queue.value.length) cursor.value = Math.max(0, queue.value.length - 1)
    await loadDetail()
  } catch (e) { showToast(e.response?.data?.detail || '失敗') }
}

const showEdit = ref(false)
const editForm = ref({})

function openEdit () {
  if (!detail.value) return
  editForm.value = {
    stem: detail.value.stem,
    option_a: detail.value.options.A || '',
    option_b: detail.value.options.B || '',
    option_c: detail.value.options.C || '',
    option_d: detail.value.options.D || '',
    option_e: detail.value.options.E || '',
    correct_letter: detail.value.correct_letter,
    explanation: detail.value.explanation || '',
    reference_page: detail.value.reference_page || ''
  }
  showEdit.value = true
}
async function saveEdit () {
  try {
    await api.patch(`/admin/questions/${current.value.id}`, editForm.value)
    showToast('已儲存')
    showEdit.value = false
    await loadDetail()
  } catch (e) { showToast(e.response?.data?.detail || '存檔失敗') }
}

if (!auth.user?.is_admin) {
  showToast('需要管理員權限')
}

onMounted(async () => {
  await loadReports()
  await loadQueue()
})
</script>

<template>
  <van-nav-bar title="管理員" left-text="首頁" left-arrow @click-left="$router.push('/')">
    <template #right>
      <van-icon name="replay" size="18" @click="tab === 'reports' ? loadReports() : loadQueue()" />
    </template>
  </van-nav-bar>

  <van-tabs v-model:active="tab" sticky>
    <van-tab name="reports" :title="`回報 (${summary.open})`" />
    <van-tab name="review" :title="`待審 (${queue.length})`" />
  </van-tabs>

  <!-- ─────── 回報 tab (error reports) ─────── -->
  <div v-if="tab === 'reports'">
    <div class="filters">
      <van-tag size="medium" :type="onlyOpen ? 'primary' : 'default'" @click="onlyOpen = true; loadReports()">
        未處理 ({{ summary.open }})
      </van-tag>
      <van-tag size="medium" :type="!onlyOpen ? 'primary' : 'default'" @click="onlyOpen = false; loadReports()">
        全部 ({{ summary.total }})
      </van-tag>
    </div>

    <van-loading v-if="reportsLoading" style="margin: 16px auto; display:block;" />

    <div v-else>
      <div v-if="!reports.length" style="padding: 32px; text-align: center; color: #888;">
        {{ onlyOpen ? '沒有未處理的回報 🎉' : '還沒有任何回報' }}
      </div>

      <div v-else class="report-list">
        <div v-for="r in reports" :key="r.id" class="report-card" :class="{ resolved: r.is_resolved }">
          <div class="card-head">
            <span class="cat" :style="{ background: CATEGORY_COLOR[r.category] || '#888' }">
              {{ CATEGORY_LABEL[r.category] || r.category }}
            </span>
            <span v-if="r.question_id" class="qid">Q#{{ r.question_id }}</span>
            <span class="user">{{ r.username || '匿名' }}</span>
            <span class="time">{{ fmtTime(r.created_at) }}</span>
            <span v-if="r.is_resolved" class="resolved-mark">✓ 已處理</span>
          </div>
          <div class="body">{{ r.body }}</div>
          <div v-if="r.page_url" class="page-url">{{ r.page_url }}</div>
          <div class="actions">
            <van-button v-if="r.question_id" size="small" @click="viewQuestion(r.question_id)">
              看題目 #{{ r.question_id }}
            </van-button>
            <van-button v-if="r.question_id" size="small" type="primary" plain @click="openOnReview(r.question_id)">
              進入待審
            </van-button>
            <van-button v-if="!r.is_resolved" size="small" type="success" @click="resolveReport(r)">
              標記已處理
            </van-button>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- ─────── 待審 tab (review queue) ─────── -->
  <div v-if="tab === 'review'">
    <div class="review-controls">
      <div style="display:flex; align-items:center; gap:8px; flex-wrap:wrap;">
        <van-checkbox v-model="onlyFlagged" @change="loadQueue" shape="square">只看人工標記</van-checkbox>
        <van-button size="mini" @click="loadQueue">重新載入</van-button>
      </div>
      <div style="display:flex; align-items:center; gap:6px;">
        <span style="font-size:13px; color:#666;">跳到</span>
        <input v-model="jumpInput" type="number" placeholder="Q#"
          class="jump-input" @keyup.enter="jumpToQ" />
        <van-button size="mini" type="primary" @click="jumpToQ">前往</van-button>
      </div>
    </div>

    <van-loading v-if="queueLoading" style="margin: 32px auto; display:block;" />
    <div v-else-if="!queue.length && adHocId == null" style="text-align:center; padding:32px; color:#888;">
      {{ onlyFlagged ? '沒有人工標記的待審題目 ✓' : '待審清單空了 ✓' }}
    </div>
    <div v-else>
      <div class="review-header">
        <span v-if="adHocId == null">{{ cursor + 1 }} / {{ queue.length }}</span>
        <span v-else style="color:#1989fa;">直接查看</span>
        <span style="margin-left: auto; font-size: 12px; color:#888;">
          Q#{{ current?.id }}
          <template v-if="detail">· {{ detail.source_code }} · {{ detail.source_ref }}</template>
        </span>
      </div>

      <div class="preview-toggle">
        <van-button
          size="small"
          :type="previewMode === 'detail' ? 'primary' : 'default'"
          @click="previewMode = 'detail'">詳解畫面</van-button>
        <van-button
          size="small"
          :type="previewMode === 'practice' ? 'primary' : 'default'"
          @click="previewMode = 'practice'; previewSelected = ''">答題畫面</van-button>
      </div>

      <van-loading v-if="detailLoading" style="margin: 16px auto; display:block;" />

      <!-- Detail (詳解) view -->
      <div v-else-if="detail && previewMode === 'detail'" class="detail">
        <van-cell-group inset title="題目">
          <div class="content stem">{{ detail.stem }}</div>
          <div v-if="detail.stem_image_urls?.length" class="imgs">
            <img v-for="(s,i) in detail.stem_image_urls" :key="i" :src="imgUrl(s)" />
          </div>
          <van-cell v-if="detail.source_info" title="出處" :value="detail.source_info" />
          <van-cell v-if="detail.question_label" title="題號" :value="detail.question_label" />
          <van-cell title="OCR" :value="`${detail.ocr_status} | needs_review=${detail.needs_review}`" />
        </van-cell-group>

        <van-cell-group inset title="選項 (正解標綠)">
          <van-cell v-for="(text, letter) in detail.options" :key="letter"
            :title="`(${letter}) ${text}`"
            :class="{ 'opt-correct': letter === detail.correct_letter }" />
        </van-cell-group>

        <van-cell-group inset title="詳解">
          <div class="content expl">{{ detail.explanation || '(無詳解)' }}</div>
          <div v-if="detail.explanation_image_urls?.length" class="imgs">
            <img v-for="(s,i) in detail.explanation_image_urls" :key="i" :src="imgUrl(s)" />
          </div>
          <van-cell v-if="detail.reference_page" title="Reference" :label="detail.reference_page" />
        </van-cell-group>
      </div>

      <!-- Practice (答題) view -->
      <div v-else-if="detail && previewMode === 'practice'" class="detail practice-mock">
        <div v-if="detail.tags?.length || detail.question_label" class="tags">
          <van-tag v-for="t in detail.tags" :key="t" type="primary" round style="margin-right:6px;">{{ t }}</van-tag>
          <van-tag v-if="detail.question_label" type="success" round>{{ detail.question_label }}</van-tag>
        </div>
        <div class="stem-bare">{{ detail.stem }}</div>
        <div v-if="detail.stem_image_urls?.length" class="images-bare">
          <img v-for="(src, i) in detail.stem_image_urls" :key="i" :src="imgUrl(src)" />
        </div>
        <van-radio-group v-model="previewSelected" class="opts-mock">
          <van-cell
            v-for="(text, letter) in detail.options" :key="letter"
            :title="`(${letter}) ${text}`"
            clickable
            @click="previewSelected = letter">
            <template #right-icon>
              <van-radio :name="letter" />
            </template>
          </van-cell>
        </van-radio-group>
        <div class="practice-hint">
          ⓘ 答題模擬 — 此為使用者作答時看到的畫面，正解與詳解隱藏
        </div>
      </div>

      <!-- QQE comms -->
      <div v-if="detail" class="qqe-section">
        <div class="qqe-title">給 QQE 的修訂建議</div>
        <div v-if="qqeRecent.length" class="qqe-history">
          <div v-for="r in qqeRecent" :key="r.id" class="qqe-item" :class="{ resolved: r.is_resolved }">
            <div class="qqe-meta">
              <span class="qqe-time">{{ new Date(r.created_at).toLocaleString('zh-TW') }}</span>
              <span v-if="r.is_resolved" class="qqe-done">✓ 已處理</span>
            </div>
            <div class="qqe-body">{{ r.body }}</div>
          </div>
        </div>
        <van-field
          v-model="qqeBody"
          rows="3" autosize type="textarea"
          placeholder="例：選項 D 應該是…，原文 P.239 上半…"
          maxlength="2000" show-word-limit
        />
        <div style="margin-top: 8px; display:flex; justify-content:flex-end;">
          <van-button size="small" type="primary" :loading="qqeSending" @click="sendQqe">
            送出給 QQE
          </van-button>
        </div>
      </div>
    </div>

    <!-- Sticky action footer -->
    <div v-if="queue.length || adHocId != null" class="review-footer">
      <van-button size="small" @click="prevQ" :disabled="adHocId == null && cursor === 0">← 上一題</van-button>
      <van-button size="small" type="warning" @click="openEdit" :disabled="!detail">編輯</van-button>
      <van-button size="small" type="success" @click="markReviewed" :disabled="!detail || adHocId != null">✓ 標已審</van-button>
      <van-button size="small" type="primary" @click="nextQ" :disabled="adHocId == null && cursor >= queue.length - 1">下一題 →</van-button>
    </div>
  </div>

  <!-- Edit modal -->
  <van-popup v-model:show="showEdit" position="bottom" round :z-index="2100" :style="{ height: '85%', padding: '16px 16px 100px' }">
    <h3>編輯 Q#{{ current?.id }}</h3>
    <van-cell-group inset>
      <van-field v-model="editForm.stem" rows="3" autosize type="textarea" label="題目" />
      <van-field v-for="L in ['a','b','c','d','e']" :key="L"
        v-model="editForm[`option_${L}`]" autosize type="textarea" :label="`(${L.toUpperCase()})`" />
      <van-field v-model="editForm.correct_letter" label="正解" maxlength="1" />
      <van-field v-model="editForm.explanation" rows="6" autosize type="textarea" label="詳解" />
      <van-field v-model="editForm.reference_page" autosize type="textarea" label="Reference" />
    </van-cell-group>
    <div style="margin-top: 16px; display:flex; gap:8px;">
      <van-button @click="showEdit = false" style="flex:1;">取消</van-button>
      <van-button type="primary" @click="saveEdit" style="flex:2;">儲存（自動標已審）</van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.filters { padding: 12px 16px; display: flex; gap: 8px; background: white; border-bottom: 1px solid #f0f0f0; }
.filters :deep(.van-tag) { cursor: pointer; padding: 6px 14px; }
.report-list { padding: 12px; padding-bottom: 80px; }
.report-card {
  background: white; padding: 14px;
  margin-bottom: 12px; border-radius: 10px;
  box-shadow: 0 2px 6px rgba(0,0,0,0.05);
}
.report-card.resolved { opacity: 0.6; }
.card-head {
  display: flex; align-items: center; flex-wrap: wrap;
  gap: 8px; margin-bottom: 8px; font-size: 12px;
}
.cat { color: white; padding: 2px 8px; border-radius: 4px; font-size: 11px; }
.qid { background:#1989fa; color:#fff; padding: 1px 6px; border-radius: 3px; font-size: 11px; }
.user { font-weight: bold; color: #333; }
.time { color: #888; font-size: 11px; }
.resolved-mark { color: #07c160; font-size: 11px; font-weight: bold; }
.body { color: #333; line-height: 1.6; white-space: pre-wrap; padding: 8px 0; }
.page-url { font-size: 11px; color: #888; padding-bottom: 8px; word-break: break-all; }
.actions { display: flex; gap: 8px; padding-top: 8px; border-top: 1px solid #f0f0f0; flex-wrap: wrap; }

.review-controls { padding: 12px 16px; display:flex; justify-content:space-between; align-items:center; gap:8px; flex-wrap:wrap; }
.jump-input { width: 90px; padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 14px; }
.jump-input:focus { outline: none; border-color: #1989fa; }
.review-header { padding: 10px 16px; display:flex; align-items:center; background:#fff; border-bottom:1px solid #eee; font-weight: bold; }
.preview-toggle { padding: 8px 16px; display:flex; gap:8px; background:#fafafa; border-bottom: 1px solid #eee; }
.review-footer { position: fixed; bottom:0; left:0; right:0; background:white; padding: 12px 16px; display:flex; gap:6px; border-top:1px solid #e5e5e5; z-index: 100; }
.detail { padding-bottom: 100px; }
.practice-mock .tags { padding: 12px 16px 0; }
.practice-mock .stem-bare { padding: 16px; line-height: 1.7; font-size: 16px; white-space: pre-wrap; }
.practice-mock .images-bare { padding: 0 16px; }
.practice-mock .images-bare img { max-width: 100%; margin-bottom: 12px; border-radius: 4px; }
.practice-mock .opts-mock { margin: 8px 0; }
.practice-mock .practice-hint { padding: 10px 16px; margin: 8px 16px; background: #fffbe6; border: 1px solid #ffe58f; border-radius: 6px; font-size: 12px; color: #614700; }

.qqe-section { margin: 16px 16px 0; padding: 12px; background: #fff8e1; border: 1px solid #ffe58f; border-radius: 6px; }
.qqe-title { font-weight: bold; color: #614700; margin-bottom: 8px; font-size: 14px; }
.qqe-history { margin-bottom: 10px; }
.qqe-item { padding: 8px 10px; background: #fff; border-radius: 4px; margin-bottom: 6px; border-left: 3px solid #1989fa; }
.qqe-item.resolved { opacity: 0.55; border-left-color: #07c160; }
.qqe-meta { display:flex; justify-content:space-between; font-size: 11px; color: #888; margin-bottom: 4px; }
.qqe-done { color: #07c160; font-weight: bold; }
.qqe-body { font-size: 13px; color: #333; white-space: pre-wrap; line-height: 1.5; }
.content { padding: 12px 16px; line-height: 1.7; white-space: pre-wrap; font-size: 14px; }
.content.expl { color:#333; background:#fafafa; }
.imgs { padding: 0 16px 12px; }
.imgs img { max-width: 100%; margin-bottom: 8px; border:1px solid #eee; border-radius:4px; }
:deep(.opt-correct) { background: #e8f9e8; }
</style>
