<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { showToast, showDialog } from 'vant'
import api, { visibleSources, SOURCE_LABEL, PAST_PAPER_YEARS, userCanSee } from '../api.js'
import InfoTooltip from '../components/InfoTooltip.vue'
import HelpButton from '../components/HelpButton.vue'
import { useHelpMode } from '../composables/useHelpMode.js'

const { helpMode } = useHelpMode()

const DASH_HELP = '**這頁是什麼？**\n一頁看完你各科的正確率與複習進度。\n\n**主要區塊**\n• 進度卡：總進度、今日新題、整體／初答／再答正確率\n• 到期分布：未來幾天有多少題到期該複習\n• 各領域進度：每科練到哪、答對率多少\n\n**🎯 設定考前目標**後，系統會算出每天該做幾題、給你建議。\n\n按右上「?」後，各欄位旁會浮現小「?」可看細節。'

const router = useRouter()
const route = useRoute()

const asUserId = computed(() => {
  const v = route.query.as_user
  if (v === undefined || v === null || v === '') return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
})
const readOnly = computed(() => asUserId.value !== null)
const targetUsername = ref('')
const ALL_SOURCE_OPTIONS = [
  { value: 'past_paper', label: SOURCE_LABEL.past_paper }
]
const SOURCE_OPTIONS = computed(() =>
  ALL_SOURCE_OPTIONS.filter(o => userCanSee(o.value))
)
const source = ref(SOURCE_OPTIONS.value[0]?.value || 'past_paper')
const yearFilter = ref(null)
const showCohort = ref(false)

const overview = ref(null)
const tagStats = ref([])
const loading = ref(true)
const allGoals = ref({})  // { source_code: { target_date, target_count, set_at_pool } }
const examGoal = computed(() => allGoals.value[source.value] || null)

async function load () {
  loading.value = true
  try {
    const overviewParams = new URLSearchParams()
    overviewParams.set('source_code', source.value)
    if (source.value === 'past_paper' && yearFilter.value !== null) {
      overviewParams.set('year', String(yearFilter.value))
    }
    const tagParams = new URLSearchParams({ source_code: source.value })
    if (source.value === 'past_paper' && yearFilter.value) {
      tagParams.set('year', String(yearFilter.value))
    }
    if (showCohort.value) tagParams.set('include_cohort', 'true')
    if (asUserId.value !== null) {
      overviewParams.set('as_user', String(asUserId.value))
      tagParams.set('as_user', String(asUserId.value))
    }
    const [o, t] = await Promise.all([
      api.get(`/dashboard/overview?${overviewParams.toString()}`),
      api.get(`/dashboard/by-tag?${tagParams.toString()}`)
    ])
    const data = o.data
    if (Array.isArray(data?.by_source)) data.by_source = visibleSources(data.by_source)
    overview.value = data
    tagStats.value = t.data
  } catch (e) {
    const status = e.response?.status
    if (status === 403) { showToast('無權限'); router.push('/'); return }
    if (status === 404) { showToast('找不到該使用者'); router.push('/admin/cohort'); return }
    showToast(e.response?.data?.detail || '載入失敗')
  } finally {
    loading.value = false
  }
}

async function loadGoal () {
  if (asUserId.value !== null) { allGoals.value = {}; return }  // read-only mode skips
  try {
    const r = await api.get('/me/preferences/exam-goal')
    allGoals.value = r.data || {}
  } catch (e) { allGoals.value = {} }
}

async function resetTargetPassword () {
  if (asUserId.value === null) return
  const name = targetUsername.value || `#${asUserId.value}`
  try {
    await showDialog({
      title: '重置密碼',
      message: `確定將 ${name} 的密碼重置為預設值，並要求下次登入時更改？`,
      showCancelButton: true,
      confirmButtonText: '重置',
      confirmButtonColor: '#ee0a24'
    })
  } catch (_) { return }
  try {
    const r = await api.post(`/admin/cohort/users/${asUserId.value}/reset-password`)
    showToast(`已重置為「${r.data.new_password}」`)
  } catch (e) {
    showToast(e.response?.data?.detail || '重置失敗')
  }
}

async function loadTargetUsername () {
  if (asUserId.value === null) { targetUsername.value = ''; return }
  try {
    const r = await api.get(`/admin/cohort/users/${asUserId.value}`)
    targetUsername.value = r.data?.username || `#${asUserId.value}`
  } catch (e) { targetUsername.value = `#${asUserId.value}` }
}

watch([source, yearFilter, showCohort], load)
watch(source, () => { yearFilter.value = null })

function colorFor (acc) {
  if (acc === null || acc === undefined) return '#bbb'
  if (acc >= 0.85) return '#07c160'
  if (acc >= 0.7) return '#ffd21e'
  if (acc >= 0.55) return '#ff976a'
  return '#ee0a24'
}

function fmtPct (acc) { return acc === null || acc === undefined ? '—' : (acc * 100).toFixed(1) + '%' }
function sourceMeta () {
  return overview.value?.by_source.find(s => s.source_code === source.value)
}

// --- Progress / pace metrics ---
const newPerDay7d = computed(() => {
  const o = overview.value
  if (!o) return 0
  return Math.round((o.new_questions_7d || 0) / 7)
})

const daysToTarget = computed(() => {
  if (!examGoal.value) return null
  const td = new Date(examGoal.value.target_date + 'T00:00:00')
  const now = new Date()
  const diff = Math.ceil((td - now) / 86400000)
  return diff
})

const requiredNewPerDay = computed(() => {
  if (!examGoal.value || !sourceMeta()) return null
  const todayDone = overview.value?.new_questions_today || 0
  const answeredBeforeToday = sourceMeta().answered_at_least_once - todayDone
  const remaining = Math.max(0, examGoal.value.target_count - answeredBeforeToday)
  const days = daysToTarget.value
  if (days === null || days <= 0) return null
  return Math.ceil(remaining / days)
})

// Color codings for 5 mini-tiles ---
function progressTone (pct) {
  if (pct >= 0.75) return 'good'
  if (pct >= 0.5) return 'mid'
  if (pct >= 0.25) return 'warn'
  return 'bad'
}
function paceTone () {
  const Z = newPerDay7d.value
  const Y = requiredNewPerDay.value
  if (Y === null) return 'neutral'
  if (Z === 0 && Y > 0) return 'bad'
  if (Z >= Y) return 'good'
  if (Z >= Y * 0.8) return 'mid'
  if (Z >= Y * 0.5) return 'warn'
  return 'bad'
}
function todayNewTone () {
  const T = overview.value?.new_questions_today || 0
  const Y = requiredNewPerDay.value
  if (Y === null) return 'neutral'
  if (T >= Y) return 'good'
  if (T >= Y * 0.5) return 'mid'
  if (T > 0) return 'warn'
  return 'bad'
}
function accTone (acc) {
  if (acc === null || acc === undefined) return 'neutral'
  if (acc >= 0.85) return 'good'
  if (acc >= 0.75) return 'mid'
  if (acc >= 0.6) return 'warn'
  return 'bad'
}

// --- Advice engine ---
const advice = computed(() => {
  const o = overview.value
  const sm = sourceMeta()
  if (!o || !sm) return null
  if (!examGoal.value) {
    return { tone: 'info', icon: 'ℹ', text: '設定考前目標以獲得個人化建議' }
  }
  if (sm.answered_at_least_once === 0) {
    const txt = requiredNewPerDay.value
      ? `尚未開始，距目標日 ${daysToTarget.value} 天，每日 ${requiredNewPerDay.value} 題即可達成`
      : '尚未開始'
    return { tone: 'info', icon: 'ℹ', text: txt }
  }
  if ((o.overdue_7plus || 0) > 5) {
    return { tone: 'bad', icon: '✗', text: '舊題目複習不足，建議先複習舊題' }
  }

  if (sm.answered_at_least_once >= examGoal.value.target_count) {
    return { tone: 'good', icon: '✓', text: '已達成考前目標，可改練其他題庫或複習舊題' }
  }
  if (requiredNewPerDay.value === null) {
    return { tone: 'warn', icon: '⚠', text: '已過目標日且未達標，請重設目標或加緊進度' }
  }

  const T = o.new_questions_today || 0
  const Y = requiredNewPerDay.value
  const r = o.retention_30d
  const pace = T < Y ? 'lag' : (T > Y * 1.5 ? 'ahead' : 'steady')
  const ret = r === null ? 'mid' : (r < 0.8 ? 'low' : (r > 0.9 ? 'high' : 'mid'))

  const matrix = {
    'lag-low':    { tone: 'bad',  icon: '✗', text: '今日目標尚未達到，再答正確率偏低，建議優先複習舊題' },
    'lag-mid':    { tone: 'warn', icon: '⚠', text: '今日目標尚未達到，再答正確率穩定，請加快新題進度' },
    'lag-high':   { tone: 'warn', icon: '⚠', text: '今日目標尚未達到，再答正確率良好，請多練新題' },
    'steady-low': { tone: 'warn', icon: '⚠', text: '今日目標達到，但再答正確率偏低，建議加強複習' },
    'steady-mid': { tone: 'good', icon: '✓', text: '今日目標達到，再答正確率穩定' },
    'steady-high':{ tone: 'good', icon: '✓', text: '今日目標達到，再答正確率良好' },
    'ahead-low':  { tone: 'warn', icon: '⚠', text: '今日目標超前，但再答正確率偏低，請放慢加強複習' },
    'ahead-mid':  { tone: 'good', icon: '✓', text: '今日目標超前，再答正確率良好，繼續努力' },
    'ahead-high': { tone: 'good', icon: '✓', text: '今日目標超前，再答正確率良好，繼續努力' }
  }
  return matrix[`${pace}-${ret}`]
})

// --- Goal-setting modal ---
const goalModalShow = ref(false)
const goalDate = ref('')
const goalCount = ref(0)
const goalDateShow = ref(false)
function openGoalModal () {
  const max = sourceMeta()?.total_questions || 100
  goalDate.value = examGoal.value?.target_date || examDate.value || ''
  // 重設目標（poolGrew）或全新目標時，預設 max；只有純「修改目標」才保留上次值
  goalCount.value = (!examGoal.value || poolGrew.value) ? max : examGoal.value.target_count
  goalModalShow.value = true
}
async function saveGoal () {
  if (!goalDate.value || goalCount.value <= 0) {
    showToast('請選日期與題數')
    return
  }
  const poolNow = sourceMeta()?.total_questions || 0
  try {
    const r = await api.put('/me/preferences/exam-goal', {
      source_code: source.value,
      target_date: goalDate.value,
      target_count: goalCount.value,
      set_at_pool: poolNow
    })
    allGoals.value = r.data?.goals || allGoals.value
    goalModalShow.value = false
    showToast('已儲存目標')
  } catch (e) {
    showToast(e.response?.data?.detail || '儲存失敗')
  }
}

const poolGrew = computed(() => {
  if (!examGoal.value || !sourceMeta()) return false
  const setAt = examGoal.value.set_at_pool
  if (!setAt) return false
  return sourceMeta().total_questions > setAt
})
async function clearGoal () {
  try {
    await showDialog({
      title: '清除目標', message: '確定移除目前的考前目標？',
      showCancelButton: true, confirmButtonText: '清除', confirmButtonColor: '#ee0a24'
    })
  } catch (_) { return }
  try {
    const r = await api.put('/me/preferences/exam-goal', {
      source_code: source.value,
      clear: true
    })
    allGoals.value = r.data?.goals || {}
    goalModalShow.value = false
    showToast('已清除')
  } catch (e) { showToast('清除失敗') }
}
function toLocalYMD (d) {
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}-${m}-${day}`
}
function pickedDate (val) {
  // vant date picker emits Date or array — handle both
  if (val instanceof Date) {
    goalDate.value = toLocalYMD(val)
  } else if (val && val.selectedValues) {
    goalDate.value = val.selectedValues.join('-')
  }
  goalDateShow.value = false
}

// Date-picker range: today → exam date (loaded from the server). If the exam
// date is unset or already past, fall back to a year out so the picker is never
// empty (the old hard-coded max could fall before today and block all dates).
const minDate = new Date()
const examDate = ref(null)
const maxDate = computed(() => {
  if (examDate.value) {
    const d = new Date(examDate.value + 'T00:00:00')
    if (d > minDate) return d
  }
  const fb = new Date(); fb.setFullYear(fb.getFullYear() + 1); return fb
})
async function loadMeta () {
  try {
    const r = await api.get('/meta/db-info')
    examDate.value = r.data?.exam_date || null
  } catch (_) { /* keep fallback */ }
}

// --- Due bar chart for next 7 days ---
const overdueTotal = computed(() => {
  const o = overview.value
  if (!o) return 0
  return (o.overdue_7plus || 0) + (o.overdue_recent || 0) + (o.due_today || 0)
})
const dueChart = computed(() => {
  const o = overview.value
  if (!o) return []
  const cells = []
  const today = new Date()
  const todayMd = `${today.getMonth() + 1}/${today.getDate()}`
  cells.push({ label: '今日到期', date: todayMd, count: overdueTotal.value, isToday: true })
  const arr = (o.due_next_7_days || []).slice(1, 8)
  for (const d of arr) {
    const dt = new Date(d.date + 'T00:00:00')
    const wkday = ['日','一','二','三','四','五','六'][dt.getDay()]
    const md = `${dt.getMonth() + 1}/${dt.getDate()}`
    cells.push({ label: `週${wkday}`, date: md, count: d.count, isToday: false })
  }
  return cells
})
const dueChartMax = computed(() => Math.max(1, ...dueChart.value.map(c => c.count)))

onMounted(() => { loadTargetUsername(); loadGoal(); loadMeta(); load() })
watch(asUserId, () => { loadTargetUsername(); loadGoal(); load() })

const HELP = {
  progress: {
    title: '進度卡片各欄位',
    body:
      '總進度：你已答過至少一次的題目 / 該領域全部題目。\n\n' +
      '今日新題：今天第一次作答的題目數；下方為每日目標。\n\n' +
      '整體正確率：所有作答（含第一次）的正確比例；下方小字為近 7 天表現。\n\n' +
      '初答正確率：每題第一次作答的正確率，反映「沒看過的新題實力」。\n\n' +
      '再答正確率：第二次以後再答的正確率，反映複習成效。'
  },
  retention: {
    title: '再答正確率',
    body:
      '過去 30 天，第二次以後再答的題目，答對的比例。\n\n' +
      '反映「複習有效，記住的題目能答對」。\n\n' +
      '> 90%：記得很牢\n80–90%：穩定\n< 80%：複習不足，先穩固再加新'
  },
  overdue: {
    title: '到期分布',
    body:
      '今日到期：所有過期 + 今日到期累積總數，FSRS 已準備好讓你複習。\n\n' +
      '未來 7 天：FSRS 排程會在那一天輪到複習的卡片數。\n\n' +
      '過期 ≥ 7 天累積偏多時，FSRS 對這批卡的記憶估計已失準，要先消化。'
  },
  cohort: {
    title: '夥伴進度',
    body:
      '與其他使用者（在該 tag 答題量 ≥ 3 的人）的比較。\n\n' +
      '夥伴已練題數平均：他們各自答過該 tag 多少不重複的題目（取平均）\n\n' +
      '夥伴正確率中位數：他們的正確率中位數（需 ≥ 3 人才顯示）'
  },
  fsrsState: {
    title: '進階：FSRS state',
    body:
      'L (Learning)：第一次看，初步建立記憶\n' +
      'R (Review)：已建立記憶，按間隔複習（健康下絕大多數應在這）\n' +
      'RL (Relearning)：原本掌握的卡忘了，需重建（>0 = 有退步）'
  }
}
</script>

<template>
  <div v-if="readOnly" class="readonly-banner">
    <span class="banner-text">目前檢視：{{ targetUsername || '…' }}（管理者唯讀模式）</span>
    <van-button
      size="mini"
      type="warning"
      @click="$router.push(`/wrong-bank?as_user=${asUserId}`)"
      style="margin-right: 6px;"
    >其錯題本</van-button>
    <van-button
      size="mini"
      type="danger"
      @click="resetTargetPassword"
      style="margin-right: 6px;"
    >重置密碼</van-button>
    <van-button size="mini" type="default" @click="$router.push('/admin/cohort')">返回 Cohort</van-button>
  </div>
  <van-nav-bar title="答題狀況" left-text="返回首頁" left-arrow @click-left="$router.push('/')">
    <template #right>
      <HelpButton title="Dashboard 說明" :content="DASH_HELP" :size="18" :style="{ marginRight: '12px' }" />
      <span @click="$router.push('/history')" style="color:#1989fa; font-size:13px; margin-right:12px;">歷史</span>
      <van-icon name="replay" size="18" @click="load" />
    </template>
  </van-nav-bar>

  <div v-if="helpMode" class="help-banner">
    💡 說明模式已開啟，各欄位旁出現的「?」可點開細節說明。
  </div>

  <div v-if="SOURCE_OPTIONS.length > 1" class="src-bar">
    <span class="src-lbl">來源</span>
    <van-tag
      v-for="o in SOURCE_OPTIONS" :key="o.value"
      size="medium"
      :type="source === o.value ? 'primary' : 'default'"
      @click="source = o.value"
      style="cursor:pointer;"
    >{{ o.label }}</van-tag>
  </div>
  <div v-if="source === 'past_paper'" class="src-bar">
    <span class="src-lbl">年份</span>
    <van-tag size="medium" :type="yearFilter === null ? 'primary' : 'default'" @click="yearFilter = null" style="cursor:pointer;">全部</van-tag>
    <van-tag
      v-for="y in PAST_PAPER_YEARS" :key="y"
      size="medium"
      :type="yearFilter === y ? 'primary' : 'default'"
      @click="yearFilter = y"
      style="cursor:pointer;"
    >{{ y }} 年</van-tag>
  </div>

  <van-loading v-if="loading" style="margin: 32px auto; display:block;" />

  <div v-else>
    <div v-if="overview && sourceMeta()" class="overview-pad">
      <div class="db-stamp">題庫更新：{{ overview.db_last_updated }}</div>

      <!-- ① 進度卡片（5 mini-tiles + advice） -->
      <div class="big-card">
        <div class="big-card-head">
          <span class="big-card-title">
            進度
            <InfoTooltip v-if="helpMode" :title="HELP.progress.title" :body="HELP.progress.body" size="12px" />
          </span>
          <van-tag
            size="medium"
            :type="poolGrew ? 'primary' : 'default'"
            @click="openGoalModal"
            style="cursor: pointer;"
          >
            {{ examGoal ? (poolGrew ? '題庫已更新，重設目標' : '修改目標') : '設定目標' }}
          </van-tag>
        </div>
        <div v-if="examGoal" class="goal-line">
          目標：{{ examGoal.target_date }} 完成 {{ examGoal.target_count }} 題
          <span v-if="daysToTarget !== null && daysToTarget >= 0">（{{ daysToTarget }} 天後）</span>
        </div>

        <div class="mini-grid">
          <div :class="['mini-cell', progressTone(sourceMeta().answered_at_least_once / Math.max(1, sourceMeta().total_questions))]">
            <div class="mini-num">{{ ((sourceMeta().answered_at_least_once / Math.max(1, sourceMeta().total_questions)) * 100).toFixed(0) }}%</div>
            <div class="mini-lbl">總進度</div>
            <div class="mini-aux">{{ sourceMeta().answered_at_least_once }}/{{ sourceMeta().total_questions }}</div>
          </div>

          <div :class="['mini-cell', todayNewTone()]">
            <div class="mini-num">{{ overview.new_questions_today }}</div>
            <div class="mini-lbl">今日新題</div>
            <div class="mini-aux">
              <template v-if="requiredNewPerDay !== null">目標 {{ requiredNewPerDay }} 題</template>
              <template v-else>未設目標</template>
            </div>
          </div>

          <div :class="['mini-cell', accTone(sourceMeta().accuracy)]">
            <div class="mini-num">{{ fmtPct(sourceMeta().accuracy) }}</div>
            <div class="mini-lbl">整體正確率</div>
            <div class="mini-aux" v-if="sourceMeta().accuracy_7d !== null">
              近 7d {{ fmtPct(sourceMeta().accuracy_7d) }}
            </div>
            <div class="mini-aux" v-else>近 7d —</div>
          </div>

          <div :class="['mini-cell', accTone(overview.first_attempt_accuracy)]">
            <div class="mini-num">{{ fmtPct(overview.first_attempt_accuracy) }}</div>
            <div class="mini-lbl">初答正確率</div>
            <div class="mini-aux"></div>
          </div>

          <div :class="['mini-cell', accTone(overview.retention_30d)]">
            <div class="mini-num">{{ fmtPct(overview.retention_30d) }}</div>
            <div class="mini-lbl">再答正確率</div>
            <div class="mini-aux"></div>
          </div>
        </div>

        <div v-if="advice" :class="['advice-row', advice.tone]">
          <span class="advice-icon">{{ advice.icon }}</span>
          <span class="advice-text">{{ advice.text }}</span>
        </div>

        <div class="advanced-row">
          <span class="dim">L {{ overview.learning_count }} ／ R {{ overview.review_count }} ／ RL {{ overview.relearning_count }}</span>
          <InfoTooltip v-if="helpMode" :title="HELP.fsrsState.title" :body="HELP.fsrsState.body" size="11px" />
        </div>
      </div>

      <!-- ② 到期分布 bar chart -->
      <div class="big-card">
        <div class="big-card-head">
          <span class="big-card-title">
            到期分布
            <InfoTooltip v-if="helpMode" :title="HELP.overdue.title" :body="HELP.overdue.body" size="12px" />
          </span>
        </div>
        <div class="due-chart">
          <div v-for="(c, idx) in dueChart" :key="idx" class="due-bar-col" :class="{ today: c.isToday }">
            <div class="due-bar-num">{{ c.count }}</div>
            <div class="due-bar-track">
              <div class="due-bar-fill" :style="{ height: ((c.count / dueChartMax) * 100) + '%' }"></div>
            </div>
            <div class="due-bar-lbl">{{ c.label }}</div>
            <div v-if="c.date" class="due-bar-date">{{ c.date }}</div>
          </div>
        </div>
        <div v-if="overview.overdue_7plus > 0" class="overdue-warn">
          ⚠ 其中 {{ overview.overdue_7plus }} 張過期 ≥ 7 天，FSRS 估計失準，建議優先消化
        </div>
      </div>
    </div>

    <!-- ③ Tag list with optional 夥伴 overlay -->
    <van-cell-group inset title="各領域進度與正確率">
      <div class="cohort-toggle-row">
        <van-checkbox v-model="showCohort" shape="square">
          <span style="font-size: 13px;">夥伴進度</span>
        </van-checkbox>
        <InfoTooltip v-if="helpMode" :title="HELP.cohort.title" :body="HELP.cohort.body" />
      </div>
      <div v-if="helpMode" class="legend">
        深灰 = 已練比例 ｜ 彩色 = 答對比例
        <span v-if="showCohort">｜ 細虛線 = 夥伴</span>
      </div>
      <div v-for="t in tagStats" :key="t.tag_id" class="tag-block">
        <div class="tag-head">
          <span class="tag-name" :title="t.tag_full_name">
            {{ t.tag_name }}<span v-if="t.tag_short_zh" class="tag-zh"> {{ t.tag_short_zh }}</span>
          </span>
          <span class="tag-meta">
            <span v-if="t.attempted_count">
              <b>{{ (t.accuracy * 100).toFixed(0) }}%</b>
              <span class="dim">({{ t.attempted_count }}/{{ t.question_count }})</span>
            </span>
            <span v-else class="dim">未答 (0/{{ t.question_count }})</span>
          </span>
        </div>
        <div class="tag-bar-wrap">
          <div class="tag-bar">
            <div class="tag-seen" :style="{ width: (t.seen_ratio * 100) + '%' }">
              <div class="tag-correct" :style="{
                width: t.accuracy === null ? '0%' : (t.accuracy * 100) + '%',
                background: colorFor(t.accuracy)
              }"></div>
            </div>
          </div>
          <!-- Two ticks (cov + acc) when meaningful cohort accuracy (≥3 peers). -->
          <template v-if="showCohort && t.cohort_accuracy !== null && t.cohort_seen_ratio !== null">
            <div class="cohort-tick cohort-cov" :style="{ left: (t.cohort_seen_ratio * 100) + '%' }"
                 :title="`夥伴已練 ${(t.cohort_seen_ratio*100).toFixed(0)}%`"></div>
            <div class="cohort-tick cohort-acc"
                 :style="{ left: (t.cohort_seen_ratio * t.cohort_accuracy * 100) + '%' }"
                 :title="`夥伴正確 ${(t.cohort_accuracy*100).toFixed(0)}% (n=${t.cohort_n})`"></div>
          </template>
        </div>
        <div v-if="showCohort && t.cohort_accuracy !== null" class="cohort-line">
          夥伴 <span class="dim">(n={{ t.cohort_n }})</span>：已練 {{ (t.cohort_seen_ratio*100).toFixed(0) }}%
          <span v-if="t.cohort_seen_ratio !== null"
                class="cohort-diff"
                :class="(t.seen_ratio - t.cohort_seen_ratio) >= 0 ? 'good' : 'bad'">
            (你{{ (t.seen_ratio - t.cohort_seen_ratio) >= 0 ? '+' : '' }}{{ ((t.seen_ratio - t.cohort_seen_ratio) * 100).toFixed(1) }}%)
          </span>
          <span class="cohort-sep">｜</span>
          正確 {{ (t.cohort_accuracy*100).toFixed(0) }}%
          <span v-if="t.accuracy !== null"
                class="cohort-diff"
                :class="(t.accuracy - t.cohort_accuracy) >= 0 ? 'good' : 'bad'">
            (你{{ t.accuracy >= t.cohort_accuracy ? '+' : '' }}{{ ((t.accuracy - t.cohort_accuracy) * 100).toFixed(1) }}%)
          </span>
        </div>
        <div v-else-if="showCohort" class="cohort-line dim">
          樣本不足（需 ≥ 3 人）
        </div>
      </div>
    </van-cell-group>
    <div style="height: 40px;"></div>
  </div>

  <!-- Goal-setting popup -->
  <van-popup v-model:show="goalModalShow" position="bottom" round :style="{ padding: '20px 16px 28px', maxHeight: '90vh', overflow: 'auto' }">
    <h3 style="margin: 0 0 12px;">設定考前目標</h3>
    <div class="goal-form">
      <div class="goal-block">
        <div class="goal-block-head">
          <span class="goal-lbl">目標日</span>
          <span class="goal-block-val">{{ goalDate || '—' }}</span>
        </div>
        <van-button size="small" plain @click="goalDateShow = true" block>
          {{ goalDate ? '更換日期' : '選擇日期' }}
        </van-button>
      </div>

      <div class="goal-block">
        <div class="goal-block-head">
          <span class="goal-lbl">目標題數</span>
        </div>
        <div class="goal-count-row">
          <input
            type="number"
            class="goal-num-input-big"
            v-model.number="goalCount"
            :min="1"
            :max="sourceMeta()?.total_questions || 1000"
          />
          <span class="goal-count-divider">/</span>
          <span class="goal-count-max">{{ sourceMeta()?.total_questions || 0 }}</span>
        </div>
      </div>

      <div class="goal-hint">
        <span v-if="goalDate">
          距離 {{ goalDate }} 約
          <b>{{ Math.ceil((new Date(goalDate + 'T00:00:00') - new Date()) / 86400000) }} 天</b>，
          每日需
          <b>{{ requiredNewPerDay ?? '—' }} 題</b>
        </span>
      </div>
      <div class="goal-actions">
        <van-button v-if="examGoal" size="small" type="danger" plain @click="clearGoal">清除目標</van-button>
        <span style="flex: 1"></span>
        <van-button size="small" plain @click="goalModalShow = false">取消</van-button>
        <van-button size="small" type="primary" @click="saveGoal">儲存</van-button>
      </div>
    </div>
  </van-popup>

  <van-calendar
    v-model:show="goalDateShow"
    :min-date="minDate"
    :max-date="maxDate"
    :default-date="goalDate ? new Date(goalDate + 'T00:00:00') : minDate"
    title="選擇目標日"
    color="#1989fa"
    @confirm="(d) => { goalDate = toLocalYMD(d); goalDateShow = false }"
  />
</template>

<style scoped>
.help-banner {
  background: #e8f3ff; color: #1989fa;
  font-size: 12px; padding: 6px 16px;
  border-bottom: 1px solid #c8dfff;
}
.overview-pad { padding: 12px 16px 4px; }
.db-stamp { font-size: 11px; color: #aaa; text-align: right; margin-bottom: 6px; }

.big-card {
  background: #fff; border-radius: 10px; padding: 12px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  margin-bottom: 12px;
}
.big-card-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
.big-card-title { font-size: 14px; font-weight: 600; color: #333; display: inline-flex; align-items: center; gap: 4px; }
.goal-line { font-size: 12px; color: #555; margin-bottom: 8px; padding: 4px 8px; background: #f5f8ff; border-radius: 4px; }


.mini-grid {
  display: grid; grid-template-columns: repeat(5, 1fr); gap: 4px;
  margin-bottom: 8px;
}
.mini-cell {
  padding: 8px 4px; border-radius: 6px; text-align: center;
  display: flex; flex-direction: column; justify-content: center;
  min-height: 64px;
}
/* Tonal palette — consistent with advice-row; warm = good→bad spectrum,
   cool grey = neutral / not-yet-set. */
.mini-cell.good    { background: #e8f7ee; color: #07734a; }
.mini-cell.mid     { background: #fff7d6; color: #9a7300; }
.mini-cell.warn    { background: #ffe5cd; color: #a84500; }
.mini-cell.bad     { background: #ffe2e2; color: #b3120c; }
.mini-cell.neutral { background: #f0f1f3; color: #555; }
.mini-cell.empty   { background: #f5f5f5; color: #aaa; }
.mini-num { font-size: 18px; font-weight: 700; line-height: 1.1; }
.mini-lbl { font-size: 10px; color: #555; margin-top: 4px; line-height: 1.2; display: inline-flex; align-items: center; gap: 2px; justify-content: center; }
.mini-cell.good .mini-lbl, .mini-cell.mid .mini-lbl, .mini-cell.warn .mini-lbl, .mini-cell.bad .mini-lbl, .mini-cell.neutral .mini-lbl { color: inherit; opacity: 0.85; }
.mini-aux { font-size: 9px; opacity: 0.7; margin-top: 2px; line-height: 1.1; }

.advice-row {
  display: flex; gap: 6px; align-items: flex-start;
  padding: 8px; border-radius: 6px;
  font-size: 12px; line-height: 1.5;
  margin-bottom: 6px;
}
.advice-row.good { background: #e8f7ee; color: #07734a; }
.advice-row.mid  { background: #f0f8ff; color: #1959b8; }
.advice-row.warn { background: #fff7e8; color: #b76b00; }
.advice-row.bad  { background: #ffe8e8; color: #c2150e; }
.advice-row.info { background: #f0f4f9; color: #555; }
.advice-icon { font-weight: 700; }
.advice-text { flex: 1; }

.advanced-row { font-size: 10px; color: #999; display: flex; align-items: center; gap: 4px; padding: 0 4px; }

.dim { color: #aaa; }

/* due chart — today bar uses warn tone, future use cool grey-blue */
.due-chart {
  display: flex; align-items: flex-end; gap: 4px; height: 130px;
  padding: 0 4px;
}
.due-bar-col { flex: 1; display: flex; flex-direction: column; align-items: center; height: 100%; }
.due-bar-col.today .due-bar-fill { background: linear-gradient(180deg, #ffb37e, #ffe5cd); }
.due-bar-num { font-size: 12px; font-weight: 600; color: #333; margin-bottom: 2px; min-height: 16px; }
.due-bar-track { flex: 1; width: 100%; display: flex; align-items: flex-end; }
.due-bar-fill { width: 100%; background: linear-gradient(180deg, #b4cee3, #dfeaf5); border-radius: 3px 3px 0 0; transition: height 0.3s; min-height: 2px; }
.due-bar-lbl { font-size: 10px; color: #666; margin-top: 4px; }
.due-bar-date { font-size: 9px; color: #aaa; }
.overdue-warn { font-size: 11px; color: #a84500; padding: 6px 8px; margin-top: 8px; background: #ffe5cd; border-radius: 4px; }

/* tags (mostly unchanged) */
.cohort-toggle-row { padding: 6px 16px 4px; display: flex; align-items: center; gap: 6px; }
.legend { padding: 0 16px 8px; font-size: 12px; color: #888; }
.legend-cohort { color: #6f42c1; }
.tag-block { padding: 6px 16px; }
.tag-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
.tag-name { font-size: 13px; }
.tag-zh { color: #888; font-size: 12px; }
.tag-meta { font-size: 12px; }
.tag-bar-wrap { position: relative; height: 16px; }
.tag-bar { height: 16px; background: #f1f1f1; border-radius: 8px; overflow: hidden; }
.tag-seen { height: 100%; background: #888; transition: width 0.3s; }
.tag-correct { height: 100%; transition: width 0.3s; }
.cohort-tick {
  position: absolute; top: -2px; bottom: -2px;
  width: 0; pointer-events: auto;
}
.cohort-tick.cohort-cov { border-left: 2px dashed #b9a4dc; }   /* light purple */
.cohort-tick.cohort-acc { border-left: 2px dashed #f5b8a3; }   /* light peach */
.cohort-line {
  font-size: 11px; color: #333; margin-top: 4px;
  display: flex; gap: 4px; align-items: center; flex-wrap: wrap;
}
.cohort-line .dim { color: #aaa; }
.cohort-sep { color: #ccc; margin: 0 2px; }
.cohort-diff.good { color: #07c160; font-weight: 600; }
.cohort-diff.bad { color: #ee0a24; font-weight: 600; }

.src-bar {
  padding: 6px 16px; display: flex; align-items: center; gap: 8px;
  background: white;
}
.src-lbl { font-size: 13px; color: #666; }
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

.goal-form { display: flex; flex-direction: column; gap: 16px; }
.goal-block { padding: 12px; background: #f7f8fa; border-radius: 8px; }
.goal-block-head { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 10px; }
.goal-lbl { font-size: 13px; color: #666; }
.goal-block-val { font-size: 14px; font-weight: 600; color: #1989fa; }
.goal-count-row {
  display: flex; align-items: baseline; gap: 8px;
  padding: 4px 4px 0;
}
.goal-num-input-big {
  width: 100px; padding: 6px 8px; border: 1px solid #ddd; border-radius: 6px;
  font-size: 18px; font-weight: 600; color: #1989fa; text-align: center;
}
.goal-num-input-big:focus { outline: none; border-color: #1989fa; }
.goal-count-divider { font-size: 16px; color: #aaa; }
.goal-count-max { font-size: 16px; color: #888; }
.quick-set-row { display: flex; gap: 6px; margin-top: 10px; }
.quick-set-row :deep(.van-tag) { cursor: pointer; padding: 3px 10px; }
.goal-hint {
  font-size: 12px; color: #555; line-height: 1.6;
  background: #fff8e1; padding: 8px 10px; border-radius: 4px;
  border-left: 3px solid #ffd21e;
}
.goal-actions { display: flex; gap: 8px; align-items: center; margin-top: 4px; }
.kd-row {
  margin: 12px 16px;
  padding: 12px;
  background: white;
  border-radius: 8px;
  box-shadow: 0 1px 3px rgba(0,0,0,0.06);
  cursor: pointer;
}
.kd-row-head { display: flex; justify-content: space-between; margin-bottom: 8px; }
.kd-row-title { font-weight: 600; color: #333; font-size: 14px; }
.kd-row-last { font-size: 12px; color: #888; }
.kd-row-body { display: flex; gap: 16px; align-items: stretch; }
/* 1/4 tile — reuses mini-cell tone classes for background/color */
.kd-tile {
  flex: 1; min-width: 0;
  display: flex; flex-direction: column; justify-content: center; align-items: center;
  text-align: center;
  border-radius: 8px;
  padding: 10px 6px;
  min-height: 72px;
}
/* Tone classes applied from progressTone() — same as mini-cell palette */
.kd-tile.good    { background: #e8f7ee; color: #07734a; }
.kd-tile.mid     { background: #fff7d6; color: #9a7300; }
.kd-tile.warn    { background: #ffe5cd; color: #a84500; }
.kd-tile.bad     { background: #ffe2e2; color: #b3120c; }
.kd-tile .mini-num { font-size: 22px; font-weight: 700; line-height: 1.1; }
.kd-tile .mini-lbl { font-size: 11px; opacity: 0.85; margin-top: 4px; }
.kd-tile .mini-aux { font-size: 12px; opacity: 0.7; margin-top: 2px; }
/* 3/4 bar chart */
.kd-mastery { flex: 3; min-width: 0; display: flex; flex-direction: column; }
/* number labels row above bar */
.kd-mast-labels { display: flex; height: 16px; margin-bottom: 2px; }
.kd-mast-label {
  display: flex; align-items: flex-end; justify-content: center;
  font-size: 11px; color: #555; font-weight: 500; min-width: 0;
}
/* bar chart header */
.kd-mastery-title { margin-bottom: 6px; }
/* bar segments */
.kd-mast-bars { display: flex; height: 20px; border-radius: 4px; overflow: hidden; background: #eee; }
.kd-mast-bar { min-width: 0; }
.kd-mast-bar.m3 { background: #07c160; }
.kd-mast-bar.m2 { background: #ff976a; }
.kd-mast-bar.m1 { background: #ee0a24; }
.kd-mast-bar.mn { background: #bbb; }
/* legend */
.kd-mast-legend { display: flex; gap: 8px; margin-top: 6px; font-size: 11px; color: #666; flex-wrap: wrap; }
.kd-mast-legend .dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 3px; vertical-align: middle; }
.kd-mast-legend .dot.m3 { background: #07c160; }
.kd-mast-legend .dot.m2 { background: #ff976a; }
.kd-mast-legend .dot.m1 { background: #ee0a24; }
.kd-mast-legend .dot.mn { background: #bbb; }
</style>
