import { ref } from 'vue'

// Shared "說明模式" (help mode) toggle.
//
// One module-level reactive ref shared by every page. Each page's title carries
// a single "?" (HelpButton). Clicking it shows that page's overview dialog AND
// flips helpMode on. Inline field-level "?" (InfoTooltip) stay hidden until
// helpMode is on (`v-if="helpMode"`), so the default screen is clean with just
// one "?".
const helpMode = ref(false)

export function useHelpMode () {
  function toggleHelpMode () { helpMode.value = !helpMode.value }
  function enableHelpMode () { helpMode.value = true }
  return { helpMode, toggleHelpMode, enableHelpMode }
}
