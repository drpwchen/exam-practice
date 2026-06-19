<script setup>
import { onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import api from '../api.js'
import { useAuthStore } from '../stores/auth.js'
import HelpButton from '../components/HelpButton.vue'
import { applyFontSize, FONT_SIZE_KEY, FONT_SIZE_DEFAULT } from '../fontSize.js'

const router = useRouter()
const auth = useAuthStore()

const SETTINGS_HELP = '這裡可以設定：\n\n• **改密碼**\n• **字體大小**：改了即時套用全站\n• **目標記憶留存率**（進階）：數字越高，系統排複習越勤、記得越牢但題量越大；越低則複習較鬆。不確定就用預設。'

// ── 改密碼 ──
const oldPw = ref('')
const newPw = ref('')
const confirmPw = ref('')
const pwLoading = ref(false)

async function changePassword () {
  if (newPw.value.length < 8) { showToast('新密碼至少 8 個字'); return }
  if (newPw.value !== confirmPw.value) { showToast('兩次新密碼不一致'); return }
  pwLoading.value = true
  try {
    await api.post('/auth/change-password', {
      old_password: oldPw.value,
      new_password: newPw.value
    })
    await auth.refreshMe()
    oldPw.value = ''; newPw.value = ''; confirmPw.value = ''
    showToast('密碼已更新')
  } catch (e) {
    showToast(e.response?.data?.detail || '更新失敗')
  } finally {
    pwLoading.value = false
  }
}

// ── 預設字體大小 ──
const FONT_MIN = 14
const FONT_MAX = 22
const fontSize = ref(FONT_SIZE_DEFAULT)

function onFontSizeChange (v) {
  fontSize.value = v
  applyFontSize(v)
  try { localStorage.setItem(FONT_SIZE_KEY, String(v)) } catch (_) { /* ignore */ }
}

// ── 進階：FSRS 複習參數 ──
const advancedOpen = ref([])
const requestRetention = ref(0.9)
const maxNewPerSession = ref(20)
const cfgLoading = ref(false)
const cfgSaving = ref(false)

async function loadStudyConfig () {
  cfgLoading.value = true
  try {
    const r = await api.get('/me/preferences/study-config')
    requestRetention.value = r.data?.request_retention ?? 0.9
    maxNewPerSession.value = r.data?.max_new_per_session ?? 20
  } catch (_) { /* keep defaults */ } finally {
    cfgLoading.value = false
  }
}

async function saveStudyConfig () {
  cfgSaving.value = true
  try {
    const r = await api.put('/me/preferences/study-config', {
      request_retention: requestRetention.value,
      max_new_per_session: maxNewPerSession.value
    })
    requestRetention.value = r.data?.request_retention ?? requestRetention.value
    maxNewPerSession.value = r.data?.max_new_per_session ?? maxNewPerSession.value
    showToast('已儲存複習參數')
  } catch (e) {
    showToast(e.response?.data?.detail || '儲存失敗')
  } finally {
    cfgSaving.value = false
  }
}

onMounted(() => {
  const stored = parseFloat(localStorage.getItem(FONT_SIZE_KEY) || '')
  fontSize.value = Number.isFinite(stored) ? stored : FONT_SIZE_DEFAULT
  loadStudyConfig()
})
</script>

<template>
  <van-nav-bar title="設定" left-text="返回首頁" left-arrow @click-left="router.push('/')">
    <template #right>
      <HelpButton title="設定說明" :content="SETTINGS_HELP" :size="18" />
    </template>
  </van-nav-bar>

  <div class="settings">
    <!-- 改密碼 -->
    <van-cell-group inset title="改密碼">
      <van-field v-model="oldPw" type="password" label="舊密碼" placeholder="目前的密碼" />
      <van-field v-model="newPw" type="password" label="新密碼" placeholder="至少 8 個字" />
      <van-field v-model="confirmPw" type="password" label="再次輸入" placeholder="再輸入一次新密碼" />
      <div style="padding: 12px 16px;">
        <van-button block type="primary" :loading="pwLoading" @click="changePassword">更新密碼</van-button>
      </div>
    </van-cell-group>

    <!-- 字體大小 -->
    <van-cell-group inset title="預設字體大小" style="margin-top: 16px;">
      <div class="font-row">
        <span class="font-sample" :style="{ fontSize: fontSize + 'px' }">範例文字 Aa</span>
        <span class="font-val">{{ fontSize }}px</span>
      </div>
      <div class="slider-row">
        <span class="slider-end">小</span>
        <van-slider
          :model-value="fontSize"
          :min="FONT_MIN" :max="FONT_MAX" :step="1"
          style="flex: 1; margin: 0 12px;"
          @update:model-value="onFontSizeChange"
        />
        <span class="slider-end">大</span>
      </div>
      <div class="hint">調整後立即套用到整個 App。</div>
    </van-cell-group>

    <!-- 進階：FSRS 複習參數 -->
    <van-cell-group inset title="進階" style="margin-top: 16px;">
      <van-collapse v-model="advancedOpen">
        <van-collapse-item title="FSRS 複習參數" name="fsrs">
          <van-loading v-if="cfgLoading" style="margin: 16px auto; display: block;" />
          <div v-else>
            <div class="cfg-block">
              <div class="cfg-head">
                <span class="cfg-lbl">目標記憶留存率</span>
                <span class="cfg-val">{{ Math.round(requestRetention * 100) }}%</span>
              </div>
              <div class="slider-row">
                <span class="slider-end">0.80</span>
                <van-slider
                  v-model="requestRetention"
                  :min="0.80" :max="0.97" :step="0.01"
                  style="flex: 1; margin: 0 12px;"
                />
                <span class="slider-end">0.97</span>
              </div>
              <div class="hint">越高 → 複習越頻繁、記得越牢但題量大；越低 → 複習較少但較易遺忘。</div>
            </div>

            <div style="padding: 4px 0 8px;">
              <van-button block type="primary" size="small" :loading="cfgSaving" @click="saveStudyConfig">
                儲存複習參數
              </van-button>
            </div>
          </div>
        </van-collapse-item>
      </van-collapse>
    </van-cell-group>

    <div style="height: 32px;"></div>
  </div>
</template>

<style scoped>
.settings { padding: 16px 0; }
.font-row {
  display: flex; align-items: baseline; justify-content: space-between;
  padding: 12px 16px 4px;
}
.font-sample { color: #333; }
.font-val { font-size: 13px; color: #1989fa; font-weight: 600; }
.slider-row { display: flex; align-items: center; padding: 8px 16px; }
.slider-end { font-size: 12px; color: #999; }
.hint { font-size: 12px; color: #888; padding: 4px 16px 12px; line-height: 1.5; }
.cfg-block { padding: 8px 0; }
.cfg-head { display: flex; align-items: center; justify-content: space-between; padding: 4px 0; }
.cfg-lbl { font-size: 14px; color: #333; }
.cfg-val { font-size: 14px; color: #1989fa; font-weight: 600; }
</style>
