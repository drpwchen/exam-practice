import { ref, onMounted, onBeforeUnmount } from 'vue'

// 鍵盤升起時 (iOS Safari) layout viewport 不變但 visualViewport 縮小，
// position:fixed 的 van-popup 會被鍵盤蓋住。此 composable 同時：
//   1. 把 popup 高度限制在可見區內
//   2. 把 popup 從 layout viewport 底部抬高 = 鍵盤高度
export function useKeyboardSafePopup(maxHeightRatio = 0.6, padding = '16px') {
  const popupStyle = ref({ height: `${maxHeightRatio * 100}%`, padding })

  function update() {
    const vv = window.visualViewport
    if (!vv) return
    const keyboardOffset = Math.max(0, window.innerHeight - vv.height - vv.offsetTop)
    const maxH = window.innerHeight * maxHeightRatio
    const availableH = vv.height - 24
    const h = Math.max(180, Math.min(maxH, availableH))
    popupStyle.value = {
      height: `${h}px`,
      padding,
      bottom: `${keyboardOffset}px`,
    }
  }

  onMounted(() => {
    update()
    window.visualViewport?.addEventListener('resize', update)
    window.visualViewport?.addEventListener('scroll', update)
  })

  onBeforeUnmount(() => {
    window.visualViewport?.removeEventListener('resize', update)
    window.visualViewport?.removeEventListener('scroll', update)
  })

  return { popupStyle }
}
