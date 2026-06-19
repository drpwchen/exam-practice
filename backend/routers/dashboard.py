"""Per-user dashboard / stats endpoints."""

from datetime import datetime, timedelta, timezone
from statistics import median

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from backend.deps import get_current_user, get_db
from backend.models import (
    Question,
    QuestionTag,
    Source,
    Tag,
    User,
    UserAnswer,
    UserQuestionState,
)

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _resolve_target_user(
    current_user: User, as_user: int | None, db: Session
) -> User:
    """If as_user is provided, return that User (admin only). Otherwise current.

    Raises 403 when a non-admin tries to use as_user, 404 when target doesn't exist.
    """
    if as_user is None:
        return current_user
    if not current_user.is_admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin required for as_user")
    target = db.get(User, as_user)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target user not found")
    return target


class SourceOverview(BaseModel):
    source_code: str
    total_questions: int
    answered_at_least_once: int
    total_attempts: int
    correct_attempts: int
    accuracy: float
    accuracy_7d: float | None = None      # rolling 7-day correctness in this source
    attempts_7d: int = 0
    due_today: int
    starred: int
    new_remaining: int
    by_year: list["YearStat"] = []  # populated for past_paper only


class YearStat(BaseModel):
    year: int
    total: int
    answered: int
    accuracy: float | None  # None if no attempts


class DueDayBucket(BaseModel):
    date: str        # YYYY-MM-DD
    count: int


class OverviewStats(BaseModel):
    total_questions: int
    answered_at_least_once: int
    total_attempts: int
    correct_attempts: int
    accuracy: float
    today_attempts: int
    today_correct: int
    due_today: int
    starred: int
    new_remaining: int
    by_source: list[SourceOverview] = []
    db_last_updated: str | None = None  # ISO date string

    # --- FSRS health metrics (cross-source) ---
    overdue_7plus: int = 0       # due_at < now - 7d
    overdue_recent: int = 0      # due_at in [now-7d, now]
    upcoming_7d: int = 0         # due_at in (now+1d, now+7d]
    learning_count: int = 0
    review_count: int = 0
    relearning_count: int = 0
    first_attempt_accuracy: float | None = None  # 1st-attempt accuracy (all time)
    retention_30d: float | None = None    # 2nd+ attempt accuracy in last 30 days
    retention_30d_n: int = 0              # sample size used to compute retention
    target_retention: float = 0.9         # FSRS default
    burden: float = 0.0                   # sum(1/S) over Review-state cards
    new_questions_7d: int = 0             # distinct first-time-answered questions in last 7d
    new_questions_today: int = 0          # distinct first-time-answered questions today
    due_next_7_days: list["DueDayBucket"] = []   # daily breakdown next 7 days


class TagStats(BaseModel):
    tag_id: int
    tag_name: str               # "01 ADL"
    tag_short_zh: str | None = None
    tag_full_name: str | None = None
    sort_order: int = 999
    question_count: int
    attempted_count: int
    accuracy: float | None  # null if not yet attempted
    last_attempt_at: datetime | None
    seen_ratio: float = 0.0   # attempted_count / question_count, 0..1
    # Cohort overlay (populated when include_cohort=true)
    cohort_seen_ratio: float | None = None    # mean coverage across active peers
    cohort_accuracy: float | None = None      # median accuracy across peers w/ ≥3 attempts
    cohort_n: int = 0                         # peers used for accuracy median


class WrongQuestionRow(BaseModel):
    question_id: int
    stem_preview: str
    source_code: str
    tags: list[str]
    lapses: int
    last_attempt_at: datetime | None
    last_was_wrong: bool
    total_attempts: int


@router.get("/overview", response_model=OverviewStats)
def overview(
    year: int | None = Query(None, description="restrict by_source totals to this year (past_paper only)"),
    source_code: str | None = Query(
        None,
        description=(
            "scope health metrics (retention, state, overdue, due_next_7_days, "
            "new_questions_7d, burden) to this source. by_source array always "
            "contains all sources."
        ),
    ),
    as_user: int | None = Query(None, description="admin-only: view another user's dashboard"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> OverviewStats:
    user = _resolve_target_user(current_user, as_user, db)
    now = datetime.now(tz=timezone.utc)
    # ── Time boundaries ───────────────────────────────────────────────
    # Calendar-day bounds use Asia/Taipei (UTC+8). DB stores all timestamps
    # in UTC; we convert at query time. UTC midnight would split "today"
    # at 08:00 Taipei, so calendar-day metrics MUST use local TZ.
    # Rolling windows (7d/30d) are relative durations — TZ-agnostic.
    taipei = timezone(timedelta(hours=8))
    today_start_local = (
        now.astimezone(taipei).replace(hour=0, minute=0, second=0, microsecond=0)
    )
    today_start = today_start_local.astimezone(timezone.utc)
    one_day_ahead = (today_start_local + timedelta(days=1)).astimezone(timezone.utc)   # end of today Taipei
    seven_days_ahead = (today_start_local + timedelta(days=8)).astimezone(timezone.utc) # end of day+7 Taipei

    # Rolling windows (relative durations)
    seven_days_ago = now - timedelta(days=7)
    thirty_days_ago = now - timedelta(days=30)
    seven_days_back = now - timedelta(days=7)

    # Resolve source filter for health metrics
    health_source_id: int | None = None
    if source_code:
        s = db.execute(
            select(Source).where(Source.code == source_code)
        ).scalar_one_or_none()
        if s is not None and user.has_source(s.code):
            health_source_id = s.id
    # Year filter only meaningful for past_paper (matches by_source semantics)
    health_year: int | None = (
        year if (year is not None and source_code == "past_paper") else None
    )

    def _scope_by_state(stmt):
        """Apply source/year filter on a stmt where UserQuestionState is the
        primary table (joins Question lazily when needed)."""
        if health_source_id is None and health_year is None:
            return stmt
        stmt = stmt.join(Question, Question.id == UserQuestionState.question_id)
        if health_source_id is not None:
            stmt = stmt.where(Question.source_id == health_source_id)
        if health_year is not None:
            stmt = stmt.where(Question.year == health_year)
        return stmt

    def _scope_by_answer(stmt):
        """Same but where UserAnswer is the primary table."""
        if health_source_id is None and health_year is None:
            return stmt
        stmt = stmt.join(Question, Question.id == UserAnswer.question_id)
        if health_source_id is not None:
            stmt = stmt.where(Question.source_id == health_source_id)
        if health_year is not None:
            stmt = stmt.where(Question.year == health_year)
        return stmt

    # Per-user source allowlist → ids; None means full access
    allowed_src_ids: list[int] | None = None
    if user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_src_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ] or [-1]

    total_q_stmt = select(func.count(Question.id))
    if allowed_src_ids is not None:
        total_q_stmt = total_q_stmt.where(Question.source_id.in_(allowed_src_ids))
    total_q = db.execute(total_q_stmt).scalar_one()

    attempts_total, attempts_correct = db.execute(
        select(
            func.count(UserAnswer.id),
            func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
        ).where(UserAnswer.user_id == user.id)
    ).one()

    today_total, today_correct = db.execute(
        select(
            func.count(UserAnswer.id),
            func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
        ).where(
            UserAnswer.user_id == user.id,
            UserAnswer.answered_at >= today_start,
        )
    ).one()

    answered_distinct = db.execute(
        select(func.count(func.distinct(UserAnswer.question_id))).where(
            UserAnswer.user_id == user.id
        )
    ).scalar_one()

    # Strict today bucket: (now, now+1d]. Overdue past now goes into overdue_7plus
    # / overdue_recent. Frontend sums all three for total queue display.
    _due_today_stmt = select(func.count(UserQuestionState.question_id)).where(
        UserQuestionState.user_id == user.id,
        UserQuestionState.due_at.is_not(None),
        UserQuestionState.due_at > now,
        UserQuestionState.due_at <= one_day_ahead,
    )
    _due_today_stmt = _scope_by_state(_due_today_stmt)
    due_today = db.execute(_due_today_stmt).scalar_one()

    starred = db.execute(
        select(func.count(UserQuestionState.question_id)).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.is_starred == True,  # noqa: E712
        )
    ).scalar_one()

    seen_qids = select(UserAnswer.question_id).where(UserAnswer.user_id == user.id)
    new_remaining_stmt = select(func.count(Question.id)).where(
        ~Question.id.in_(seen_qids)
    )
    if allowed_src_ids is not None:
        new_remaining_stmt = new_remaining_stmt.where(
            Question.source_id.in_(allowed_src_ids)
        )
    new_remaining = db.execute(new_remaining_stmt).scalar_one()

    accuracy = (attempts_correct or 0) / attempts_total if attempts_total else 0.0

    # Per-source breakdown — respect per-user source allowlist
    by_source: list[SourceOverview] = []
    for src in db.scalars(select(Source)).all():
        if not user.has_source(src.code):
            continue
        # year filter only meaningful for past_paper
        year_filter = year if (year is not None and src.code == "past_paper") else None

        q_base = select(func.count(Question.id)).where(Question.source_id == src.id)
        if year_filter is not None:
            q_base = q_base.where(Question.year == year_filter)
        s_total = db.execute(q_base).scalar_one()

        seen_q = (
            select(func.count(func.distinct(UserAnswer.question_id)))
            .join(Question, Question.id == UserAnswer.question_id)
            .where(UserAnswer.user_id == user.id, Question.source_id == src.id)
        )
        if year_filter is not None:
            seen_q = seen_q.where(Question.year == year_filter)
        s_seen = db.execute(seen_q).scalar_one()

        attempts_q = (
            select(
                func.count(UserAnswer.id),
                func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
            )
            .join(Question, Question.id == UserAnswer.question_id)
            .where(UserAnswer.user_id == user.id, Question.source_id == src.id)
        )
        if year_filter is not None:
            attempts_q = attempts_q.where(Question.year == year_filter)
        s_attempts, s_correct = db.execute(attempts_q).one()

        due_q = (
            select(func.count(UserQuestionState.question_id))
            .join(Question, Question.id == UserQuestionState.question_id)
            .where(
                UserQuestionState.user_id == user.id,
                UserQuestionState.due_at.is_not(None),
                UserQuestionState.due_at <= now,
                Question.source_id == src.id,
            )
        )
        if year_filter is not None:
            due_q = due_q.where(Question.year == year_filter)
        s_due = db.execute(due_q).scalar_one()

        starred_q = (
            select(func.count(UserQuestionState.question_id))
            .join(Question, Question.id == UserQuestionState.question_id)
            .where(
                UserQuestionState.user_id == user.id,
                UserQuestionState.is_starred == True,  # noqa: E712
                Question.source_id == src.id,
            )
        )
        if year_filter is not None:
            starred_q = starred_q.where(Question.year == year_filter)
        s_starred = db.execute(starred_q).scalar_one()

        # Rolling 7-day accuracy within this source
        seven_q = (
            select(
                func.count(UserAnswer.id),
                func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
            )
            .join(Question, Question.id == UserAnswer.question_id)
            .where(
                UserAnswer.user_id == user.id,
                Question.source_id == src.id,
                UserAnswer.answered_at >= seven_days_ago,
            )
        )
        if year_filter is not None:
            seven_q = seven_q.where(Question.year == year_filter)
        s_attempts_7d, s_correct_7d = db.execute(seven_q).one()
        s_accuracy_7d = (
            round((s_correct_7d or 0) / s_attempts_7d, 4)
            if s_attempts_7d
            else None
        )

        new_q = select(func.count(Question.id)).where(
            Question.source_id == src.id,
            ~Question.id.in_(
                select(UserAnswer.question_id).where(UserAnswer.user_id == user.id)
            ),
        )
        if year_filter is not None:
            new_q = new_q.where(Question.year == year_filter)
        s_new = db.execute(new_q).scalar_one()

        # For past_paper, also break down stats per-year so the dashboard can
        # show 112/113/114 cards. Only populated when the request itself isn't
        # already year-scoped.
        by_year: list[YearStat] = []
        if src.code == "past_paper" and year_filter is None:
            year_rows = db.execute(
                select(Question.year, func.count(Question.id))
                .where(Question.source_id == src.id, Question.year.is_not(None))
                .group_by(Question.year)
                .order_by(Question.year.desc())
            ).all()
            for y, ytotal in year_rows:
                yans = db.execute(
                    select(func.count(func.distinct(UserAnswer.question_id)))
                    .join(Question, Question.id == UserAnswer.question_id)
                    .where(
                        UserAnswer.user_id == user.id,
                        Question.source_id == src.id,
                        Question.year == y,
                    )
                ).scalar_one()
                yatt, ycor = db.execute(
                    select(
                        func.count(UserAnswer.id),
                        func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
                    )
                    .join(Question, Question.id == UserAnswer.question_id)
                    .where(
                        UserAnswer.user_id == user.id,
                        Question.source_id == src.id,
                        Question.year == y,
                    )
                ).one()
                acc = round((ycor or 0) / yatt, 4) if yatt else None
                by_year.append(YearStat(year=y, total=ytotal, answered=yans, accuracy=acc))

        by_source.append(SourceOverview(
            source_code=src.code,
            total_questions=s_total,
            answered_at_least_once=s_seen,
            total_attempts=s_attempts,
            correct_attempts=s_correct or 0,
            accuracy=round((s_correct or 0) / s_attempts, 4) if s_attempts else 0.0,
            accuracy_7d=s_accuracy_7d,
            attempts_7d=s_attempts_7d or 0,
            due_today=s_due,
            starred=s_starred,
            new_remaining=s_new,
            by_year=by_year,
        ))

    # ---- FSRS health metrics (scoped to health_source_id when set) ----
    def _state_count(state_val: int) -> int:
        stmt = select(func.count(UserQuestionState.question_id)).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.state == state_val,
        )
        return db.execute(_scope_by_state(stmt)).scalar_one()

    def _due_bucket(*conds) -> int:
        stmt = select(func.count(UserQuestionState.question_id)).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.due_at.is_not(None),
            *conds,
        )
        return db.execute(_scope_by_state(stmt)).scalar_one()

    overdue_7plus = _due_bucket(UserQuestionState.due_at < seven_days_back)
    overdue_recent = _due_bucket(
        UserQuestionState.due_at >= seven_days_back,
        UserQuestionState.due_at <= now,
    )
    upcoming_7d = _due_bucket(
        UserQuestionState.due_at > one_day_ahead,
        UserQuestionState.due_at <= seven_days_ahead,
    )
    learning_count = _state_count(1)
    review_count = _state_count(2)
    relearning_count = _state_count(3)

    # Retention: % correct on 2nd-or-later attempts in last 30 days.
    # Approximation of FSRS true retention without per-review state snapshot.
    # Subquery: first-attempt timestamp per question, used to exclude initial answers.
    ua_prior = (
        select(
            UserAnswer.question_id,
            func.min(UserAnswer.answered_at).label("first_at"),
        )
        .where(UserAnswer.user_id == user.id)
        .group_by(UserAnswer.question_id)
        .subquery()
    )
    _ret_stmt = (
        select(
            func.count(UserAnswer.id),
            func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
        )
        .join(ua_prior, ua_prior.c.question_id == UserAnswer.question_id)
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.answered_at >= thirty_days_ago,
            UserAnswer.answered_at > ua_prior.c.first_at,
        )
    )
    _ret_stmt = _scope_by_answer(_ret_stmt)
    retention_n, retention_correct = db.execute(_ret_stmt).one()
    retention_30d: float | None = (
        round((retention_correct or 0) / retention_n, 4) if retention_n else None
    )

    # First-attempt accuracy: correctness on the FIRST attempt per question
    # (all time). Reuses ua_prior; scoped by the same source filter.
    _first_stmt = (
        select(
            func.count(UserAnswer.id),
            func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
        )
        .join(ua_prior, ua_prior.c.question_id == UserAnswer.question_id)
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.answered_at == ua_prior.c.first_at,
        )
    )
    _first_stmt = _scope_by_answer(_first_stmt)
    first_n, first_correct = db.execute(_first_stmt).one()
    first_attempt_accuracy: float | None = (
        round((first_correct or 0) / first_n, 4) if first_n else None
    )

    # Burden: sum(1/S) over Review-state cards with valid stability
    _burden_stmt = (
        select(UserQuestionState.stability)
        .where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.state == 2,
            UserQuestionState.stability.is_not(None),
            UserQuestionState.stability > 0,
        )
    )
    _burden_stmt = _scope_by_state(_burden_stmt)
    burden_rows = db.execute(_burden_stmt).scalars().all()
    burden = round(sum(1.0 / s for s in burden_rows), 2)

    # New questions in last 7 days: count distinct questions whose FIRST answer
    # falls inside the window. Per-question first-attempt timestamp:
    _new_q_inner = (
        select(
            UserAnswer.question_id,
            func.min(UserAnswer.answered_at).label("first_at"),
        )
        .where(UserAnswer.user_id == user.id)
    )
    _new_q_inner = _scope_by_answer(_new_q_inner)
    new_q_subq = _new_q_inner.group_by(UserAnswer.question_id).subquery()
    new_questions_7d = db.execute(
        select(func.count())
        .select_from(new_q_subq)
        .where(new_q_subq.c.first_at >= seven_days_ago)
    ).scalar_one()
    new_questions_today = db.execute(
        select(func.count())
        .select_from(new_q_subq)
        .where(new_q_subq.c.first_at >= today_start)
    ).scalar_one()

    # Due bucket per day for next 7 calendar days (Taipei). i=0 is today.
    due_next_7_days: list[DueDayBucket] = []
    for i in range(7):
        day_start_local = today_start_local + timedelta(days=i)
        day_end_local = day_start_local + timedelta(days=1)
        day_start = day_start_local.astimezone(timezone.utc)
        day_end = day_end_local.astimezone(timezone.utc)
        stmt = select(func.count(UserQuestionState.question_id)).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.due_at.is_not(None),
            UserQuestionState.due_at >= day_start,
            UserQuestionState.due_at < day_end,
        )
        cnt = db.execute(_scope_by_state(stmt)).scalar_one()
        due_next_7_days.append(
            DueDayBucket(date=day_start_local.strftime("%Y-%m-%d"), count=cnt)
        )

    # Single source of truth: settings.db_updated_date (config.py).
    # Bumped whenever new questions are loaded into DB.
    from backend.config import settings as _settings
    db_last_updated = _settings.db_updated_date

    return OverviewStats(
        total_questions=total_q,
        answered_at_least_once=answered_distinct,
        total_attempts=attempts_total,
        correct_attempts=attempts_correct or 0,
        accuracy=round(accuracy, 4),
        today_attempts=today_total,
        today_correct=today_correct or 0,
        due_today=due_today,
        starred=starred,
        new_remaining=new_remaining,
        by_source=by_source,
        db_last_updated=db_last_updated,
        overdue_7plus=overdue_7plus,
        overdue_recent=overdue_recent,
        upcoming_7d=upcoming_7d,
        learning_count=learning_count,
        review_count=review_count,
        relearning_count=relearning_count,
        first_attempt_accuracy=first_attempt_accuracy,
        retention_30d=retention_30d,
        retention_30d_n=retention_n or 0,
        target_retention=0.9,
        burden=burden,
        new_questions_7d=new_questions_7d,
        new_questions_today=new_questions_today,
        due_next_7_days=due_next_7_days,
    )


@router.get("/by-tag", response_model=list[TagStats])
def by_tag(
    source_code: str | None = Query(None, description="filter by source"),
    year: int | None = Query(None, description="filter by year (past_paper only), deprecated — prefer years"),
    years: str | None = Query(None, description="comma-separated years for past_paper, e.g. 110,111,112"),
    as_user: int | None = Query(None, description="admin-only: view another user's tag stats"),
    include_cohort: bool = Query(False, description="overlay cohort coverage + accuracy median"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> list[TagStats]:
    """One row per major tag with: total questions, attempted, accuracy.
    Optional source filter narrows the question set."""
    user = _resolve_target_user(current_user, as_user, db)
    source_id_filter: int | None = None
    if source_code:
        s = db.execute(select(Source).where(Source.code == source_code)).scalar_one_or_none()
        if s is not None:
            if not user.has_source(s.code):
                return []
            source_id_filter = s.id

    # Normalise year filter: `years` (multi) takes precedence over legacy `year` (single)
    year_list: list[int] | None = None
    if years:
        year_list = [int(y) for y in years.split(",") if y.strip().isdigit()]
    elif year is not None:
        year_list = [year]

    # Per-user allowed-source filter (used when source_code not given)
    allowed_src_ids: list[int] | None = None
    if user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_src_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ] or [-1]

    q_count_stmt = (
        select(
            Tag.id,
            Tag.name,
            Tag.display_name,
            Tag.short_zh,
            Tag.sort_order,
            func.count(func.distinct(Question.id)).label("q_count"),
        )
        .outerjoin(QuestionTag, QuestionTag.tag_id == Tag.id)
        .outerjoin(Question, Question.id == QuestionTag.question_id)
        .where(Tag.level == "major")
    )
    if source_id_filter is not None:
        q_count_stmt = q_count_stmt.where(
            (Question.source_id == source_id_filter) | (Question.id.is_(None))
        )
    elif allowed_src_ids is not None:
        q_count_stmt = q_count_stmt.where(
            (Question.source_id.in_(allowed_src_ids)) | (Question.id.is_(None))
        )
    if year_list is not None:
        q_count_stmt = q_count_stmt.where(
            (Question.year.in_(year_list)) | (Question.id.is_(None))
        )
    q_count_stmt = q_count_stmt.group_by(
        Tag.id, Tag.name, Tag.display_name, Tag.short_zh, Tag.sort_order
    )
    rows = db.execute(q_count_stmt).all()

    # Cohort aggregations: per-tag, average coverage and median accuracy across
    # peers (excluding the current user). Only computed when include_cohort=true.
    cohort_cov_per_tag: dict[int, list[float]] = {}
    cohort_acc_per_tag: dict[int, list[float]] = {}
    if include_cohort:
        cohort_filter_clauses = []
        if source_id_filter is not None:
            cohort_filter_clauses.append(Question.source_id == source_id_filter)
        elif allowed_src_ids is not None:
            cohort_filter_clauses.append(Question.source_id.in_(allowed_src_ids))
        if year_list is not None:
            cohort_filter_clauses.append(Question.year.in_(year_list))

        # Per (tag, peer) attempts + correct + distinct seen
        peer_rows = db.execute(
            select(
                QuestionTag.tag_id,
                UserAnswer.user_id,
                func.count(UserAnswer.id).label("att"),
                func.sum(func.iif(UserAnswer.is_correct, 1, 0)).label("cor"),
                func.count(func.distinct(UserAnswer.question_id)).label("seen"),
            )
            .join(Question, Question.id == UserAnswer.question_id)
            .join(QuestionTag, QuestionTag.question_id == UserAnswer.question_id)
            .where(
                UserAnswer.user_id != user.id,
                *cohort_filter_clauses,
            )
            .group_by(QuestionTag.tag_id, UserAnswer.user_id)
        ).all()
        for tag_id, _peer, att, cor, seen in peer_rows:
            if att and att >= 3:
                cohort_acc_per_tag.setdefault(tag_id, []).append((cor or 0) / att)
            if seen:
                cohort_cov_per_tag.setdefault(tag_id, []).append(seen or 0)

    out: list[TagStats] = []
    for tag_id, tag_name, tag_full, tag_zh, sort_order, q_count in rows:
        # Per-tag attempts for this user
        attempts_q = (
            select(
                func.count(UserAnswer.id),
                func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
                func.max(UserAnswer.answered_at),
                func.count(func.distinct(UserAnswer.question_id)),
            )
            .join(QuestionTag, QuestionTag.question_id == UserAnswer.question_id)
            .where(QuestionTag.tag_id == tag_id, UserAnswer.user_id == user.id)
        )
        if (
            source_id_filter is not None
            or year_list is not None
            or allowed_src_ids is not None
        ):
            attempts_q = attempts_q.join(
                Question, Question.id == UserAnswer.question_id
            )
            if source_id_filter is not None:
                attempts_q = attempts_q.where(Question.source_id == source_id_filter)
            elif allowed_src_ids is not None:
                attempts_q = attempts_q.where(Question.source_id.in_(allowed_src_ids))
            if year_list is not None:
                attempts_q = attempts_q.where(Question.year.in_(year_list))
        attempts_total, attempts_correct, last_at, attempted_q = db.execute(attempts_q).one()
        accuracy = (
            round((attempts_correct or 0) / attempts_total, 4)
            if attempts_total
            else None
        )
        cohort_seen_ratio: float | None = None
        cohort_accuracy: float | None = None
        cohort_n = 0
        if include_cohort:
            covs = cohort_cov_per_tag.get(tag_id, [])
            if covs and q_count:
                cohort_seen_ratio = round(sum(covs) / len(covs) / q_count, 4)
            accs = cohort_acc_per_tag.get(tag_id, [])
            if len(accs) >= 3:
                cohort_accuracy = round(median(accs), 4)
                cohort_n = len(accs)

        out.append(
            TagStats(
                tag_id=tag_id,
                tag_name=f"{sort_order:02d} {tag_name}",
                tag_short_zh=tag_zh,
                tag_full_name=tag_full,
                sort_order=sort_order,
                question_count=q_count,
                attempted_count=attempted_q or 0,
                accuracy=accuracy,
                last_attempt_at=last_at,
                seen_ratio=((attempted_q or 0) / q_count) if q_count else 0.0,
                cohort_seen_ratio=cohort_seen_ratio,
                cohort_accuracy=cohort_accuracy,
                cohort_n=cohort_n,
            )
        )
    out.sort(key=lambda r: r.sort_order)  # by official 26 編號
    return out


@router.get("/wrong-list", response_model=list[WrongQuestionRow])
def wrong_list(
    limit: int = Query(50, ge=1, le=200),
    source_code: str | None = Query(None),
    year: int | None = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[WrongQuestionRow]:
    """Questions the user has missed (lapses>=1 or last attempt wrong)."""
    # Most recent answer per question for this user
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
            UserAnswer.answered_at,
            UserAnswer.is_correct,
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

    state_attempts = (
        select(
            UserQuestionState.question_id,
            UserQuestionState.lapses,
        )
        .where(UserQuestionState.user_id == user.id)
        .subquery()
    )

    attempts_count = (
        select(
            UserAnswer.question_id,
            func.count(UserAnswer.id).label("n"),
        )
        .where(UserAnswer.user_id == user.id)
        .group_by(UserAnswer.question_id)
        .subquery()
    )

    src_filter_id: int | None = None
    if source_code:
        s = db.execute(select(Source).where(Source.code == source_code)).scalar_one_or_none()
        if s is not None:
            if not user.has_source(s.code):
                return []
            src_filter_id = s.id

    # Build per-user allowed source id filter (None = full access)
    allowed_src_ids: list[int] | None = None
    if user.allowed_sources:
        allowed_codes = {
            c.strip() for c in user.allowed_sources.split(",") if c.strip()
        }
        allowed_src_ids = [
            sid for (sid,) in db.execute(
                select(Source.id).where(Source.code.in_(allowed_codes))
            ).all()
        ] or [-1]  # empty list → match nothing

    rows_stmt = (
        select(
            Question.id,
            Question.stem,
            Source.code,
            state_attempts.c.lapses,
            last_join.c.answered_at,
            last_join.c.is_correct,
            attempts_count.c.n,
        )
        .join(Source, Source.id == Question.source_id)
        .join(last_join, last_join.c.question_id == Question.id)
        .outerjoin(state_attempts, state_attempts.c.question_id == Question.id)
        .outerjoin(attempts_count, attempts_count.c.question_id == Question.id)
        .where(
            (state_attempts.c.lapses >= 1) | (last_join.c.is_correct == False)  # noqa: E712
        )
        .order_by(last_join.c.answered_at.desc())
        .limit(limit)
    )
    if src_filter_id is not None:
        rows_stmt = rows_stmt.where(Question.source_id == src_filter_id)
    elif allowed_src_ids is not None:
        rows_stmt = rows_stmt.where(Question.source_id.in_(allowed_src_ids))
    if year is not None:
        rows_stmt = rows_stmt.where(Question.year == year)
    rows = db.execute(rows_stmt).all()

    out: list[WrongQuestionRow] = []
    for qid, stem, src_code, lapses, last_at, last_was, n in rows:
        tag_names = [
            n2 for (n2,) in db.execute(
                select(Tag.name)
                .join(QuestionTag, QuestionTag.tag_id == Tag.id)
                .where(QuestionTag.question_id == qid)
            ).all()
        ]
        out.append(
            WrongQuestionRow(
                question_id=qid,
                stem_preview=(stem or "")[:120],
                source_code=src_code,
                tags=tag_names,
                lapses=lapses or 0,
                last_attempt_at=last_at,
                last_was_wrong=not last_was,
                total_attempts=n or 0,
            )
        )
    return out
