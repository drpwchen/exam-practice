<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import api from '../api.js'

const router = useRouter()
const tab = ref('pending')   // pending | approved | rejected | all
const counts = ref({ pending: 0, approved: 0, rejected: 0, total: 0, questions_with_any: 0 })
const queue = ref([])
const loading = ref(false)

const detail = ref(null)         // selected paraphrase detail
const showDetail = ref(false)
const detailLoading = ref(false)

// Edit-mode state (admin overrides variant text before approving)
const editing = ref(false)
const editForm = ref({ stem: '', option_a: '', option_b: '', option_c: '', option_d: '', option_e: '' })

async function loadCounts () {
  try {
    const r = await api.get('/admin/paraphrase/counts')
    counts.value = r.data
  } catch (e) { showToast('counts 載入失敗') }
}

async function loadQueue () {
  loading.value = true
  try {
    const r = await api.get('/admin/paraphrase/queue', {
      params: { status_filter: tab.value, limit: 200 }
    })
    queue.value = r.data
  } catch (e) {
    showToast('queue 載入失敗')
  } finally {
    loading.value = false
  }
}

async function openDetail (id) {
  detailLoading.value = true
  showDetail.value = true
  editing.value = false
  try {
    const r = await api.get(`/admin/paraphrase/${id}`)
    detail.value = r.data
    editForm.value = {
      stem: r.data.stem,
      option_a: r.data.option_a,
      option_b: r.data.option_b,
      option_c: r.data.option_c,
      option_d: r.data.option_d,
      option_e: r.data.option_e || ''
    }
  } catch (e) {
    showToast('detail 載入失敗')
    showDetail.value = false
  } finally {
    detailLoading.value = false
  }
}

async function approve () {
  if (!detail.value) return
  try {
    await api.post(`/admin/paraphrase/${detail.value.id}/approve`)
    showToast('✓ 已通過')
    showDetail.value = false
    await Promise.all([loadCounts(), loadQueue()])
  } catch (e) { showToast('通過失敗') }
}

async function reject () {
  if (!detail.value) return
  try {
    await api.post(`/admin/paraphrase/${detail.value.id}/reject`)
    showToast('✗ 已拒絕')
    showDetail.value = false
    await Promise.all([loadCounts(), loadQueue()])
  } catch (e) { showToast('拒絕失敗') }
}

async function saveEdit () {
  if (!detail.value) return
  try {
    const body = { ...editForm.value }
    if (!body.option_e) body.option_e = null
    await api.post(`/admin/paraphrase/${detail.value.id}/edit`, body)
    showToast('✓ 已編輯並通過')
    showDetail.value = false
    await Promise.all([loadCounts(), loadQueue()])
  } catch (e) {
    showToast(e.response?.data?.detail || '編輯失敗')
  }
}

watch(tab, loadQueue)

onMounted(async () => {
  await Promise.all([loadCounts(), loadQueue()])
})

const verdictColor = (v) => {
  if (v === 'SAME') return 'success'
  if (v === 'CHANGED') return 'danger'
  if (v === 'UNSURE') return 'warning'
  return 'default'
}

// Naive char-level diff: highlight chars that differ between original and variant
// at the same index. Good enough for spotting flipped negation, swapped numbers.
function diffMark (orig, variant) {
  const o = orig || ''
  const v = variant || ''
  const max = Math.max(o.length, v.length)
  const out = []
  for (let i = 0; i < max; i++) {
    const co = o[i] || ''
    const cv = v[i] || ''
    out.push({ char: cv, diff: co !== cv })
  }
  return out
}
</script>

<template>
  <van-nav-bar title="改寫題審核" left-text="返回" left-arrow @click-left="router.push('/')" />

  <div class="overview">
    <div class="stat"><div class="num">{{ counts.pending }}</div><div class="lab">待審</div></div>
    <div class="stat"><div class="num">{{ counts.approved }}</div><div class="lab">已通過</div></div>
    <div class="stat"><div class="num">{{ counts.rejected }}</div><div class="lab">已拒絕</div></div>
    <div class="stat"><div class="num">{{ counts.questions_with_any }}</div><div class="lab">含變體題數</div></div>
  </div>

  <van-tabs v-model:active="tab" sticky offset-top="46px">
    <van-tab name="pending" title="待審" />
    <van-tab name="approved" title="已通過" />
    <van-tab name="rejected" title="已拒絕" />
    <van-tab name="all" title="全部" />
  </van-tabs>

  <div v-if="loading" style="padding:32px; text-align:center;"><van-loading /></div>

  <van-list v-else>
    <van-cell
      v-for="row in queue" :key="row.id"
      clickable @click="openDetail(row.id)"
    >
      <template #title>
        <span style="font-weight:bold;">Q{{ row.question_id }} v{{ row.variant_idx }}</span>
        <van-tag v-if="row.judge_verdict" :type="verdictColor(row.judge_verdict)" style="margin-left:8px;">
          {{ row.judge_verdict }}
        </van-tag>
        <van-tag style="margin-left:6px;" :type="row.status === 'approved' ? 'success' : (row.status === 'rejected' ? 'danger' : 'warning')">
          {{ row.status }}
        </van-tag>
      </template>
      <template #label>
        <div class="preview">{{ row.stem_preview }}</div>
      </template>
    </van-cell>
    <div v-if="!queue.length" style="padding:32px; text-align:center; color:#aaa;">空</div>
  </van-list>

  <van-popup
    v-model:show="showDetail" position="bottom" round
    :style="{ height: '92%' }"
  >
    <div v-if="detailLoading" style="padding:32px; text-align:center;"><van-loading /></div>
    <div v-else-if="detail" class="detail">
      <div class="detail-head">
        <span style="font-weight:bold; font-size:16px;">
          Q{{ detail.question_id }} · 變體 #{{ detail.variant_idx }}
        </span>
        <van-tag v-if="detail.judge_verdict" :type="verdictColor(detail.judge_verdict)" style="margin-left:8px;">
          Judge: {{ detail.judge_verdict }}
        </van-tag>
        <van-tag :type="detail.status === 'approved' ? 'success' : (detail.status === 'rejected' ? 'danger' : 'warning')" style="margin-left:6px;">
          {{ detail.status }}
        </van-tag>
        <span style="margin-left:auto; font-size:12px; color:#999;">
          {{ detail.model_id }}
        </span>
      </div>

      <div v-if="detail.validation_notes" class="notes">
        <details>
          <summary>Validation notes</summary>
          <pre>{{ detail.validation_notes }}</pre>
        </details>
      </div>

      <!-- Side-by-side stem -->
      <div class="diff-section">
        <div class="col">
          <div class="col-head">原題 stem</div>
          <div class="text">{{ detail.original_stem }}</div>
        </div>
        <div class="col">
          <div class="col-head">改寫 stem <span style="color:#fa8c16;">(差異紅字)</span></div>
          <div v-if="!editing" class="text">
            <span v-for="(c, i) in diffMark(detail.original_stem, detail.stem)" :key="i" :class="{ d: c.diff }">{{ c.char }}</span>
          </div>
          <van-field v-else v-model="editForm.stem" type="textarea" autosize rows="3" />
        </div>
      </div>

      <!-- Side-by-side options -->
      <div v-for="L in ['a','b','c','d','e']" :key="L">
        <div v-if="detail['option_' + L]" class="diff-section">
          <div class="col">
            <div class="col-head">原 ({{ L.toUpperCase() }}){{ detail.correct_letter === L.toUpperCase() ? ' ★ 正解' : '' }}</div>
            <div class="text">{{ detail['original_option_' + L] }}</div>
          </div>
          <div class="col">
            <div class="col-head">改寫 ({{ L.toUpperCase() }})</div>
            <div v-if="!editing" class="text">
              <span v-for="(c, i) in diffMark(detail['original_option_' + L], detail['option_' + L])" :key="i" :class="{ d: c.diff }">{{ c.char }}</span>
            </div>
            <van-field v-else v-model="editForm['option_' + L]" type="textarea" autosize rows="2" />
          </div>
        </div>
      </div>

      <div class="action-bar">
        <template v-if="!editing">
          <van-button size="small" type="success" @click="approve">✓ 通過</van-button>
          <van-button size="small" type="danger" @click="reject">✗ 拒絕</van-button>
          <van-button size="small" plain @click="editing = true">✎ 編輯後通過</van-button>
          <van-button size="small" plain @click="showDetail = false">關閉</van-button>
        </template>
        <template v-else>
          <van-button size="small" type="primary" @click="saveEdit">儲存並通過</van-button>
          <van-button size="small" plain @click="editing = false">取消編輯</van-button>
        </template>
      </div>
    </div>
  </van-popup>
</template>

<style scoped>
.overview { display: flex; padding: 12px; gap: 8px; background: white; }
.stat { flex: 1; text-align: center; padding: 8px; background: #f6f6f6; border-radius: 6px; }
.stat .num { font-size: 22px; font-weight: bold; color: #1989fa; }
.stat .lab { font-size: 11px; color: #888; margin-top: 4px; }
.preview { color: #666; font-size: 13px; line-height: 1.5; margin-top: 4px; }
.detail { padding: 16px; padding-bottom: 100px; }
.detail-head { display: flex; align-items: center; flex-wrap: wrap; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #eee; }
.notes { margin-bottom: 12px; }
.notes pre { font-size: 11px; background: #f6f6f6; padding: 8px; border-radius: 4px; overflow-x: auto; white-space: pre-wrap; }
.diff-section { display: flex; gap: 8px; margin-bottom: 12px; padding: 8px; background: #fafafa; border-radius: 6px; }
.col { flex: 1; min-width: 0; }
.col-head { font-size: 11px; color: #888; font-weight: bold; margin-bottom: 4px; }
.text { font-size: 14px; line-height: 1.6; white-space: pre-wrap; word-break: break-word; }
.text .d { background: #fff1f0; color: #cf1322; font-weight: bold; }
.action-bar {
  position: fixed; bottom: 0; left: 0; right: 0;
  background: white; padding: 12px 16px;
  border-top: 1px solid #eee;
  display: flex; gap: 8px; justify-content: space-around;
}
</style>
