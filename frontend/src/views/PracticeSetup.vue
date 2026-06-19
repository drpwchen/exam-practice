<script setup>
import { computed, onMounted, ref, watch, watchEffect } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api, { visibleSources, PAST_PAPER_YEARS, userCanSee } from '../api.js'
import { useSessionStore } from '../stores/session.js'
import HelpButton from '../components/HelpButton.vue'

const SETUP_HELP = '**在這裡設定本次練習：**\n\n• **模式**：智慧（自動混合新題＋到期複習）／新題／複習（到期題、自己常錯、星號題）／夥伴常錯／隨機\n• **領域、年份、題數**\n• **預設把握度、是否打亂選項**\n\n設好按「開始練習」即進入作答。不確定選哪個模式就用**智慧**。'

const router = useRouter()

const tags = ref([])
const sourceMeta = ref(null)
const allSourceMeta = ref([])
const dbLastUpdated = ref(null)
const tagStats = ref([])
const tagStatsMap = computed(() => {
  const m = new Map()
  tagStats.value.forEach(s => m.set(s.tag_id, s))
  return m
})

// Per-user source allowlist: filter the picker to sources this user can see.
// If only one is allowed, auto-select it and hide the radio group.
const ALL_SOURCE_OPTIONS = [
  { code: 'past_paper', title: '考古題', label: '110-114 年' }
]
const visibleSourceOptions = computed(() =>
  ALL_SOURCE_OPTIONS.filter(o => userCanSee(o.code))
)
const showSourcePicker = computed(() => visibleSourceOptions.value.length > 1)

const source = ref(visibleSourceOptions.value[0]?.code || 'past_paper')
const yearSet = ref(new Set(PAST_PAPER_YEARS))
// Top-level mode picker. 複習 (review_group) is a UI grouping that fans out
// into reviewType (到期題目 / 自己的常錯題 / 星號題). The actual picker `mode`
// sent to the API is derived below.
const topMode = ref('smart')        // smart / new / review_group / cohort_wrong / random
const reviewType = ref('review')    // review / wrong / starred — only when topMode==='review_group'
const REVIEW_SUBMODES = ['review', 'wrong', 'starred']
const mode = computed(() =>
  topMode.value === 'review_group' ? reviewType.value : topMode.value
)
const tagIdSet = ref(new Set())
const limit = ref(10)
const defaultConfidence = ref(3)
const excludeMastered = ref(false)
const shuffleOpts = ref(true)

// 大家錯題 sub-options
const cohortLevel = ref('common')           // hardest / hard / common
const cohortExcludeDays = ref(0)            // 0=不排除 / 1 / 3 / 5
const cohortTierCounts = ref(null)          // { hardest, hard, common } | null
const COHORT_LEVELS = [
  { value: 'hardest', label: '最難' },
  { value: 'hard',    label: '偏難' },
  { value: 'common',  label: '初答常錯' }
]
// Shared 排除近期答對 options (大家錯題 + 自己的常錯題)
const DAY_OPTIONS = [
  { value: 0, label: '不排除' },
  { value: 1, label: '排除1天內' },
  { value: 3, label: '排除3天內' },
  { value: 5, label: '排除5天內' }
]
function cohortLevelLabel (lv) {
  const base = COHORT_LEVELS.find(o => o.value === lv)?.label || lv
  const n = cohortTierCounts.value?.[lv]
  return n != null ? `${base} (n=${n}題)` : base
}

// 自己的常錯題 sub-options
const wrongKindSet = ref(new Set())   // empty = 全部 (whole pool, incl. uncategorised)
const wrongExcludeDays = ref(0)       // 0=不排除 / 1 / 3 / 5
const starExcludeDays = ref(0)        // 星號題 0=不排除 / 1 / 3 / 5
const WRONG_KINDS = [
  { value: 'lapse',  label: '死穴', hint: '答錯≥2次' },
  { value: 'forget', label: '易忘', hint: 'R<60%' },
  { value: 'blind',  label: '盲區', hint: '信心高卻錯' },
  { value: 'recent', label: '新錯', hint: '最近答錯' }
]
function toggleWrongKind (v) {
  if (wrongKindSet.value.has(v)) wrongKindSet.value.delete(v)
  else wrongKindSet.value.add(v)
  wrongKindSet.value = new Set(wrongKindSet.value)
}

// 複習 sub-mode available-question counts — { due, wrong, starred, wrong_kinds }
const reviewCounts = ref(null)
function reviewTypeLabel (base, key) {
  const n = reviewCounts.value?.[key]
  return n != null ? `${base}（${n} 題）` : base
}
function wrongKindLabel (o) {
  const n = reviewCounts.value?.wrong_kinds?.[o.value]
  return n != null ? `${o.label} ${n}` : o.label
}
const wrongAllCount = computed(() => reviewCounts.value?.wrong)
// 排除近期答對 day-option labels with resulting pool size (n=幾題)
function wrongExcludeLabel (o) {
  const n = reviewCounts.value?.by_exclude?.[String(o.value)]?.wrong
  return n != null ? `${o.label}（${n}題）` : o.label
}
function starExcludeLabel (o) {
  const n = reviewCounts.value?.by_exclude?.[String(o.value)]?.starred
  return n != null ? `${o.label}（${n}題）` : o.label
}
function cohortExcludeLabel (o) {
  const n = cohortTierCounts.value?.by_exclude?.[String(o.value)]?.[cohortLevel.value]
  return n != null ? `${o.label}（${n}題）` : o.label
}

const STORAGE_KEY_PREFIX = 'practice-setup'

const tagPickerShow = ref(false)

function applySetup (s) {
  if (!s) return
  if (s.source && userCanSee(s.source)) source.value = s.source
  if (Array.isArray(s.years)) yearSet.value = new Set(s.years)
  if (s.mode) {
    // Map the stored actual mode back onto the grouped UI controls.
    if (REVIEW_SUBMODES.includes(s.mode)) {
      topMode.value = 'review_group'
      reviewType.value = s.mode
    } else {
      topMode.value = s.mode
    }
  }
  if (Array.isArray(s.wrong_kinds)) wrongKindSet.value = new Set(s.wrong_kinds)
  if (typeof s.wrong_exclude_recent_days === 'number') wrongExcludeDays.value = s.wrong_exclude_recent_days
  if (typeof s.starred_exclude_recent_days === 'number') starExcludeDays.value = s.starred_exclude_recent_days
  if (Array.isArray(s.tag_ids)) tagIdSet.value = new Set(s.tag_ids)
  if (typeof s.limit === 'number') limit.value = s.limit
  if (typeof s.default_confidence === 'number') defaultConfidence.value = s.default_confidence
  if (typeof s.exclude_mastered === 'boolean') excludeMastered.value = s.exclude_mastered
  if (typeof s.shuffle === 'boolean') shuffleOpts.value = s.shuffle
  if (s.cohort_level) cohortLevel.value = s.cohort_level
  if (typeof s.cohort_exclude_recent_days === 'number') cohortExcludeDays.value = s.cohort_exclude_recent_days
}

async function loadSavedSetup () {
  // Server-side prefs (cross-device) take precedence; fall back to localStorage
  // if API fails (e.g., offline / new endpoint not deployed yet).
  try {
    const r = await api.get('/me/preferences/practice-setup')
    if (r.data) {
      applySetup(r.data)
      return
    }
  } catch (_) { /* fall through to local */ }
  try {
    const raw = localStorage.getItem(`${STORAGE_KEY_PREFIX}:${source.value}`)
    if (raw) applySetup(JSON.parse(raw))
  } catch (_) { /* ignore corrupt entry */ }
}

// stale-while-revalidate cache: show last-known values instantly, refresh in background
const CACHE_KEYS = {
  overview: 'practice-setup:overview-cache',
  tags: 'practice-setup:tags-cache',
  byTag: 'practice-setup:by-tag-cache'  // keyed map: { [statsKey]: stats[] }
}

function readCache (key) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : null
  } catch (_) { return null }
}

function writeCache (key, value) {
  try { localStorage.setItem(key, JSON.stringify(value)) } catch (_) {}
}

function loadAllCaches () {
  const overview = readCache(CACHE_KEYS.overview)
  if (overview) {
    allSourceMeta.value = overview.allSourceMeta ?? []
    dbLastUpdated.value = overview.dbLastUpdated ?? null
  }
  const tagsCache = readCache(CACHE_KEYS.tags)
  if (Array.isArray(tagsCache)) tags.value = tagsCache
}

onMounted(async () => {
  loadAllCaches()
  await loadSavedSetup()
  try {
    const [tRes, oRes] = await Promise.all([
      api.get('/tags'),
      api.get('/dashboard/overview')
    ])
    tags.value = tRes.data.filter(x => x.level === 'major')
    allSourceMeta.value = visibleSources(oRes.data.by_source)
    dbLastUpdated.value = oRes.data.db_last_updated || null
    writeCache(CACHE_KEYS.tags, tags.value)
    writeCache(CACHE_KEYS.overview, {
      allSourceMeta: allSourceMeta.value,
      dbLastUpdated: dbLastUpdated.value
    })
  } catch (e) { showToast('載入失敗') }
})

// Re-pick source meta when the selected source changes
watchEffect(() => {
  sourceMeta.value = allSourceMeta.value.find(s => s.source_code === source.value) || null
})

// Reload tag completion stats whenever source or year selection changes
const statsKey = computed(() =>
  `${source.value}:${Array.from(yearSet.value).sort().join(',')}`
)
let statsSeq = 0
async function fetchTagStats () {
  const seq = ++statsSeq
  // Show cached stats instantly while we refresh
  const cache = readCache(CACHE_KEYS.byTag) || {}
  if (Array.isArray(cache[statsKey.value])) tagStats.value = cache[statsKey.value]
  try {
    const params = new URLSearchParams({ source_code: source.value })
    if (source.value === 'past_paper' && yearSet.value.size > 0) {
      params.set('years', Array.from(yearSet.value).sort().join(','))
    }
    const res = await api.get(`/dashboard/by-tag?${params.toString()}`)
    if (seq !== statsSeq) return
    tagStats.value = res.data
    cache[statsKey.value] = res.data
    writeCache(CACHE_KEYS.byTag, cache)
  } catch (_) { /* keep cached value on failure */ }
}
watch(statsKey, fetchTagStats, { immediate: true })
watch(tagPickerShow, (open) => {
  if (open && tagStats.value.length === 0) fetchTagStats()
})

// 大家錯題 per-tier candidate counts — refresh when mode is selected or the
// source/tag filters change (counts respect the current scoping). A sequence
// guard drops out-of-order responses so rapid tag toggles don't leave a stale
// count showing.
let cohortSeq = 0
async function fetchCohortTiers () {
  const seq = ++cohortSeq
  try {
    const params = { source_code: source.value }
    if (tagIdSet.value.size) params.tag_ids = Array.from(tagIdSet.value).join(',')
    const r = await api.get('/practice/cohort-tiers', { params })
    if (seq !== cohortSeq) return
    cohortTierCounts.value = r.data
  } catch (_) { if (seq === cohortSeq) cohortTierCounts.value = null }
}
watch(
  () => [topMode.value, source.value, Array.from(tagIdSet.value).sort().join(',')],
  () => { if (topMode.value === 'cohort_wrong') fetchCohortTiers() },
  { immediate: true }
)

// 複習 sub-mode counts — refresh when 複習 is open or the source/tag/year
// filters change (counts respect the current scoping, same as 大家錯題).
let reviewSeq = 0
async function fetchReviewCounts () {
  const seq = ++reviewSeq
  try {
    const params = { source_code: source.value }
    if (tagIdSet.value.size) params.tag_ids = Array.from(tagIdSet.value).join(',')
    if (source.value === 'past_paper' && yearSet.value.size) {
      params.years = Array.from(yearSet.value).sort().join(',')
    }
    const r = await api.get('/practice/review-counts', { params })
    if (seq !== reviewSeq) return
    reviewCounts.value = r.data
  } catch (_) { if (seq === reviewSeq) reviewCounts.value = null }
}
watch(
  () => [
    topMode.value, source.value,
    Array.from(tagIdSet.value).sort().join(','),
    Array.from(yearSet.value).sort().join(',')
  ],
  () => { if (topMode.value === 'review_group') fetchReviewCounts() },
  { immediate: true }
)

function tagLabel (t) {
  const num = String(t.sort_order ?? '').padStart(2, '0')
  return t.short_zh ? `${num} ${t.name} (${t.short_zh})` : `${num} ${t.name}`
}

function tagCompletionColor (t) {
  const s = tagStatsMap.value.get(t.id)
  if (!s || s.question_count === 0) return '#999'      // 灰：無符合題目
  if (s.seen_ratio >= 0.7) return '#07c160'            // 綠：高完成率
  if (s.seen_ratio >= 0.3) return '#ff976a'            // 橙：中等
  return '#ee0a24'                                     // 紅：低完成率
}

function tagCountText (t) {
  const s = tagStatsMap.value.get(t.id)
  if (!s) return '0/0'
  return `${s.attempted_count}/${s.question_count}`
}

const allYearsSelected = computed(() => PAST_PAPER_YEARS.every(y => yearSet.value.has(y)))
function toggleAllYears () {
  yearSet.value = allYearsSelected.value ? new Set() : new Set(PAST_PAPER_YEARS)
}
function toggleTag (t) {
  if (tagIdSet.value.has(t.id)) tagIdSet.value.delete(t.id)
  else tagIdSet.value.add(t.id)
  tagIdSet.value = new Set(tagIdSet.value)
}
function isSelected (t) { return tagIdSet.value.has(t.id) }
function selectedTagName () {
  const n = tagIdSet.value.size
  return n === 0 ? '不限 (全部領域)' : `已選 ${n} 個領域`
}

const sessionStore = useSessionStore()

function start () {
  const tagIds = Array.from(tagIdSet.value)
  const years = source.value === 'past_paper' ? Array.from(yearSet.value) : []
  // 本次題數 only applies to 智慧/新題; other modes run unlimited (0=不限).
  const effectiveLimit = (topMode.value === 'smart' || topMode.value === 'new') ? limit.value : 0
  const setup = {
    source: source.value,
    years,
    mode: mode.value,
    tag_ids: tagIds,
    limit: effectiveLimit,
    default_confidence: defaultConfidence.value,
    exclude_mastered: excludeMastered.value,
    shuffle: shuffleOpts.value,
    cohort_level: cohortLevel.value,
    cohort_exclude_recent_days: cohortExcludeDays.value,
    wrong_kinds: Array.from(wrongKindSet.value),
    wrong_exclude_recent_days: wrongExcludeDays.value,
    starred_exclude_recent_days: starExcludeDays.value
  }
  api.put('/me/preferences/practice-setup', setup).catch(() => { /* best-effort */ })
  try { localStorage.setItem(`${STORAGE_KEY_PREFIX}:${source.value}`, JSON.stringify(setup)) } catch (_) { /* ignore */ }

  sessionStore.startSession({ ...setup, tag_ids: tagIds })
  router.push('/practice')
}

function toggleYear (y) {
  if (yearSet.value.has(y)) yearSet.value.delete(y)
  else yearSet.value.add(y)
  yearSet.value = new Set(yearSet.value)
}
</script>

<template>
  <van-nav-bar title="練習設定" left-text="返回" left-arrow @click-left="$router.push('/')">
    <template #right>
      <HelpButton title="練習設定說明" :content="SETUP_HELP" :size="18" />
    </template>
  </van-nav-bar>

  <div class="setup">
    <van-cell-group v-if="showSourcePicker" inset title="題目來源">
      <van-radio-group v-model="source">
        <van-cell
          v-for="o in visibleSourceOptions" :key="o.code"
          :title="o.title" :label="o.label" clickable
          @click="source = o.code"
        >
          <template #right-icon><van-radio :name="o.code" /></template>
        </van-cell>
      </van-radio-group>
    </van-cell-group>

    <div style="padding:6px 16px 0; color:#888; font-size:12px;">
      可用題庫：{{ sourceMeta ? sourceMeta.total_questions : '—' }} 題 · 你已答過 {{ sourceMeta ? sourceMeta.answered_at_least_once : '—' }} 題 · 到期 {{ sourceMeta ? sourceMeta.due_today : '—' }} · 星標 {{ sourceMeta ? sourceMeta.starred : '—' }}
    </div>

    <van-cell-group v-if="source === 'past_paper'" inset title="年份 (可多選)" style="margin-top:16px;">
      <div style="padding:12px 16px; display:flex; gap:12px; flex-wrap:wrap;">
        <van-tag
          size="medium"
          :type="allYearsSelected ? 'primary' : 'default'"
          @click="toggleAllYears"
        >全選</van-tag>
        <van-tag
          v-for="y in [...PAST_PAPER_YEARS].reverse()" :key="y"
          size="medium"
          :type="yearSet.has(y) ? 'primary' : 'default'"
          @click="toggleYear(y)"
        >{{ y }} 年</van-tag>
      </div>
    </van-cell-group>

    <van-cell-group inset title="領域篩選 (可多選)" style="margin-top:16px;">
      <van-field readonly clickable label="領域" :model-value="selectedTagName()" @click="tagPickerShow = true" />
      <div v-if="tagIdSet.size" style="padding: 8px 16px;">
        <van-tag
          v-for="t in tags.filter(t => tagIdSet.has(t.id))" :key="t.id"
          type="primary" closeable
          style="margin: 2px 4px;"
          @close="toggleTag(t)"
        >{{ String(t.sort_order).padStart(2,'0') }} {{ t.name }}</van-tag>
      </div>
    </van-cell-group>

    <van-cell-group inset title="練習模式" style="margin-top:16px;">
      <van-radio-group v-model="topMode">
        <van-cell icon="bulb-o" title="智慧" label="新題 + 到期複習 + 星標題混合，依熟悉度自動排程" clickable @click="topMode = 'smart'">
          <template #right-icon><van-radio name="smart" /></template>
        </van-cell>
        <van-cell icon="plus" title="新題" label="從未答對過的題目（答錯會留著重出）" clickable @click="topMode = 'new'">
          <template #right-icon><van-radio name="new" /></template>
        </van-cell>
        <van-cell icon="replay" title="複習" label="到期題目 / 自己的常錯題 / 星號題" clickable @click="topMode = 'review_group'">
          <template #right-icon><van-radio name="review_group" /></template>
        </van-cell>
        <van-cell icon="friends-o" title="夥伴常錯" label="全體最常答錯的題目" clickable @click="topMode = 'cohort_wrong'">
          <template #right-icon><van-radio name="cohort_wrong" /></template>
        </van-cell>
        <van-cell icon="exchange" title="隨機" label="完全隨機，不看熟悉度" clickable @click="topMode = 'random'">
          <template #right-icon><van-radio name="random" /></template>
        </van-cell>
      </van-radio-group>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'review_group'" inset title="複習類型" style="margin-top:16px;">
      <van-radio-group v-model="reviewType">
        <van-cell :title="reviewTypeLabel('到期題目', 'due')" label="只挑 FSRS 到期需要複習的題目" clickable @click="reviewType = 'review'">
          <template #right-icon><van-radio name="review" /></template>
        </van-cell>
        <van-cell :title="reviewTypeLabel('自己的常錯題', 'wrong')" label="你答錯或不熟的題，依「下次錯的機會」排序" clickable @click="reviewType = 'wrong'">
          <template #right-icon><van-radio name="wrong" /></template>
        </van-cell>
        <van-cell :title="reviewTypeLabel('星號題', 'starred')" label="只練你加星的題目，依出錯可能性排序（最近答過也會再出）" clickable @click="reviewType = 'starred'">
          <template #right-icon><van-radio name="starred" /></template>
        </van-cell>
      </van-radio-group>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'review_group' && reviewType === 'wrong'" inset title="失敗模式 (可多選)" style="margin-top:16px;">
      <div style="padding:12px 16px; display:flex; gap:8px; flex-wrap:wrap;">
        <van-tag
          size="medium"
          :type="wrongKindSet.size === 0 ? 'primary' : 'default'"
          @click="wrongKindSet = new Set()"
        >全部<span v-if="wrongAllCount != null"> {{ wrongAllCount }}</span></van-tag>
        <van-tag
          v-for="o in WRONG_KINDS" :key="o.value"
          size="medium"
          :type="wrongKindSet.has(o.value) ? 'primary' : 'default'"
          @click="toggleWrongKind(o.value)"
        >{{ wrongKindLabel(o) }}<span style="opacity:.7;">·{{ o.hint }}</span></van-tag>
      </div>
      <div style="padding:0 16px 12px; color:#888; font-size:12px;">
        多選＝聯集。「全部」是完整常錯池，含「以前錯過但最近答對」這類不屬任一標籤的題；選特定模式時這些題不會出現。
      </div>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'review_group' && reviewType === 'wrong'" inset title="排除近期答對" style="margin-top:16px;">
      <div style="padding:12px 16px; display:flex; gap:8px; flex-wrap:wrap;">
        <van-tag
          v-for="o in DAY_OPTIONS" :key="o.value"
          size="medium"
          :type="wrongExcludeDays === o.value ? 'primary' : 'default'"
          @click="wrongExcludeDays = o.value"
        >{{ wrongExcludeLabel(o) }}</van-tag>
      </div>
      <div style="padding:0 16px 12px; color:#888; font-size:12px;">
        排除你近 N 天內已答對的題；答錯的會繼續出現讓你再練。選「不排除」則完整照排序出。
      </div>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'review_group' && reviewType === 'starred'" inset title="排除近期答對" style="margin-top:16px;">
      <div style="padding:12px 16px; display:flex; gap:8px; flex-wrap:wrap;">
        <van-tag
          v-for="o in DAY_OPTIONS" :key="o.value"
          size="medium"
          :type="starExcludeDays === o.value ? 'primary' : 'default'"
          @click="starExcludeDays = o.value"
        >{{ starExcludeLabel(o) }}</van-tag>
      </div>
      <div style="padding:0 16px 12px; color:#888; font-size:12px;">
        排除你近 N 天內已答對的題；答錯的會繼續出現讓你再練。選「不排除」則完整照排序出。
      </div>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'cohort_wrong'" inset title="涵蓋難度" style="margin-top:16px;">
      <div style="padding:12px 16px; display:flex; gap:8px; flex-wrap:wrap;">
        <van-tag
          v-for="o in COHORT_LEVELS" :key="o.value"
          size="medium"
          :type="cohortLevel === o.value ? 'primary' : 'default'"
          @click="cohortLevel = o.value"
        >{{ cohortLevelLabel(o.value) }}</van-tag>
      </div>
      <div style="padding:0 16px 12px; color:#888; font-size:12px;">
        依「全體再答錯誤率」由高到低出題。最難＝大家初答錯且再答仍常錯的題。
      </div>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'cohort_wrong'" inset title="排除近期答對" style="margin-top:16px;">
      <div style="padding:12px 16px; display:flex; gap:8px; flex-wrap:wrap;">
        <van-tag
          v-for="o in DAY_OPTIONS" :key="o.value"
          size="medium"
          :type="cohortExcludeDays === o.value ? 'primary' : 'default'"
          @click="cohortExcludeDays = o.value"
        >{{ cohortExcludeLabel(o) }}</van-tag>
      </div>
      <div style="padding:0 16px 12px; color:#888; font-size:12px;">
        排除你近 N 天內已答對的題；答錯的會繼續出現讓你再練。選「不排除」則完整照排序出。
      </div>
    </van-cell-group>

    <van-cell-group v-if="topMode === 'smart' || topMode === 'new'" inset title="本次題數" style="margin-top:16px;">
      <van-radio-group v-model="limit" direction="horizontal" style="padding:12px 16px;">
        <van-radio :name="5" style="margin-right:12px;">5</van-radio>
        <van-radio :name="10" style="margin-right:12px;">10</van-radio>
        <van-radio :name="20" style="margin-right:12px;">20</van-radio>
        <van-radio :name="50" style="margin-right:12px;">50</van-radio>
        <van-radio :name="0">不限</van-radio>
      </van-radio-group>
    </van-cell-group>

    <van-cell-group inset title="預設把握度" style="margin-top:16px;">
      <div style="padding:8px 16px; font-size:12px; color:#888;">每題開始時自動選的把握度，覺得多數題目自己不熟可以選「猜的」</div>
      <van-radio-group v-model="defaultConfidence" direction="horizontal" style="padding:12px 16px;">
        <van-radio :name="1" style="margin-right:16px;">猜的</van-radio>
        <van-radio :name="2" style="margin-right:16px;">普通</van-radio>
        <van-radio :name="3">很確定</van-radio>
      </van-radio-group>
    </van-cell-group>

    <van-cell-group inset style="margin-top:16px;">
      <van-cell title="排除已熟練題目" label="跳過熟悉度高的題（你已會的）" center>
        <template #right-icon><van-switch v-model="excludeMastered" /></template>
      </van-cell>
      <van-cell title="打亂選項順序" label="關閉後選項保持原 A-E（詳解標 ABCDE 對得起來，但容易死記順序）" center>
        <template #right-icon><van-switch v-model="shuffleOpts" /></template>
      </van-cell>
    </van-cell-group>

    <div class="footer">
      <van-button block round type="primary" @click="start">開始練習</van-button>
    </div>
  </div>

  <van-popup v-model:show="tagPickerShow" position="bottom" round :style="{ height:'70%' }">
    <div style="padding:12px 16px; display:flex; justify-content:space-between; align-items:center;">
      <span style="font-weight:bold;">選擇大領域 (可多選)</span>
      <span style="display:flex; gap:8px;">
        <van-button size="small" plain @click="tagIdSet = new Set()">清空</van-button>
        <van-button size="small" type="primary" @click="tagPickerShow = false">完成</van-button>
      </span>
    </div>
    <van-cell
      v-for="t in tags" :key="t.id"
      :title="tagLabel(t)"
      clickable
      :class="{ 'tag-selected': isSelected(t) }"
      @click="toggleTag(t)"
    >
      <template #right-icon>
        <span class="tag-count-badge" :style="{ backgroundColor: tagCompletionColor(t) }">
          {{ tagCountText(t) }}
        </span>
        <van-checkbox :model-value="isSelected(t)" style="margin-left:8px;" />
      </template>
    </van-cell>
  </van-popup>
</template>

<style scoped>
.setup { padding-bottom: 100px; }
.footer {
  position: fixed; bottom: 0; left: 0; right: 0;
  padding: 12px 16px; background: white;
  border-top: 1px solid #e5e5e5; z-index: 10;
}
:deep(.tag-selected) { background: #e8f3ff; }
.tag-count-badge {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  min-width: 48px;
  text-align: center;
  border-radius: 10px;
  padding: 1px 8px;
  line-height: 1.6;
  flex-shrink: 0;
  color: white;
  font-weight: 500;
}
</style>
