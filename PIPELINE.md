> **⚠️ Importing a new batch?** This file documents the parser internals only.
> The full end-to-end SOP (file cleanup, audit, backup, verify) lives in
> **[docs/IMPORT_PROCEDURE.md](docs/IMPORT_PROCEDURE.md)** — read that first.

# Question → DB pipeline (standardized)

This is the canonical workflow for taking a fresh PDF (or fix) and getting it
into the practice app. Designed so each step is **idempotent**, **resumable**,
and produces **artifacts you can audit before the next step**.

The app uses a single question source: **past_paper** (考古題). Point the
`PAST_PAPER_DIR` setting (or `.env`) at the directory holding the source PDFs.

```
              ┌─────────────────────────────────────────────────────┐
              │                  source PDFs                        │
              │  $PAST_PAPER_DIR/<year>年解題.pdf  (past_paper)     │
              └────────────────────┬────────────────────────────────┘
                                   │
       ┌──────── stage 1 ─────────►│  fitz extract → page text + images
       │                           │  pipeline/extract_pages.py
       │                           │  pipeline/extract_images*.py
       │                           ▼
       │                  data/ocr_raw/past_paper/<stem>/page_NNN.*
       │                  data/question_images/q_<id>_pPP_iN.png
       │
       ├──── stage 2 ──────────────►  parse → upsert into DB
       │                              pipeline/load_kao_gu.py
       │                              pipeline/parsers/kao_gu.py
       │                              ▼
       │                     data/app.db (idempotent by source_ref)
       │
       ├──── stage 3 ──────────────►  tag classification
       │                              pipeline/tag_kao_gu_from_gongbi.py
       │
       ├──── stage 4 ──────────────►  audit
       │                              tools/sanity_check.py    (rule-based, 0 LLM)
       │                              tools/coverage_audit.py  (PDF count vs DB)
       │
       ├──── stage 5 ──────────────►  fix iteratively
       │                              ▸ patch parser (pipeline/parsers/...)
       │                              ▸ re-run loader (idempotent upsert)
       │                              ▸ re-run sanity_check to verify
       │                              ▸ commit each parser improvement
       │
       └──── stage 6 ──────────────►  ship data/app.db
                                      Use `sqlite3 .backup` for a clean copy
                                      (raw copy of an open DB leaves WAL state
                                      malformed); then restart the API.
```

> Note: pipeline filenames still carry the legacy `kao_gu` slug. The source
> code value they write to the DB is `past_paper`.

## Quick reference

```bash
# Stage 1 — extract pages
PYTHONIOENCODING=utf-8 python -m pipeline.extract_pages

# Stage 2 — re-parse + upsert (idempotent, 'reviewed' rows skipped)
PYTHONIOENCODING=utf-8 python -m pipeline.load_kao_gu

# Stage 3 — tag classification
PYTHONIOENCODING=utf-8 python -m pipeline.tag_kao_gu_from_gongbi

# Stage 4 — audits
python -m tools.sanity_check
python -m tools.coverage_audit
```

## Audit categories — what each finding means

`tools/sanity_check.py` (rule-only):

| Category | Meaning | Action |
|---|---|---|
| `options_too_few` | < 4 non-empty options | parser bug — fix in `pipeline/parsers/` |
| `answer_not_in_options` | `correct_letter` points to empty option | usually paired with `options_too_few` |
| `option_too_long` | option > 2000 chars | parser leaked next question's text → tighten boundary regex |
| `stem_too_short` / `stem_too_long` | stem < 10 or > 2000 | check PDF — sometimes legit |
| `explanation_suspiciously_short` | explanation present but < 10 chars | usually parser fragment; check raw PDF |
| `explanation_no_paragraphs` | > 200 chars but no `\n` | parser collapsed bullets into wall of text → fix `_normalize_paragraphs` |
| `stem_no_paragraphs` | stem > 200 chars on single line | OCR / parser missed sentence breaks |
| `duplicate_stem_exact` | two questions share verbatim stem | same-source = real dup |
| `past_paper_no_year` | past_paper row missing year | upstream bug in `load_kao_gu.py` |
| `no_tags` | question has no tag | run `tag_kao_gu_from_gongbi.py` |

## Idempotency contract

`pipeline.load_kao_gu`:
- Match by `(source_id, source_ref)`
- `existing.ocr_status == 'reviewed'` → SKIP (manual edits preserved)
- otherwise → UPDATE all parsed fields

So you can re-run after every parser tweak without losing manual review state.
The `created`/`updated`/`skipped` counts in the loader output tell you what
happened.

## Manual review path

If a question can't be fixed by the parser (e.g. PDF source itself is missing
characters), the workflow is:

1. Identify the question id
2. Manually fix via the admin UI's vault append flow (or direct SQL if no UI)
3. **Set `ocr_status='reviewed'` and `needs_review=False`** so future loader
   re-runs don't overwrite your fix

## Anti-patterns (don't do these)

- ❌ Raw copy of `data/app.db` while the API is running — uvicorn might be
  holding the WAL → corrupt copy. Use `sqlite3.backup()` first.
- ❌ Skip `__pycache__` cleanup before running parser smoke tests after a code
  change: `find pipeline -name __pycache__ -type d -exec rm -rf {} +`
- ❌ Add a parser regex rule without spot-checking 5+ samples first.

## Future improvements

- Add a `spot_review` tool that walks `needs_review=True` rows in a TUI for
  fast manual review without leaving the terminal.
- Define an explicit `ParserOutput` schema so multiple parsers can be
  composed (e.g. text-extract + image-position → unified Question record).
