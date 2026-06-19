<script setup>
import { computed, ref } from 'vue'
import { userAllowedSources } from '../api.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  dbUpdatedDate: { type: String, default: '' }
})
const emit = defineEmits(['update:modelValue'])

const step = ref(0)
const isRestricted = computed(() => userAllowedSources() !== null)
const updateLine = computed(() =>
  (!isRestricted.value && props.dbUpdatedDate) ? `\n\n📅 題庫更新日期：**${props.dbUpdatedDate}**` : ''
)

// Lightweight inline markup → HTML. Supports **bold**, ==highlight==, [c]色字[/c].
// Source strings are all hard-coded here (no user input), so v-html is safe.
const HL = '<span class="hl">'
function render (text) {
  return text
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<b>$1</b>')
    .replace(/==(.+?)==/g, '$1')
    .replace(/\[c\](.+?)\[\/c\]/g, '<span class="cc">$1</span>')
    .replace(/\n/g, '<br>')
}

const STEPS = computed(() => {
  const dashBody =
    '一頁看完自己的複習狀況：\n' +
    '• **各領域進度**：每個領域看過幾題、正確率多少\n' +
    '• **今日／本週**：做了多少題、初答 vs 再答正確率\n' +
    '• **🎯 考前目標**：設定哪天看完全部題目，每天追蹤該做幾題\n' +
    '• **👥 夥伴進度**：和其他人在各領域的進度、答對率比較'

  const sourceBody =
    '本題庫為 **114 年復健科考古題（模擬版）**。\n\n' +
    '**用法**\n' +
    '1. 選想練的領域（同正式考試，各領域一起出現）\n' +
    '2. 選題數、預設把握度、是否打亂選項\n' +
    '3. 答完看詳解、加心得、調整出現頻率\n' +
    '4. 過程中隨時可按「結束」看本次成績\n\n' +
    '📌 偶爾會遇到**複選題**（題幹有紅色「複選」徽章），選項變 checkbox，全對才算對。\n\n' +
    '準備好了，按下面開始吧 💪'

  return [
    {
      icon: '🧭',
      title: '題庫領航',
      body:
        '為復健專科考試設計的**練習系統**。\n\n' +
        '接下來幾步介紹特色與用法，看完按開始就好。\n\n' +
        '（首頁右上「使用說明」可隨時再叫出來）'
    },
    {
      icon: '🧠',
      title: '特色一：間隔複習 Spaced repetition',
      body:
        '系統綜合**答題時間、正確性、自評把握度、看過次數、上次間隔**，自動挑題複習。\n\n' +
        '目標：**用最少的重複，記住全部題目**。\n\n' +
        '所以熟的題會慢慢消失、不熟的題反覆回來——這是設計，不是 bug。\n\n' +
        '（演算法是 **FSRS-6**，和 Anki 同家族）'
    },
    {
      icon: '🔀',
      title: '特色二：打亂選項',
      body:
        '每次答題都會**==選項隨機打亂==**，避免你死記答案位置。\n\n' +
        '看詳解時選項會**恢復原始順序**，方便對照解析。'
    },
    {
      icon: '📖',
      title: '特色三：詳解內嵌 + 圖片題',
      body:
        '答完**馬上看詳解**，附完整解析與**參考頁碼**。\n\n' +
        '🖼 支援**圖片題**（X 光、超音波、圖表等），圖片直接內嵌題目中。'
    },
    {
      icon: '⭐',
      title: '特色四：錯題區（個人錯題本）',
      body:
        '集中你容易答錯的題：**手動星標** + 系統**自動偵測**的常錯題。\n\n' +
        '錯題依**失敗型態**自動分類：**死穴 / 易忘 / 盲區（把握十足卻答錯）/ 新錯**，可只挑某一種弱點來練。\n\n' +
        '📊 可依**預測錯題機率**排序——考前直接看最容易錯的題。'
    },
    {
      icon: '🔥',
      title: '特色五：大家的錯題本',
      body:
        '看**全體最常答錯的題目排行**，找出大家共同的弱點。\n\n' +
        '✅ 練習時也能直接**選「大家的錯題」當練習範圍**，集中攻克高頻陷阱題。'
    },
    {
      icon: '📊',
      title: '特色六：Dashboard',
      body: dashBody
    },
    {
      icon: '🎯',
      title: '特色七：分數預測',
      body:
        '依你的答題狀況，**預估正式考試可能得分**。\n\n' +
        '會比較**「持續複習」vs「不複習」**兩種情境，看看現在開始複習能多拿幾分。\n\n' +
        '💡 **要預測準，請從第一次接觸考題就用本系統練**——「初答正確率」是估新題實力的關鍵，已在別處做過會讓初答虛高、預測偏樂觀。\n\n' +
        '（僅供參考，別焦慮 🙂）'
    },
    {
      icon: '📄',
      title: '特色八：PDF 匯入轉題庫',
      body:
        '可把 **PDF 講義／考卷自動轉成題庫**，省去手動建題。\n\n' +
        '（此功能由管理者操作）'
    },
    {
      icon: '⏱',
      title: '計時器：掌握答題節奏',
      body:
        '正式考試 **200 題、160 分鐘**，平均每題 **48 秒**。\n\n' +
        '練習時計時器顏色代表節奏：\n' +
        '🟢 綠　< 36 秒（正常）\n' +
        '🟠 橘　36–48 秒（接近平均上限）\n' +
        '🔴 紅　> 48 秒（已超出平均配速）\n\n' +
        '只是參考、不會強制跳題。常出現紅色 → 要練習**加快閱讀與決策**。'
    },
    {
      icon: '🗂',
      title: '題目來源 + 用法',
      body: sourceBody
    }
  ]
})

function close (markSeen = true) {
  if (markSeen) {
    try { localStorage.setItem('intro-seen-v9', '1') } catch (_) { /* ignore */ }
  }
  step.value = 0
  emit('update:modelValue', false)
}

function next () {
  if (step.value < STEPS.value.length - 1) step.value++
  else close(true)
}
function prev () {
  if (step.value > 0) step.value--
}
</script>

<template>
  <van-popup
    :show="modelValue"
    @update:show="emit('update:modelValue', $event)"
    position="center"
    round
    :close-on-click-overlay="false"
    :z-index="2200"
    :style="{ width: '90%', maxWidth: '460px', padding: '24px 20px 16px' }"
  >
    <div class="intro-skip" @click="close(true)">略過 ✕</div>

    <div class="intro-icon">{{ STEPS[step].icon }}</div>
    <div class="intro-title">{{ STEPS[step].title }}</div>
    <!-- eslint-disable-next-line vue/no-v-html — content is hard-coded above -->
    <div class="intro-body" v-html="render(STEPS[step].body + (step === 0 ? updateLine : ''))"></div>

    <div class="intro-dots">
      <span
        v-for="(_, i) in STEPS" :key="i"
        :class="['dot', { active: i === step }]"
      />
    </div>

    <div class="intro-actions">
      <van-button v-if="step > 0" plain size="small" @click="prev">← 上一步</van-button>
      <van-button v-else plain size="small" @click="close(false)">下次再看</van-button>
      <van-button type="primary" size="small" @click="next">
        {{ step === STEPS.length - 1 ? '開始練習 →' : '下一步 →' }}
      </van-button>
    </div>
  </van-popup>
</template>

<style scoped>
.intro-skip { position: absolute; top: 12px; right: 16px; font-size: 12px; color: #999; cursor: pointer; }
.intro-icon { text-align: center; font-size: 48px; margin: 8px 0 12px; }
.intro-title { text-align: center; font-size: 20px; font-weight: bold; color: #1989fa; margin-bottom: 16px; }
.intro-body { font-size: 14px; line-height: 1.7; color: #333; min-height: 200px; padding: 0 4px; }
.intro-body :deep(b) { font-weight: 700; }
.intro-body :deep(.hl) { background: #fff3a0; border-radius: 3px; padding: 0 2px; font-weight: 600; }
.intro-body :deep(.cc) { color: #ee0a24; font-weight: 600; }
.intro-dots { display: flex; justify-content: center; gap: 6px; margin: 20px 0 16px; flex-wrap: wrap; }
.dot { width: 8px; height: 8px; border-radius: 50%; background: #ddd; transition: background 0.2s; }
.dot.active { background: #1989fa; }
.intro-actions { display: flex; gap: 8px; justify-content: space-between; }
</style>
