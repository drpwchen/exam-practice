<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth.js'
import IntroDialog from '../components/IntroDialog.vue'
import api, { userAllowedSources } from '../api.js'

// 預期得分落點:公開給全體,僅排除 source-restricted / 非核心考生
const PROJECTION_EXCLUDED = [43, 44, 45, 46]
const canSeeProjection = computed(() => !PROJECTION_EXCLUDED.includes(auth.user?.id))
// source-restricted users (e.g. 考古題-only) get a filtered intro, not none
const isRestricted = computed(() => userAllowedSources() !== null)
const cohortPublic = ref(false)

const router = useRouter()
const auth = useAuthStore()
const introShow = ref(false)
// 大家的錯題本：admin 一律可見；非 admin 等 6/1 flag 翻開才見。
// source-restricted 使用者看不到（會混入他們無法存取的來源題目）
const showCohort = computed(() => !!auth.user?.is_admin || (cohortPublic.value && !isRestricted.value))

const dbUpdatedDate = ref('')
const examDateStr = ref('')       // YYYY-MM-DD，畫面顯示用
async function loadDbInfo () {
  try {
    const r = await api.get('/meta/db-info')
    dbUpdatedDate.value = r.data?.db_updated_date || ''
    cohortPublic.value = !!r.data?.cohort_wrongbook_public
    if (r.data?.exam_date) {
      // 考試日 (YYYY-MM-DD) from server; 倒數至當天 13:30 Asia/Taipei。
      examDateStr.value = r.data.exam_date
      const parsed = Date.parse(`${r.data.exam_date}T13:30:00+08:00`)
      if (!Number.isNaN(parsed)) examAtMs.value = parsed
    }
  } catch (_) { /* fallback empty */ }
}

const pendingReportCount = ref(0)
async function loadPendingReports () {
  if (!auth.user?.is_admin) return
  try {
    const r = await api.get('/error-reports', { params: { only_open: true } })
    pendingReportCount.value = Array.isArray(r.data) ? r.data.length : 0
  } catch (_) { pendingReportCount.value = 0 }
}

// 筆試倒數:預設值,server 的 exam_date 載入後覆寫 (見 loadDbInfo)。
const examAtMs = ref(Date.parse('2026-09-13T13:30:00+08:00'))
// 倒數用真實時間 (UTC+8)，每秒前進。
const now = ref(Date.now())
let tickHandle = null

const remaining = computed(() => {
  const ms = Math.max(0, examAtMs.value - now.value)
  const days = Math.floor(ms / 86400000)
  const hours = Math.floor((ms % 86400000) / 3600000)
  const mins = Math.floor((ms % 3600000) / 60000)
  const secs = Math.floor((ms % 60000) / 1000)
  return { ms, days, hours, mins, secs, passed: ms === 0 }
})

const pad2 = (n) => String(n).padStart(2, '0')

onMounted(() => {
  try {
    if (!localStorage.getItem('intro-seen-v9')) introShow.value = true
  } catch (_) { /* localStorage unavailable */ }
  tickHandle = setInterval(() => { now.value = Date.now() }, 1000)
  loadDbInfo()
  loadPendingReports()
})
onUnmounted(() => {
  if (tickHandle) clearInterval(tickHandle)
})

function showIntroAgain () { introShow.value = true }
function logout () { auth.logout(); router.push('/login') }

async function forceReload () {
  // PWA service worker can serve stale assets after a deploy. This wipes
  // every cache + unregisters the SW + forces a hard reload, so the user
  // gets the latest dist on the next request.
  try {
    if ('caches' in window) {
      const keys = await caches.keys()
      await Promise.all(keys.map(k => caches.delete(k)))
    }
    if ('serviceWorker' in navigator) {
      const regs = await navigator.serviceWorker.getRegistrations()
      await Promise.all(regs.map(r => r.unregister()))
    }
  } catch (_) { /* best-effort */ }
  // location.reload(true) is deprecated but still works on Safari mobile;
  // adding a cache-bust query param is a reliable belt-and-suspenders.
  const u = new URL(location.href)
  u.searchParams.set('_cb', Date.now().toString(36))
  location.replace(u.toString())
}
</script>

<template>
  <van-nav-bar>
    <template #left>
      <van-icon
        name="replay"
        size="20"
        color="#ff976a"
        @click="forceReload"
        title="清除快取並強制重新載入新版"
        style="cursor:pointer; padding:4px;"
      />
    </template>
    <template #title>
      <span class="nav-title">題庫領航</span>
    </template>
    <template #right>
      <span @click="showIntroAgain" style="color:#1989fa;font-size:13px;margin-right:12px;">使用說明</span>
      <span @click="logout" style="color:#888;font-size:13px;">登出</span>
    </template>
  </van-nav-bar>

  <IntroDialog v-model="introShow" :db-updated-date="dbUpdatedDate" />

  <!-- 單一行：剩 N 天(主角) + 日期/時間(小) (UTC+8)，每秒跳 -->
  <div class="countdown" :class="{ 'countdown-passed': remaining.passed }">
    <span class="cd-label">距離筆試</span>
    <template v-if="!remaining.passed">
      <span class="cd-days">剩<b class="cd-days-num">{{ remaining.days }}</b>天</span>
      <span v-if="examDateStr" class="cd-date">{{ examDateStr }}</span>
      <span class="cd-clock">{{ pad2(remaining.hours) }}:{{ pad2(remaining.mins) }}:{{ pad2(remaining.secs) }}</span>
    </template>
    <span v-else class="cd-passed">考試已開始 — 加油</span>
  </div>

  <a
    v-if="!remaining.passed && remaining.days < 1"
    class="relax-song"
    href="https://music.youtube.com/watch?v=V3SbZKjCUWE&si=77YKkdBi90ebM656"
    target="_blank"
    rel="noopener"
  >🎵 放輕鬆，聽首歌？</a>

  <div style="padding: 16px 0;">
    <!-- ① 開始練習 -->
    <van-cell-group inset title="開始練習">
      <van-cell
        icon="edit"
        title="開始練習"
        label="選模式 / tag / 題數，開始本次練習"
        is-link
        size="large"
        @click="$router.push('/setup')"
      />
    </van-cell-group>

    <!-- ② 錯題區 -->
    <van-cell-group inset title="錯題區" style="margin-top: 16px;">
      <van-cell icon="star-o" title="錯題區（個人錯題本）" label="手動星標 + 自動常錯題卡片" is-link size="large" @click="$router.push('/wrong-bank')" />
      <van-cell
        v-if="showCohort"
        icon="fire-o"
        title="大家的錯題本"
        label="全體最常答錯的題目排行"
        is-link size="large"
        @click="$router.push('/cohort-wrong')"
      />
    </van-cell-group>

    <!-- ③ 數據與設定 -->
    <van-cell-group inset title="數據與設定" style="margin-top: 16px;">
      <van-cell icon="chart-trending-o" title="Dashboard" label="練習進度、各領域熟悉度、答題統計" is-link size="large" @click="$router.push('/dashboard')" />
      <van-cell
        v-if="canSeeProjection"
        icon="bulb-o"
        title="預期得分落點"
        label="依答題狀況預估考試得分（複習 vs 不複習）"
        is-link size="large"
        @click="$router.push('/score-projection')"
      />
      <van-cell
        icon="setting-o"
        title="設定"
        label="改密碼、字體大小、複習參數"
        is-link size="large"
        @click="$router.push('/settings')"
      />
    </van-cell-group>

    <!-- ④ 管理者（僅 admin 顯示） -->
    <van-cell-group v-if="auth.user?.is_admin" inset title="管理者" style="margin-top: 16px;">
      <van-cell
        icon="setting-o"
        :title="pendingReportCount > 0 ? `Admin（${pendingReportCount} 待處理）` : 'Admin'"
        label="使用者回報處理"
        is-link size="large"
        :class="{ 'admin-pending': pendingReportCount > 0 }"
        @click="$router.push('/admin')"
      />
      <van-cell icon="friends-o" title="整體狀況" label="同儕進度、容易錯的題、cohort 概覽" is-link size="large" @click="$router.push('/admin/cohort')" />
    </van-cell-group>

    <div class="site-credit">
      <div>題庫領航 · 製作：陳柏威</div>
      <a
        class="gh-link"
        href="https://github.com/drpwchen/exam-practice"
        target="_blank"
        rel="noopener"
      ><van-icon name="newspaper-o" /> 原始碼 GitHub</a>
    </div>
  </div>
</template>

<style scoped>
.nav-title {
  user-select: none;
  -webkit-user-select: none;
}
.countdown {
  margin: 12px 16px 4px;
  padding: 8px 14px;
  border-radius: 8px;
  background: #f0faf3;
  color: #07c160;
  border: 1px solid #cdedd9;
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: center;
  gap: 2px 8px;
}
.countdown-passed {
  background: #f5f5f5;
  color: #888;
  border-color: #e5e5e5;
}
/* label / 日期 / 時鐘：與「數據與設定」同級的小字 */
.cd-label, .cd-date, .cd-clock { font-size: 14px; opacity: 0.85; }
.cd-clock { font-variant-numeric: tabular-nums; }
/* 剩 N 天：天數是主角，與「題庫領航」同級 */
.cd-days { font-size: 14px; opacity: 0.85; display: inline-flex; align-items: baseline; }
.cd-days-num {
  font-size: 24px;
  font-weight: 800;
  line-height: 1;
  margin: 0 3px;
  font-variant-numeric: tabular-nums;
}
.cd-passed { font-size: 16px; font-weight: 600; }
.relax-song {
  display: block;
  text-align: center;
  margin: 0 16px 4px;
  color: #1989fa;
  font-size: 13px;
  text-decoration: none;
}
.relax-song:active {
  opacity: 0.6;
}
.site-credit {
  text-align: center;
  color: #bbb;
  font-size: 12px;
  margin: 28px 16px 16px;
}
.gh-link {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  margin-top: 6px;
  color: #1989fa;
  text-decoration: none;
  font-size: 12px;
}
.gh-link:active { opacity: 0.6; }
.admin-pending {
  background: #e8f7ee !important;
}
.admin-pending :deep(.van-cell__title) { color: #07734a; font-weight: 600; }
</style>
