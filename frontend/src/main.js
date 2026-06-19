import { createApp } from 'vue'
import { createPinia } from 'pinia'
import piniaPluginPersistedstate from 'pinia-plugin-persistedstate'
import Vant from 'vant'
import 'vant/lib/index.css'
import App from './App.vue'
import router from './router.js'
import { initFontSize } from './fontSize.js'

// Apply the saved font-size preference before mount so there's no flash.
initFontSize()

const pinia = createPinia()
pinia.use(piniaPluginPersistedstate)

const app = createApp(App)
app.use(pinia)
app.use(router)
app.use(Vant)
app.mount('#app')

window.addEventListener('vite:preloadError', () => {
  window.location.reload()
})
