"""Independent 錯題區: starred (manual) + frequently-wrong (auto)."""

import math
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.deps import get_current_user, get_db
from backend.models import (
    Question,
    QuestionTag,
    Source,
    Tag,
    User,
    UserAnswer,
    UserNote,
    UserQuestionState,
)

router = APIRouter(prefix="/api/wrong-bank", tags=["wrong-bank"])

# `risk` is the new default — composite re-fail probability.
# Legacy `smart` (FSRS retrievability ascending) removed: it answered
# "what to study next", which is the practice-flow lens. WrongBank's lens
# is "what's about to fail me again", so we score on R + lapses + conf-conflict.
VALID_SORTS = {"risk", "lapses", "recent_wrong", "tag", "starred"}

# Category filters layered on top of `kind`. Each tags a card with one or
# more failure modes. They are not mutually exclusive — a card can be both
# 死穴 (chronic) and 盲區 (overconfident).
VALID_KINDS = {"all", "star", "auto", "lapse", "forget", "blind", "recent"}

CONF3_WINDOW_DAYS = 30
FORGET_R_THRESHOLD = 0.6   # cards with R below this counted as 易忘
LAPSE_THRESHOLD = 2        # lapses ≥ N counted as 死穴


def _resolve_target_user(
    current_user: User, as_user: int | None, db: Session
) -> User:
    """Mirror dashboard's pattern — admin can view another user's wrong bank."""
    if as_user is None:
        return current_user
    if not current_user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required for as_user")
    target = db.get(User, as_user)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    return target


def active_user_count(db: Session, source_id: int) -> int:
    """活躍使用者 = 在此題庫來源中至少作答過 1 題的相異 user 數.

    Denominator for cohort wrong-rate thresholds and displays. Counting from
    UserAnswer keeps it source-scoped (a user who only practised another source
    is not counted here). Returns 0 if nobody has answered.
    """
    return int(
        db.execute(
            select(func.count(func.distinct(UserAnswer.user_id)))
            .join(Question, Question.id == UserAnswer.question_id)
            .where(Question.source_id == source_id)
        ).scalar()
        or 0
    )


class WrongBankCard(BaseModel):
    question_id: int
    source_code: str
    year: int | None
    question_label: str | None         # "112 年 第 47 題" for past_paper
    stem: str
    correct_letter: str
    correct_text: str
    options: dict[str, str]            # {A: text, ...} for 顯示所有選項
    tags: list[str]
    is_starred: bool
    is_auto_wrong: bool                # True if lapses>=1 OR last attempt wrong
    lapses: int
    last_attempt_at: datetime | None
    last_was_wrong: bool
    note: str | None = None            # user's心得
    # Risk-mode signals (always populated, used by sort=risk and category badges)
    r_now: float                       # current FSRS retrievability, 0..1
    conf3_wrong_30d: int               # times answered conf=3 yet wrong in last 30d
    risk_score: float                  # (1-R) × (1+0.3·lapses) × (1+0.5·conf3)
    categories: list[str]              # subset of {lapse, forget, blind, recent}


@router.get("", response_model=list[WrongBankCard])
def list_cards(
    kind: str = Query(
        "all",
        description=(
            "all / star / auto"
            " / lapse (≥2 lapses, 死穴)"
            " / forget (R<60% in Review, 易忘)"
            " / blind (recent conf=3 wrong, 盲區)"
            " / recent (last attempt wrong, lapses<2, 新錯)"
        ),
    ),
    source_code: str | None = Query(None),
    year: int | None = Query(None),
    tag_id: int | None = Query(None),
    sort: str = Query(
        "risk",
        description=(
            "risk=composite re-fail probability (default) / lapses / "
            "recent_wrong / tag / starred"
        ),
    ),
    include_dismissed: bool = Query(
        False, description="include rows the user has dismissed from 錯題本"
    ),
    limit: int = Query(200, ge=1, le=500),
    as_user: int | None = Query(None, description="admin-only: view another user's wrong bank"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[WrongBankCard]:
    if sort not in VALID_SORTS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bad sort: {sort}")
    if kind not in VALID_KINDS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bad kind: {kind}")
    user = _resolve_target_user(current_user, as_user, db)
    # 1. Most recent attempt per question (for is_auto_wrong)
    sub_last = (
        select(
            UserAnswer.question_id,
            func.max(UserAnswer.answered_at).label("last_at"),
        )
        .where(UserAnswer.user_id == user.id)
        .group_by(UserAnswer.question_id)
        .subquery()
    )
    last_join = (
        select(
            UserAnswer.question_id,
            UserAnswer.is_correct,
            UserAnswer.answered_at,
        )
        .join(
            sub_last,
            and_(
                sub_last.c.question_id == UserAnswer.question_id,
                sub_last.c.last_at == UserAnswer.answered_at,
            ),
        )
        .where(UserAnswer.user_id == user.id)
        .subquery()
    )

    # 2. Build candidate question ids
    starred_q = (
        select(UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.is_starred == True,  # noqa: E712
        )
    )
    auto_wrong_q = (
        select(UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.lapses >= 1,
        )
    ).union(
        select(last_join.c.question_id).where(last_join.c.is_correct == False)  # noqa: E712
    )

    starred_set_pre = {qid for (qid,) in db.execute(starred_q).all()}
    auto_set = {qid for (qid,) in db.execute(auto_wrong_q).all()}
    base_set = starred_set_pre | auto_set

    # Pre-compute per-question signals needed to evaluate category filters and
    # later to score risk. We compute these once over the base candidate set.
    now = datetime.now(tz=timezone.utc)
    conf3_cutoff = now - timedelta(days=CONF3_WINDOW_DAYS)

    # state lookup (lapses, stability, last_review_at, current state)
    state_map: dict[int, UserQuestionState] = {}
    if base_set:
        state_map = {
            s.question_id: s for s in db.scalars(
                select(UserQuestionState).where(
                    UserQuestionState.user_id == user.id,
                    UserQuestionState.question_id.in_(base_set),
                )
            ).all()
        }

    # conf3_wrong_30d per question
    conf3_map: dict[int, int] = {}
    if base_set:
        for qid, n in db.execute(
            select(UserAnswer.question_id, func.count(UserAnswer.id))
            .where(
                UserAnswer.user_id == user.id,
                UserAnswer.confidence == 3,
                UserAnswer.is_correct == False,  # noqa: E712
                UserAnswer.answered_at >= conf3_cutoff,
                UserAnswer.question_id.in_(base_set),
            )
            .group_by(UserAnswer.question_id)
        ).all():
            conf3_map[qid] = n

    # last attempt per qid (already built earlier as last_join, materialise it)
    last_map = {
        r.question_id: r for r in db.execute(
            select(last_join).where(last_join.c.question_id.in_(base_set))
        ).all()
    } if base_set else {}

    def _r_now(st: UserQuestionState | None) -> float:
        # FSRS-6 retrievability R(t) = (1 + t/(9·S))^-1, lower = more forgotten.
        # Treat unstabilised cards as fully forgotten (worst case).
        if st is None or st.stability is None or st.last_review_at is None:
            return 0.0
        last = st.last_review_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        t_days = max(0.0, (now - last).total_seconds() / 86400.0)
        s = max(0.01, st.stability)
        return (1.0 + t_days / (9.0 * s)) ** -1

    def _categories(qid: int) -> list[str]:
        st = state_map.get(qid)
        last = last_map.get(qid)
        cats: list[str] = []
        if st and st.lapses >= LAPSE_THRESHOLD:
            cats.append("lapse")
        if st and st.state == 2 and _r_now(st) < FORGET_R_THRESHOLD:
            cats.append("forget")
        if conf3_map.get(qid, 0) >= 1:
            cats.append("blind")
        if last and not last.is_correct and (st.lapses if st else 0) < LAPSE_THRESHOLD:
            cats.append("recent")
        return cats

    # Apply kind filter to derive working qid_set
    if kind == "star":
        qid_set = set(starred_set_pre)
    elif kind == "auto":
        qid_set = set(auto_set)
    elif kind == "all":
        qid_set = set(base_set)
    else:
        # category-based filter — keep only cards tagged with that category
        qid_set = {q for q in base_set if kind in _categories(q)}

    if not include_dismissed:
        dismissed = {
            qid for (qid,) in db.execute(
                select(UserQuestionState.question_id).where(
                    UserQuestionState.user_id == user.id,
                    UserQuestionState.dismissed_from_wrong_bank == True,  # noqa: E712
                )
            ).all()
        }
        # Starred rows ignore dismiss (user explicitly wants to keep)
        qid_set = (qid_set - dismissed) | (qid_set & starred_set_pre)

    if not qid_set:
        return []

    starred_set = starred_set_pre  # alias for downstream readability
    state_lookup = state_map       # already populated over base_set ⊇ qid_set
    last_lookup = last_map

    stmt = (
        select(
            Question,
            Source.code,
            func.coalesce(UserQuestionState.is_starred, False).label("starred"),
            func.coalesce(UserQuestionState.lapses, 0).label("lapses_col"),
            func.coalesce(UserQuestionState.difficulty, 5.0).label("difficulty"),
            func.coalesce(UserQuestionState.priority_boost, 1.0).label("boost"),
            UserQuestionState.stability,
            UserQuestionState.last_review_at,
            UserQuestionState.due_at,
        )
        .join(Source, Source.id == Question.source_id)
        .outerjoin(
            UserQuestionState,
            and_(
                UserQuestionState.question_id == Question.id,
                UserQuestionState.user_id == user.id,
            ),
        )
        .where(Question.id.in_(qid_set))
    )
    if source_code:
        s = db.execute(select(Source).where(Source.code == source_code)).scalar_one_or_none()
        if s is not None:
            if not user.has_source(s.code):
                return []
            stmt = stmt.where(Question.source_id == s.id)
    elif user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ] or [-1]
        stmt = stmt.where(Question.source_id.in_(allowed_ids))
    if year is not None:
        stmt = stmt.where(Question.year == year)
    if tag_id is not None:
        stmt = stmt.join(
            QuestionTag, QuestionTag.question_id == Question.id
        ).where(QuestionTag.tag_id == tag_id)

    # In-DB ordering for the simple sorts; risk/tag/recent_wrong need post-load sort.
    if sort == "lapses":
        stmt = stmt.order_by(desc("lapses_col"), desc(Question.id))
    elif sort == "starred":
        score_expr = (
            (func.coalesce(UserQuestionState.difficulty, 5.0) - 5.0)
            + func.coalesce(UserQuestionState.lapses, 0)
            + (func.coalesce(UserQuestionState.priority_boost, 1.0) - 1.0) * 2.0
        )
        stmt = stmt.order_by(desc("starred"), desc(score_expr), desc(Question.id))
    stmt = stmt.limit(limit)

    rows = db.execute(stmt).all()

    # Post-load sorts that need Python-side computation.
    # `risk` is the new default — composite score capturing forgetting +
    # personal historic difficulty + metacognition gap.
    def _risk(qid: int, stability, last_review, lapses) -> float:
        st = state_map.get(qid)
        r = _r_now(st) if st else 0.0
        lap = (st.lapses if st else (lapses or 0))
        conf3 = conf3_map.get(qid, 0)
        return (1.0 - r) * (1.0 + 0.3 * lap) * (1.0 + 0.5 * conf3)

    if sort == "risk":
        rows = sorted(
            rows,
            key=lambda r: (-_risk(r[0].id, r[6], r[7], r[3]), -(r[0].id)),
        )
    elif sort == "recent_wrong":
        rows = sorted(
            rows,
            key=lambda r: (
                -(last_lookup.get(r[0].id).answered_at.timestamp()
                  if last_lookup.get(r[0].id) and last_lookup.get(r[0].id).answered_at
                  else 0),
            ),
        )
    elif sort == "tag":
        # Group by primary tag (first by sort_order). Pull tags eagerly.
        primary_tag_lookup: dict[int, tuple[int, str]] = {}
        for q_obj, *_ in rows:
            tn = db.execute(
                select(Tag.sort_order, Tag.name)
                .join(QuestionTag, QuestionTag.tag_id == Tag.id)
                .where(QuestionTag.question_id == q_obj.id)
                .order_by(Tag.sort_order)
                .limit(1)
            ).first()
            primary_tag_lookup[q_obj.id] = tn or (999, "")
        rows = sorted(
            rows,
            key=lambda r: (
                primary_tag_lookup[r[0].id][0],
                primary_tag_lookup[r[0].id][1],
                -(r[3] or 0),
            ),
        )
    note_lookup = {
        n.question_id: n.body for n in db.scalars(
            select(UserNote).where(UserNote.user_id == user.id)
        ).all()
    }
    out: list[WrongBankCard] = []
    for q, src_code, _starred, _lapses, _diff, _boost, _stab, _last_rev, _due in rows:
        st = state_lookup.get(q.id)
        last = last_lookup.get(q.id)
        last_was_wrong = bool(last and not last.is_correct)
        is_auto = (st and st.lapses >= 1) or last_was_wrong

        tag_names = [
            n for (n,) in db.execute(
                select(Tag.name)
                .join(QuestionTag, QuestionTag.tag_id == Tag.id)
                .where(QuestionTag.question_id == q.id)
                .order_by(Tag.sort_order)
            ).all()
        ]
        question_label = None
        if q.year is not None and q.source_ref and "/Q" in q.source_ref:
            try:
                qnum = int(q.source_ref.split("/Q")[1])
                question_label = f"{q.year} 年 第 {qnum} 題"
            except ValueError:
                pass

        correct_text = " / ".join(
            (getattr(q, f"option_{L.lower()}", None) or "")
            for L in (q.correct_letter or "")
        )
        opt_dict = {
            L: getattr(q, f"option_{L.lower()}")
            for L in ("A", "B", "C", "D", "E")
            if getattr(q, f"option_{L.lower()}")
        }
        r_val = _r_now(st) if st else 0.0
        c3 = conf3_map.get(q.id, 0)
        lap_n = st.lapses if st else 0
        risk_val = (1.0 - r_val) * (1.0 + 0.3 * lap_n) * (1.0 + 0.5 * c3)
        out.append(WrongBankCard(
            question_id=q.id,
            source_code=src_code,
            year=q.year,
            question_label=question_label,
            stem=q.stem,
            correct_letter=q.correct_letter,
            correct_text=correct_text,
            options=opt_dict,
            tags=tag_names,
            is_starred=q.id in starred_set,
            is_auto_wrong=bool(is_auto),
            lapses=lap_n,
            last_attempt_at=last.answered_at if last else None,
            last_was_wrong=last_was_wrong,
            note=note_lookup.get(q.id),
            r_now=round(r_val, 4),
            conf3_wrong_30d=c3,
            risk_score=round(risk_val, 4),
            categories=_categories(q.id),
        ))
    return out


class WrongBankStats(BaseModel):
    total: int
    star: int
    lapse: int      # 死穴: lapses ≥ 2
    forget: int     # 易忘: R < 0.6 in Review state
    blind: int      # 盲區: ≥1 conf=3 wrong in last 30d
    recent: int     # 新錯: last attempt wrong, lapses < 2


@router.get("/stats", response_model=WrongBankStats)
def stats(
    source_code: str | None = Query(None),
    year: int | None = Query(None),
    as_user: int | None = Query(None, description="admin-only: view another user's stats"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> WrongBankStats:
    """Counts per failure-mode category. Used for top-of-list summary cards."""
    user = _resolve_target_user(current_user, as_user, db)
    now = datetime.now(tz=timezone.utc)
    conf3_cutoff = now - timedelta(days=CONF3_WINDOW_DAYS)

    # base candidate set = starred ∪ auto-wrong, same as list_cards
    starred_q = (
        select(UserQuestionState.question_id).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.is_starred == True,  # noqa: E712
        )
    )
    sub_last = (
        select(
            UserAnswer.question_id,
            func.max(UserAnswer.answered_at).label("last_at"),
        )
        .where(UserAnswer.user_id == user.id)
        .group_by(UserAnswer.question_id)
        .subquery()
    )
    last_join = (
        select(
            UserAnswer.question_id,
            UserAnswer.is_correct,
            UserAnswer.answered_at,
        )
        .join(
            sub_last,
            and_(
                sub_last.c.question_id == UserAnswer.question_id,
                sub_last.c.last_at == UserAnswer.answered_at,
            ),
        )
        .where(UserAnswer.user_id == user.id)
        .subquery()
    )
    auto_wrong_q = (
        select(UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.lapses >= 1,
        )
    ).union(
        select(last_join.c.question_id).where(last_join.c.is_correct == False)  # noqa: E712
    )

    starred_set = {qid for (qid,) in db.execute(starred_q).all()}
    auto_set = {qid for (qid,) in db.execute(auto_wrong_q).all()}
    base_set = starred_set | auto_set

    # Optional source/year filters scope the base set
    if source_code or year is not None:
        scope_q = select(Question.id).where(Question.id.in_(base_set))
        if source_code:
            s = db.execute(
                select(Source).where(Source.code == source_code)
            ).scalar_one_or_none()
            if s is not None and user.has_source(s.code):
                scope_q = scope_q.where(Question.source_id == s.id)
            else:
                base_set = set()
        if year is not None:
            scope_q = scope_q.where(Question.year == year)
        if base_set:
            base_set = {qid for (qid,) in db.execute(scope_q).all()}

    if not base_set:
        return WrongBankStats(
            total=0, star=0, lapse=0, forget=0, blind=0, recent=0
        )

    # exclude dismissed (but keep starred-and-dismissed visible, mirrors list_cards)
    dismissed = {
        qid for (qid,) in db.execute(
            select(UserQuestionState.question_id).where(
                UserQuestionState.user_id == user.id,
                UserQuestionState.dismissed_from_wrong_bank == True,  # noqa: E712
            )
        ).all()
    }
    base_set = (base_set - dismissed) | (base_set & starred_set)

    state_map = {
        s.question_id: s for s in db.scalars(
            select(UserQuestionState).where(
                UserQuestionState.user_id == user.id,
                UserQuestionState.question_id.in_(base_set),
            )
        ).all()
    }
    last_map = {
        r.question_id: r for r in db.execute(
            select(last_join).where(last_join.c.question_id.in_(base_set))
        ).all()
    }
    conf3_map: dict[int, int] = {}
    for qid, n in db.execute(
        select(UserAnswer.question_id, func.count(UserAnswer.id))
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.confidence == 3,
            UserAnswer.is_correct == False,  # noqa: E712
            UserAnswer.answered_at >= conf3_cutoff,
            UserAnswer.question_id.in_(base_set),
        )
        .group_by(UserAnswer.question_id)
    ).all():
        conf3_map[qid] = n

    def _r_now(st: UserQuestionState | None) -> float:
        if st is None or st.stability is None or st.last_review_at is None:
            return 0.0
        last = st.last_review_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        t_days = max(0.0, (now - last).total_seconds() / 86400.0)
        s = max(0.01, st.stability)
        return (1.0 + t_days / (9.0 * s)) ** -1

    lapse = forget = blind = recent = 0
    for qid in base_set:
        st = state_map.get(qid)
        last = last_map.get(qid)
        if st and st.lapses >= LAPSE_THRESHOLD:
            lapse += 1
        if st and st.state == 2 and _r_now(st) < FORGET_R_THRESHOLD:
            forget += 1
        if conf3_map.get(qid, 0) >= 1:
            blind += 1
        if last and not last.is_correct and (st.lapses if st else 0) < LAPSE_THRESHOLD:
            recent += 1

    return WrongBankStats(
        total=len(base_set),
        star=len(starred_set & base_set),
        lapse=lapse,
        forget=forget,
        blind=blind,
        recent=recent,
    )


class BulkAction(BaseModel):
    question_ids: list[int]
    action: str  # dismiss / undismiss / star / unstar


@router.post("/bulk")
def bulk_action(
    body: BulkAction,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not body.question_ids:
        return {"updated": 0}
    if body.action not in {"dismiss", "undismiss", "star", "unstar"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bad action: {body.action}")

    existing = {
        s.question_id: s for s in db.scalars(
            select(UserQuestionState).where(
                UserQuestionState.user_id == user.id,
                UserQuestionState.question_id.in_(body.question_ids),
            )
        ).all()
    }
    updated = 0
    for qid in body.question_ids:
        s = existing.get(qid)
        if s is None:
            # Only create row if action requires it (star/dismiss). Skip for unstar/undismiss.
            if body.action in ("star", "dismiss"):
                s = UserQuestionState(user_id=user.id, question_id=qid)
                db.add(s)
            else:
                continue
        if body.action == "dismiss":
            s.dismissed_from_wrong_bank = True
        elif body.action == "undismiss":
            s.dismissed_from_wrong_bank = False
        elif body.action == "star":
            s.is_starred = True
        elif body.action == "unstar":
            s.is_starred = False
        updated += 1
    db.commit()
    return {"updated": updated}


# ─────────────────────────────────────────────────────────────────────────
# 大家的錯題本 (cohort common-wrong) — aggregate-only, soft-launch gated.
# ─────────────────────────────────────────────────────────────────────────
class CohortWrongCard(BaseModel):
    question_id: int
    source_code: str
    year: int | None
    question_label: str | None         # "112 年 第 47 題" for past_paper
    stem: str
    correct_letter: str
    correct_text: str
    options: dict[str, str]            # {A: text, ...} for 顯示所有選項
    is_multi: bool
    tags: list[str]
    is_starred: bool                   # viewer's own star
    note: str | None                   # viewer's own 心得
    # NOTE: absolute headcounts (distinct_users / wrong_users / attempts /
    # re_attempts) are intentionally NOT exposed — they would leak the cohort
    # size. Only ratios (%) of 活躍使用者 are returned.
    wrong_pct: float                   # 初答錯誤率 = 初答錯人數 / 答過該題人數, %
    has_re: bool                       # 是否有人再答過（決定 re_pct 顯示與否）
    re_pct: float                      # 再答錯誤率 = 答錯的再答 / 再答總數, %
    # viewer's personal wrong-bank overlay (empty/None if never answered)
    personal_seen: bool
    personal_risk: float | None
    personal_r_now: float | None
    personal_lapses: int
    personal_conf3: int                # viewer conf=3 yet wrong, last 30d
    personal_categories: list[str]     # subset of lapse/forget/blind/recent


@router.get("/cohort", response_model=list[CohortWrongCard])
def cohort_wrong(
    min_wrong_users: int = Query(
        3, ge=1, description="≥ N 人初答錯才收錄（預設 3；不顯示總人數、不洩漏規模）"
    ),
    tag_id: int | None = Query(None),
    sort: str = Query("wrong_pct", description="wrong_pct (初答錯誤率) / re_pct (再答錯誤率)"),
    limit: int | None = Query(None, ge=1, description="Optional cap. Omit to return all matching."),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[CohortWrongCard]:
    """大家的錯題本 — questions the cohort gets wrong on FIRST attempt most often.

    初答錯誤率 = (#users whose first answer was wrong) / (#users who answered).
    First-attempt only, so retries don't dilute the signal. Aggregate-only —
    never exposes who answered what, nor the cohort size. The display threshold
    is expressed as 「≥ N% of 活躍使用者 初答錯」 (not an absolute headcount) so
    the page never reveals how many people use the system. Internally we resolve
    N% against active_user_count(past_paper) into a minimum 初答錯人數 and filter
    on that. Soft-launch gate: admin always; everyone once
    settings.cohort_wrongbook_public flips True. Computed over the single
    past_paper source. Each card also carries the *viewer's* own personal
    wrong-bank signals.
    """
    if not (current_user.is_admin or settings.cohort_wrongbook_public):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "「大家的錯題本」尚未開放")
    if sort not in {"wrong_pct", "re_pct"}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Bad sort: {sort}")

    # Fixed minimum 初答錯人數 (default 3). A plain "≥ N people got it wrong"
    # threshold does NOT reveal the cohort size (it's a floor, not "N of M"),
    # and the cards still expose only error *rates* (%), never headcounts.
    need = min_wrong_users

    # Per (user, question) attempt order: rn==1 = first attempt, rn>1 = retry.
    rn = func.row_number().over(
        partition_by=(UserAnswer.user_id, UserAnswer.question_id),
        order_by=(UserAnswer.answered_at, UserAnswer.id),
    ).label("rn")
    asub = select(
        UserAnswer.question_id.label("qid"),
        UserAnswer.is_correct.label("ic"),
        rn,
    ).subquery()
    agg = (
        select(
            asub.c.qid.label("qid"),
            func.sum(func.iif(asub.c.rn == 1, 1, 0)).label("uniq_users"),
            func.sum(
                func.iif(asub.c.rn == 1, func.iif(asub.c.ic, 0, 1), 0)
            ).label("first_wrong"),
            func.count().label("attempts"),
            func.sum(func.iif(asub.c.rn > 1, 1, 0)).label("re_attempts"),
            func.sum(
                func.iif(asub.c.rn > 1, func.iif(asub.c.ic, 0, 1), 0)
            ).label("re_wrong"),
        )
        .group_by(asub.c.qid)
        .subquery()
    )

    stmt = (
        select(
            Question,
            Source.code,
            agg.c.uniq_users,
            agg.c.first_wrong,
            agg.c.re_attempts,
            agg.c.re_wrong,
        )
        .join(Source, Source.id == Question.source_id)
        .join(agg, agg.c.qid == Question.id)
        .where(Source.code == "past_paper")
        .where(agg.c.first_wrong >= need)
    )
    # Only show questions from sources the viewer may access.
    if current_user.allowed_sources:
        allowed_codes = {
            c.strip() for c in current_user.allowed_sources.split(",") if c.strip()
        }
        stmt = stmt.where(Source.code.in_(allowed_codes))
    if tag_id is not None:
        stmt = stmt.join(
            QuestionTag, QuestionTag.question_id == Question.id
        ).where(QuestionTag.tag_id == tag_id)

    rate = agg.c.first_wrong * 1.0 / agg.c.uniq_users
    re_rate = func.coalesce(
        agg.c.re_wrong * 1.0 / func.nullif(agg.c.re_attempts, 0), 0.0
    )
    if sort == "re_pct":
        stmt = stmt.order_by(re_rate.desc(), rate.desc())
    else:  # wrong_pct (default)
        stmt = stmt.order_by(rate.desc(), agg.c.first_wrong.desc())
    if limit is not None:
        stmt = stmt.limit(limit)

    rows = db.execute(stmt).all()
    qids = [r[0].id for r in rows]

    # cohort tags + viewer's own star/note + viewer's personal signals
    tag_map: dict[int, list[str]] = {}
    starred_set: set[int] = set()
    note_map: dict[int, str] = {}
    now = datetime.now(tz=timezone.utc)
    conf3_cutoff = now - timedelta(days=CONF3_WINDOW_DAYS)
    pstate: dict[int, UserQuestionState] = {}
    pconf3: dict[int, int] = {}
    plast: dict[int, bool] = {}
    if qids:
        for qid, name in db.execute(
            select(QuestionTag.question_id, Tag.name)
            .join(Tag, Tag.id == QuestionTag.tag_id)
            .where(QuestionTag.question_id.in_(qids))
            .order_by(Tag.sort_order)
        ).all():
            tag_map.setdefault(qid, []).append(name)
        starred_set = {
            qid for (qid,) in db.execute(
                select(UserQuestionState.question_id).where(
                    UserQuestionState.user_id == current_user.id,
                    UserQuestionState.question_id.in_(qids),
                    UserQuestionState.is_starred == True,  # noqa: E712
                )
            ).all()
        }
        note_map = {
            n.question_id: n.body for n in db.scalars(
                select(UserNote).where(
                    UserNote.user_id == current_user.id,
                    UserNote.question_id.in_(qids),
                )
            ).all()
        }
        pstate = {
            s.question_id: s for s in db.scalars(
                select(UserQuestionState).where(
                    UserQuestionState.user_id == current_user.id,
                    UserQuestionState.question_id.in_(qids),
                )
            ).all()
        }
        for qid, n in db.execute(
            select(UserAnswer.question_id, func.count(UserAnswer.id))
            .where(
                UserAnswer.user_id == current_user.id,
                UserAnswer.confidence == 3,
                UserAnswer.is_correct == False,  # noqa: E712
                UserAnswer.answered_at >= conf3_cutoff,
                UserAnswer.question_id.in_(qids),
            )
            .group_by(UserAnswer.question_id)
        ).all():
            pconf3[qid] = n
        last_sub = (
            select(
                UserAnswer.question_id.label("qid"),
                func.max(UserAnswer.answered_at).label("m"),
            )
            .where(
                UserAnswer.user_id == current_user.id,
                UserAnswer.question_id.in_(qids),
            )
            .group_by(UserAnswer.question_id)
            .subquery()
        )
        for qid, ic in db.execute(
            select(UserAnswer.question_id, UserAnswer.is_correct)
            .join(
                last_sub,
                and_(
                    last_sub.c.qid == UserAnswer.question_id,
                    last_sub.c.m == UserAnswer.answered_at,
                ),
            )
            .where(UserAnswer.user_id == current_user.id)
        ).all():
            plast[qid] = bool(ic)

    def _r_now(st: UserQuestionState | None) -> float:
        if st is None or st.stability is None or st.last_review_at is None:
            return 0.0
        last = st.last_review_at
        if last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        t_days = max(0.0, (now - last).total_seconds() / 86400.0)
        return (1.0 + t_days / (9.0 * max(0.01, st.stability))) ** -1

    out: list[CohortWrongCard] = []
    for q, src_code, uniq, first_wrong, re_attempts, re_wrong in rows:
        question_label = None
        if q.year is not None and q.source_ref and "/Q" in q.source_ref:
            try:
                question_label = f"{q.year} 年 第 {int(q.source_ref.split('/Q')[1])} 題"
            except ValueError:
                pass
        correct_text = " / ".join(
            (getattr(q, f"option_{L.lower()}", None) or "")
            for L in (q.correct_letter or "")
        )
        opt_dict = {
            L: getattr(q, f"option_{L.lower()}")
            for L in ("A", "B", "C", "D", "E")
            if getattr(q, f"option_{L.lower()}")
        }
        # viewer's personal overlay
        st = pstate.get(q.id)
        seen = (st is not None) or (q.id in plast)
        if seen:
            r = _r_now(st)
            lap = st.lapses if st else 0
            c3 = pconf3.get(q.id, 0)
            risk = (1.0 - r) * (1.0 + 0.3 * lap) * (1.0 + 0.5 * c3)
            cats: list[str] = []
            if st and st.lapses >= LAPSE_THRESHOLD:
                cats.append("lapse")
            if st and st.state == 2 and r < FORGET_R_THRESHOLD:
                cats.append("forget")
            if c3 >= 1:
                cats.append("blind")
            if plast.get(q.id) is False and (st.lapses if st else 0) < LAPSE_THRESHOLD:
                cats.append("recent")
            p_risk: float | None = round(risk, 4)
            p_r: float | None = round(r, 4)
        else:
            lap = c3 = 0
            cats = []
            p_risk = p_r = None
        out.append(CohortWrongCard(
            question_id=q.id,
            source_code=src_code,
            year=q.year,
            question_label=question_label,
            stem=q.stem,
            correct_letter=q.correct_letter,
            correct_text=correct_text,
            options=opt_dict,
            is_multi=bool(q.is_multi),
            tags=tag_map.get(q.id, []),
            is_starred=q.id in starred_set,
            note=note_map.get(q.id),
            wrong_pct=round((first_wrong or 0) * 100.0 / uniq, 1) if uniq else 0.0,
            has_re=bool(re_attempts),
            re_pct=round((re_wrong or 0) * 100.0 / re_attempts, 1) if re_attempts else 0.0,
            personal_seen=seen,
            personal_risk=p_risk,
            personal_r_now=p_r,
            personal_lapses=lap,
            personal_conf3=c3,
            personal_categories=cats,
        ))
    return out
