// Lightweight inline markup → HTML for help/說明 strings.
//
// All inputs are author-authored static strings (no user input), so the HTML
// produced here is safe to feed to v-html / vant's allow-html.
//
// Conversions (HTML is escaped first, so source text can't inject tags):
//   **x**     → <strong>x</strong>
//   ==x==     → <mark>x</mark>            (淡黃底高亮)
//   [c]x[/c]  → <span style="color:#1989fa">x</span>   (主題色)
//   \n        → <br>
export function mk (text) {
  if (text === null || text === undefined) return ''
  return String(text)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/==(.+?)==/g, '<mark>$1</mark>')
    .replace(/\[c\](.+?)\[\/c\]/g, '<span style="color:#1989fa">$1</span>')
    .replace(/\n/g, '<br>')
}
