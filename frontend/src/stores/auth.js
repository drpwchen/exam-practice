import { defineStore } from 'pinia'
import api from '../api.js'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    token: localStorage.getItem('token') || '',
    user: JSON.parse(localStorage.getItem('user') || 'null')
  }),
  actions: {
    async login (username, password) {
      const r = await api.post('/auth/login', { username, password })
      this.token = r.data.access_token
      this.user = r.data.user
      localStorage.setItem('token', this.token)
      localStorage.setItem('user', JSON.stringify(this.user))
    },
    async refreshMe () {
      const r = await api.get('/auth/me')
      this.user = r.data
      localStorage.setItem('user', JSON.stringify(this.user))
    },
    async changePassword (oldPw, newPw) {
      const r = await api.post('/auth/change-password', {
        old_password: oldPw, new_password: newPw
      })
      this.user = r.data
      localStorage.setItem('user', JSON.stringify(this.user))
    },
    logout () {
      this.token = ''
      this.user = null
      localStorage.removeItem('token')
      localStorage.removeItem('user')
    }
  }
})
