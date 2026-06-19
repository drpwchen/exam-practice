<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api from '../api.js'
import InfoTooltip from '../components/InfoTooltip.vue'
import HelpButton from '../components/HelpButton.vue'
import { useHelpMode } from '../composables/useHelpMode.js'

const { helpMode } = useHelpMode()

const LAB_HELP = '**這頁在做什麼？**\n依你目前的答題狀況，**預估你正式考試的可能得分**（滿分 100）。\n\n**怎麼算的？**\n把考卷拆成「題庫剛好有的題」和「沒看過的新題」，分科用你的正確率與記憶狀況推算，再依各科配分加權成總分。\n\n**重點看什麼？**\n• **複習 vs 不複習**兩條：差距 = 現在開始複習能多拿幾分\n• **最壞打算**：完全不複習、運氣也差時的底線\n\n僅供參考，別焦慮 🙂　按右上「?」後，各欄位旁會出現「?」可看細節。'

const HELP = {
  coverage: {
    title: '「覆蓋率」是什麼？',
    body:
      '預估正式考題裡，有多少比例是你練過的題庫裡有的。\n\n' +
      '拉高 = 假設很多題會原封不動考出來；拉低 = 假設大多是沒看過的新題。\n\n' +
      '不確定就抓保守一點（50–60%）。'
  },
  scoreRate: {
    title: '「得分率」怎麼來的？',
    body:
      '這科你預計能拿到的分數比例。\n\n' +
      '系統用你「練過題的正確率＋考試當天還記得的機率」，加上「沒看過的新題用初答正確率、完全不會就當五選一盲猜 20%」綜合估出。'
  },
  scenario: {
    title: '「複習」和「不複習」差在哪？',
    body:
      '不複習：從現在都不再複習，記憶自然衰減到考試日。\n\n' +
      '複習：假設你照系統的到期進度持續複習到考前，記憶會更牢、考試日記得更多。\n\n' +
      '兩條的差距 = 現在開始複習，預計能多拿幾分。'
  },
  band: {
    title: '保守 / 中位 / 樂觀？',
    body:
      '同一情境下的可能範圍（統計信賴區間）。\n\n' +
      '保守 = 剛好都考你不熟的、運氣也差；\n' +
      '中位 = 最可能的落點；\n' +
      '樂觀 = 反過來，運氣好。\n\n' +
      '某科你練越少，這個範圍會拉得越寬（越不確定）。'
  },
  worst: {
    title: '「最壞打算」代表什麼？',
    body:
      '= 完全不複習 ＋ 運氣偏差的情況下，預估的分數底線。\n\n' +
      '實際成績幾乎不會低於這個數字，可以當心理底線。'
  }
}

const router = useRouter()
const loading = ref(true)
const data = ref(null)
const coverage = ref(0.65)
const passMark = ref(60)
const examDelta = ref(0)

let debounce = null
watch([coverage, examDelta], () => {
  clearTimeout(debounce)
  debounce = setTimeout(load, 250)
})

async function load () {
  loading.value = true
  try {
    const r = await api.get('/lab/score-projection', {
      params: { coverage: coverage.value, pass_mark: passMark.value, exam_delta: examDelta.value }
    })
    data.value = r.data
  } catch (e) {
    if (e.response?.status === 404) {
      showToast('無權限'); router.push('/'); return
    }
    showToast(e.response?.data?.detail || '載入失敗')
  } finally {
    loading.value = false
  }
}

function fp (v) { return (v === null || v === undefined) ? '—' : (v * 100).toFixed(0) + '%' }

// scale 0..100 分 → 百分比寬度
function px (score) { return Math.max(0, Math.min(100, score)) + '%' }

// 固定按官方 1-26 編號排列
const sortedTags = computed(() => {
  if (!data.value) return []
  return [...data.value.tags].sort((a, b) => a.sort_order - b.sort_order)
})

onMounted(load)
</script>

<template>
  <div v-if="helpMode" class="lab-banner">📊 依你的答題狀況預估筆試得分:複習 vs 不複習兩種情境,可調題庫覆蓋比例。僅供參考,別焦慮 🙂</div>
  <van-nav-bar title="預期得分落點" left-text="返回" left-arrow @click-left="$router.push('/')">
    <template #right>
      <HelpButton title="預期得分落點說明" :content="LAB_HELP" :size="18" :style="{ marginRight: '10px' }" />
      <van-icon name="replay" size="18" @click="load" />
    </template>
  </van-nav-bar>

  <van-loading v-if="loading && !data" style="margin:32px auto;display:block;" />

  <div v-if="data" class="wrap">
    <!-- 控制區 -->
    <van-cell-group inset class="ctrl">
      <div class="ctrl-row">
        <span class="ctrl-label">預測實際考題與本考古題庫重疊比例<InfoTooltip v-if="helpMode" :title="HELP.coverage.title" :body="HELP.coverage.body" /></span>
        <span class="ctrl-val">{{ (coverage * 100).toFixed(0) }}%</span>
      </div>
      <van-slider v-model="coverage" :min="0.4" :max="0.9" :step="0.05" active-color="#07c160" />
      <div v-if="helpMode" class="ctrl-hint">
        我們題庫押中真實原題的基準比例。各科再依該科題庫題量自動調整:題目多的科覆蓋高、少的科多為新題。
      </div>

      <div class="ctrl-row" style="margin-top:14px;">
        <span class="ctrl-label">考試 vs 練習正確率</span>
        <span class="ctrl-val">{{ examDelta > 0 ? '+' : '' }}{{ (examDelta * 100).toFixed(0) }}%</span>
      </div>
      <van-slider v-model="examDelta" :min="-0.1" :max="0.1" :step="0.01" active-color="#1989fa" />
      <div v-if="helpMode" class="ctrl-hint">
        考試時正確率相對練習的增減。+ 表示上場更穩、− 表示臨場掉分。預設 0 = 考試等同練習。
      </div>
    </van-cell-group>

    <!-- 總分區間 -->
    <van-cell-group inset class="bands">
      <div class="band-title">預測總分(滿分 100,及格 {{ passMark }})<InfoTooltip v-if="helpMode" :title="HELP.band.title" :body="HELP.band.body" /></div>

      <div class="worst">
        <div class="worst-label">最壞打算<InfoTooltip v-if="helpMode" :title="HELP.worst.title" :body="HELP.worst.body" /></div>
        <div class="worst-num" :class="{ fail: data.total_noreview.lo < passMark }">{{ data.total_noreview.lo }}</div>
        <div class="worst-sub">分(不複習 + 保守估計){{ data.total_noreview.lo < passMark ? ' · 低於及格線' : ' · 仍過及格線' }}</div>
      </div>

      <div class="band-block">
        <div class="band-head"><span class="dot nr"></span>不複習(維持現狀到 {{ data.exam_date }})<InfoTooltip v-if="helpMode" :title="HELP.scenario.title" :body="HELP.scenario.body" /></div>
        <div class="band-track">
          <div class="band-fill nr" :style="{ left: px(data.total_noreview.lo), width: px(data.total_noreview.hi - data.total_noreview.lo) }"></div>
          <div class="band-mid" :style="{ left: px(data.total_noreview.mid) }"></div>
          <div class="pass-line" :style="{ left: px(passMark) }"></div>
        </div>
        <div class="band-nums">
          <span>保守 {{ data.total_noreview.lo }}</span>
          <b>中位 {{ data.total_noreview.mid }}</b>
          <span>樂觀 {{ data.total_noreview.hi }}</span>
        </div>
      </div>

      <div class="band-block">
        <div class="band-head"><span class="dot rv"></span>考前持續複習</div>
        <div class="band-track">
          <div class="band-fill rv" :style="{ left: px(data.total_review.lo), width: px(data.total_review.hi - data.total_review.lo) }"></div>
          <div class="band-mid" :style="{ left: px(data.total_review.mid) }"></div>
          <div class="pass-line" :style="{ left: px(passMark) }"></div>
        </div>
        <div class="band-nums">
          <span>保守 {{ data.total_review.lo }}</span>
          <b>中位 {{ data.total_review.mid }}</b>
          <span>樂觀 {{ data.total_review.hi }}</span>
        </div>
      </div>

      <div class="gain">
        複習可多拿
        <b>{{ (data.total_review.mid - data.total_noreview.mid).toFixed(1) }}</b>
        分(中位)
      </div>
    </van-cell-group>

    <!-- 分科分解 -->
    <van-cell-group inset class="tags">
      <div class="band-title">各科預估得分率<InfoTooltip v-if="helpMode" :title="HELP.scoreRate.title" :body="HELP.scoreRate.body" /></div>
      <div v-if="helpMode" class="legend">橫條長度＝預估得分率(0-100%)　·　深綠=複習後　淺橘=不複習</div>
      <div v-for="t in sortedTags" :key="t.tag_id" class="trow">
        <div class="tname">
          {{ t.tag_short_zh || t.tag_name }}
          <span class="tpts">配分 {{ t.score_points }}</span>
          <span v-if="t.attempts < 5" class="warn">樣本少</span>
        </div>
        <div v-if="t.weight_note" class="wnote">※ {{ t.weight_note }}</div>
        <div class="tbar-bg">
          <div class="tbar-nr" :style="{ width: (t.s_noreview * 100) + '%' }"></div>
          <div class="tbar-rv" :style="{ width: (t.s_review * 100) + '%' }"></div>
          <span class="tbar-pct">{{ (t.s_review * 100).toFixed(0) }}%</span>
        </div>
        <div class="tmeta">
          覆蓋 {{ (t.coverage * 100).toFixed(0) }}%　初答 {{ fp(t.a_first) }}　複習 {{ fp(t.a_master) }}　記憶 {{ fp(t.r_exam) }}　({{ t.seen }}題)
        </div>
      </div>
    </van-cell-group>

    <div style="height:24px;"></div>
  </div>
</template>

<style scoped>
.lab-banner { background:#e8f7ee; color:#07734a; font-size:12px; text-align:center; padding:6px; }
.wrap { padding-bottom:16px; }
.ctrl { margin-top:12px; padding:12px 16px 16px; }
.ctrl-row { display:flex; justify-content:space-between; align-items:baseline; margin-bottom:8px; }
.ctrl-label { font-size:14px; font-weight:600; }
.ctrl-val { font-size:18px; font-weight:700; color:#07c160; }
.ctrl-hint { font-size:11px; color:#999; margin-top:10px; line-height:1.5; }

.bands { margin-top:14px; padding:14px 16px; }
.band-title { font-size:14px; font-weight:700; margin-bottom:12px; display:flex; align-items:center; }
.worst { background:#fff5f3; border:1px solid #ffd9c2; border-radius:8px; padding:10px 14px; margin-bottom:16px; text-align:center; }
.worst-label { font-size:12px; color:#888; display:flex; align-items:center; justify-content:center; }
.worst-num { font-size:34px; font-weight:800; line-height:1.1; color:#07c160; }
.worst-num.fail { color:#ee0a24; }
.worst-sub { font-size:11px; color:#999; }
.band-block { margin-bottom:16px; }
.band-head { font-size:12px; color:#555; margin-bottom:6px; display:flex; align-items:center; }
.dot { width:9px; height:9px; border-radius:50%; display:inline-block; margin-right:5px; }
.dot.nr { background:#ff976a; }
.dot.rv { background:#07c160; }
.band-track { position:relative; height:22px; background:#f2f3f5; border-radius:5px; overflow:hidden; }
.band-fill { position:absolute; top:0; height:100%; opacity:0.35; }
.band-fill.nr { background:#ff976a; }
.band-fill.rv { background:#07c160; }
.band-mid { position:absolute; top:0; height:100%; width:2px; background:#323233; }
.pass-line { position:absolute; top:0; height:100%; width:2px; background:#ee0a24; }
.band-nums { display:flex; justify-content:space-between; font-size:11px; color:#888; margin-top:4px; }
.band-nums b { color:#323233; font-size:13px; }
.gain { text-align:center; font-size:13px; color:#555; margin-top:4px; }
.gain b { color:#07c160; font-size:16px; }

.tags { margin-top:14px; padding:14px 16px; }
.legend { font-size:11px; color:#999; margin-bottom:10px; }
.trow { margin-bottom:12px; }
.tname { font-size:13px; font-weight:600; }
.tpts { font-size:11px; color:#1989fa; margin-left:6px; }
.wnote { font-size:11px; color:#ee8a3a; margin-top:2px; }
.warn { font-size:10px; color:#ff976a; margin-left:6px; border:1px solid #ffd9c2; border-radius:3px; padding:0 3px; }
.tbar-bg { position:relative; height:18px; background:#f2f3f5; border-radius:3px; margin:4px 0; }
.tbar-nr { position:absolute; top:0; left:0; height:100%; background:#ffc9a8; border-radius:3px; }
.tbar-rv { position:absolute; top:0; left:0; height:100%; background:#07c160; opacity:0.55; border-radius:3px; }
.tbar-pct { position:absolute; right:6px; top:0; line-height:18px; font-size:11px; font-weight:600; color:#444; }
.tmeta { font-size:11px; color:#999; }
</style>
