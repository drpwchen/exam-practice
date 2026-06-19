// Shared font-size preference.
//
// Vant 4 sizes its components in px (not rem), so setting `html { font-size }`
// alone does NOT visibly enlarge the UI. To make the preference actually scale
// everything (Vant components + our own px styles) we apply a page `zoom`
// relative to the default size. zoom is supported in all Chromium browsers and
// Safari (the mobile targets for this PWA).
export const FONT_SIZE_KEY = 'app-font-size'
export const FONT_SIZE_DEFAULT = 17
export const FONT_SIZE_MIN = 14
export const FONT_SIZE_MAX = 22

export function applyFontSize (px) {
  const n = Number(px)
  if (!Number.isFinite(n)) return
  const clamped = Math.min(FONT_SIZE_MAX, Math.max(FONT_SIZE_MIN, n))
  // scale the whole page relative to the default (17px → 1.0)
  document.documentElement.style.zoom = String(clamped / FONT_SIZE_DEFAULT)
}

// Read the stored size (falling back to default) and apply it. Call once at
// startup.
export function initFontSize () {
  const stored = parseFloat(localStorage.getItem(FONT_SIZE_KEY) || '')
  applyFontSize(Number.isFinite(stored) ? stored : FONT_SIZE_DEFAULT)
}
