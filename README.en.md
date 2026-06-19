Language： [繁體中文](README.md) ｜ **English**

# exam-practice · Specialty Board-Exam Practice Platform

> A past-exam practice system built around **FSRS spaced repetition**. It started as a side project I built to prepare for the **PMR (Physical Medicine & Rehabilitation) specialty board exam** — for colleagues in my program and for myself — and is now cleaned up into an open, self-hostable version that works for any specialty board exam.

🔗 **Live demo**: <https://exam-demo.drpwchen.com> (a demo account is built in — just open and try it)

---

## What it is

One of the most effective ways to prepare for a written board exam is to drill past questions and review the ones you keep getting wrong. Just staring at questions to quiz yourself gets tiring, so I built my own:

- Import **past-exam PDFs** and turn them into a practice-ready question bank.
- Use **FSRS (Free Spaced Repetition Scheduler)** to schedule each question's next appearance from its memory curve — so everything actually sticks.
- Practice alongside the residents in your program and see **everyone's progress and the commonly-missed questions**.
- Project your **exam-day score** from per-topic performance and review state.

> This is a solo, side-project codebase — feel free to fork it for your own specialty.

## Core features

### 1 · Question ingestion (PDF → question bank)
Past exams usually come as PDFs. The repo ships an import pipeline:

```
PDF ──► PyMuPDF text + image extraction ──► auto whitespace crop ──► parser splits stem/options/answer
    ──► idempotent upsert by source_ref ──► rule-based sanity check ──► manual review in the admin console
```

- When text extraction is unreliable, **explanations are shown as a page screenshot** so option/answer text is never garbled by OCR.
- Figure-based questions (X-ray, ultrasound, scales) are extracted and cropped, attached to the stem or explanation.
- The admin console lets you edit any question, with full revision history.

### 2 · Practice engine (FSRS three-pool scheduling)
- Every answer logs **correctness, confidence, and response time**, mapped to an FSRS rating that updates the card's memory state.
- Question selection uses a three-pool strategy: **due reviews (70%) + new (20%) + starred (10%)**, ordered toward the "about to forget but still recoverable" sweet spot.
- Practice modes: smart scheduling / new-question sprint / review (due / wrong-answer bank / starred) / **shared wrong-answer bank** / random.
- Options are **reshuffled each time** (with a recoverable seed) to reduce position-based memorization.

### 3 · Personal & shared wrong-answer bank
- **Personal bank**: automatically collects what you got wrong, auto-classified by **failure mode** — **chronic** (repeated lapses) / **fading** (retrievability already low) / **blind spot** (answered with full confidence yet wrong = overconfidence) / **fresh** (last attempt wrong); you can drill a single weakness type, and star, annotate, or retune a card's frequency.
- **Shared bank**: aggregates the questions everyone misses most (ranked by first-attempt / re-attempt error rate) so a study group can see shared weak spots and shore them up together.

### 4 · Score prediction
- Projects a total-score range from **per-topic accuracy × that topic's real-exam weight × FSRS-estimated retention on exam day** — with Wilson 95% confidence intervals and a "keep reviewing vs. no review" comparison.
- The model splits each topic into **first-attempt accuracy** (your strength on unseen questions) and **post-review accuracy** (your hit rate once a card is relearned); the review scenario **simulates FSRS reviews card-by-card up to exam day** rather than applying a fixed constant.
- 💡 **For an accurate prediction, use this system from your very first exposure to the questions** — first-attempt accuracy is the key signal for "ability on new questions"; if you've already done those questions elsewhere, your first attempt here is inflated and the prediction skews optimistic.

## Demo data & licensing notice

So that anyone can clone and immediately see the full feature set, the repo ships a **sample question bank**:

- **20 real PMR past-exam questions** (year 114) — stems and options, from publicly available exam material.
- The 20 explanations were **written by Claude** with textbook citations (Braddom / DeLisa, etc.), for demonstration only and **may contain errors**.
- Some questions are **AI-generated** to showcase image-based and multiple-select questions, and are not real PMR exam content.
- **All users and answer history are programmatically synthesized** and correspond to no real examinee.

## Tech stack

| Layer | Tech |
|---|---|
| Backend | FastAPI · SQLAlchemy 2.0 · SQLite (WAL) · JWT auth · FSRS-6 |
| Frontend | Vue 3 (Composition API) · Vant 4 · Vite · Pinia · PWA |
| Pipeline | PyMuPDF (fitz) · Pillow · rule-based parser |

## Quick start

Requires Python 3.11+ and Node 18+.

```bash
# 1. Backend
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp env.example .env             # edit SECRET_KEY, ADMIN_USERNAME, etc.
python -m pipeline.seed_demo    # build the sample DB (20 questions + synthetic users/answers)
uvicorn backend.main:app --reload

# 2. Frontend (separate terminal)
cd frontend
npm install
npm run dev                     # open http://localhost:5173
```

The demo account is printed by `seed_demo`. Log in to practice and explore the dashboard, wrong-answer bank, and score prediction.

## Bring your own question bank

1. Drop your past-exam PDFs into `PAST_PAPER_DIR` (set in `.env`).
2. Run the import pipeline (see [`docs/IMPORT_PROCEDURE.md`](docs/IMPORT_PROCEDURE.md)).
3. Check parsing quality with `python -m tools.sanity_check`, then proofread in the admin console.
4. Switching specialties: just replace the question bank and topic taxonomy (`seeds/`) — the engine and scheduler don't change.

> 📋 A full **pre-use setup checklist** (exam date, topic taxonomy, question-tagging) and **copy-paste LLM prompts** for organizing your question bank → [SETUP.md](SETUP.md)

## ⚠️ Security checklist before going live

This is a **multi-user system that stores personal answer data**. Before exposing it publicly:

- **Replace `SECRET_KEY`** with a strong random string (never reuse the example value).
- Give each user a simple default password, then enable **forced password change on first login**.
- Serve over **HTTPS** (ideally behind a reverse proxy / Cloudflare Tunnel).
- Keep the built-in **rate limiting** on the auth endpoints.
- **Never** commit `.env` or a production `data/*.db` (already in `.gitignore`).
- **Back up the database** regularly and lock down the admin account.

## License

[MIT](LICENSE). Fork it, adapt it, use it to prep for your own board exam.

---

*Built by 陳柏威 (Po-Wei Chen) — a PMR physician. If this helps you prep for your boards, a ⭐ is appreciated.*
