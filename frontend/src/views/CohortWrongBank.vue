<script setup>
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api, { SOURCE_LABEL } from '../api.js'
import { useAuthStore } from '../stores/auth.js'
import InfoTooltip from '../components/InfoTooltip.vue'
import HelpButton from '../components/HelpButton.vue'
import { useHelpMode } from '../composables/useHelpMode.js'

const { helpMode } = useHelpMode()

const COHORT_HELP = '**這頁是什麼？**\n全體使用者最常答錯的題目排行，幫你找出大家普遍會踩的雷，提前補強。\n\n**怎麼看？**\n• 「初答錯 X%」= 第一次作答就答錯的人比例（越高越難）\n• 「再答錯 X%」= 練過後再答仍錯的比例（高=容易反覆栽）\n• 卡片上若出現「風險／R／我錯幾次」是針對你自己算的\n\n按一下卡片可看完整題目與詳解。'

const router = useRouter()
const auth = useAuthStore()
const isAdmin = computed(() => !!auth.user?.is_admin)

const cards = ref([])
const tags = ref([])
const loading = ref(false)
const tagId = ref('')
const sort = ref('wrong_pct')
const wrongPctMin = ref(0)        // 「初答錯誤率 ≥ x%」filter（前端）
const reWrongPctMin = ref(0)      // 「再答錯誤率 ≥ x%」filter（前端）
const isPublic = ref(true)
const showAllOpts = ref(true)     // 全域：預設展開所有選項
const page = ref(1)
const PAGE_SIZE = 20

const SORT_OPTIONS = [
  { value: 'wrong_pct', label: '初答錯誤率' },
  { value: 're_pct',    label: '再答錯誤率' },
  { value: 'tag',       label: '領域' }
]
const PCT_OPTIONS = [
  { value: 0,  label: '不限' },
  { value: 50, label: '≥50%' },
  { value: 70, label: '≥70%' }
]
const RE_PCT_OPTIONS = [
  { value: 0,  label: '不限' },
  { value: 10, label: '≥10%' },
  { value: 20, label: '≥20%' }
]
const tagPickerShow = ref(false)

function tagLabel (t) {
  const num = String(t.sort_order ?? '').padStart(2, '0')
  return t.short_zh ? `${num} ${t.name} (${t.short_zh})` : `${num} ${t.name}`
}
function selectedTagName () {
  const t = tags.value.find(x => x.id === tagId.value)
  return t ? tagLabel(t) : '不限'
}
function srcLabel (code) { return SOURCE_LABEL?.[code] || code }
function fmtPct (v) { return (v === null || v === undefined) ? '—' : Math.round(v * 100) + '%' }
function categoryBadgeColor (cat) {
  return { lapse: '#ee0a24', forget: '#ff976a', blind: '#ffd21e', recent: '#888' }[cat] || '#bbb'
}
function categoryBadgeLabel (cat) {
  return { lapse: '死穴', forget: '易忘', blind: '盲區', recent: '新錯' }[cat] || cat
}

// 前端 filter（初答錯誤率）+ sort（含領域）；門檻（≥3 人初答錯）後端已固定
const filteredCards = computed(() => {
  let list = cards.value.filter(c =>
    c.wrong_pct >= wrongPctMin.value && c.re_pct >= reWrongPctMin.value)
  if (sort.value === 're_pct') {
    list = [...list].sort((a, b) => b.re_pct - a.re_pct || b.wrong_pct - a.wrong_pct)
  } else if (sort.value === 'tag') {
    list = [...list].sort((a, b) =>
      (a.tags[0] || '~').localeCompare(b.tags[0] || '~', 'zh-Hant') ||
      b.wrong_pct - a.wrong_pct)
  } else {
    list = [...list].sort((a, b) => b.wrong_pct - a.wrong_pct || b.re_pct - a.re_pct)
  }
  return list
})
const totalPages = computed(() => Math.max(1, Math.ceil(filteredCards.value.length / PAGE_SIZE)))
const pagedCards = computed(() =>
  filteredCards.value.slice((page.value - 1) * PAGE_SIZE, page.value * PAGE_SIZE)
)

async function loadMeta () {
  try {
    const r = await api.get('/meta/db-info')
    isPublic.value = !!r.data?.cohort_wrongbook_public
  } catch (_) { /* default true */ }
}
async function loadTags () {
  try {
    const r = await api.get('/tags')
    tags.value = r.data.filter(x => x.level === 'major')
  } catch (_) { /* optional */ }
}
async function load () {
  loading.value = true
  try {
    const params = { sort: 'wrong_pct', source_code: 'past_paper' }
    if (tagId.value) params.tag_id = tagId.value
    const r = await api.get('/wrong-bank/cohort', { params })
    cards.value = r.data
  } catch (e) {
    if (e.response?.status === 403) { showToast('此功能尚未開放'); router.push('/'); return }
    showToast(e.response?.data?.detail || '載入失敗')
  } finally { loading.value = false }
}
// 返回時還原頁碼/捲動位置（僅限「從題目返回」這一次）
const STORAGE_KEY = 'cohortWrongState'
const restoring = ref(false)
function saveState () {
  try {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({
      tagId: tagId.value, sort: sort.value,
      wrongPctMin: wrongPctMin.value, reWrongPctMin: reWrongPctMin.value,
      showAllOpts: showAllOpts.value, page: page.value, scrollY: window.scrollY
    }))
  } catch (_) { /* sessionStorage unavailable */ }
}
function viewQuestion (qid) { saveState(); router.push(`/question/${qid}`) }

// 多選 + 打星
const selectMode = ref(false)
const selectedQids = ref(new Set())
const busy = ref(false)
function toggleSelect (qid) {
  if (selectedQids.value.has(qid)) selectedQids.value.delete(qid)
  else selectedQids.value.add(qid)
  selectedQids.value = new Set(selectedQids.value)
}
function selectAllPage () { selectedQids.value = new Set([...selectedQids.value, ...pagedCards.value.map(c => c.question_id)]) }
function clearSelection () { selectedQids.value = new Set() }
function enterSelectMode () { selectMode.value = true }
function exitSelectMode () { selectMode.value = false; clearSelection() }
async function bulkStar (action) {
  const ids = Array.from(selectedQids.value)
  if (!ids.length) { showToast('沒有選取的題目'); return }
  busy.value = true
  try {
    await api.post('/wrong-bank/bulk', { question_ids: ids, action })
    const starred = action === 'star'
    cards.value.forEach(c => { if (selectedQids.value.has(c.question_id)) c.is_starred = starred })
    showToast(`已${starred ? '加星' : '取消星'} ${ids.length} 題`)
    clearSelection()
  } catch (e) {
    showToast(e.response?.data?.detail || '操作失敗')
  } finally { busy.value = false }
}

onMounted(async () => {
  let saved = null
  try { saved = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || 'null') } catch (_) { /* */ }
  if (saved) {
    restoring.value = true
    tagId.value = saved.tagId ?? ''
    sort.value = saved.sort ?? 'wrong_pct'
    wrongPctMin.value = saved.wrongPctMin ?? 0
    reWrongPctMin.value = saved.reWrongPctMin ?? 0
    showAllOpts.value = !!saved.showAllOpts
    sessionStorage.removeItem(STORAGE_KEY)   // 還原一次即清（只針對返回）
  }
  await Promise.all([loadMeta(), loadTags(), load()])
  if (saved) {
    page.value = saved.page ?? 1
    await nextTick()
    window.scrollTo(0, saved.scrollY || 0)
    restoring.value = false
  }
})
watch(tagId, () => { if (!restoring.value) load() })
watch([tagId, wrongPctMin, reWrongPctMin, sort], () => { if (!restoring.value) page.value = 1 })

const HELP = {
  firstwrong: {
    title: '「初答錯 X%」是什麼意思？',
    body:
      '所有做過這題的人，第一次作答就答錯的比例。\n\n' +
      '數字越高 = 這題越多人一看就被坑 = 越值得注意。\n\n' +
      '只算第一次，所以反覆練習的正確不會把難度沖淡。'
  },
  risk: {
    title: '卡片上的「風險」代表什麼？',
    body:
      '這題「你自己」考試時再答錯的可能性，值越高越該複習。\n\n' +
      '系統依你已忘多少、過去錯幾次、有沒有「自以為會卻錯」綜合算出。'
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
      '系統幫你把錯題分四類：\n\n' +
      '🔴 死穴：你已答錯 ≥ 2 次\n' +
      '🟠 易忘：學過但快忘了\n' +
      '🟡 盲區：很有把握卻答錯（最危險）\n' +
      '⚪ 新錯：最近第一次答錯'
  }
}
</script>

<template>
  <van-nav-bar title="大家的錯題本" left-text="首頁" left-arrow @click-left="$router.push('/')">
    <template #right>
      <HelpButton title="大家的錯題本說明" :content="COHORT_HELP" :size="18" />
    </template>
  </van-nav-bar>

  <div v-if="helpMode" class="help-banner">
    💡 初答錯誤率<InfoTooltip :title="HELP.firstwrong.title" :body="HELP.firstwrong.body" /> 是全體的；
    風險<InfoTooltip :title="HELP.risk.title" :body="HELP.risk.body" /> /
    R<InfoTooltip :title="HELP.retrievability.title" :body="HELP.retrievability.body" /> /
    失敗模式<InfoTooltip :title="HELP.categories.title" :body="HELP.categories.body" /> 是你自己的
  </div>

  <div class="filters">
    <div class="filter-row">
      <span class="lbl">領域</span>
      <van-tag size="medium" :type="!tagId ? 'primary' : 'default'" @click="tagId = ''">全部</van-tag>
      <van-tag size="medium" type="default" @click="tagPickerShow = true">
        {{ selectedTagName() === '不限' ? '選領域...' : selectedTagName() }}
      </van-tag>
    </div>
    <div class="filter-row">
      <span class="lbl">初答錯誤率</span>
      <van-tag
        v-for="o in PCT_OPTIONS" :key="o.value"
        size="medium" :type="wrongPctMin === o.value ? 'primary' : 'default'"
        @click="wrongPctMin = o.value"
      >{{ o.label }}</van-tag>
    </div>
    <div class="filter-row">
      <span class="lbl">再答錯誤率</span>
      <van-tag
        v-for="o in RE_PCT_OPTIONS" :key="o.value"
        size="medium" :type="reWrongPctMin === o.value ? 'primary' : 'default'"
        @click="reWrongPctMin = o.value"
      >{{ o.label }}</van-tag>
    </div>
    <div class="filter-row">
      <span class="lbl">排序</span>
      <van-tag
        v-for="o in SORT_OPTIONS" :key="o.value"
        size="medium" :type="sort === o.value ? 'primary' : 'default'"
        @click="sort = o.value"
      >{{ o.label }}</van-tag>
    </div>
  </div>

  <van-popup v-model:show="tagPickerShow" position="bottom" round :style="{ height: '60%' }">
    <div style="padding: 12px 16px; font-weight: bold;">選擇大領域</div>
    <van-cell title="不限" is-link @click="tagId = ''; tagPickerShow = false" />
    <van-cell v-for="t in tags" :key="t.id" :title="tagLabel(t)" is-link @click="tagId = t.id; tagPickerShow = false" />
  </van-popup>

  <van-loading v-if="loading" style="margin: 16px auto; display:block;" />

  <div v-else>
    <div class="counter-row">
      <span class="counter">共 {{ filteredCards.length }} 題</span>
      <van-button size="mini" :type="showAllOpts ? 'primary' : 'default'" plain @click="showAllOpts = !showAllOpts">
        {{ showAllOpts ? '收合選項' : '顯示所有選項' }}
      </van-button>
      <template v-if="!selectMode">
        <van-button size="mini" type="primary" plain @click="enterSelectMode" :disabled="!filteredCards.length">選擇</van-button>
      </template>
      <template v-else>
        <span class="select-info" v-if="selectedQids.size">已選 {{ selectedQids.size }}</span>
        <van-button size="mini" plain @click="selectAllPage">本頁全選</van-button>
        <van-button size="mini" plain @click="clearSelection" :disabled="!selectedQids.size">清除</van-button>
        <van-button size="mini" plain @click="exitSelectMode">取消</van-button>
      </template>
    </div>

    <div v-if="!filteredCards.length" style="padding: 32px; text-align: center; color: #888;">
      沒有符合條件的常錯題
    </div>
    <div v-else class="card-grid">
      <div
        v-for="c in pagedCards" :key="c.question_id"
        class="card" :class="{ selected: selectMode && selectedQids.has(c.question_id) }"
      >
        <div class="card-row">
          <van-checkbox
            v-if="selectMode"
            :model-value="selectedQids.has(c.question_id)"
            @click.stop="toggleSelect(c.question_id)"
            shape="square" style="margin-right: 8px; align-self: flex-start;"
          />
          <div class="card-body" @click="selectMode ? toggleSelect(c.question_id) : viewQuestion(c.question_id)">
            <div class="meta-line">
              <!-- 全體：領域(灰) + 初答錯誤率 -->
              <span v-for="t in c.tags" :key="t" class="chip">{{ t }}</span>
              <span v-if="c.question_label" class="chip">{{ c.question_label }}</span>
              <span class="chip rate">初答錯 {{ c.wrong_pct }}%</span>
              <span class="chip rate2">再答錯 {{ c.has_re ? c.re_pct + '%' : '—' }}</span>
              <!-- 個人（| 分隔） -->
              <template v-if="c.personal_seen || c.is_starred">
                <span class="divider">|</span>
                <span v-if="c.is_starred" class="chip star">★</span>
                <template v-if="c.personal_seen">
                  <span class="chip risk">風險 {{ c.personal_risk?.toFixed(2) }}</span>
                  <span class="chip">R {{ fmtPct(c.personal_r_now) }}</span>
                  <span v-if="c.personal_lapses > 0" class="chip">我錯 {{ c.personal_lapses }} 次</span>
                  <span v-if="c.personal_conf3 > 0" class="chip blind">信心高卻錯 {{ c.personal_conf3 }}</span>
                  <span
                    v-for="cat in c.personal_categories" :key="cat"
                    class="cat-badge" :style="{ background: categoryBadgeColor(cat) }"
                  >{{ categoryBadgeLabel(cat) }}</span>
                </template>
              </template>
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
            <div v-if="c.note" class="card-note">📝 {{ c.note }}</div>
          </div>
        </div>
      </div>

      <van-pagination
        v-if="totalPages > 1"
        v-model="page" :total-items="filteredCards.length" :items-per-page="PAGE_SIZE"
        :show-page-size="5" force-ellipses prev-text="上一頁" next-text="下一頁"
        style="margin: 12px 0 80px;"
      />
    </div>
  </div>

  <div v-if="selectMode && selectedQids.size" class="bulk-bar">
    <van-button size="small" type="warning" :loading="busy" @click="bulkStar('star')">★ 加星</van-button>
    <van-button size="small" plain :loading="busy" @click="bulkStar('unstar')">取消星</van-button>
  </div>
</template>

<style scoped>
.preview-banner {
  position: sticky; top: 0; z-index: 1500;
  background: #fff3cd; color: #8a6d00;
  padding: 8px 12px; font-size: 12px; line-height: 1.4;
  border-bottom: 1px solid #ffe69c;
}
.help-banner { background: #e8f3ff; color: #1989fa; font-size: 12px; padding: 8px 16px; line-height: 1.7; border-bottom: 1px solid #c8dfff; }
.intro { padding: 12px 16px; font-size: 13px; color: #666; background: #fff; line-height: 1.6; }
.filters { padding: 8px 16px 12px; background: white; border-bottom: 1px solid #f0f0f0; }
.filter-row { display: flex; align-items: center; gap: 8px; margin-bottom: 8px; flex-wrap: wrap; }
.filter-row .lbl { width: 75px; flex-shrink: 0; white-space: nowrap; font-size: 13px; color: #666; }
.filter-row :deep(.van-tag) { cursor: pointer; padding: 4px 10px; }

.counter-row { padding: 12px 16px; display: flex; align-items: center; gap: 8px; color: #666; font-size: 13px; flex-wrap: wrap; }
.counter-row .counter { flex: 1; }
.counter-row .select-info { color: #1989fa; font-weight: bold; }

.card-grid { padding: 0 12px 80px; }
.card { background: white; padding: 12px; margin-bottom: 12px; border-radius: 10px; box-shadow: 0 2px 6px rgba(0,0,0,0.05); }
.card.selected { background: #e6f4ff; }
.card-row { display: flex; }
.card-body { flex: 1; cursor: pointer; }

.card-stats { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; font-size: 11px; margin-bottom: 6px; }
.stat-pill { padding: 2px 6px; border-radius: 4px; }
.stat-pill.wrong { background: #ffe8e8; color: #ee0a24; font-weight: 600; }
.stat-pill.pct { background: #fff8e1; color: #b78500; }
.stat-pill.att { background: #f1f1f1; color: #666; }

.tag-row { margin-bottom: 8px; display: flex; gap: 4px; flex-wrap: wrap; align-items: center; }
.badge { font-size: 11px; padding: 2px 6px; border-radius: 4px; background: #f0f0f0; color: #555; }
.badge.star { background: #ffd21e; color: #333; font-weight: bold; }
.badge.year { background: #07c160; color: white; }
.badge.src { background: #8a8a8a; color: white; }
.badge.tag { background: #1989fa; color: white; }
.divider { color: #ccc; font-weight: bold; margin: 0 2px; }
.meta-pill { font-size: 11px; background: #f1f1f1; color: #666; padding: 2px 6px; border-radius: 4px; }
.meta-pill.risk { background: #ffe8e8; color: #ee0a24; }
.meta-pill.blind { background: #fff8e1; color: #b78500; }
.cat-badge { font-size: 11px; color: #fff; padding: 2px 6px; border-radius: 4px; font-weight: 500; }
.meta-line { display: flex; flex-wrap: wrap; gap: 4px; align-items: center; margin-bottom: 8px; font-size: 11px; }
.chip { padding: 2px 6px; border-radius: 4px; background: #eee; color: #555; }
.chip.rate { background: #fff1f0; color: #ee0a24; font-weight: 600; }
.chip.rate2 { background: #fff4e5; color: #d46b08; font-weight: 600; }
.chip.star { background: #ffd21e; color: #333; font-weight: bold; }
.chip.risk { background: #ffe8e8; color: #ee0a24; }
.chip.blind { background: #fff8e1; color: #b78500; }

.card-stem {
  font-size: 14px; color: #333; line-height: 1.5;
  display: -webkit-box; -webkit-line-clamp: 4; -webkit-box-orient: vertical; overflow: hidden;
}
.card-answer { margin-top: 10px; padding-top: 8px; border-top: 1px dashed #e5e5e5; font-size: 13px; color: #07c160; }
.ans-letter { font-weight: bold; margin-right: 4px; }
.opt-list { margin-top: 8px; display: flex; flex-direction: column; gap: 4px; }
.opt-item { font-size: 13px; line-height: 1.5; color: #444; padding: 4px 8px; border-radius: 4px; background: #f7f7f7; }
.opt-item.correct { background: #e8f9e8; color: #0a7a2f; font-weight: 500; }
.card-note { margin-top: 8px; padding: 8px; background: #fff8e1; border-left: 3px solid #ffd21e; border-radius: 4px; font-size: 13px; color: #5f4400; white-space: pre-wrap; }

.bulk-bar { position: fixed; bottom: 0; left: 0; right: 0; background: #fff8e6; border-top: 1px solid #ffe58f; padding: 10px 16px; display: flex; gap: 8px; justify-content: center; z-index: 100; }
</style>
