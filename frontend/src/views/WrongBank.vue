<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import api, { SOURCE_LABEL, userCanSee } from '../api.js'
import InfoTooltip from '../components/InfoTooltip.vue'
import HelpButton from '../components/HelpButton.vue'
import { useKeyboardSafePopup } from '../composables/useKeyboardSafePopup.js'
import { useHelpMode } from '../composables/useHelpMode.js'

const { helpMode } = useHelpMode()

const WRONGBANK_HELP = '**這頁是什麼？**\n集中你容易答錯的題，考前優先複習這裡。\n\n**題目從哪來？**\n• 你**手動加星**的題\n• 系統依你的紀錄**自動偵測**的常錯題（依「下次再錯的機率」排序）\n\n**可以做什麼？**\n批次**加星 / 調整出現頻率 / 移出**（移出後若再答錯會自動加回）。'

const router = useRouter()
const route = useRoute()
const { popupStyle: notePopupStyle } = useKeyboardSafePopup()

const asUserId = computed(() => {
  const v = route.query.as_user
  if (v === undefined || v === null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
})
const readOnly = computed(() => asUserId.value !== null)
const targetUsername = ref('')

async function loadTargetUsername () {
  if (asUserId.value === null) { targetUsername.value = ''; return }
  try {
    const r = await api.get(`/admin/cohort/users/${asUserId.value}`)
    targetUsername.value = r.data?.username || `#${asUserId.value}`
  } catch (e) { targetUsername.value = `#${asUserId.value}` }
}
const ALL_SOURCE_OPTIONS = [
  { value: 'past_paper', label: SOURCE_LABEL.past_paper }
]
const SOURCE_OPTIONS = computed(() =>
  ALL_SOURCE_OPTIONS.filter(o => userCanSee(o.value))
)
const source = ref(SOURCE_OPTIONS.value[0]?.value || 'past_paper')

const cards = ref([])
const stats = ref(null)
const tags = ref([])
const loading = ref(false)
const kind = ref('all')
const tagId = ref('')
const sort = ref('risk')
const includeDismissed = ref(false)

// Order: 風險 > 個別失敗模式 > 領域 > 星號
const SORT_OPTIONS = [
  { value: 'risk',         label: '再錯風險' },
  { value: 'lapses',       label: '答錯次數' },
  { value: 'recent_wrong', label: '錯誤時間' },
  { value: 'tag',          label: '領域' }
]

const KIND_OPTIONS = [
  { value: 'all',    label: '全部',    color: '#1989fa', count: c => c?.total },
  { value: 'lapse',  label: '死穴',    color: '#ee0a24', count: c => c?.lapse, emoji: '🔴' },
  { value: 'forget', label: '易忘',    color: '#ff976a', count: c => c?.forget, emoji: '🟠' },
  { value: 'blind',  label: '盲區',    color: '#ffd21e', count: c => c?.blind, emoji: '🟡' },
  { value: 'recent', label: '新錯',    color: '#888',    count: c => c?.recent, emoji: '⚪' },
  { value: 'star',   label: '★ 手動',  color: '#ffd21e', count: c => c?.star }
]

async function loadTags () {
  const r = await api.get('/tags')
  tags.value = r.data.filter(x => x.level === 'major')
}

async function loadStats () {
  try {
    const params = { source_code: source.value }
    if (asUserId.value !== null) params.as_user = asUserId.value
    const r = await api.get('/wrong-bank/stats', { params })
    stats.value = r.data
  } catch (e) { /* silent: stats are decorative */ }
}

async function load () {
  loading.value = true
  try {
    const params = {
      kind: kind.value,
      source_code: source.value,
      sort: sort.value,
      limit: 200,
      include_dismissed: includeDismissed.value
    }
    if (tagId.value) params.tag_id = tagId.value
    if (asUserId.value !== null) params.as_user = asUserId.value
    const r = await api.get('/wrong-bank', { params })
    cards.value = r.data
  } catch (e) {
    if (e.response?.status === 403) {
      showToast('無權限'); router.push('/'); return
    }
    if (e.response?.status === 404) {
      showToast('找不到該使用者'); router.push('/admin/cohort'); return
    }
    showToast(e.response?.data?.detail || '載入失敗')
  } finally {
    loading.value = false
  }
}

const tagPickerShow = ref(false)
const sortPickerShow = ref(false)
function tagLabel (t) {
  const num = String(t.sort_order ?? '').padStart(2, '0')
  return t.short_zh ? `${num} ${t.name} (${t.short_zh})` : `${num} ${t.name}`
}
function selectedTagName () {
  const t = tags.value.find(x => x.id === tagId.value)
  return t ? tagLabel(t) : '不限'
}
const sortLabel = computed(() => SORT_OPTIONS.find(o => o.value === sort.value)?.label || '')

onMounted(async () => {
  await loadTargetUsername()
  await loadTags()
  let saved = null
  try { saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null') } catch (_) { /* */ }
  if (saved && asUserId.value === null) {
    restoring.value = true
    kind.value = saved.kind ?? 'all'
    tagId.value = saved.tagId ?? ''
    sort.value = saved.sort ?? 'risk'
    includeDismissed.value = !!saved.includeDismissed
    source.value = saved.source ?? source.value
    showAllOpts.value = !!saved.showAllOpts
    sessionStorage.removeItem(STORAGE_KEY)   // 還原一次即清（只針對返回）
  } else { saved = null }
  await Promise.all([loadStats(), load()])
  if (saved) {
    page.value = saved.page ?? 1
    await nextTick()
    window.scrollTo(0, saved.scrollY || 0)
    restoring.value = false
  }
})
watch([kind, tagId, sort, includeDismissed, source], async () => {
  if (restoring.value) return
  page.value = 1
  await load()
  if (source) await loadStats()
})
watch(asUserId, async () => { await loadTargetUsername(); await Promise.all([loadStats(), load()]) })

const showAllOpts = ref(true)
const page = ref(1)
const PAGE_SIZE = 20
const totalPages = computed(() => Math.max(1, Math.ceil(cards.value.length / PAGE_SIZE)))
const pagedCards = computed(() => cards.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE))

// 返回時還原頁碼/捲動位置（僅限「從題目返回」這一次）
const STORAGE_KEY = 'wrongBankState'
const restoring = ref(false)
function saveState () {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      kind: kind.value, tagId: tagId.value, sort: sort.value,
      includeDismissed: includeDismissed.value, source: source.value,
      showAllOpts: showAllOpts.value, page: page.value, scrollY: window.scrollY
    }))
  } catch (_) { /* sessionStorage unavailable */ }
}
function viewQuestion (qid) { saveState(); router.push(`/question/${qid}`) }

const selectMode = ref(false)
const selectedQids = ref(new Set())
function toggleSelect (qid) {
  if (selectedQids.value.has(qid)) selectedQids.value.delete(qid)
  else selectedQids.value.add(qid)
  selectedQids.value = new Set(selectedQids.value)
}
function selectAll () { selectedQids.value = new Set(cards.value.map(c => c.question_id)) }
function clearSelection () { selectedQids.value = new Set() }
function enterSelectMode () { selectMode.value = true }
function exitSelectMode () { selectMode.value = false; clearSelection() }

const busy = ref(false)
const noteEditShow = ref(false)
const noteEditQid = ref(null)
const noteEditBody = ref('')
const noteSaving = ref(false)

async function openNoteEdit (card) {
  noteEditQid.value = card.question_id
  try {
    const r = await api.get(`/notes/${card.question_id}`)
    noteEditBody.value = r.data?.body || ''
  } catch (_) { noteEditBody.value = card.note || '' }
  noteEditShow.value = true
}

async function saveNoteEdit () {
  if (noteEditQid.value == null) return
  noteSaving.value = true
  try {
    await api.put(`/notes/${noteEditQid.value}`, { body: noteEditBody.value })
    const c = cards.value.find(x => x.question_id === noteEditQid.value)
    if (c) c.note = noteEditBody.value
    showToast('已存')
    noteEditShow.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '存檔失敗')
  } finally { noteSaving.value = false }
}

async function deleteNoteEdit () {
  if (noteEditQid.value == null) return
  try {
    await showConfirmDialog({
      title: '刪除心得',
      message: '這題的心得會永久刪除，確定？',
      confirmButtonColor: '#ee0a24'
    })
  } catch (_) { return }
  noteSaving.value = true
  try {
    await api.delete(`/notes/${noteEditQid.value}`)
    const c = cards.value.find(x => x.question_id === noteEditQid.value)
    if (c) c.note = null
    showToast('已刪除')
    noteEditShow.value = false
  } catch (e) {
    showToast(e.response?.data?.detail || '刪除失敗')
  } finally { noteSaving.value = false }
}

async function bulk (action) {
  const ids = Array.from(selectedQids.value)
  if (!ids.length) { showToast('沒有選取的題目'); return }
  busy.value = true
  try {
    await api.post('/wrong-bank/bulk', { question_ids: ids, action })
    showToast(`已${{
      dismiss: '移出錯題本',
      undismiss: '加回錯題本',
      star: '加星',
      unstar: '取消星'
    }[action]} ${ids.length} 題`)
    clearSelection()
    await Promise.all([load(), loadStats()])
  } catch (e) {
    showToast(e.response?.data?.detail || '操作失敗')
  } finally { busy.value = false }
}

function categoryBadgeColor (cat) {
  return { lapse: '#ee0a24', forget: '#ff976a', blind: '#ffd21e', recent: '#888' }[cat] || '#bbb'
}
function categoryBadgeLabel (cat) {
  return { lapse: '死穴', forget: '易忘', blind: '盲區', recent: '新錯' }[cat] || cat
}

function fmtPct (v) {
  if (v === null || v === undefined) return '—'
  return Math.round(v * 100) + '%'
}

const HELP = {
  risk: {
    title: '「風險」是怎麼排的？',
    body:
      '預設排序用的分數，越高 = 你考試時越可能再答錯。\n\n' +
      '系統綜合你「已忘多少、過去錯幾次、有沒有自以為會卻錯」算出，把最該複習的題排前面。'
  },
  retrievability: {
    title: '「R」是什麼？',
    body:
      '系統估你「現在」還記得這題的機率。\n\n' +
      '90% = 還記得牢；50% = 一半機率忘；0% = 大概全忘了。'
  },
  categories: {
    title: '死穴 / 易忘 / 盲區 / 新錯？',
    body:
      '系統幫你把錯題分四類，方便針對性複習：\n\n' +
      '🔴 死穴：你已答錯 ≥ 2 次\n' +
      '🟠 易忘：學過但快忘了\n' +
      '🟡 盲區：很有把握卻答錯（最危險）\n' +
      '⚪ 新錯：最近第一次答錯\n\n' +
      '一題可同時屬多類。考前最該優先：死穴 + 盲區。'
  }
}
</script>

<template>
  <div v-if="readOnly" class="readonly-banner">
    <span class="banner-text">目前檢視：{{ targetUsername || '…' }}（管理者唯讀模式）</span>
    <van-button
      size="mini"
      type="warning"
      @click="$router.push(`/dashboard?as_user=${asUserId}`)"
      style="margin-right: 6px;"
    >其 Dashboard</van-button>
    <van-button size="mini" type="default" @click="$router.push('/admin/cohort')">返回 Cohort</van-button>
  </div>
  <van-nav-bar title="錯題區" left-text="首頁" left-arrow @click-left="$router.push('/')">
    <template #right>
      <HelpButton title="錯題區說明" :content="WRONGBANK_HELP" :size="18" />
    </template>
  </van-nav-bar>

  <div v-if="helpMode" class="help-banner">
    💡 風險<InfoTooltip :title="HELP.risk.title" :body="HELP.risk.body" /> /
    R<InfoTooltip :title="HELP.retrievability.title" :body="HELP.retrievability.body" /> /
    失敗模式<InfoTooltip :title="HELP.categories.title" :body="HELP.categories.body" /> 都是你自己的再錯指標
  </div>

  <!-- Stat cards -->
  <div v-if="stats" class="stats-row">
    <div
      v-for="opt in KIND_OPTIONS.filter(o => o.value !== 'star')"
      :key="opt.value"
      :class="['stat-card', { active: kind === opt.value }]"
      :style="{ borderColor: kind === opt.value ? opt.color : '#eee' }"
      @click="kind = opt.value"
    >
      <div class="stat-num" :style="{ color: opt.color }">{{ opt.count(stats) ?? 0 }}</div>
      <div class="stat-lbl">
        <span v-if="opt.emoji">{{ opt.emoji }}</span> {{ opt.label }}
      </div>
    </div>
    <div :class="['stat-card', { active: kind === 'star' }]"
         :style="{ borderColor: kind === 'star' ? '#ffd21e' : '#eee' }"
         @click="kind = 'star'">
      <div class="stat-num" style="color: #ffd21e;">{{ stats.star ?? 0 }}</div>
      <div class="stat-lbl">★ 手動</div>
    </div>
  </div>

  <div class="filters">
    <div v-if="SOURCE_OPTIONS.length > 1" class="filter-row">
      <span class="lbl">來源</span>
      <van-tag
        v-for="o in SOURCE_OPTIONS" :key="o.value"
        size="medium"
        :type="source === o.value ? 'primary' : 'default'"
        @click="source = o.value"
      >{{ o.label }}</van-tag>
    </div>
    <div class="filter-row">
      <span class="lbl">領域</span>
      <van-tag size="medium" :type="!tagId ? 'primary' : 'default'" @click="tagId = ''">全部</van-tag>
      <van-tag size="medium" type="default" @click="tagPickerShow = true">{{ selectedTagName() === '不限' ? '選領域...' : selectedTagName() }}</van-tag>
    </div>
    <div class="filter-row">
      <span class="lbl">排序</span>

      <!-- Wide screen: all sort chips inline -->
      <span class="sort-wide">
        <van-tag
          v-for="o in SORT_OPTIONS" :key="o.value"
          size="medium"
          :type="sort === o.value ? 'primary' : 'default'"
          @click="sort = o.value"
        >{{ o.label }}</van-tag>
      </span>

      <!-- Narrow screen: current chip + 切換 -->
      <span class="sort-narrow">
        <van-tag size="medium" type="primary">{{ sortLabel }}</van-tag>
        <van-tag size="medium" plain @click="sortPickerShow = true">切換</van-tag>
      </span>

      <van-checkbox v-model="includeDismissed" shape="square" style="margin-left: auto;">
        <span style="font-size: 12px; color: #888;">含已移出</span>
      </van-checkbox>
    </div>
  </div>

  <van-popup v-model:show="tagPickerShow" position="bottom" round :style="{ height: '60%' }">
    <div style="padding: 12px 16px; font-weight: bold;">選擇大領域</div>
    <van-cell title="不限" is-link @click="tagId = ''; tagPickerShow = false" />
    <van-cell v-for="t in tags" :key="t.id" :title="tagLabel(t)" is-link @click="tagId = t.id; tagPickerShow = false" />
  </van-popup>

  <van-popup v-model:show="sortPickerShow" position="bottom" round>
    <div style="padding: 12px 16px; font-weight: bold;">排序方式</div>
    <van-cell
      v-for="o in SORT_OPTIONS" :key="o.value"
      :title="o.label" is-link
      :value="sort === o.value ? '✓' : ''"
      @click="sort = o.value; sortPickerShow = false"
    />
  </van-popup>

  <van-loading v-if="loading" style="margin: 16px auto; display:block;" />

  <div v-else>
    <div class="counter-row">
      <span class="counter">共 {{ cards.length }} 題</span>
      <van-button size="mini" :type="showAllOpts ? 'primary' : 'default'" plain @click="showAllOpts = !showAllOpts">
        {{ showAllOpts ? '收合選項' : '顯示所有選項' }}
      </van-button>
      <template v-if="!selectMode">
        <van-button size="mini" type="primary" plain @click="enterSelectMode" :disabled="!cards.length">選擇</van-button>
      </template>
      <template v-else>
        <span class="select-info" v-if="selectedQids.size">已選 {{ selectedQids.size }}</span>
        <van-button size="mini" plain @click="selectAll" v-if="cards.length">全選</van-button>
        <van-button size="mini" plain @click="clearSelection" :disabled="!selectedQids.size">清除</van-button>
        <van-button size="mini" plain @click="exitSelectMode">取消</van-button>
      </template>
    </div>

    <div v-if="!cards.length" style="padding: 32px; text-align: center; color: #888;">
      此分類沒有題目 🎉
    </div>

    <div v-else class="card-grid">
      <div
        v-for="c in pagedCards" :key="c.question_id"
        class="card"
        :class="{ selected: selectMode && selectedQids.has(c.question_id) }"
      >
        <div class="card-row">
          <van-checkbox
            v-if="selectMode"
            :model-value="selectedQids.has(c.question_id)"
            @click.stop="toggleSelect(c.question_id)"
            shape="square"
            style="margin-right: 8px; align-self: flex-start;"
          />
          <div
            class="card-body"
            @click="selectMode ? toggleSelect(c.question_id) : viewQuestion(c.question_id)"
          >
            <div class="meta-line">
              <!-- 領域(灰) -->
              <span v-for="t in c.tags" :key="t" class="chip">{{ t }}</span>
              <span v-if="c.question_label" class="chip">{{ c.question_label }}</span>
              <!-- 個人指標（| 分隔） -->
              <span class="divider">|</span>
              <span v-if="c.is_starred" class="chip star">★</span>
              <span class="chip risk">風險 {{ c.risk_score.toFixed(2) }}</span>
              <span class="chip">R {{ fmtPct(c.r_now) }}</span>
              <span v-if="c.lapses > 0" class="chip">我錯 {{ c.lapses }} 次</span>
              <span v-if="c.conf3_wrong_30d > 0" class="chip blind">信心高卻錯 {{ c.conf3_wrong_30d }}</span>
              <span
                v-for="cat in c.categories" :key="cat"
                class="cat-badge" :style="{ background: categoryBadgeColor(cat) }"
              >{{ categoryBadgeLabel(cat) }}</span>
            </div>
            <div class="card-stem">{{ c.stem }}</div>
            <div v-if="!showAllOpts" class="card-answer">
              <span class="ans-letter">({{ c.correct_letter }})</span>
              {{ c.correct_text }}
            </div>
            <div v-if="showAllOpts" class="opt-list">
              <div
                v-for="(text, letter) in c.options" :key="letter"
                class="opt-item" :class="{ correct: (c.correct_letter || '').includes(letter) }"
              ><b>({{ letter }})</b> {{ text }}</div>
            </div>
            <div v-if="c.note" class="card-note" @click.stop="openNoteEdit(c)">
              📝 {{ c.note }}
              <span class="note-edit-hint">點擊編輯</span>
            </div>
          </div>
        </div>
      </div>
      <van-pagination
        v-if="totalPages > 1"
        v-model="page" :total-items="cards.length" :items-per-page="PAGE_SIZE"
        :show-page-size="5" force-ellipses prev-text="上一頁" next-text="下一頁"
        style="margin: 12px 0 80px;"
      />
    </div>
  </div>

  <van-popup v-model:show="noteEditShow" position="bottom" round :style="notePopupStyle">
    <h3 style="margin: 0 0 12px;">編輯心得</h3>
    <van-field
      v-model="noteEditBody" rows="6" autosize type="textarea"
      placeholder="例：詳解寫得好、概念常錯、容易記錯..."
    />
    <div style="margin-top: 16px; display: flex; gap: 8px;">
      <van-button
        v-if="cards.find(c => c.question_id === noteEditQid)?.note"
        type="danger" plain :loading="noteSaving" @click="deleteNoteEdit"
        style="flex: 1;"
      >刪除</van-button>
      <van-button type="primary" :loading="noteSaving" @click="saveNoteEdit" style="flex: 2;">
        儲存
      </van-button>
    </div>
  </van-popup>

  <div v-if="selectMode && selectedQids.size" class="bulk-bar">
    <van-button size="small" type="warning" :loading="busy" @click="bulk('star')">★ 加星</van-button>
    <van-button size="small" plain :loading="busy" @click="bulk('unstar')">取消星</van-button>
    <van-button size="small" type="danger" plain :loading="busy" @click="bulk('dismiss')">移出</van-button>
    <van-button v-if="includeDismissed" size="small" plain :loading="busy" @click="bulk('undismiss')">加回</van-button>
  </div>
</template>

<style scoped>
.help-banner { background: #e8f3ff; color: #1989fa; font-size: 12px; padding: 6px 16px; border-bottom: 1px solid #c8dfff; }
.help-cats-line { padding: 6px 16px; font-size: 12px; color: #888; display: flex; align-items: center; gap: 4px; }

.stats-row {
  display: grid; grid-template-columns: repeat(6, 1fr); gap: 6px;
  padding: 12px 16px 4px; background: white;
}
.stat-card {
  background: #fff; border: 2px solid #eee; border-radius: 8px;
  padding: 8px 4px; text-align: center; cursor: pointer;
  transition: border-color 0.2s, transform 0.1s;
  user-select: none;
}
.stat-card.active { transform: translateY(-1px); box-shadow: 0 2px 4px rgba(0,0,0,0.08); }
.stat-num { font-size: 18px; font-weight: 700; line-height: 1.2; }
.stat-lbl { font-size: 11px; color: #555; margin-top: 2px; }

.filters { padding: 12px 16px; background: white; border-bottom: 1px solid #f0f0f0; }
.filter-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.filter-row .lbl { width: 40px; font-size: 13px; color: #666; }
.filter-row :deep(.van-tag) { cursor: pointer; padding: 4px 10px; }
.sort-wide { display: none; gap: 6px; }
.sort-narrow { display: inline-flex; gap: 6px; }
@media (min-width: 600px) {
  .sort-wide { display: inline-flex; }
  .sort-narrow { display: none; }
}

.counter-row { padding: 12px 16px; display: flex; align-items: center; gap: 8px; color: #666; font-size: 13px; }
.counter-row .counter { flex: 1; }
.counter-row .select-info { color: #1989fa; font-weight: bold; }

.card-grid { padding: 0 12px 80px; }
.card { background: white; padding: 12px; margin-bottom: 12px; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); }
.card.selected { background: #e6f4ff; }
.card-row { display: flex; }
.card-body { flex: 1; cursor: pointer; }

.card-risk-row {
  display: flex; flex-wrap: wrap; gap: 4px; align-items: center;
  font-size: 11px; margin-bottom: 6px;
}
.risk-pill { background: #ffe8e8; color: #ee0a24; padding: 2px 6px; border-radius: 4px; }
.meta-pill { background: #f1f1f1; color: #666; padding: 2px 6px; border-radius: 4px; }
.meta-pill.blind { background: #fff8e1; color: #b78500; }
.cat-badge { color: #fff; padding: 2px 6px; border-radius: 4px; font-weight: 500; }

.card-tags { margin-bottom: 8px; display: flex; gap: 4px; flex-wrap: wrap; }
.meta-line { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin-bottom: 8px; font-size: 11px; }
.chip { padding: 2px 6px; border-radius: 4px; background: #eee; color: #555; }
.chip.risk { background: #ffe8e8; color: #ee0a24; }
.chip.star { background: #ffd21e; color: #333; font-weight: bold; }
.chip.blind { background: #fff8e1; color: #b78500; }
.badge { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: #f0f0f0; color: #555; }
.badge.star { background: #ffd21e; color: #333; font-weight: bold; }
.badge.year { background: #07c160; color: white; }
.badge.tag { background: #1989fa; color: white; }

.card-stem {
  font-size: 14px; color: #333; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical;
  overflow: hidden;
}
.card-answer { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #e5e5e5; font-size: 13px; color: #07c160; }
.ans-letter { font-weight: bold; margin-right: 4px; }
.opt-toggle { margin-top: 8px; font-size: 12px; color: #1989fa; cursor: pointer; }
.opt-list { margin-top: 6px; display: flex; flex-direction: column; gap: 4px; }
.opt-item { font-size: 13px; line-height: 1.5; color: #444; padding: 4px 8px; border-radius: 4px; background: #f7f7f7; }
.opt-item.correct { background: #e8f9e8; color: #0a7a2f; font-weight: 500; }
.card-note { margin-top: 8px; padding: 8px; background: #fff8e1; border-left: 3px solid #ffd21e; border-radius: 4px; font-size: 13px; color: #5f4400; white-space: pre-wrap; cursor: pointer; position: relative; }
.note-edit-hint { font-size: 10px; color: #1989fa; margin-left: 6px; }
.card-note-empty { margin-top: 8px; padding: 6px 8px; background: #f5f5f5; border-radius: 4px; font-size: 12px; color: #999; cursor: pointer; text-align: center; border: 1px dashed #ddd; }

.bulk-bar { position: fixed; bottom: 0; left: 0; right: 0; background: #fff8e6; border-top: 1px solid #ffe58f; padding: 10px 16px; display: flex; gap: 8px; justify-content: center; z-index: 100; }

.readonly-banner {
  position: sticky; top: 0; z-index: 1500;
  background: #ee0a24; color: #fff;
  padding: 8px 12px;
  display: flex; align-items: center; justify-content: space-between;
  font-size: 13px; font-weight: 500;
  box-shadow: 0 2px 4px rgba(0,0,0,0.1);
}
.readonly-banner .banner-text { flex: 1; }
.readonly-banner :deep(.van-button) { flex: 0 0 auto; }
</style>
