"""Build a self-contained demo database for the public sample.

Loads the rewritten past-paper sample questions (+ a few clearly-synthetic demo
questions) from ``demo/``, copies their images into ``data/question_images/``,
then synthesises a small cohort of demo users with plausible answer histories so
the dashboard, wrong-answer bank, cohort analytics and score prediction all
render out of the box.

Everything here is synthetic or independently rewritten — see ``demo/README``.

Run:  python -m pipeline.seed_demo
"""
from __future__ import annotations

import json
import random
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from backend.auth import hash_password
from backend.config import settings
from backend.db import Base, SessionLocal, engine
from backend.models import (
    Question,
    QuestionImage,
    QuestionTag,
    Source,
    Tag,
    User,
    UserAnswer,
    UserQuestionState,
)

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
IMAGES_SRC = DEMO / "images"
DATA_IMAGES = ROOT / "data" / "question_images"

RNG = random.Random(42)          # reproducible demo
NOW = datetime.now(tz=timezone.utc)

# FSRS Rating enum values (fsrs lib): Again=1, Hard=2, Good=3, Easy=4
R_AGAIN, R_HARD, R_GOOD, R_EASY = 1, 2, 3, 4
STATE_REVIEW, STATE_RELEARN = 2, 3


# ───────────────────────── schema reset ─────────────────────────
def reset_db() -> None:
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    db_path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        p = Path(str(db_path) + suffix)
        if p.exists():
            p.unlink()
    Base.metadata.create_all(engine)
    DATA_IMAGES.mkdir(parents=True, exist_ok=True)


# ───────────────────────── reference data ─────────────────────────
def seed_source(s) -> Source:
    src_def = yaml.safe_load((ROOT / "seeds" / "sources.yaml").read_text("utf-8"))[0]
    src = Source(
        code=src_def["code"],
        display_name=src_def["display_name"],
        description=src_def.get("description"),
    )
    s.add(src)
    s.flush()
    return src


def seed_tags(s) -> dict[str, Tag]:
    rows = yaml.safe_load((ROOT / "seeds" / "tags_major.yaml").read_text("utf-8"))
    out: dict[str, Tag] = {}
    for t in rows:
        tag = Tag(
            name=t["code"],
            level="major",
            is_official=True,
            display_name=t.get("display_name"),
            short_zh=t.get("short_zh"),
            sort_order=t.get("sort_order", 999),
        )
        s.add(tag)
        out[t["code"]] = tag
    s.flush()
    return out


# ───────────────────────── questions ─────────────────────────
def _copy_img(name: str) -> None:
    src = IMAGES_SRC / name
    if src.exists():
        shutil.copy2(src, DATA_IMAGES / name)


def seed_questions(s, source: Source, tags: dict[str, Tag]) -> list[Question]:
    real = json.loads((DEMO / "demo_questions.json").read_text("utf-8"))
    synth = json.loads((DEMO / "demo_questions_synthetic.json").read_text("utf-8"))
    questions: list[Question] = []

    for item in real + synth:
        opts = item["options"]
        stem_imgs = item.get("stem_images") or []
        expl_img = item.get("explanation_image")
        has_image = bool(stem_imgs)

        q = Question(
            source_id=source.id,
            source_ref=item["source_ref"],
            year=item.get("year"),
            stem=item["stem"],
            option_a=opts.get("A", ""),
            option_b=opts.get("B", ""),
            option_c=opts.get("C", ""),
            option_d=opts.get("D", ""),
            option_e=opts.get("E"),
            correct_letter=item["correct_letter"],
            is_multi=bool(item.get("is_multi")),
            explanation=item.get("explanation"),
            reference_page=(item.get("citation") or "")[:64] or None,
            has_image=has_image,
            ocr_status="reviewed",
            needs_review=False,
        )
        s.add(q)
        s.flush()

        # tag
        code = item.get("topic_code")
        if code in tags:
            s.add(QuestionTag(question_id=q.id, tag_id=tags[code].id))

        order = 0
        # stem images (synthetic figures only — no copyrighted exam scans)
        for fn in stem_imgs:
            _copy_img(fn)
            s.add(QuestionImage(question_id=q.id, filename=fn,
                                is_explanation=False, display_order=order))
            order += 1
        # explanation pages (our own rendered "PDF-style" pages: 題目頁 + 詳解頁)
        # — two pages so the in-app viewer shows prev/next navigation.
        for pg in (f"expl_{item['n']:02d}.png", f"expl_{item['n']:02d}_2.png"):
            if (IMAGES_SRC / pg).exists():
                _copy_img(pg)
                s.add(QuestionImage(question_id=q.id, filename=pg,
                                    is_explanation=True, display_order=order))
                order += 1

        questions.append(q)
    s.flush()
    return questions


# ───────────────────────── users + answer history ─────────────────────────
def seed_users(s) -> tuple[User, list[User]]:
    pw = hash_password(settings.default_password)
    admin = User(username=settings.admin_username, password_hash=pw,
                 is_admin=True, must_change_password=False)
    # demo is an admin so the public demo can showcase the admin views too;
    # safe because DEMO_MODE makes every write a no-op.
    demo = User(username="demo", password_hash=pw,
                is_admin=True, must_change_password=False)
    s.add_all([admin, demo])
    cohort = [admin, demo]
    for i in range(1, 11):
        u = User(username=f"resident{i:02d}", password_hash=pw,
                 is_admin=False, must_change_password=False)
        s.add(u)
        cohort.append(u)
    s.flush()
    return demo, cohort


def _wrong_pick(correct: str, is_multi: bool) -> str:
    letters = ["A", "B", "C", "D", "E"]
    if is_multi:
        # flip one membership to produce a wrong set
        cset = set(correct)
        cand = RNG.choice(letters)
        if cand in cset:
            cset.discard(cand)
        else:
            cset.add(cand)
        return "".join(sorted(cset)) or "A"
    wrong = [l for l in letters if l != correct]
    return RNG.choice(wrong)


def simulate(s, user: User, questions: list[Question], *, skill: float,
             coverage: float) -> None:
    """Create plausible answer history + FSRS state for one user."""
    for q in questions:
        if RNG.random() > coverage:
            continue  # leaves the card in the "new" pool
        reps = RNG.choices([1, 2, 3, 4], weights=[4, 3, 2, 1])[0]
        # spread reviews over the last ~45 days
        first = NOW - timedelta(days=RNG.uniform(5, 45), hours=RNG.uniform(0, 12))
        gap = (NOW - first) / max(reps, 1)
        last_correct = True
        for r in range(reps):
            at = first + gap * r + timedelta(hours=RNG.uniform(0, 6))
            # earlier reps are harder; mastery grows with reps
            p = min(0.97, max(0.18, skill - 0.12 * (reps - 1 - r) + RNG.uniform(-0.1, 0.1)))
            correct = RNG.random() < p
            last_correct = correct
            conf = RNG.choices([1, 2, 3], weights=[3, 4, 3])[0]
            if not correct:
                rating, sel = R_AGAIN, _wrong_pick(q.correct_letter, q.is_multi)
            else:
                rating = {1: R_HARD, 2: R_GOOD, 3: R_EASY}[conf]
                sel = q.correct_letter
            s.add(UserAnswer(
                user_id=user.id, question_id=q.id, answered_at=at,
                selected_letter=sel, is_correct=correct, confidence=conf,
                time_ms=RNG.randint(4000, 30000),
                shuffle_seed=RNG.randint(0, 2**31 - 1), fsrs_rating=rating,
            ))
        # final card state
        if last_correct:
            stability = RNG.uniform(6, 55)
            state = STATE_REVIEW
        else:
            stability = RNG.uniform(0.4, 6)
            state = STATE_RELEARN
        last_review = first + gap * (reps - 1)
        # ~45% overdue (due in the past) so the review pool is populated
        if RNG.random() < 0.45:
            due = NOW - timedelta(days=RNG.uniform(0, 8))
        else:
            due = NOW + timedelta(days=stability * RNG.uniform(0.4, 1.1))
        s.add(UserQuestionState(
            user_id=user.id, question_id=q.id,
            stability=round(stability, 2),
            difficulty=round(RNG.uniform(3.5, 8.0), 2),
            state=state, step=0, due_at=due, last_review_at=last_review,
            reps=reps, lapses=sum(1 for _ in range(reps) if RNG.random() < 0.25),
            priority_boost=1.0,
            is_starred=(RNG.random() < 0.12),
        ))


def main() -> None:
    reset_db()
    s = SessionLocal()
    try:
        source = seed_source(s)
        tags = seed_tags(s)
        questions = seed_questions(s, source, tags)
        demo, cohort = seed_users(s)

        for u in cohort:
            if u.username == "demo":
                skill, coverage = 0.72, 0.85   # the public demo login — rich, mid profile
            elif u.is_admin:
                skill, coverage = 0.80, 0.90
            else:
                skill, coverage = RNG.uniform(0.5, 0.92), RNG.uniform(0.55, 1.0)
            simulate(s, u, questions, skill=skill, coverage=coverage)

        s.commit()
        n_ans = s.query(UserAnswer).count()
        print(f"✓ seeded {len(questions)} questions, {len(cohort)} users, "
              f"{n_ans} answers into {settings.database_url}")
        print(f"  demo login →  username: demo   password: {settings.default_password}")
        print(f"  admin login → username: {settings.admin_username}   "
              f"password: {settings.default_password}")
    finally:
        s.close()


if __name__ == "__main__":
    main()
