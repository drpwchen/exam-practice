# Architecture

A single FastAPI process serves the JSON API and the built Vue SPA. SQLite
(WAL mode) is the only datastore — no external services required to run.

```
Browser ──HTTP──► FastAPI ──► SQLAlchemy ──► SQLite (data/app.db)
  (Vue SPA)         │
                    ├─ JWT auth (bcrypt + HS256)
                    ├─ FSRS-6 scheduler (per-card memory state)
                    └─ /api/static/images/*  (question + explanation images)
```

## Layout

```
backend/        FastAPI app
  main.py         entrypoint, router mounts, SPA + image serving
  config.py       env-driven settings (.env via pydantic-settings)
  db.py           engine, session, UTC datetime type
  models.py       SQLAlchemy ORM (all tables)
  schemas.py      Pydantic request/response models
  auth.py         bcrypt + JWT + short-lived answer tokens
  fsrs_service.py FSRS-6 wrapper (rating + scheduling)
  routers/        one module per API area (practice, dashboard, wrong_bank, …)
  services/       question_picker (3-pool selection), shuffle (option scramble)

frontend/       Vue 3 + Vant 4 + Vite + Pinia PWA
  src/views/      one .vue per route (Practice, Feedback, Dashboard, …)
  src/components/ shared UI (PdfViewer, HelpButton, …)
  src/api.js      axios instance + auth interceptor
  src/stores/     Pinia (auth, current practice session)

pipeline/       PDF → question-bank import + seed_demo
tools/          rule-based QA (sanity_check, coverage_audit, …)
seeds/          sources + topic taxonomy (YAML)
demo/           the public sample dataset (see demo/README)
```

## Data model (key tables)

| Table | Purpose |
|---|---|
| `users` | accounts; `is_admin`, `allowed_sources` allowlist |
| `sources` | question sources (the public build ships one: `past_paper`) |
| `tags` | topic taxonomy (major subspecialties) |
| `questions` / `question_tags` / `question_images` | the question bank |
| `question_revisions` | full edit history for admin corrections |
| `user_answers` | every answer (correctness, confidence, time, FSRS rating) |
| `user_question_state` | per-(user,question) FSRS-6 card state + overrides |
| `user_notes` / `user_preferences` | per-user notes and settings |

## The three pillars in code

1. **Ingestion** — `pipeline/` extracts text + images from PDFs, parses
   stems/options/answers, and idempotently upserts by `(source_id, source_ref)`.
   `tools/sanity_check.py` flags parse problems; the admin console fixes them.
2. **Practice** — `services/question_picker.py` selects the next question from a
   three-pool mix (due reviews / new / starred). `fsrs_service.py` maps the
   answer to an FSRS rating and reschedules the card. Options are shuffled with a
   recoverable seed (`services/shuffle.py`).
3. **Prediction & cohort** — `routers/lab_dashboard.py` projects a score from
   per-topic accuracy × exam weight × FSRS retrievability on the exam date.
   `routers/wrong_bank.py` (cohort) and `routers/admin_cohort.py` aggregate
   across users for the shared wrong-answer bank.

## Scheduler design notes

The interesting parts of `services/question_picker.py`, for the curious:

- **Exam-cram "sweet spot" ordering.** Due cards aren't ordered most-overdue-
  first. Urgency = `R^1.4 · (1−R)^0.6`, a tent function peaking at **R ≈ 0.7**
  (about to forget but still recoverable) — the best return on limited pre-exam
  time. Deeply-forgotten cards (`R < 0.5`) would otherwise flood the queue, so
  they're **rate-limited to a 1-in-4 interleave** instead of dominating.
- **Three pools by weighted draw, not fixed quotas.** "70/20/10" (due / new /
  starred) is a per-draw weighted dice roll with fallback when a pool is empty;
  new cards are capped per session, with an auto-reset so the user never gets
  stuck on an exhausted pool.
- **Unified risk score.** The wrong-bank, starred, and frequently-wrong views
  all rank by the same `risk = (1−R) · (1 + 0.3·lapses) · (1 + 0.5·conf3_wrong)`
  — "most likely to fail me again" rather than "what to study next". The
  `conf3_wrong` term penalises answers given with full confidence yet wrong.
- **"No mastery, no escape."** New-only mode keeps a question in the new pool
  until it's answered *correctly* (not merely seen); the "exclude recently
  practised" filter only excludes questions whose **last** attempt was correct —
  ones you got wrong keep coming back.

## Auth

- Login returns a 7-day JWT carried in `Authorization: Bearer`.
- Each served question also issues a short-lived **answer token** (30 min)
  binding `user_id + question_id + shuffle_seed`, so the server can verify the
  submitted display letter against the original without trusting the client.

## Demo data

The public repo ships no production database. `pipeline/seed_demo.py` builds
`data/app.db` from `demo/` — 20 rewritten past-paper questions, a few synthetic
demo questions, and a synthetic cohort with plausible answer histories so every
view renders. See `demo/README` for the licensing notes.
