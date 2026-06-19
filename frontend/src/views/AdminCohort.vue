<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api from '../api.js'

const router = useRouter()

const overview = ref(null)
const users = ref([])
const projections = ref([])
const loading = ref(true)

const userSort = ref('attempts') // attempts | answered | total_attempts | first_accuracy | re_accuracy | review_mid | worst | last_active | username

async function load () {
  loading.value = true
  try {
    const [o, u, p] = await Promise.all([
      api.get('/admin/cohort/overview'),
      api.get('/admin/cohort/users'),
      api.get('/admin/cohort/projections')
    ])
    overview.value = o.data
    projections.value = p.data
    const pm = {}
    p.data.forEach((x) => { pm[x.user_id] = x })
    users.value = u.data.map((row) => ({
      ...row,
      review_mid: pm[row.user_id]?.review_mid ?? null,
      worst: pm[row.user_id]?.noreview_lo ?? null
    }))
  } catch (e) {
    showToast(e.response?.data?.detail || '載入失敗')
  } finally {
    loading.value = false
  }
}

onMounted(load)

const sortedUsers = computed(() => {
  const rows = [...users.value]
  const k = userSort.value
  rows.sort((a, b) => {
    if (k === 'username') return a.username.localeCompare(b.username, 'zh-Hant')
    if (k === 'last_active') {
      const ta = a.last_active ? new Date(a.last_active).getTime() : 0
      const tb = b.last_active ? new Date(b.last_active).getTime() : 0
      return tb - ta
    }
    if (k === 'worst') {
      // 最壞分數:升序(最危險在前),無資料排最後
      return (a.worst ?? 999) - (b.worst ?? 999)
    }
    if (k === 'first_accuracy' || k === 're_accuracy' || k === 'accuracy' || k === 'review_mid') {
      const va = a[k] ?? -1
      const vb = b[k] ?? -1
      return vb - va
    }
    return (b[k] ?? 0) - (a[k] ?? 0)
  })
  return rows
})

function fmtPct (x) { return x === null || x === undefined ? '—' : (x * 100).toFixed(1) + '%' }
function fmtTime (iso) {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('zh-TW', { hour12: false }).replace(/:\d{2}$/, '')
}
</script>

<template>
  <van-nav-bar title="整體狀況 (Cohort)" left-text="首頁" left-arrow @click-left="$router.push('/')">
    <template #right>
      <van-icon name="replay" size="18" @click="load" />
    </template>
  </van-nav-bar>

  <van-loading v-if="loading" style="margin: 32px auto; display:block;" />

  <div v-else-if="overview" style="padding-bottom: 80px;">
    <!-- Overview block -->
    <van-cell-group inset title="Cohort 概覽" style="margin-top: 12px;">
      <van-grid :column-num="3" :border="false">
        <van-grid-item>
          <div class="stat">
            <div class="num">{{ overview.total_users }}</div>
            <div class="lbl">總用戶</div>
          </div>
        </van-grid-item>
        <van-grid-item>
          <div class="stat">
            <div class="num">{{ overview.active_users_7d }}</div>
            <div class="lbl">7 天內活躍</div>
          </div>
        </van-grid-item>
        <van-grid-item>
          <div class="stat">
            <div class="num">{{ overview.total_attempts }}</div>
            <div class="lbl">總答題人次</div>
          </div>
        </van-grid-item>
        <van-grid-item>
          <div class="stat">
            <div class="num">{{ fmtPct(overview.overall_accuracy) }}</div>
            <div class="lbl">整體正確率</div>
          </div>
        </van-grid-item>
        <van-grid-item>
          <div class="stat">
            <div class="num">{{ overview.average_attempts_per_user }}</div>
            <div class="lbl">人均題數</div>
          </div>
        </van-grid-item>
        <van-grid-item>
          <div class="stat">
            <div class="num">{{ overview.sessions_estimated }}</div>
            <div class="lbl">概估 sessions</div>
          </div>
        </van-grid-item>
      </van-grid>
    </van-cell-group>

    <!-- Top active users -->
    <van-cell-group inset title="最活躍 Top 5" style="margin-top: 16px;">
      <van-cell v-for="(u, i) in overview.top_active_users" :key="u.username">
        <template #title>
          <span class="rank">#{{ i + 1 }}</span>
          <span class="uname">{{ u.username }}</span>
        </template>
        <template #value>
          <span class="muted">{{ u.attempts }} 題</span>
          <span class="acc">{{ fmtPct(u.accuracy) }}</span>
        </template>
      </van-cell>
    </van-cell-group>

    <!-- Per-user progress -->
    <van-cell-group inset title="每人進度" style="margin-top: 16px;">
      <div class="sort-row">
        <span class="lbl">排序</span>
        <van-tag size="medium" :type="userSort === 'username' ? 'primary' : 'default'" @click="userSort = 'username'">姓名</van-tag>
        <van-tag size="medium" :type="userSort === 'answered' ? 'primary' : 'default'" @click="userSort = 'answered'">已答題數</van-tag>
        <van-tag size="medium" :type="userSort === 'total_attempts' ? 'primary' : 'default'" @click="userSort = 'total_attempts'">總答次數</van-tag>
        <van-tag size="medium" :type="userSort === 'first_accuracy' ? 'primary' : 'default'" @click="userSort = 'first_accuracy'">初答</van-tag>
        <van-tag size="medium" :type="userSort === 're_accuracy' ? 'primary' : 'default'" @click="userSort = 're_accuracy'">再答</van-tag>
        <van-tag size="medium" :type="userSort === 'review_mid' ? 'primary' : 'default'" @click="userSort = 'review_mid'">複習預估</van-tag>
        <van-tag size="medium" :type="userSort === 'worst' ? 'primary' : 'default'" @click="userSort = 'worst'">最壞</van-tag>
        <van-tag size="medium" :type="userSort === 'last_active' ? 'primary' : 'default'" @click="userSort = 'last_active'">最近活動</van-tag>
      </div>
      <div class="user-table">
        <div class="user-row user-head">
          <span class="col-name">姓名</span>
          <span class="col-num">已答題數</span>
          <span class="col-num">總答次數</span>
          <span class="col-num">初答</span>
          <span class="col-num">再答</span>
          <span class="col-num">複習</span>
          <span class="col-num">最壞</span>
          <span class="col-time">最近活動</span>
        </div>
        <div v-for="u in sortedUsers" :key="u.user_id" class="user-row">
          <span class="col-name">
            <a
              class="user-link"
              href="javascript:void(0)"
              @click="router.push({ path: '/dashboard', query: { as_user: u.user_id } })"
            >{{ u.username }}</a>
            <span v-if="u.is_admin" class="admin-tag">A</span>
          </span>
          <span class="col-num">{{ u.answered }}</span>
          <span class="col-num">{{ u.total_attempts }}</span>
          <span class="col-num" :class="{ 'low': u.first_accuracy !== null && u.first_accuracy < 0.6, 'high': u.first_accuracy !== null && u.first_accuracy >= 0.8 }">
            {{ fmtPct(u.first_accuracy) }}
          </span>
          <span class="col-num" :class="{ 'low': u.re_accuracy !== null && u.re_accuracy < 0.6, 'high': u.re_accuracy !== null && u.re_accuracy >= 0.8 }">
            {{ fmtPct(u.re_accuracy) }}
          </span>
          <span class="col-num" :class="{ faint: u.answered < 30 }">{{ u.review_mid ?? '—' }}</span>
          <span class="col-num" :class="{ low: u.worst !== null && u.worst < 60 && u.answered >= 30, faint: u.answered < 30 }">{{ u.worst ?? '—' }}</span>
          <span class="col-time">{{ fmtTime(u.last_active) }}</span>
        </div>
      </div>
    </van-cell-group>
  </div>
</template>

<style scoped>
.stat { text-align: center; }
.stat .num { font-size: 20px; font-weight: bold; color: #1989fa; }
.stat .lbl { font-size: 11px; color: #888; margin-top: 4px; }

.rank { color: #888; margin-right: 8px; font-size: 13px; }
.uname { font-weight: 500; }
.muted { color: #888; font-size: 12px; margin-right: 8px; }
.acc { color: #1989fa; font-weight: 500; }

.sort-row {
  padding: 10px 16px; display: flex; align-items: center;
  gap: 6px; flex-wrap: wrap; border-bottom: 1px solid #f0f0f0;
}
.sort-row .lbl { font-size: 12px; color: #666; margin-right: 4px; }
.sort-row :deep(.van-tag) { cursor: pointer; padding: 4px 10px; }

.user-table { padding: 4px 0; }
.user-row {
  display: grid;
  grid-template-columns: 0.7fr 0.55fr 0.55fr 0.6fr 0.45fr 0.45fr 0.5fr 0.5fr 0.75fr;
  padding: 6px 10px;
  font-size: 12px;
  border-bottom: 1px solid #f7f7f7;
  align-items: center;
}
.col-num.faint { color: #ccc; }
.user-head { font-weight: 600; color: #888; font-size: 11px; background: #fafafa; }
.col-name { display: flex; align-items: center; gap: 4px; }
.user-link { color: #1989fa; text-decoration: none; cursor: pointer; }
.user-link:active { opacity: 0.6; }
.col-num { text-align: right; font-variant-numeric: tabular-nums; }
.col-time { text-align: right; color: #888; font-size: 11px; }
.col-num.low { color: #ee0a24; }
.col-num.high { color: #07c160; }
.admin-tag {
  display: inline-block; background: #ffd21e; color: #333;
  font-size: 10px; padding: 1px 4px; border-radius: 3px; font-weight: bold;
}

</style>
