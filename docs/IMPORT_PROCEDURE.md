# 新題目匯入流程 SOP

每次有新的考古題解題 PDF 要進題庫時走這套流程。所有題目都屬單一來源
**past_paper**(考古題)。來源 PDF 目錄由設定 `PAST_PAPER_DIR`(或 `.env`)指定,
預設 `./source_pdfs`。

## 0. 前置:盤點

```bash
# 列出來源 PDF 目錄
ls ./source_pdfs/
# 看 DB 已有哪些年份
python -c "
import sqlite3
c = sqlite3.connect('data/app.db')
rows = c.execute(\"SELECT DISTINCT q.year FROM questions q JOIN sources s ON q.source_id=s.id WHERE s.code='past_paper' ORDER BY 1\").fetchall()
[print(r[0]) for r in rows]
"
```

對比清單找出**新增的年份/檔案**。

## 1. 檔名清理(必做)

某些雲端同步會把檔名變成 `XX年解題.pdf .pdf`(雙副檔名)。`Path.stem` 只去最後一個
→ slug 對不上 DB → 產生重複。匯入前先正規化:

```bash
cd ./source_pdfs && python -c "
import os
for f in os.listdir('.'):
    if f.endswith('.pdf .pdf'):
        new = f[:-9]+'.pdf'
        if os.path.exists(new):
            print('SKIP target exists:', f); continue
        os.rename(f, new); print('renamed:', f)
"
```

## 2. ⚠️ 強制備份 DB(任何 DB 操作前一定要做)

```bash
cp data/app.db "data/app_pre_$(date +%Y%m%d_%H%M).db"
```

## 3. extract_pages → load → prerender

```bash
# 3a. 抽 PDF 頁成 PNG + 收 native text(idempotent)
PYTHONIOENCODING=utf-8 python -m pipeline.extract_pages

# 3b. 跑 loader(idempotent upsert,只更新 ocr_status!='reviewed' 的列)
PYTHONIOENCODING=utf-8 python -m pipeline.load_kao_gu

# 3c. tag 分類(filename → tag 對映)
PYTHONIOENCODING=utf-8 python -m pipeline.tag_kao_gu_from_gongbi

# 3d. 裁掉 PNG 邊緣空白(idempotent:已裁過的會 skip)。Dry-run 先看可用 --dry-run
python scripts/crop_whitespace.py --apply data/question_images
```

## 4. 驗 user-data 沒被動到

```bash
python -c "
import sqlite3
c = sqlite3.connect('data/app.db')
print('users:', c.execute('SELECT COUNT(*) FROM users').fetchone()[0])
print('user_answers:', c.execute('SELECT COUNT(*) FROM user_answers').fetchone()[0])
print('past_paper:', c.execute('SELECT COUNT(*) FROM questions q JOIN sources s ON q.source_id=s.id WHERE s.code=\"past_paper\"').fetchone()[0])
print('multi:', c.execute('SELECT COUNT(*) FROM questions WHERE is_multi=1').fetchone()[0])
"
```

loader 是 idempotent upsert,只動 questions;users/user_answers 應維持不變。
若數字異常變小 → **中止、不要繼續**,先從備份還原。

## 5. 跑 audit(修明顯結構問題)

```bash
# 5a. 純規則 sanity check(立即,0 LLM)
PYTHONIOENCODING=utf-8 python -m tools.sanity_check
# → data/_sanity_<TS>.md

# 5b. 覆蓋率審查(PDF 題號 vs DB)
PYTHONIOENCODING=utf-8 python -m tools.coverage_audit
# → data/_coverage_audit.md
```

### 哪些 finding 必修、哪些可忽略

**必修(parser bug,影響使用者答題)**:
- `options_too_few` — 缺選項
- `answer_not_in_options` — 正解指向空選項(sanity_check 已 `is_multi`-aware,複選不誤報)
- `stem_too_short` (<10) — 多半是 parser 切錯
- `no_tags` — 題目沒對應到 tag,filename → tag 對映壞了
- `past_paper_no_year` — past_paper 列缺年份

**可忽略(內容/格式,不影響答題)**:
- `stem_too_long` (>2000) — 偶爾真的長,看內容
- `duplicate_stem_exact` — 真的同題才算重複

修法:小問題改 parser(`pipeline/parsers/kao_gu.py`)後**重跑 step 3b** loader
(idempotent)。修不了的個案到 admin UI 手動改 + 設 `ocr_status='reviewed'` 保護不
被 loader 覆蓋。

## 6. 更新 db_updated_date(顯示「題庫更新日期」)

`db_updated_date` 由環境變數/設定提供(`backend/config.py` 的預設值)。匯入新批次後
更新它當作更新標記。

---

## Bugs to be aware of(踩過的坑)

- **WAL 未 checkpoint**: 在 API 還開著時直接複製 `app.db` 可能漏掉 `app.db-wal` 裡
  的最新資料。任何複製/搬移 DB 前先停服務,並 `PRAGMA wal_checkpoint(TRUNCATE)`,
  或用 `sqlite3.backup()` 產生乾淨副本。
- **雙副檔名 `.pdf .pdf`**: 一定要先 rename(step 1),否則 slug 對不上 → 重複。
- **多選題格式**: `1. ... 2. ... 答案` 形式需要 multi-select parser 路徑。
- **`A.` 點號選項**: 部分檔改用 `A. B. C.` 而非 `(A) (B) (C)`,parser 已支援兩種。
- **CHECK constraint**: questions/user_answers 的 `letter IN ('A'..'E')` 已放寬,
  可存 `ACE` 多選字串。

---

## 對 Claude / AI agent 的指示

如果你是 AI agent,使用者請你「匯入新題目」「load new questions」之類的要求:
1. 先把這份檔案從頭到尾讀完一次,然後照走 step 1-6。
2. step 2(備份)與 step 4(user-data 驗證)不可跳。
3. step 4 的 user/answer 數量驗證**必須執行**,不對就中止並還原備份。
4. 完成後更新 `db_updated_date` 當作更新標記。
