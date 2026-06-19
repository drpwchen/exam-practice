<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast, showConfirmDialog } from 'vant'
import api from '../api.js'

const router = useRouter()
const rows = ref([])
const total = ref(0)
const offset = ref(0)
const limit = 50
const loading = ref(false)

const selectMode = ref(false)
const selectedIds = ref(new Set())
const busy = ref(false)

async function load (resetOffset = true) {
  if (resetOffset) offset.value = 0
  loading.value = true
  try {
    const r = await api.get(`/history?limit=${limit}&offset=${offset.value}`)
    rows.value = r.data.rows
    total.value = r.data.total
  } catch (e) {
    showToast('載入失敗')
  } finally {
    loading.value = false
  }
}

async function nextPage () { offset.value += limit; await load(false) }
async function prevPage () { offset.value = Math.max(0, offset.value - limit); await load(false) }

async function deleteOne (row) {
  try {
    await showConfirmDialog({ title: '刪掉這筆答題', message: row.stem_preview })
    await api.delete(`/history/answer/${row.answer_id}`)
    showToast('已刪')
    await load(false)
  } catch (_) {}
}

async function resetQuestion (row) {
  try {
    await showConfirmDialog({
      title: '重置這題的所有紀錄',
      message: '刪掉此題所有答案 + 排程，會被當新題重出'
    })
    await api.delete(`/history/question/${row.question_id}`)
    showToast('已重置')
    await load(false)
  } catch (_) {}
}

async function resetAll () {
  try {
    await showConfirmDialog({
      title: '⚠ 重置全部答題',
      message: '會刪掉你所有答題紀錄與排程',
      confirmButtonColor: '#ee0a24'
    })
    await api.delete('/history/all')
    showToast('全部清空')
    await load()
  } catch (_) {}
}

function toggleSelect (id) {
  if (selectedIds.value.has(id)) selectedIds.value.delete(id)
  else selectedIds.value.add(id)
  selectedIds.value = new Set(selectedIds.value)
}
function selectAll () {
  selectedIds.value = new Set(rows.value.map(r => r.answer_id))
}
function clearSelection () { selectedIds.value = new Set() }
function enterSelectMode () { selectMode.value = true }
function exitSelectMode () {
  selectMode.value = false
  clearSelection()
}

async function bulkDelete () {
  const ids = Array.from(selectedIds.value)
  if (!ids.length) return
  try {
    await showConfirmDialog({
      title: `刪除 ${ids.length} 筆答題`,
      message: '只刪這幾筆答題紀錄，不重置排程',
      confirmButtonColor: '#ee0a24'
    })
  } catch (_) { return }
  busy.value = true
  try {
    await api.post('/history/bulk-delete-answers', { answer_ids: ids })
    showToast(`已刪 ${ids.length} 筆`)
    exitSelectMode()
    await load(false)
  } catch (e) {
    showToast(e.response?.data?.detail || '刪除失敗')
  } finally { busy.value = false }
}

function viewQ (qid) { router.push(`/question/${qid}`) }
function fmtTime (iso) { return new Date(iso).toLocaleString('zh-TW', { hour12: false }) }

onMounted(load)
</script>

<template>
  <van-nav-bar title="答題歷史" left-text="返回" left-arrow @click-left="$router.back()">
    <template #right>
      <span v-if="!selectMode" @click="resetAll" style="color:#ee0a24; font-size:13px;">全部清空</span>
    </template>
  </van-nav-bar>

  <div class="counter-row">
    <span class="counter">
      共 {{ total }} 筆 (顯示第 {{ offset+1 }}-{{ Math.min(offset+limit, total) }} 筆)
    </span>
    <template v-if="!selectMode">
      <van-button size="mini" type="primary" plain @click="enterSelectMode" :disabled="!rows.length">選擇</van-button>
    </template>
    <template v-else>
      <span class="select-info" v-if="selectedIds.size">已選 {{ selectedIds.size }}</span>
      <van-button size="mini" plain @click="selectAll" v-if="rows.length">全選</van-button>
      <van-button size="mini" plain @click="clearSelection" :disabled="!selectedIds.size">清除</van-button>
      <van-button size="mini" plain @click="exitSelectMode">取消</van-button>
    </template>
  </div>

  <van-loading v-if="loading" style="margin: 16px auto; display:block;" />

  <van-cell-group inset>
    <template v-for="r in rows" :key="r.answer_id">
      <van-swipe-cell v-if="!selectMode">
        <van-cell
          :title="`#${r.question_id} ${r.stem_preview}...`"
          :label="`${fmtTime(r.answered_at)} | 選 (${r.selected_letter}) ${r.is_correct ? '✓' : '✗'} | 把握 ${r.confidence} | ${(r.time_ms/1000).toFixed(1)}s | ${r.tags.join(' ')}`"
          is-link
          @click="viewQ(r.question_id)"
        />
        <template #right>
          <van-button square type="warning" text="重置題" style="height:100%;" @click="resetQuestion(r)" />
          <van-button square type="danger" text="刪此筆" style="height:100%;" @click="deleteOne(r)" />
        </template>
      </van-swipe-cell>
      <van-cell
        v-else
        :class="{ 'row-selected': selectedIds.has(r.answer_id) }"
        @click="toggleSelect(r.answer_id)"
        clickable
      >
        <template #icon>
          <van-checkbox
            :model-value="selectedIds.has(r.answer_id)"
            shape="square"
            style="margin-right: 8px;"
          />
        </template>
        <template #title>
          <span>#{{ r.question_id }} {{ r.stem_preview }}...</span>
        </template>
        <template #label>
          {{ fmtTime(r.answered_at) }} | 選 ({{ r.selected_letter }}) {{ r.is_correct ? '✓' : '✗' }} | 把握 {{ r.confidence }} | {{ (r.time_ms/1000).toFixed(1) }}s
        </template>
      </van-cell>
    </template>
  </van-cell-group>

  <div v-if="!loading && !rows.length" style="text-align:center; padding:32px; color:#888;">
    還沒答過題
  </div>

  <div style="padding: 16px; display:flex; gap:8px;" v-if="total > limit">
    <van-button :disabled="offset === 0" @click="prevPage" style="flex:1;">← 上一頁</van-button>
    <van-button :disabled="offset + limit >= total" type="primary" @click="nextPage" style="flex:1;">下一頁 →</van-button>
  </div>

  <div v-if="selectMode && selectedIds.size" class="bulk-bar">
    <van-button size="small" type="danger" :loading="busy" @click="bulkDelete">刪除 {{ selectedIds.size }} 筆</van-button>
  </div>
</template>

<style scoped>
.counter-row {
  padding: 12px 16px; display: flex; align-items: center; gap: 8px;
  color: #666; font-size: 13px;
}
.counter-row .counter { flex: 1; }
.counter-row .select-info { color: #1989fa; font-weight: bold; }
.row-selected { background: #e6f4ff !important; }
.bulk-bar {
  position: fixed; bottom: 0; left: 0; right: 0;
  background: #fff8e6;
  border-top: 1px solid #ffe58f;
  padding: 10px 16px;
  display: flex; gap: 8px; justify-content: center;
  z-index: 100;
}
</style>
