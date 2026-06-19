import axios from 'axios'
import { useAuthStore } from './stores/auth.js'
import router from './router.js'

const api = axios.create({
  baseURL: '/api',
  // 40s: cold requests after an idle period may need to re-establish TLS over a
  // slow link; a shorter timeout trips on the first click after a pause and
  // surfaces a spurious "載入失敗".
  timeout: 40000
})

api.interceptors.request.use((config) => {
  const auth = useAuthStore()
  if (auth.token) {
    config.headers.Authorization = `Bearer ${auth.token}`
  }
  return config
})

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    // Retry once on timeout / network error: a cold connection can drop the
    // first request after an idle period. One transparent retry recovers it
    // instead of showing the user a "失敗".
    const cfg = err.config
    const retriable = !err.response &&
      (err.code === 'ECONNABORTED' || err.code === 'ERR_NETWORK')
    if (retriable && cfg && !cfg._retried) {
      cfg._retried = true
      return api(cfg)
    }
    const status = err.response?.status
    if (status === 401) {
      const auth = useAuthStore()
      auth.logout()
      router.push('/login')
    } else if (status === 428) {
      router.push('/change-password')
    }
    return Promise.reject(err)
  }
)

export function imgUrl (src) {
  if (!src) return ''
  const auth = useAuthStore()
  if (!auth.token) return src
  const sep = src.includes('?') ? '&' : '?'
  return `${src}${sep}token=${encodeURIComponent(auth.token)}`
}

// HIDDEN_SOURCES kept as the gate point for any future retire scenarios.
export const HIDDEN_SOURCES = new Set()
export function visibleSources (rows) {
  if (!Array.isArray(rows)) return []
  return rows.filter(r => !HIDDEN_SOURCES.has(r.source_code ?? r.code))
}

export const SOURCE_LABEL = {
  past_paper: '考古題'
}
export const PAST_PAPER_YEARS = [110, 111, 112, 113, 114]

// Per-user source allowlist helpers. Backend exposes a comma-separated
// `allowed_sources` field on `auth.user`; null/empty means "full access".
// Frontend uses this to hide pickers / cells / tabs the current user can't
// reach (the BE already 403s, this just keeps the UI clean).
export function userAllowedSources () {
  const auth = useAuthStore()
  const raw = auth.user?.allowed_sources
  if (!raw) return null  // null = full access
  return new Set(String(raw).split(',').map(s => s.trim()).filter(Boolean))
}
export function userCanSee (code) {
  const allow = userAllowedSources()
  if (allow === null) return true
  return allow.has(code)
}

export default api
