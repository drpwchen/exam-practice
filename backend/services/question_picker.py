"""Three-pool question picker: due review + new + starred, with priority_boost.

Per session:
- Pool A (70%): due review cards (state==Review/Learning, due_at <= now)
  Order: exam-cram priority (not raw most-overdue). Cards in the "about to
  forget but still recoverable" band (FSRS R≈0.7) lead — cheap to lock in
  before the exam; high-R cards (still solid) are deferred; deep-forgotten
  cards (R < R_DEEP) are chipped at a fixed 1-in-DEEP_INTERLEAVE quota so weak
  spots are neither starved nor allowed to swamp the limited daily budget.
  priority_boost still surfaces a card toward the front.
- Pool B (20%): new cards (no UserQuestionState row OR last_review is None)
  Cap: 20 new per session to avoid drowning review queue
- Pool C (10%): starred cards (is_starred=True)
- Pool A empty → 80% B + 20% C
- All pools empty → return None

Source / tag filtering narrows the candidate pool but FSRS state is per-question
regardless of source — switching sources does not reset history.
"""

import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.orm import Session

from backend.models import (
    Question,
    QuestionTag,
    UserAnswer,
    UserQuestionState,
)

# Default per-session new-card cap. Overridable per user via
# UserPreferences.study_config.max_new_per_session (threaded through
# pick_next_question_id → new_cap below).
NEW_CARD_SESSION_CAP = 20
POOL_RATIO = (("A", 0.7), ("B", 0.2), ("C", 0.1))

# 出錯可能性 (risk) window for the conf=3-yet-wrong signal, shared with 錯題本.
RISK_CONF3_WINDOW_DAYS = 30

# 自己的常錯題 failure-mode taxonomy — mirrors 錯題本 (wrong_bank.py) so the
# practice filter and the review page label the same cards identically.
WRONG_FORGET_R_THRESHOLD = 0.6   # Review-state card with R below this = 易忘
WRONG_LAPSE_THRESHOLD = 2        # lapses ≥ N = 死穴
WRONG_KIND_VALUES = {"lapse", "forget", "blind", "recent"}


def _wrong_pool_rows(db: Session, user_id: int, base):
    """Rows (qid, lapses, state, stability, last_review_at, last_is_correct) for
    the user's 常錯 pool (lapses>=1 OR most recent attempt wrong) within the
    `base` candidate subquery. Shared by the picker and the count endpoint."""
    sub_last = (
        select(
            UserAnswer.question_id,
            func.max(UserAnswer.answered_at).label("last_at"),
        )
        .where(UserAnswer.user_id == user_id)
        .group_by(UserAnswer.question_id)
        .subquery()
    )
    last_join = (
        select(UserAnswer.question_id, UserAnswer.is_correct)
        .join(
            sub_last,
            and_(
                sub_last.c.question_id == UserAnswer.question_id,
                sub_last.c.last_at == UserAnswer.answered_at,
            ),
        )
        .where(UserAnswer.user_id == user_id)
        .subquery()
    )
    return db.execute(
        select(
            UserQuestionState.question_id,
            UserQuestionState.lapses,
            UserQuestionState.state,
            UserQuestionState.stability,
            UserQuestionState.last_review_at,
            last_join.c.is_correct,
        )
        .join(base, base.c.id == UserQuestionState.question_id)
        .join(last_join, last_join.c.question_id == UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user_id,
            (UserQuestionState.lapses >= 1) | (last_join.c.is_correct == False),  # noqa: E712
        )
    ).all()


def _wrong_conf3_map(db: Session, user_id: int, qids, now: datetime) -> dict[int, int]:
    """conf=3-yet-wrong counts in the last RISK_CONF3_WINDOW_DAYS, per qid."""
    qids = list(qids)
    if not qids:
        return {}
    cutoff = now - timedelta(days=RISK_CONF3_WINDOW_DAYS)
    return {
        qid: n for qid, n in db.execute(
            select(UserAnswer.question_id, func.count(UserAnswer.id))
            .where(
                UserAnswer.user_id == user_id,
                UserAnswer.confidence == 3,
                UserAnswer.is_correct == False,  # noqa: E712
                UserAnswer.answered_at >= cutoff,
                UserAnswer.question_id.in_(qids),
            )
            .group_by(UserAnswer.question_id)
        ).all()
    }


def _wrong_category_set(lapses, state, r: float, last_ok, conf3: int) -> set[str]:
    """Failure-mode tags for one card — mirrors 錯題本 (wrong_bank.py)."""
    cats: set[str] = set()
    if (lapses or 0) >= WRONG_LAPSE_THRESHOLD:
        cats.add("lapse")
    if state == 2 and r < WRONG_FORGET_R_THRESHOLD:
        cats.add("forget")
    if conf3 >= 1:
        cats.add("blind")
    if not last_ok and (lapses or 0) < WRONG_LAPSE_THRESHOLD:
        cats.add("recent")
    return cats

# 大家錯題 (cohort_wrong) difficulty tiers, calibrated 2026-06-02 against the
# live answer distribution. 再答錯誤率 is generally low (≥20% only ~17 q, ≥10%
# ~190 q) while 初答錯誤率 spreads well, so the ladder leans on 初答錯誤率 and
# uses 再答錯誤率 as a secondary tightener. Each tier: minimum 初答錯人數
# (min_fw), 初答錯誤率 % (min_fpct), 再答錯誤率 % (min_repct). Sort within a
# tier is 再答錯誤率 desc (user's requested primary key).
COHORT_TIERS: dict[str, dict[str, float]] = {
    "hardest": {"min_fw": 5, "min_fpct": 60.0, "min_repct": 10.0},  # ~32 q
    "hard":    {"min_fw": 5, "min_fpct": 50.0, "min_repct": 5.0},   # ~105 q
    "common":  {"min_fw": 3, "min_fpct": 40.0, "min_repct": 0.0},   # ~182 q
}
COHORT_DEFAULT_TIER = "common"


@dataclass
class PickerSessionState:
    new_cards_introduced: int = 0


_session_state_by_user: dict[int, PickerSessionState] = {}


def _get_session_state(user_id: int) -> PickerSessionState:
    st = _session_state_by_user.get(user_id)
    if st is None:
        st = PickerSessionState()
        _session_state_by_user[user_id] = st
    return st


def reset_session(user_id: int) -> None:
    _session_state_by_user.pop(user_id, None)


def _candidate_filter(
    db: Session, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
):
    """Build common filter on Question table."""
    stmt = select(Question.id).where(Question.is_archived == False)  # noqa: E712
    if source_id is not None:
        stmt = stmt.where(Question.source_id == source_id)
    elif allowed_source_ids is not None:
        # Only enforce when caller didn't already specify a single source.
        # Empty list → no allowed sources at all → match nothing.
        if not allowed_source_ids:
            stmt = stmt.where(False)
        else:
            stmt = stmt.where(Question.source_id.in_(allowed_source_ids))
    if years:
        stmt = stmt.where(Question.year.in_(years))
    elif year is not None:
        stmt = stmt.where(Question.year == year)
    if tag_ids:
        stmt = stmt.join(QuestionTag, QuestionTag.question_id == Question.id).where(
            QuestionTag.tag_id.in_(tag_ids)
        ).distinct()
    if exclude_qids:
        stmt = stmt.where(~Question.id.in_(exclude_qids))
    return stmt


# Pool A (due review) ordering — exam-cram tuned. Rationale: under a fixed
# exam deadline with a backlog the user cannot fully clear daily, raw
# most-overdue-first spends the limited budget on deeply-forgotten cards
# (R≈0.5, ~half lapse → reset) while reliably-recoverable near-due cards
# (R≈0.7-0.9) decay unreviewed. The urgency tent R^A·(1-R)^B peaks at
# A/(A+B)=0.7 → prioritise "about to forget but still recoverable", defer
# still-solid high-R, and trickle deep-forgotten cards via a fixed quota.
R_DEEP = 0.5            # R below this = deep-forgotten weak pool (rate-limited)
URGENCY_A = 1.4        # R exponent
URGENCY_B = 0.6        # (1-R) exponent  → tent peak at 0.7
DEEP_INTERLEAVE = 4    # serve 1 deep-forgotten card every Nth pick


def _interleave_deep(primary: list[int], deep: list[int], every: int) -> list[int]:
    """Merge `primary` (sweet-spot order) with `deep` (weak-spot order),
    inserting one `deep` card after every (every-1) primary cards. Leftovers
    of whichever list outlasts the other are appended in order."""
    if not deep:
        return primary
    if not primary:
        return deep
    out: list[int] = []
    pi = di = 0
    slot = 0
    while pi < len(primary) or di < len(deep):
        take_deep = (slot % every == every - 1)
        if take_deep and di < len(deep):
            out.append(deep[di]); di += 1
        elif pi < len(primary):
            out.append(primary[pi]); pi += 1
        elif di < len(deep):
            out.append(deep[di]); di += 1
        slot += 1
    return out


def _pool_a_due(
    db: Session, user_id: int, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], now: datetime, year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
) -> list[tuple[int, float]]:
    """Return [(question_id, serve_rank)] for due review cards, ordered by
    exam-cram priority (sweet-spot R first + rate-limited deep-forgotten)."""
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()
    stmt = (
        select(
            UserQuestionState.question_id,
            UserQuestionState.due_at,
            UserQuestionState.priority_boost,
            UserQuestionState.stability,
            UserQuestionState.last_review_at,
        )
        .join(base, base.c.id == UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.due_at.is_not(None),
            UserQuestionState.due_at <= now,
        )
    )
    rows = db.execute(stmt).all()
    primary: list[tuple[int, float]] = []   # (qid, metric) higher = sooner
    deep: list[tuple[int, float, float]] = []  # (qid, r, overdue_sec)
    for qid, due_at, boost, stability, last_review_at in rows:
        # Both due_at and now should be aware in UTC; if naive, treat as UTC
        if due_at and due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)
        overdue = (now - due_at).total_seconds()  # positive = overdue
        boost = max(boost or 1.0, 0.1)
        r = _retrievability(now, stability, last_review_at)
        if r < R_DEEP and boost <= 1.0:
            deep.append((qid, r, overdue))
        else:
            urgency = (r ** URGENCY_A) * ((1.0 - r) ** URGENCY_B)
            # boost guarantees a manually-prioritised card leads the band
            metric = urgency * boost + (boost - 1.0)
            primary.append((qid, metric))
    primary.sort(key=lambda x: x[1], reverse=True)        # highest urgency first
    deep.sort(key=lambda x: (x[1], -x[2]))                # lowest R, then most overdue
    merged = _interleave_deep(
        [qid for qid, _ in primary],
        [qid for qid, _, _ in deep],
        DEEP_INTERLEAVE,
    )
    return [(qid, float(i)) for i, qid in enumerate(merged)]


def _pool_b_new(
    db: Session, user_id: int, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], limit: int, year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
) -> list[int]:
    """Cards with no state row OR with state row but stability is None."""
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()
    has_state = (
        select(UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.stability.is_not(None),
        )
        .subquery()
    )
    stmt = (
        select(base.c.id)
        .where(~base.c.id.in_(select(has_state.c.question_id)))
        .order_by(func.random())
        .limit(limit)
    )
    return [r[0] for r in db.execute(stmt).all()]


def _pool_b_new_until_correct(
    db: Session, user_id: int, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], limit: int, year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
) -> list[int]:
    """新題 = 從未答對過的題. A card stays "new" while every attempt was wrong;
    it leaves the pool only once the user answers it correctly at least once.

    Used by the explicit `mode=="new"` (只寫新題) filter so a wrong answer does
    not silently retire a card from 新題. The smart default keeps the
    FSRS-based `_pool_b_new` (wrong cards resurface via the due-review pool).
    """
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()
    answered_correct = (
        select(UserAnswer.question_id)
        .where(
            UserAnswer.user_id == user_id,
            UserAnswer.is_correct == True,  # noqa: E712
        )
        .subquery()
    )
    stmt = (
        select(base.c.id)
        .where(~base.c.id.in_(select(answered_correct.c.question_id)))
        .order_by(func.random())
        .limit(limit)
    )
    return [r[0] for r in db.execute(stmt).all()]


def _pool_c_starred(
    db: Session, user_id: int, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], now: datetime, year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
) -> list[int]:
    """Starred cards that are also FSRS-due (or never-reviewed).

    Pool C is a weighting boost for cards the user starred, not an override
    of FSRS scheduling.  A starred card with due_at in the future was
    previously surfaced 10% of the time regardless of schedule — across a
    session reset (chain cleared, exclude_qids empty) that produced the
    cycling reported on 2026-05-12 (Q1872/1866/etc reappearing within
    seconds of being answered).  Filter to due-or-new keeps the boost but
    honors FSRS due_at.
    """
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()
    stmt = (
        select(UserQuestionState.question_id)
        .join(base, base.c.id == UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.is_starred == True,  # noqa: E712
            or_(
                UserQuestionState.due_at.is_(None),
                UserQuestionState.due_at <= now,
            ),
        )
        .order_by(func.random())
    )
    return [r[0] for r in db.execute(stmt).all()]


def pick_wrong_question_id(
    db: Session,
    user_id: int,
    tag_ids: list[int] | None = None,
    source_id: int | None = None,
    year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
    exclude_qids: set[int] | None = None,
    wrong_kinds: list[str] | None = None,
    exclude_recent_days: int = 0,
) -> int | None:
    """自己的常錯題: cards answered wrong (lapses>=1 OR most recent attempt wrong),
    ordered by 下次錯的機會 (risk) desc — the same composite as 錯題本:
      risk = (1-R) × (1+0.3·lapses) × (1+0.5·conf3_wrong_30d)

    wrong_kinds optionally narrows to failure-mode subsets (lapse 死穴 / forget
    易忘 / blind 盲區 / recent 新錯), union when several given. An empty list — or
    one containing "all" — means the whole 常錯 pool, which intentionally
    INCLUDES cards matching no single category (e.g. wrong once long ago but the
    last attempt was correct and R is still healthy). Those uncategorised cards
    only surface under "全部"; selecting any specific mode excludes them.

    exclude_recent_days skips cards the user answered in the last N days so
    re-entering the mode advances instead of restarting on the same top cards
    (0 = 不排除).
    """
    now = datetime.now(tz=timezone.utc)
    exclude_qids = set(exclude_qids or set()) | _recent_answered_qids(
        db, user_id, exclude_recent_days, now
    )
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()

    rows = _wrong_pool_rows(db, user_id, base)
    if not rows:
        return None
    conf3_map = _wrong_conf3_map(db, user_id, [r[0] for r in rows], now)

    raw_kinds = wrong_kinds or []
    kinds = {k for k in raw_kinds if k in WRONG_KIND_VALUES}
    filter_active = bool(kinds) and "all" not in raw_kinds

    best_qid: int | None = None
    best_score = -1.0
    for qid, lapses, state, stability, last_review_at, last_ok in rows:
        r = _retrievability(now, stability, last_review_at)
        conf3 = conf3_map.get(qid, 0)
        if filter_active and not (
            _wrong_category_set(lapses, state, r, last_ok, conf3) & kinds
        ):
            continue
        risk = (1.0 - r) * (1.0 + 0.3 * (lapses or 0)) * (1.0 + 0.5 * conf3)
        if risk > best_score:
            best_score = risk
            best_qid = qid
    return best_qid


def _retrievability(now: datetime, stability, last_review_at) -> float:
    """FSRS-6 R(t) = (1 + t/(9·S))^-1. Unstabilised cards → 0 (worst case)."""
    if stability is None or last_review_at is None:
        return 0.0
    last = last_review_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    t_days = max(0.0, (now - last).total_seconds() / 86400.0)
    return (1.0 + t_days / (9.0 * max(0.01, stability))) ** -1


def pick_starred_question_id(
    db: Session, user_id: int, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
    exclude_recent_days: int = 0,
) -> int | None:
    """星號題: starred cards ordered by 出錯可能性 (risk), ignoring FSRS due.

    Unlike Pool C (which only surfaces due-or-new starred cards), this mode
    deliberately ignores due_at and mastery — a starred card stays eligible
    even if answered recently. Risk score mirrors 錯題本:
      risk = (1-R) × (1+0.3·lapses) × (1+0.5·conf3_wrong_30d)

    exclude_recent_days optionally skips cards answered in the last N days so
    re-entering the mode advances instead of restarting on the same top cards
    (0 = 不排除).
    """
    now = datetime.now(tz=timezone.utc)
    exclude_qids = set(exclude_qids or set()) | _recent_answered_qids(
        db, user_id, exclude_recent_days, now
    )
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()
    rows = db.execute(
        select(
            UserQuestionState.question_id,
            UserQuestionState.stability,
            UserQuestionState.last_review_at,
            UserQuestionState.lapses,
        )
        .join(base, base.c.id == UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.is_starred == True,  # noqa: E712
        )
    ).all()
    if not rows:
        return None

    qids = [r[0] for r in rows]
    conf3_cutoff = now - timedelta(days=RISK_CONF3_WINDOW_DAYS)
    conf3_map: dict[int, int] = {}
    for qid, n in db.execute(
        select(UserAnswer.question_id, func.count(UserAnswer.id))
        .where(
            UserAnswer.user_id == user_id,
            UserAnswer.confidence == 3,
            UserAnswer.is_correct == False,  # noqa: E712
            UserAnswer.answered_at >= conf3_cutoff,
            UserAnswer.question_id.in_(qids),
        )
        .group_by(UserAnswer.question_id)
    ).all():
        conf3_map[qid] = n

    best_qid: int | None = None
    best_score = -1.0
    for qid, stability, last_review_at, lapses in rows:
        r = _retrievability(now, stability, last_review_at)
        risk = (
            (1.0 - r)
            * (1.0 + 0.3 * (lapses or 0))
            * (1.0 + 0.5 * conf3_map.get(qid, 0))
        )
        if risk > best_score:
            best_score = risk
            best_qid = qid
    return best_qid


def _cohort_agg_subquery(
    db: Session, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], year: int | None,
    years: list[int] | None, allowed_source_ids: list[int] | None,
):
    """Shared (base, agg, fpct_expr, repct_expr) for cohort-wrong pick + count.

    base   — candidate Question.id subquery (filters applied)
    agg    — per-question cohort aggregates (uniq/first_wrong/re_attempts/re_wrong)
    fpct   — 初答錯誤率 % expression over agg
    repct  — 再答錯誤率 % expression over agg (0 when no retries)

    Cohort stats are computed over the single past_paper source. (The legacy
    multi-source build excluded one source here; with a single source the
    standard candidate filter already scopes the pool correctly.)
    """
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    ).subquery()

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
            func.sum(func.iif(asub.c.rn == 1, 1, 0)).label("uniq"),
            func.sum(
                func.iif(asub.c.rn == 1, func.iif(asub.c.ic, 0, 1), 0)
            ).label("first_wrong"),
            func.sum(func.iif(asub.c.rn > 1, 1, 0)).label("re_attempts"),
            func.sum(
                func.iif(asub.c.rn > 1, func.iif(asub.c.ic, 0, 1), 0)
            ).label("re_wrong"),
        )
        .group_by(asub.c.qid)
        .subquery()
    )
    fpct = agg.c.first_wrong * 100.0 / agg.c.uniq
    repct = func.coalesce(
        agg.c.re_wrong * 100.0 / func.nullif(agg.c.re_attempts, 0), 0.0
    )
    return base, agg, fpct, repct


def _cohort_tier(level: str | None) -> dict[str, float]:
    return COHORT_TIERS.get(level or COHORT_DEFAULT_TIER, COHORT_TIERS[COHORT_DEFAULT_TIER])


def _recent_answered_qids(
    db: Session, user_id: int, days: int, now: datetime
) -> set[int]:
    """qids whose most recent attempt within the last `days` days was correct
    (for the 排除近期練過 filter). Only correctly-answered questions are
    excluded — a question practised inside the window whose latest attempt was
    wrong stays in the pool so the user keeps drilling it (沒答對的不排除).
    Empty when days <= 0."""
    if days <= 0:
        return set()
    cutoff = now - timedelta(days=days)
    # Walk attempts newest-first per question; the first row seen for each qid
    # is its latest attempt in the window — exclude only if that one was correct.
    rows = db.execute(
        select(UserAnswer.question_id, UserAnswer.is_correct)
        .where(
            UserAnswer.user_id == user_id,
            UserAnswer.answered_at >= cutoff,
        )
        .order_by(UserAnswer.question_id, UserAnswer.answered_at.desc())
    ).all()
    seen: set[int] = set()
    correct: set[int] = set()
    for qid, is_correct in rows:
        if qid in seen:
            continue
        seen.add(qid)
        if is_correct:
            correct.add(qid)
    return correct


def pick_cohort_wrong_question_id(
    db: Session, user_id: int, source_id: int | None, tag_ids: list[int] | None,
    exclude_qids: set[int], year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
    level: str | None = None,
    exclude_recent_days: int = 0,
) -> int | None:
    """大家錯題: cohort common-wrong cards ordered by 再答錯誤率 (re_pct) desc.

    Tier (最難/偏難/大家常錯) sets the 初答錯人數 / 初答錯誤率 / 再答錯誤率 gate
    (see COHORT_TIERS). Selection ignores the viewer's personal FSRS schedule —
    surfaces by cohort stats regardless of when they last reviewed. The
    `exclude_recent_days` filter optionally skips questions the viewer answered
    in the last N days so re-entering the mode advances instead of restarting on
    the same top questions (0 = 不排除). Aggregate-only.
    """
    tier = _cohort_tier(level)
    now = datetime.now(tz=timezone.utc)
    exclude_qids = set(exclude_qids) | _recent_answered_qids(
        db, user_id, exclude_recent_days, now
    )
    base, agg, fpct, repct = _cohort_agg_subquery(
        db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
    )
    stmt = (
        select(agg.c.qid)
        .join(base, base.c.id == agg.c.qid)
        .where(
            agg.c.first_wrong >= tier["min_fw"],
            fpct >= tier["min_fpct"],
            repct >= tier["min_repct"],
        )
        .order_by(repct.desc(), fpct.desc(), agg.c.first_wrong.desc())
        .limit(1)
    )
    return db.execute(stmt).scalar()


def _cohort_tier_counts(db, base, agg, fpct, repct) -> dict[str, int]:
    out: dict[str, int] = {}
    for name, tier in COHORT_TIERS.items():
        n = db.execute(
            select(func.count())
            .select_from(agg)
            .join(base, base.c.id == agg.c.qid)
            .where(
                agg.c.first_wrong >= tier["min_fw"],
                fpct >= tier["min_fpct"],
                repct >= tier["min_repct"],
            )
        ).scalar()
        out[name] = int(n or 0)
    return out


def count_cohort_tiers(
    db: Session, source_id: int | None = None, tag_ids: list[int] | None = None,
    year: int | None = None, years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
    user_id: int | None = None,
) -> dict:
    """Per-tier candidate counts (for the practice-setup tier labels).

    Returns {hardest, hard, common}. When user_id is given, also returns
    `by_exclude: {window: {tier: n}}` — per-tier counts after dropping the
    viewer's recently-answered questions, for each 排除近期練過 option."""
    base, agg, fpct, repct = _cohort_agg_subquery(
        db, source_id, tag_ids, exclude_qids=set(), year=year, years=years,
        allowed_source_ids=allowed_source_ids,
    )
    out: dict = _cohort_tier_counts(db, base, agg, fpct, repct)

    if user_id is not None:
        now = datetime.now(tz=timezone.utc)
        by_exclude: dict[str, dict] = {}
        for w in EXCLUDE_WINDOWS:
            if w == 0:
                by_exclude["0"] = {k: out[k] for k in COHORT_TIERS}
                continue
            recent = _recent_answered_qids(db, user_id, w, now)
            b2, a2, f2, r2 = _cohort_agg_subquery(
                db, source_id, tag_ids, exclude_qids=recent, year=year,
                years=years, allowed_source_ids=allowed_source_ids,
            )
            by_exclude[str(w)] = _cohort_tier_counts(db, b2, a2, f2, r2)
        out["by_exclude"] = by_exclude
    return out


# 排除近期練過 day options surfaced in the practice-setup UI (0 = 不排除).
EXCLUDE_WINDOWS = [0, 1, 3, 5]


def count_review_pools(
    db: Session, user_id: int, source_id: int | None = None,
    tag_ids: list[int] | None = None, year: int | None = None,
    years: list[int] | None = None,
    allowed_source_ids: list[int] | None = None,
) -> dict:
    """Available-question counts for the 複習 sub-modes, respecting the same
    source/tag/year filters the FE uses. Returns:
      {due, wrong, starred, wrong_kinds: {lapse, forget, blind, recent},
       by_exclude: {window: {wrong, starred}}}

    due/starred mirror their pickers (due = due_at<=now; starred ignores due).
    wrong = whole 常錯 pool; wrong_kinds = per-failure-mode counts (a card can
    fall in several, and ~uncategorised cards count only toward `wrong`).
    by_exclude gives the resulting wrong/starred pool sizes after dropping
    cards answered in the last N days, for each 排除近期練過 option."""
    now = datetime.now(tz=timezone.utc)
    base = _candidate_filter(
        db, source_id, tag_ids, exclude_qids=set(), year=year, years=years,
        allowed_source_ids=allowed_source_ids,
    ).subquery()

    due = db.execute(
        select(func.count())
        .select_from(UserQuestionState)
        .join(base, base.c.id == UserQuestionState.question_id)
        .where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.due_at.is_not(None),
            UserQuestionState.due_at <= now,
        )
    ).scalar()

    starred_qids = [
        qid for (qid,) in db.execute(
            select(UserQuestionState.question_id)
            .join(base, base.c.id == UserQuestionState.question_id)
            .where(
                UserQuestionState.user_id == user_id,
                UserQuestionState.is_starred == True,  # noqa: E712
            )
        ).all()
    ]

    rows = _wrong_pool_rows(db, user_id, base)
    wrong_qids = [r[0] for r in rows]
    conf3_map = _wrong_conf3_map(db, user_id, wrong_qids, now)
    kind_counts = {k: 0 for k in ("lapse", "forget", "blind", "recent")}
    for qid, lapses, state, stability, last_review_at, last_ok in rows:
        r = _retrievability(now, stability, last_review_at)
        cats = _wrong_category_set(
            lapses, state, r, last_ok, conf3_map.get(qid, 0)
        )
        for c in cats:
            kind_counts[c] += 1

    by_exclude: dict[str, dict] = {}
    for w in EXCLUDE_WINDOWS:
        recent = _recent_answered_qids(db, user_id, w, now) if w else set()
        by_exclude[str(w)] = {
            "wrong": sum(1 for q in wrong_qids if q not in recent),
            "starred": sum(1 for q in starred_qids if q not in recent),
        }

    return {
        "due": int(due or 0),
        "wrong": len(rows),
        "starred": len(starred_qids),
        "wrong_kinds": kind_counts,
        "by_exclude": by_exclude,
    }


MASTERED_STABILITY_DAYS = 30


def _exclude_mastered_qids(db: Session, user_id: int) -> set[int]:
    """Return question_ids the user has mastered (stability > N days)."""
    return {
        qid for (qid,) in db.execute(
            select(UserQuestionState.question_id).where(
                UserQuestionState.user_id == user_id,
                UserQuestionState.stability >= MASTERED_STABILITY_DAYS,
            )
        ).all()
    }


def pick_next_question_id(
    db: Session,
    user_id: int,
    source_id: int | None = None,
    tag_ids: list[int] | None = None,
    exclude_qids: set[int] | None = None,
    priority_qids: list[int] | None = None,
    year: int | None = None,
    years: list[int] | None = None,
    mode: str = "smart",
    exclude_mastered: bool = False,
    allowed_source_ids: list[int] | None = None,
    cohort_level: str | None = None,
    cohort_exclude_recent_days: int = 0,
    wrong_kinds: list[str] | None = None,
    wrong_exclude_recent_days: int = 0,
    starred_exclude_recent_days: int = 0,
    max_new_per_session: int | None = None,
) -> int | None:
    """Pick the next question id for this user. Returns None if none available.

    max_new_per_session overrides the per-session new-card cap (Pool B and the
    explicit `new` mode). None → NEW_CARD_SESSION_CAP.

    priority_qids: if non-empty, the picker tries to return one of these
    first (subject to source/tag/year filters). Used for in-session relearn:
    a wrong answer enqueues the qid to come back after N other questions,
    and the FE passes the ready-to-resurface ones here so they jump the
    line ahead of fresh FSRS picks.

    Modes:
      smart        — three-pool blend (default)
      new          — only never-seen cards
      review       — only FSRS-due review cards
      wrong        — 自己的常錯題 by 下次錯的機會 (risk), optional failure-mode filter
      starred      — starred cards by 出錯可能性, ignores due/mastery
      cohort_wrong — cohort common-wrong cards by 再答錯誤率
      random       — random from filtered pool, ignores FSRS state
    """
    exclude_qids = set(exclude_qids or set())
    if exclude_mastered:
        exclude_qids |= _exclude_mastered_qids(db, user_id)
    now = datetime.now(tz=timezone.utc)
    new_cap = NEW_CARD_SESSION_CAP if max_new_per_session is None else max_new_per_session

    # Priority queue: in-session relearn cards that have aged enough to
    # resurface. Filter against base candidate set so source/tag/year still
    # apply, then return the first one that exists in the pool.
    if priority_qids:
        # Strip excluded qids first (priority should not override exclude —
        # FE only passes ready ones, exclude is the pending-relearn ones).
        pri = [q for q in priority_qids if q not in exclude_qids]
        if pri:
            base_stmt = _candidate_filter(
                db, source_id, tag_ids, exclude_qids=set(), year=year, years=years,
                allowed_source_ids=allowed_source_ids,
            )
            valid = {
                qid for (qid,) in db.execute(
                    base_stmt.where(Question.id.in_(pri))
                ).all()
            }
            for qid in pri:  # preserve FE-chosen order
                if qid in valid:
                    return qid

    if mode == "wrong":
        return pick_wrong_question_id(
            db, user_id=user_id, tag_ids=tag_ids, source_id=source_id,
            year=year, years=years, allowed_source_ids=allowed_source_ids,
            exclude_qids=exclude_qids, wrong_kinds=wrong_kinds,
            exclude_recent_days=wrong_exclude_recent_days,
        )
    if mode == "starred":
        return pick_starred_question_id(
            db, user_id, source_id, tag_ids, exclude_qids, year, years,
            allowed_source_ids, exclude_recent_days=starred_exclude_recent_days,
        )
    if mode == "cohort_wrong":
        return pick_cohort_wrong_question_id(
            db, user_id, source_id, tag_ids, exclude_qids, year, years,
            allowed_source_ids, level=cohort_level,
            exclude_recent_days=cohort_exclude_recent_days,
        )
    if mode == "random":
        base = _candidate_filter(
            db, source_id, tag_ids, exclude_qids, year, years, allowed_source_ids
        ).subquery()
        row = db.execute(
            select(base.c.id).order_by(func.random()).limit(1)
        ).scalar()
        return row
    if mode == "new":
        rows = _pool_b_new_until_correct(
            db, user_id, source_id, tag_ids, exclude_qids, new_cap,
            year, years, allowed_source_ids,
        )
        return rows[0] if rows else None
    if mode == "review":
        pool = _pool_a_due(
            db, user_id, source_id, tag_ids, exclude_qids, now, year, years,
            allowed_source_ids,
        )
        return pool[0][0] if pool else None

    # smart (default) — three-pool mix
    pool_a = _pool_a_due(
        db, user_id, source_id, tag_ids, exclude_qids, now, year, years,
        allowed_source_ids,
    )
    sess = _get_session_state(user_id)
    new_remaining = max(0, new_cap - sess.new_cards_introduced)
    pool_b = _pool_b_new(
        db, user_id, source_id, tag_ids, exclude_qids, new_remaining, year, years,
        allowed_source_ids,
    )
    pool_c = _pool_c_starred(
        db, user_id, source_id, tag_ids, exclude_qids, now, year, years,
        allowed_source_ids,
    )

    # Roll dice to pick which pool to draw from based on POOL_RATIO,
    # falling back if the chosen pool is empty.
    rng = random.Random()
    chosen_pool = rng.choices(
        [name for name, _ in POOL_RATIO],
        weights=[w for _, w in POOL_RATIO],
        k=1,
    )[0]
    order = [chosen_pool] + [n for n, _ in POOL_RATIO if n != chosen_pool]

    for pool_name in order:
        if pool_name == "A" and pool_a:
            return pool_a[0][0]
        if pool_name == "B" and pool_b:
            qid = pool_b[0]
            sess.new_cards_introduced += 1
            return qid
        if pool_name == "C" and pool_c:
            return pool_c[0]

    # All pools empty — if the new-card cap was the only reason pool B was
    # empty (no due reviews yet, e.g. user switches sources or returns before
    # cards mature), reset the cap and try once more so the user isn't stuck.
    if sess.new_cards_introduced >= new_cap:
        sess.new_cards_introduced = 0
        retry_b = _pool_b_new(
            db, user_id, source_id, tag_ids, exclude_qids, new_cap,
            year, years, allowed_source_ids,
        )
        if retry_b:
            sess.new_cards_introduced += 1
            return retry_b[0]
    return None
