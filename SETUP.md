# 開始使用前的設定清單 · Setup Checklist

把這套系統換成「你自己專科」要用之前，先準備好下面這幾件事。最後一節提供可
直接複製給 LLM（ChatGPT / Claude 等）的 prompt 範本，幫你把考題整理進系統。

---

## 1. 基本設定（`.env`）

`cp env.example .env` 後編輯：

| 變數 | 說明 |
|---|---|
| `SECRET_KEY` | 簽 JWT 用，務必換成高強度隨機字串：`python -c "import secrets;print(secrets.token_urlsafe(48))"` |
| `ADMIN_USERNAME` / `DEFAULT_PASSWORD` | 管理員帳密；上線前改掉 |
| `EXAM_DATE` | **你的考試日期（YYYY-MM-DD）** — 倒數、目標日、分數預測都靠它。先設好 |
| `EXAM_SECONDS_PER_Q` | **作答配速（每題秒數）= 考試總分鐘 × 60 ÷ 題數**。作答計時器依此變色（0.75× 轉橘、1× 轉紅）。例：200 題 160 分鐘 → 48 |
| `COHORT_WRONGBOOK_PUBLIC` | 「大家的錯題本」是否對非管理員開放 |

## 2. 你的專科命題領域（tags）

`seeds/tags_major.yaml` 定義「次專科 / 命題領域」清單（範例是復健科 26 領域）。
換成你專科的領域：每筆有 `code`（英文代號）、`short_zh`（中文短名）、
`display_name`（完整名稱）、`sort_order`。題目會掛在這些領域下，Dashboard、
大家的錯題本、分數預測都依此分科。

## 3. 題目來源與「領域標記方法」

- **來源 PDF**：把歷屆考題 PDF 放進 `PAST_PAPER_DIR`（`.env` 設定）。
- **題目→領域怎麼標**：三種方式擇一或混用——
  1. **檔名規則**：PDF 依領域命名（如 `01.ADL.pdf`），匯入時自動套用該領域標籤。
  2. **匯入後在 admin 後台逐題指定**（題量少時最直接）。
  3. **用 LLM 批次判斷**（見下方 prompt B）。

## 4. 使用者（讀書會成員）

- 管理員可在後台新增成員、給預設密碼、開啟「首次登入強制改密」。
- 公開展示站請只開**非管理員的 demo 帳號**，不要對外露出 admin 登入。

## 5. 匯入你的題庫

1. PDF 放好 → 跑匯入管線（細節見 [`docs/IMPORT_PROCEDURE.md`](docs/IMPORT_PROCEDURE.md)）。
2. `python -m tools.sanity_check` 檢查解析品質。
3. admin 後台校對題幹/選項/答案/詳解。
4. `python -m pipeline.seed_demo` 只用於「示範資料」；正式使用請匯入你自己的題庫。

> 詳解呈現方式：系統把詳解當「頁面圖片」顯示（避免 OCR 誤植）。你可以用解題
> PDF 的頁截圖，或像示範資料那樣把文字詳解渲染成圖片（見 `demo/build_demo_assets.py`）。

---

## 與 LLM 協作的 prompt 範本

考題整理很適合交給 LLM。下面 prompt 直接複製、把你的素材貼上即可。

### A. 把考題（PDF 文字 / 圖片 OCR）轉成題庫 JSON

```
你是考題結構化助手。我會貼上一份考題文字，請輸出 JSON 陣列，每題格式：
{
  "source_ref": "<年份>/Q<題號>",
  "year": <年份數字>,
  "topic_code": "<次專科代號，見我提供的清單>",
  "stem": "<題幹>",
  "options": {"A":"...","B":"...","C":"...","D":"...","E":"..."},
  "correct_letter": "<正解，如 'A'；多選用 'ABD'>",
  "is_multi": <true/false>
}
規則：只輸出 JSON、不要其他文字；選項照原文；無 E 選項就省略 E；
不確定正解就把 correct_letter 設為 "" 並在該題加 "needs_review": true。
我的次專科代號清單：<貼上 seeds/tags_major.yaml 的 code 清單>

考題文字：
<貼上考題>
```

### B. 幫題目自動標記次專科

```
依下面的次專科清單，判斷每題最適合的 topic_code，輸出 {source_ref: topic_code} 的對照。
不確定的標 "REVIEW"。次專科清單（代號＝中文）：
<貼上 code 與 short_zh>
題目：
<貼上題幹清單>
```

### C. 重寫 / 撰寫詳解（標注教科書出處）

```
為每題撰寫 3–6 句繁體中文詳解：說明為什麼正解正確、關鍵誘答為何錯，並標注
標準教科書出處（如 `Braddom 6e Ch.44`）。語氣精簡、像考點筆記。
重要：用你自己的話寫，不要照抄任何既有共筆/詳解（版權考量）；不確定的事實
就保守陳述，並提醒「可能有誤」。
題目與正解：
<貼上 stem / options / correct_letter>
```

### D. 建立你專科的命題領域清單（tags）

```
我要準備「<你的專科>」專科考試的題庫分類。請參考考選部/學會公布的命題大綱，
列出 15–30 個次專科領域，每個給：code（英文縮寫）、short_zh（中文短名）、
display_name（完整名稱）。輸出成 YAML，格式：
- code: ADL
  short_zh: 日常生活活動
  display_name: Activities of Daily Living
```

> ⚠️ LLM 產生的內容（分類、詳解、正解）都要**人工複核**再寫進系統；正解尤其要
> 對照官方答案。題庫內容的著作權請自行確認，公開散布前務必釐清來源授權。
