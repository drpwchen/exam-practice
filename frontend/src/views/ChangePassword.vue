<script setup>
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { showToast } from 'vant'
import { useAuthStore } from '../stores/auth.js'

const oldPw = ref('')
const newPw = ref('')
const confirmPw = ref('')
const loading = ref(false)
const auth = useAuthStore()
const router = useRouter()

async function submit () {
  if (newPw.value.length < 8) { showToast('新密碼至少 8 個字'); return }
  if (newPw.value !== confirmPw.value) { showToast('兩次新密碼不一致'); return }
  loading.value = true
  try {
    await auth.changePassword(oldPw.value, newPw.value)
    showToast('密碼已更新')
    router.push('/')
  } catch (e) {
    showToast(e.response?.data?.detail || '更新失敗')
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <van-nav-bar title="更新密碼" />
  <div style="padding: 16px;">
    <p style="color: #888;">第一次登入請更換預設密碼</p>
  </div>
  <van-form @submit="submit">
    <van-cell-group inset>
      <van-field v-model="oldPw" type="password" label="舊密碼" :rules="[{ required: true }]" />
      <van-field v-model="newPw" type="password" label="新密碼 (≥8)" :rules="[{ required: true }]" />
      <van-field v-model="confirmPw" type="password" label="再次輸入" :rules="[{ required: true }]" />
    </van-cell-group>
    <div style="margin: 16px;">
      <van-button round block type="primary" native-type="submit" :loading="loading">
        更新
      </van-button>
    </div>
  </van-form>
</template>
