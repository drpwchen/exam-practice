<script setup>
import { ref, watch, computed } from 'vue'
import { imgUrl } from '../api.js'

// Image-page viewer: shows a set of pre-rendered explanation page images with
// zoom + pagination + neighbour preloading. (No /source-pdf endpoint — the URLs
// are passed in directly, e.g. /api/static/images/expl_05.png.)
const props = defineProps({
  images: { type: Array, default: () => [] }
})

const page = ref(0)

// Zoom: stored in localStorage as a float multiplier (0.5 – 3.0, step 0.25)
const ZOOM_KEY = 'pdf-scale'
const ZOOM_MIN = 0.5
const ZOOM_MAX = 3.0
const ZOOM_STEP = 0.25
const ZOOM_DEFAULT = 1.0

const zoom = ref(parseFloat(localStorage.getItem(ZOOM_KEY) || ZOOM_DEFAULT))

function saveZoom (v) {
  zoom.value = v
  localStorage.setItem(ZOOM_KEY, String(v))
}
function zoomIn () {
  if (zoom.value < ZOOM_MAX) saveZoom(Math.round((zoom.value + ZOOM_STEP) * 100) / 100)
}
function zoomOut () {
  if (zoom.value > ZOOM_MIN) saveZoom(Math.round((zoom.value - ZOOM_STEP) * 100) / 100)
}

// Image width: base 720px × zoom, capped at 100% of container on narrow screens
const imgStyle = computed(() => ({
  maxWidth: `min(100%, ${Math.round(720 * zoom.value)}px)`
}))

const total = computed(() => props.images?.length || 0)
const multi = computed(() => total.value > 1)
const currentSrc = computed(() => {
  const s = props.images?.[page.value]
  return s ? imgUrl(s) : ''
})

// Preload the adjacent pages so navigating feels instant.
function preloadNeighbours () {
  for (const i of [page.value - 1, page.value + 1]) {
    if (i >= 0 && i < total.value) {
      const img = new Image()
      img.src = imgUrl(props.images[i])
    }
  }
}

watch(page, preloadNeighbours)
watch(() => props.images, () => {
  page.value = 0
  preloadNeighbours()
}, { immediate: true })

function prev () { if (page.value > 0) page.value-- }
function next () { if (page.value < total.value - 1) page.value++ }
</script>

<template>
  <div class="pdf-panel" v-if="total > 0">
    <!-- Top controls: prev / label / next (only when multiple pages) -->
    <div v-if="multi" class="pdf-controls pdf-controls-top">
      <button class="pdf-nav" :disabled="page === 0" @click="prev">&#8592;</button>
      <span class="pdf-pageinfo">第 {{ page + 1 }} / {{ total }} 頁</span>
      <button class="pdf-nav" :disabled="page >= total - 1" @click="next">&#8594;</button>
    </div>

    <img v-if="currentSrc" :src="currentSrc" class="pdf-img" :style="imgStyle" :alt="`page ${page + 1}`" />

    <!-- Bottom controls: 3-col grid — left spacer | nav centered | zoom right -->
    <div class="pdf-controls-bottom">
      <div class="pdf-bottom-spacer"></div>
      <div class="pdf-nav-group">
        <template v-if="multi">
          <button class="pdf-nav" :disabled="page === 0" @click="prev">&#8592;</button>
          <span class="pdf-pageinfo">第 {{ page + 1 }} / {{ total }} 頁</span>
          <button class="pdf-nav" :disabled="page >= total - 1" @click="next">&#8594;</button>
        </template>
      </div>
      <div class="pdf-zoom-group">
        <button class="pdf-zoom-btn" :disabled="zoom <= ZOOM_MIN" @click="zoomOut">－</button>
        <span class="pdf-zoom-label">{{ Math.round(zoom * 100) }}%</span>
        <button class="pdf-zoom-btn" :disabled="zoom >= ZOOM_MAX" @click="zoomIn">＋</button>
      </div>
    </div>
  </div>
</template>

<style scoped>
.pdf-panel { background: #f5f5f5; border: 1px solid #e0e0e0; border-radius: 6px; padding: 12px; }

.pdf-controls {
  display: flex; align-items: center; gap: 8px;
  flex-wrap: wrap; justify-content: center;
}
.pdf-controls-top { margin-bottom: 8px; }

/* Bottom row: 3-column grid so nav stays truly centered */
.pdf-controls-bottom {
  display: grid;
  grid-template-columns: 1fr auto 1fr;
  align-items: center;
  margin-top: 8px;
}
.pdf-bottom-spacer { /* left counterweight — same visual weight as zoom group */ }
.pdf-nav-group { display: flex; align-items: center; gap: 8px; justify-content: center; }
.pdf-zoom-group { display: flex; align-items: center; gap: 4px; justify-content: flex-end; }

.pdf-pageinfo { font-size: 13px; color: #444; text-align: center; }

.pdf-nav {
  background: none; border: 1px solid #ccc; border-radius: 4px;
  font-size: 16px; width: 32px; height: 32px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: #333;
}
.pdf-nav:disabled { opacity: 0.35; cursor: default; }
.pdf-nav:not(:disabled):active { background: #e8e8e8; }

.pdf-zoom-btn {
  background: none; border: 1px solid #ccc; border-radius: 4px;
  font-size: 15px; width: 28px; height: 28px; cursor: pointer;
  display: flex; align-items: center; justify-content: center;
  color: #333; line-height: 1;
}
.pdf-zoom-btn:disabled { opacity: 0.35; cursor: default; }
.pdf-zoom-btn:not(:disabled):active { background: #e8e8e8; }

.pdf-zoom-label { font-size: 12px; color: #666; min-width: 36px; text-align: center; }

@media (max-width: 719px) {
  .pdf-zoom-group { display: none; }
}

.pdf-img { display: block; margin: 0 auto; box-shadow: 0 2px 8px rgba(0,0,0,0.1); background: white; }
</style>
