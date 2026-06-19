"""GET /api/practice/next, POST /api/practice/answer."""

import random
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.auth import create_answer_token, decode_answer_token
from backend.config import settings
from backend.deps import get_current_user, get_db, require_password_changed
from backend.fsrs_service import apply_answer
from backend.routers.preferences import load_study_config
from backend.models import (
    Question,
    QuestionImage,
    QuestionParaphrase,
    QuestionTag,
    Source,
    Tag,
    User,
    UserAnswer,
    UserNote,
    UserQuestionState,
)
from backend.schemas import (
    AnswerResponse,
    AnswerSubmit,
    ImageRef,
    NextQuestionResponse,
    OptionDisplay,
)
from backend.services import question_picker
from backend.services.shuffle import (
    display_to_original,
    make_seed,
    original_to_display,
    remap_letter_refs,
    shuffle_letters,
)

router = APIRouter(prefix="/api/practice", tags=["practice"])


def _existing_letters(q: Question) -> list[str]:
    out = ["A", "B", "C", "D"]
    if q.option_e:
        out.append("E")
    return out


def _option_text(q: Question, letter: str) -> str:
    return getattr(q, f"option_{letter.lower()}") or ""


def _pick_paraphrase(
    db: Session, question_id: int
) -> QuestionParaphrase | None:
    """Return a random approved paraphrase variant, or None."""
    rows = db.execute(
        select(QuestionParaphrase).where(
            QuestionParaphrase.question_id == question_id,
            QuestionParaphrase.status == "approved",
        )
    ).scalars().all()
    return random.choice(rows) if rows else None


@router.get("/cohort-tiers")
def cohort_tier_counts(
    source_code: str | None = Query(None, description="past_paper"),
    tag_ids: str | None = Query(None, description="comma-separated tag ids"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Per-tier candidate counts for the 大家錯題 difficulty picker labels.

    Returns {hardest, hard, common}. Same access gate as the 大家的錯題本.
    """
    if not (user.is_admin or settings.cohort_wrongbook_public):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "「大家錯題」尚未開放")

    source_id: int | None = None
    if source_code:
        src = db.execute(
            select(Source).where(Source.code == source_code)
        ).scalar_one_or_none()
        if src is not None and user.has_source(src.code):
            source_id = src.id

    tag_id_list: list[int] = []
    if tag_ids:
        try:
            tag_id_list = [int(t) for t in tag_ids.split(",") if t.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad tag_ids")

    allowed_src_ids: list[int] | None = None
    if user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_src_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ]

    return question_picker.count_cohort_tiers(
        db, source_id=source_id, tag_ids=tag_id_list,
        allowed_source_ids=allowed_src_ids, user_id=user.id,
    )


@router.get("/review-counts")
def review_pool_counts(
    source_code: str | None = Query(None, description="past_paper"),
    tag_ids: str | None = Query(None, description="comma-separated tag ids"),
    year: int | None = Query(None, description="past_paper single year"),
    years: str | None = Query(None, description="past_paper comma-separated years"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Available-question counts for the 複習 sub-modes (到期/自己常錯/星號 and
    per-failure-mode), respecting the same source/tag/year filters. Personal
    data only — no cohort gate."""
    source_id: int | None = None
    if source_code:
        src = db.execute(
            select(Source).where(Source.code == source_code)
        ).scalar_one_or_none()
        if src is not None and user.has_source(src.code):
            source_id = src.id
        else:
            # Unknown/forbidden source → no candidates.
            return {"due": 0, "wrong": 0, "starred": 0,
                    "wrong_kinds": {"lapse": 0, "forget": 0, "blind": 0, "recent": 0}}

    is_past_paper = source_code == "past_paper"
    effective_year = year if is_past_paper else None
    effective_years: list[int] = []
    if is_past_paper and years:
        try:
            effective_years = [int(y) for y in years.split(",") if y.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad years")

    tag_id_list: list[int] = []
    if tag_ids:
        try:
            tag_id_list = [int(t) for t in tag_ids.split(",") if t.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad tag_ids")

    allowed_src_ids: list[int] | None = None
    if user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_src_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ]

    return question_picker.count_review_pools(
        db, user_id=user.id, source_id=source_id, tag_ids=tag_id_list,
        year=effective_year, years=effective_years,
        allowed_source_ids=allowed_src_ids,
    )


@router.get("/next", response_model=NextQuestionResponse)
def next_question(
    source_code: str = Query(..., description="past_paper"),
    tag_id: int | None = Query(None, description="single tag (legacy)"),
    tag_ids: str | None = Query(None, description="comma-separated tag ids (multi-select)"),
    year: int | None = Query(None, description="optional past_paper year filter (single)"),
    years: str | None = Query(None, description="optional comma-separated year list (multi)"),
    mode: str = Query(
        "smart",
        description="smart=FSRS three-pool / new=only new / review=only due / wrong=missed/lapsed / starred=星號題 by 出錯可能性 / cohort_wrong=大家錯題 by 再答錯誤率 / random=random",
    ),
    cohort_level: str = Query(
        "common",
        description="大家錯題 tier: hardest=最難 / hard=偏難 / common=大家常錯",
    ),
    cohort_exclude_recent_days: int = Query(
        0,
        ge=0,
        description="大家錯題: skip questions answered in the last N days (0=不排除)",
    ),
    wrong_kinds: str | None = Query(
        None,
        description="自己的常錯題 failure-mode filter: comma-separated subset of lapse/forget/blind/recent (empty or 'all' = whole pool)",
    ),
    wrong_exclude_recent_days: int = Query(
        0,
        ge=0,
        description="自己的常錯題: skip questions answered in the last N days (0=不排除)",
    ),
    starred_exclude_recent_days: int = Query(
        0,
        ge=0,
        description="星號題: skip questions answered in the last N days (0=不排除)",
    ),
    exclude_mastered: bool = Query(False, description="skip cards with stability > 30 days"),
    shuffle: bool = Query(True, description="if false, options preserve original A-E order"),
    use_paraphrase: bool = Query(
        False,
        description="when True, serve an approved paraphrase variant if one exists; falls back to original silently",
    ),
    exclude_qids: str | None = Query(
        None,
        description="comma-separated qids to skip — typically session-already-answered correctly + relearn-pending",
    ),
    priority_qids: str | None = Query(
        None,
        description="comma-separated qids to prefer — typically relearn cards whose in-session retry-after has elapsed",
    ),
    db: Session = Depends(get_db),
    user: User = Depends(require_password_changed),
) -> NextQuestionResponse:
    source = db.execute(select(Source).where(Source.code == source_code)).scalar_one_or_none()
    if source is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Unknown source")
    if not user.has_source(source.code):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "source not allowed for this user"
        )

    # 大家錯題 mode exposes cohort aggregate signal — same gate as the
    # 大家的錯題本 page (admin always; everyone once public flag flips on).
    if mode == "cohort_wrong" and not (
        user.is_admin or settings.cohort_wrongbook_public
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "「大家錯題」尚未開放"
        )

    # Year filter only meaningful for past_paper
    effective_year = year if source.code == "past_paper" else None
    effective_years: list[int] = []
    if source.code == "past_paper" and years:
        try:
            effective_years = [int(y) for y in years.split(",") if y.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad years")

    # Resolve tag_ids list (multi) — fallback to tag_id (single)
    tag_id_list: list[int] = []
    if tag_ids:
        try:
            tag_id_list = [int(t) for t in tag_ids.split(",") if t.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad tag_ids")
    elif tag_id:
        tag_id_list = [tag_id]

    def _parse_qid_csv(s: str | None) -> list[int]:
        if not s:
            return []
        try:
            return [int(t) for t in s.split(",") if t.strip()]
        except ValueError:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Bad qids list")

    exclude_set = set(_parse_qid_csv(exclude_qids))
    priority_list = _parse_qid_csv(priority_qids)
    wrong_kind_list = [k for k in (wrong_kinds.split(",") if wrong_kinds else []) if k.strip()]

    # Per-user allowed source ids (None = full access). Even with `source.id`
    # already pinned to one value, pass the list through so internal helpers
    # (and any future code path that defaults source) cannot leak forbidden
    # questions.
    allowed_src_ids: list[int] | None = None
    if user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_src_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ]

    qid = question_picker.pick_next_question_id(
        db,
        user_id=user.id,
        source_id=source.id,
        tag_ids=tag_id_list,
        exclude_qids=exclude_set,
        priority_qids=priority_list,
        year=effective_year,
        years=effective_years,
        mode=mode,
        exclude_mastered=exclude_mastered,
        allowed_source_ids=allowed_src_ids,
        cohort_level=cohort_level,
        cohort_exclude_recent_days=cohort_exclude_recent_days,
        wrong_kinds=wrong_kind_list,
        wrong_exclude_recent_days=wrong_exclude_recent_days,
        starred_exclude_recent_days=starred_exclude_recent_days,
        max_new_per_session=load_study_config(db, user.id)["max_new_per_session"],
    )
    if qid is None:
        from backend.access_log import log_next
        log_next(
            user_id=user.id, mode=mode, source_code=source_code,
            tag_ids=tag_id_list, exclude_count=len(exclude_set),
            priority_count=len(priority_list), returned_qid=None,
            due_at_iso=None, state=None, step=None,
        )
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No questions available")

    q = db.get(Question, qid)
    if not q:
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Picker returned missing q")

    # Diagnostic: log which qid the picker returned + its FSRS state snapshot.
    # Captures the picker's view of due_at so cycling bugs can be postmortemed.
    from backend.access_log import log_next
    _picked_state = db.execute(
        select(UserQuestionState.due_at, UserQuestionState.state, UserQuestionState.step)
        .where(UserQuestionState.user_id == user.id, UserQuestionState.question_id == qid)
    ).one_or_none()
    log_next(
        user_id=user.id, mode=mode, source_code=source_code,
        tag_ids=tag_id_list, exclude_count=len(exclude_set),
        priority_count=len(priority_list), returned_qid=qid,
        due_at_iso=(_picked_state[0].isoformat() if _picked_state and _picked_state[0] else None),
        state=(_picked_state[1] if _picked_state else None),
        step=(_picked_state[2] if _picked_state else None),
    )

    letters = _existing_letters(q)
    if shuffle:
        seed = make_seed()
    else:
        # Identity seed: shuffle_letters with this seed returns letters in
        # original order (we use seed=0 marker; service treats it as no-op).
        seed = 0
    shuffled = shuffle_letters(seed, letters) if shuffle else list(letters)

    # Choose text source: paraphrase variant (if requested + available) or original.
    paraphrase = _pick_paraphrase(db, q.id) if use_paraphrase else None
    if paraphrase is not None:
        served_stem = paraphrase.stem
        text_for = lambda L: getattr(paraphrase, f"option_{L.lower()}") or ""  # noqa: E731
    else:
        served_stem = q.stem
        text_for = lambda L: _option_text(q, L)  # noqa: E731

    options = [
        OptionDisplay(display=chr(ord("A") + i), text=text_for(orig))
        for i, orig in enumerate(shuffled)
    ]
    token = create_answer_token(user.id, q.id, seed)

    img_rows = db.execute(
        select(QuestionImage).where(QuestionImage.question_id == q.id).order_by(
            QuestionImage.display_order
        )
    ).scalars().all()
    stem_image_urls = [
        f"/api/static/images/{r.filename}" for r in img_rows if not r.is_explanation
    ]
    explanation_image_urls = [
        f"/api/static/images/{r.filename}" for r in img_rows if r.is_explanation
    ]
    explanation_images = [
        ImageRef(
            url=f"/api/static/images/{r.filename}",
            option_letter=r.option_letter,
        )
        for r in img_rows
        if r.is_explanation
    ]

    tag_names = [
        n
        for (n,) in db.execute(
            select(Tag.name)
            .join(QuestionTag, QuestionTag.tag_id == Tag.id)
            .where(QuestionTag.question_id == q.id)
        ).all()
    ]

    note = db.execute(
        select(UserNote).where(UserNote.user_id == user.id, UserNote.question_id == q.id)
    ).scalar_one_or_none()
    state = db.execute(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id == q.id,
        )
    ).scalar_one_or_none()

    real_source_code = source_code
    # Build label like "112 年 第 7 題" for past_paper (source_ref = "112/Q007")
    question_label: str | None = None
    if q.year is not None and q.source_ref and "/Q" in q.source_ref:
        try:
            qnum = int(q.source_ref.split("/Q")[1])
            question_label = f"{q.year} 年 第 {qnum} 題"
        except ValueError:
            pass
    original_options_text = (
        {L: _option_text(q, L) for L in letters} if paraphrase is not None else None
    )
    return NextQuestionResponse(
        answer_token=token,
        question_id=q.id,
        source_code=real_source_code,
        stem=served_stem,
        options=options,
        stem_image_urls=stem_image_urls,
        explanation_image_urls=explanation_image_urls,
        explanation_images=explanation_images,
        tags=tag_names,
        has_user_note=note is not None,
        is_starred=bool(state and state.is_starred),
        priority_boost=state.priority_boost if state else 1.0,
        year=q.year,
        question_label=question_label,
        paraphrase_id=paraphrase.id if paraphrase else None,
        paraphrase_variant_idx=paraphrase.variant_idx if paraphrase else None,
        original_stem=q.stem if paraphrase else None,
        original_options_text=original_options_text,
        is_multi=bool(q.is_multi),
    )


@router.post("/answer", response_model=AnswerResponse)
def submit_answer(
    body: AnswerSubmit,
    db: Session = Depends(get_db),
    user: User = Depends(require_password_changed),
) -> AnswerResponse:
    payload = decode_answer_token(body.answer_token)
    if not payload:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Invalid or expired answer token")
    if payload["uid"] != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Token does not belong to current user")
    qid = int(payload["qid"])
    seed = int(payload["seed"])

    q = db.get(Question, qid)
    if q is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question gone")

    # Defence in depth: forbid submitting answers for sources the user can't access
    src_code_check = db.execute(
        select(Source.code).where(Source.id == q.source_id)
    ).scalar_one()
    if not user.has_source(src_code_check):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "source not allowed for this user"
        )

    letters = _existing_letters(q)
    valid_displays = {chr(ord("A") + i) for i in range(len(letters))}

    if q.is_multi:
        # Multi-select: selected_display is concatenated letters, e.g. "ACE".
        # Empty string = abstain (scored as wrong; lets users skip on confidence).
        selected_set = set(body.selected_display)
        if not selected_set.issubset(valid_displays):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"selected_display out of range for {len(letters)}-option question",
            )
        selected_originals = sorted(
            display_to_original(seed, d, letters) for d in selected_set
        )
        selected_original = "".join(selected_originals)
        correct_originals = sorted(set(q.correct_letter))
        correct_display = "".join(
            sorted(original_to_display(seed, o, letters) for o in correct_originals)
        )
        is_correct = selected_original == "".join(correct_originals)
    else:
        if body.selected_display not in valid_displays:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"selected_display out of range for {len(letters)}-option question",
            )
        selected_original = display_to_original(seed, body.selected_display, letters)
        correct_display = original_to_display(seed, q.correct_letter, letters)
        is_correct = selected_original == q.correct_letter

    # Get/create state
    state = db.execute(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id == q.id,
        )
    ).scalar_one_or_none()
    if state is None:
        state = UserQuestionState(user_id=user.id, question_id=q.id)
        db.add(state)
        db.flush()

    study_cfg = load_study_config(db, user.id)
    rating, new_due = apply_answer(
        state, is_correct, body.confidence, body.time_ms,
        q.median_time_ms,
        request_retention=study_cfg["request_retention"],
    )
    # Auto-resurrect from wrong-bank: a user can dismiss a question from
    # their 錯題本, but if they answer it wrong again, it should come back.
    if not is_correct and state.dismissed_from_wrong_bank:
        state.dismissed_from_wrong_bank = False

    answer = UserAnswer(
        user_id=user.id,
        question_id=q.id,
        answered_at=datetime.now(tz=timezone.utc),
        selected_letter=selected_original,
        is_correct=is_correct,
        confidence=body.confidence,
        time_ms=body.time_ms,
        shuffle_seed=seed,
        fsrs_rating=int(rating),
    )
    db.add(answer)
    db.flush()

    # Forensic log: structured record so if user_answers gets wiped, we can
    # replay from data/access.log. See backend/access_log.py + 2026-05-11
    # incident postmortem in memory/reference_exam_practice_deploy.md.
    from backend.access_log import log_answer
    log_answer(
        user_id=user.id,
        question_id=q.id,
        selected_letter=selected_original,
        correct_letter=q.correct_letter,
        is_correct=is_correct,
        confidence=body.confidence,
        time_ms=body.time_ms,
        shuffle_seed=seed,
    )

    # Lazy update median_time_ms (every 5 answers)
    counts = db.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.question_id == q.id)
    ).scalar_one()
    if counts and counts % 5 == 0:
        new_median = db.execute(
            select(func.avg(UserAnswer.time_ms)).where(UserAnswer.question_id == q.id)
        ).scalar()
        if new_median:
            q.median_time_ms = int(new_median)

    db.commit()

    original_options = {
        L: getattr(q, f"option_{L.lower()}") or "" for L in letters
    }
    # Friendly source label so user can find the original
    source_info: str | None = None
    src_code = db.execute(select(Source.code).where(Source.id == q.source_id)).scalar_one()
    if src_code == "past_paper" and q.year is not None:
        source_info = f"{q.year}年解題.pdf"
    return AnswerResponse(
        answer_id=answer.id,
        correct_letter=q.correct_letter,
        correct_display=correct_display,
        selected_display=body.selected_display,
        selected_letter=selected_original,
        is_correct=is_correct,
        explanation=q.explanation,
        explanation_remapped=remap_letter_refs(q.explanation, seed, letters),
        reference_page=q.reference_page,
        fsrs_rating=int(rating),
        next_due=new_due,
        original_options=original_options,
        was_shuffled=(seed != 0),
        source_info=source_info,
        dispute_note=q.dispute_note,
    )
