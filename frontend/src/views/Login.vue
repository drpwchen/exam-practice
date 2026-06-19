<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import { useAuthStore } from '../stores/auth.js'

const username = ref('')
const password = ref('')
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()

async function submit () {
  if (!username.value || !password.value) {
    showToast('請輸入姓名和密碼')
    return
  }
  loading.value = true
  try {
    await auth.login(username.value, password.value)
    if (auth.user.must_change_password) {
      router.push('/change-password')
    } else {
      router.push('/')
    }
  } catch (e) {
    if (e.response?.status === 429) {
      showToast('登入太頻繁，請稍候 1 分鐘再試')
    } else {
      showToast(e.response?.data?.detail || e.response?.data?.error || '登入失敗')
    }
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="hero">
      <h2>題庫領航</h2>
      <p class="hint">使用中文姓名登入</p>
    </div>
    <van-form @submit="submit">
      <van-cell-group inset>
        <van-field
          v-model="username"
          name="username"
          label="姓名"
          placeholder="王小明"
          :rules="[{ required: true }]"
        />
        <van-field
          v-model="password"
          type="password"
          name="password"
          label="密碼"
          :rules="[{ required: true }]"
        />
      </van-cell-group>
      <div style="margin: 16px;">
        <van-button round block type="primary" native-type="submit" :loading="loading">
          登入
        </van-button>
      </div>
    </van-form>
    <div class="credit">by 陳柏威 · 2026</div>
  </div>
</template>

<style scoped>
.login-wrap { padding-top: 60px; }
.hero { text-align: center; padding: 0 16px 24px; }
.hero h2 { margin: 0 0 8px; }
.hint { color: #888; font-size: 14px; }
.credit { text-align: center; color: #bbb; font-size: 12px; margin-top: 40px; }
</style>
