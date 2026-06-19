import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { VitePWA } from 'vite-plugin-pwa'
import { execSync } from 'node:child_process'

let buildHash = 'dev'
let buildDate = ''
try {
  buildHash = execSync('git rev-parse --short HEAD', { stdio: ['ignore', 'pipe', 'ignore'] })
    .toString().trim()
  buildDate = execSync('git log -1 --format=%cd --date=short', { stdio: ['ignore', 'pipe', 'ignore'] })
    .toString().trim()
} catch (_) { /* git unavailable — leave defaults */ }

export default defineConfig({
  define: {
    __BUILD_HASH__: JSON.stringify(buildHash),
    __BUILD_DATE__: JSON.stringify(buildDate),
  },
  plugins: [
    vue(),
    VitePWA({
      registerType: 'autoUpdate',
      manifest: {
        name: '題庫領航',
        short_name: '題庫領航',
        description: '復健科專科考試題庫練習',
        theme_color: '#1989fa',
        background_color: '#ffffff',
        display: 'standalone',
        lang: 'zh-TW',
        icons: []
      }
    })
  ],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true
      }
    }
  }
})
