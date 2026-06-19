<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api from '../api.js'
import { useSessionStore } from '../stores/session.js'

const router = useRouter()
const session = useSessionStore()

onMounted(() => {
  if (!session.chain.length) {
    router.replace('/')
  }
})

const total = computed(() => session.chain.length)
const correct = computed(() => session.correctCount)
const accuracy = computed(() => total.value
  ? (correct.value / total.value * 100).toFixed(1)
  : '—')
const totalTimeMs = computed(() => session.totalTimeMs)
const avgTimeMs = computed(() => total.value
  ? Math.round(totalTimeMs.value / total.value)
  : 0)

const selectMode = ref(false)
const selectedQids = ref(new Set())
function toggleSelect (qid) {
  if (selectedQids.value.has(qid)) selectedQids.value.delete(qid)
  else selectedQids.value.add(qid)
  selectedQids.value = new Set(selectedQids.value)
}
function selectAll () {
  selectedQids.value = new Set(session.chain.map(e => e.question?.question_id).filter(Boolean))
}
function clearSelection () { selectedQids.value = new Set() }
function enterSelectMode () { selectMode.value = true }
function exitSelectMode () {
  selectMode.value = false
  clearSelection()
}

const showBoostPopup = ref(false)
const boostValue = ref(2.0)
const busy = ref(false)

async function bulkSetStar (starred) {
  const ids = Array.from(selectedQids.value)
  if (!ids.length) { showToast('沒有選取的題目'); return }
  busy.value = true
  try {
    await api.post('/overrides/bulk', { question_ids: ids, is_starred: starred })
    ids.forEach(qid => {
      const idx = session.chain.findIndex(x => x.question?.question_id === qid)
      if (idx >= 0) session.patchChainQuestion(idx, { is_starred: starred })
    })
    showToast(`已${starred ? '加星' : '取消星'} ${ids.length} 題`)
    clearSelection()
  } catch (_) { showToast('操作失敗') } finally { busy.value = false }
}

async function bulkSetBoost () {
  const ids = Array.from(selectedQids.value)
  if (!ids.length) { showToast('沒有選取的題目'); return }
  busy.value = true
  try {
    await api.post('/overrides/bulk', { question_ids: ids, priority_boost: boostValue.value })
    ids.forEach(qid => {
      const idx = session.chain.findIndex(x => x.question?.question_id === qid)
      if (idx >= 0) session.patchChainQuestion(idx, { priority_boost: boostValue.value })
    })
    showToast(`已將 ${ids.length} 題頻率設為 ${boostValue.value}x`)
    showBoostPopup.value = false
    clearSelection()
  } catch (_) { showToast('操作失敗') } finally { busy.value = false }
}

function fmtTime (ms) {
  if (!ms) return '—'
  const s = ms / 1000
  if (s < 60) return s.toFixed(1) + ' 秒'
  const m = Math.floor(s / 60)
  const r = Math.floor(s % 60)
  return `${m}:${r.toString().padStart(2, '0')}`
}

function viewFeedback (idx) {
  session.setFeedbackIndex(idx)
  router.push('/feedback')
}

function startAgain () {
  session.clearSession()
  router.push('/setup')
}
function home () { session.clearSession(); router.push('/') }
</script>

<template>
  <van-nav-bar title="本次練習結果" left-text="首頁" left-arrow @click-left="home" />

  <div class="summary">
    <div class="big-stat">
      <div class="num" :class="{ good: accuracy >= 70, mid: accuracy >= 40 && accuracy < 70, bad: accuracy < 40 }">
        {{ accuracy }}%
      </div>
      <div class="lbl">正確率（{{ correct }} / {{ total }}）</div>
    </div>
    <van-grid :column-num="2" :border="false" style="background:transparent;">
      <van-grid-item>
        <div class="stat"><div class="n">{{ fmtTime(totalTimeMs) }}</div><div class="l">總用時</div></div>
      </van-grid-item>
      <van-grid-item>
        <div class="stat"><div class="n">{{ fmtTime(avgTimeMs) }}</div><div class="l">平均/題</div></div>
      </van-grid-item>
    </van-grid>
  </div>

  <div class="select-bar">
    <template v-if="!selectMode">
      <span class="select-info">點題目進詳解；要批次操作請按右側「選擇」</span>
      <van-button size="mini" type="primary" plain @click="enterSelectMode">選擇</van-button>
    </template>
    <template v-else>
      <span class="select-info">
        已選 <b>{{ selectedQids.size }}</b> / {{ total }}
      </span>
      <van-button size="mini" plain @click="selectAll">全選</van-button>
      <van-button size="mini" plain @click="clearSelection" :disabled="!selectedQids.size">清除</van-button>
      <van-button size="mini" plain @click="exitSelectMode">取消</van-button>
    </template>
  </div>

  <van-cell-group inset title="本次答的題目（點開看詳解）">
    <div
      v-for="(e, i) in session.chain"
      :key="i"
      class="entry"
      :class="{ selected: selectMode && selectedQids.has(e.question?.question_id) }"
    >
      <div class="entry-row">
        <van-checkbox
          v-if="selectMode"
          :model-value="selectedQids.has(e.question?.question_id)"
          @click.stop="toggleSelect(e.question?.question_id)"
          shape="square"
          style="margin-right: 8px;"
        />
        <div
          class="entry-body"
          @click="selectMode ? toggleSelect(e.question?.question_id) : viewFeedback(i)"
        >
          <div class="entry-meta">
            <span class="num">#{{ i + 1 }}</span>
            <span class="result" :class="{ ok: e.answer?.is_correct, wrong: !e.answer?.is_correct }">
              {{ e.answer?.is_correct ? '✓' : '✗' }}
            </span>
            <span class="letters">
              選 ({{ e.answer?.selected_letter }}) / 答 ({{ e.answer?.correct_letter }})
            </span>
            <span v-if="e.question?.is_starred" class="star">★</span>
            <span v-for="t in (e.question?.tags || [])" :key="t" class="tag">{{ t }}</span>
            <span class="time">{{ fmtTime(e.answer?.time_ms) }}</span>
          </div>
          <div class="entry-stem">
            {{ e.question?.stem?.slice(0, 100) }}{{ (e.question?.stem?.length || 0) > 100 ? '...' : '' }}
          </div>
        </div>
      </div>
    </div>
  </van-cell-group>

  <div v-if="selectMode && selectedQids.size" class="bulk-bar">
    <van-button size="small" type="warning" :loading="busy" @click="bulkSetStar(true)">★ 加星</van-button>
    <van-button size="small" plain :loading="busy" @click="bulkSetStar(false)">取消星</van-button>
    <van-button size="small" plain :loading="busy" @click="boostValue = 2.0; showBoostPopup = true">⚙ 改頻率</van-button>
  </div>

  <div class="footer">
    <van-button block plain @click="home" style="flex:1;">回首頁</van-button>
    <van-button block type="primary" @click="startAgain" style="flex:1;">再練一輪</van-button>
  </div>

  <van-popup v-model:show="showBoostPopup" position="bottom" round :style="{ padding: '16px' }">
    <h3 style="margin: 0 0 12px;">調整出題頻率（{{ selectedQids.size }} 題）</h3>
    <p style="color:#888; font-size:13px;">1x = 正常；3x = 比正常頻繁 3 倍；0.5x = 一半</p>
    <van-stepper v-model="boostValue" :step="0.5" :min="0.5" :max="5" decimal-length="1" />
    <div style="margin-top: 16px;">
      <van-button block type="primary" :loading="busy" @click="bulkSetBoost">套用</van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.summary { padding: 24px 16px 8px; text-align: center; background: white; }
.big-stat .num { font-size: 48px; font-weight: bold; color: #1989fa; }
.big-stat .num.good { color: #07c160; }
.big-stat .num.mid { color: #ff976a; }
.big-stat .num.bad { color: #ee0a24; }
.big-stat .lbl { color: #888; font-size: 14px; margin-top: 4px; }
.stat { text-align: center; }
.stat .n { font-size: 18px; font-weight: bold; color: #333; }
.stat .l { font-size: 12px; color: #888; margin-top: 2px; }
.select-bar {
  margin: 12px 16px 0;
  padding: 8px 12px;
  background: white;
  border-radius: 6px;
  display: flex; align-items: center; gap: 8px;
  font-size: 13px; color: #555;
}
.select-bar .select-info { flex: 1; }
.entry { padding: 10px 14px; border-bottom: 1px solid #f0f0f0; }
.entry.selected { background: #e6f4ff; }
.entry-row { display: flex; align-items: flex-start; }
.entry-body { flex: 1; cursor: pointer; }
.entry-meta {
  display: flex; align-items: center; gap: 6px; flex-wrap: wrap;
  font-size: 12px; color: #666;
}
.entry-meta .num { font-weight: bold; color: #333; }
.entry-meta .result.ok { color: #07c160; font-size: 14px; }
.entry-meta .result.wrong { color: #ee0a24; font-size: 14px; }
.entry-meta .star { color: #ffd21e; }
.entry-meta .tag { background: #1989fa; color: white; padding: 1px 6px; border-radius: 3px; }
.entry-meta .time { margin-left: auto; color: #888; }
.entry-stem { margin-top: 4px; font-size: 13px; color: #444; line-height: 1.4; }
.bulk-bar {
  position: sticky; bottom: 60px; left: 0; right: 0;
  background: #fff8e6;
  border-top: 1px solid #ffe58f;
  padding: 10px 16px;
  display: flex; gap: 8px; justify-content: center;
  z-index: 90;
}
.footer {
  padding: 12px 16px 24px;
  display: flex; gap: 8px;
  background: white;
  border-top: 1px solid #e5e5e5;
  margin-top: 12px;
}
</style>
