"""Admin-only cohort analytics: per-user progress, frequently-wrong, overview.

Numbers are aggregated across the whole DB (single past_paper source).
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import case, distinct, func, null, select
from sqlalchemy.orm import Session

from backend.auth import hash_password
from backend.config import settings
from backend.deps import get_db, require_admin
from backend.models import Question, Source, User, UserAnswer

router = APIRouter(prefix="/api/admin/cohort", tags=["admin-cohort"])


class UserProgressRow(BaseModel):
    user_id: int
    username: str
    is_admin: bool
    answered: int        # distinct questions answered at least once
    total_attempts: int  # total attempts (incl. retries)
    attempts: int        # total attempts (incl. retries)
    correct: int
    accuracy: float | None  # null if no attempts
    first_attempts: int       # = answered (one per question)
    first_correct: int
    first_accuracy: float | None
    re_attempts: int
    re_correct: int
    re_accuracy: float | None
    last_active: datetime | None


class FrequentlyWrongRow(BaseModel):
    question_id: int
    stem_preview: str
    source_code: str
    distinct_users: int
    wrong_users: int      # distinct users with ≥1 wrong attempt
    attempts: int
    wrong_attempts: int
    wrong_pct: float


class CohortOverview(BaseModel):
    total_users: int
    active_users_7d: int           # answered something in last 7 days
    total_attempts: int
    overall_accuracy: float
    average_attempts_per_user: float
    sessions_estimated: int        # distinct (user_id, calendar-day) pairs
    top_active_users: list[dict]   # [{username, attempts, accuracy}, ...]


def _visible_question_ids_subq():
    """Subquery: all visible (past_paper) question ids."""
    return (
        select(Question.id)
        .join(Source, Source.id == Question.source_id)
        .where(Source.code == "past_paper")
    )


@router.get("/users", response_model=list[UserProgressRow])
def cohort_users(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[UserProgressRow]:
    """Per-user progress table. Includes users with 0 attempts.

    Splits accuracy into 初答 (first attempt per question) vs 再答 (subsequent
    retries), matching the wrong-bank cohort definition.
    """
    visible_q = _visible_question_ids_subq()

    # Per (user, question) attempt order: rn==1 first attempt, rn>1 retry.
    rn = func.row_number().over(
        partition_by=(UserAnswer.user_id, UserAnswer.question_id),
        order_by=(UserAnswer.answered_at, UserAnswer.id),
    ).label("rn")
    asub = select(
        UserAnswer.user_id.label("uid"),
        UserAnswer.question_id.label("qid"),
        UserAnswer.is_correct.label("ic"),
        UserAnswer.answered_at.label("at"),
        UserAnswer.id.label("aid"),
        rn,
    ).where(UserAnswer.question_id.in_(visible_q)).subquery()

    per_user = (
        select(
            asub.c.uid.label("uid"),
            func.count(asub.c.aid).label("attempts"),
            func.coalesce(func.sum(func.iif(asub.c.ic, 1, 0)), 0).label("correct"),
            func.sum(func.iif(asub.c.rn == 1, 1, 0)).label("first_attempts"),
            func.sum(
                func.iif(asub.c.rn == 1, func.iif(asub.c.ic, 1, 0), 0)
            ).label("first_correct"),
            func.sum(func.iif(asub.c.rn > 1, 1, 0)).label("re_attempts"),
            func.sum(
                func.iif(asub.c.rn > 1, func.iif(asub.c.ic, 1, 0), 0)
            ).label("re_correct"),
            func.max(asub.c.at).label("last_active"),
        )
        .group_by(asub.c.uid)
        .subquery()
    )

    stmt = (
        select(
            User.id,
            User.username,
            User.is_admin,
            func.coalesce(per_user.c.attempts, 0).label("attempts"),
            func.coalesce(per_user.c.correct, 0).label("correct"),
            func.coalesce(per_user.c.first_attempts, 0).label("first_attempts"),
            func.coalesce(per_user.c.first_correct, 0).label("first_correct"),
            func.coalesce(per_user.c.re_attempts, 0).label("re_attempts"),
            func.coalesce(per_user.c.re_correct, 0).label("re_correct"),
            per_user.c.last_active,
        )
        .select_from(User)
        .join(per_user, per_user.c.uid == User.id, isouter=True)
        .order_by(func.coalesce(per_user.c.attempts, 0).desc(), User.username)
    )

    out: list[UserProgressRow] = []
    for (
        uid, name, is_admin,
        attempts, correct,
        first_att, first_cor,
        re_att, re_cor,
        last,
    ) in db.execute(stmt).all():
        acc = round(correct / attempts, 4) if attempts else None
        first_acc = round(first_cor / first_att, 4) if first_att else None
        re_acc = round(re_cor / re_att, 4) if re_att else None
        out.append(
            UserProgressRow(
                user_id=uid,
                username=name,
                is_admin=bool(is_admin),
                answered=first_att or 0,
                total_attempts=attempts or 0,
                attempts=attempts or 0,
                correct=correct or 0,
                accuracy=acc,
                first_attempts=first_att or 0,
                first_correct=first_cor or 0,
                first_accuracy=first_acc,
                re_attempts=re_att or 0,
                re_correct=re_cor or 0,
                re_accuracy=re_acc,
                last_active=last,
            )
        )
    return out


@router.get("/frequently-wrong", response_model=list[FrequentlyWrongRow])
def cohort_frequently_wrong(
    min_wrong_users: int = Query(3, ge=1, description="only questions with >= N distinct users who answered wrong"),
    limit: int = Query(200, ge=1, le=500),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[FrequentlyWrongRow]:
    """Questions the cohort gets wrong most often, ranked by number of distinct wrong users."""
    # iif(is_correct, NULL, user_id) → NULL for correct rows, user_id for wrong rows
    # COUNT(DISTINCT ...) ignores NULL, so this counts distinct users with ≥1 wrong answer
    wrong_user_expr = func.count(
        distinct(func.iif(UserAnswer.is_correct, null(), UserAnswer.user_id))
    )
    stmt = (
        select(
            Question.id,
            Question.stem,
            Source.code,
            func.count(distinct(UserAnswer.user_id)).label("uniq_users"),
            wrong_user_expr.label("wrong_users"),
            func.count(UserAnswer.id).label("attempts"),
            func.coalesce(
                func.sum(func.iif(UserAnswer.is_correct, 0, 1)), 0
            ).label("wrong"),
        )
        .join(Source, Source.id == Question.source_id)
        .join(UserAnswer, UserAnswer.question_id == Question.id)
        .where(Source.code == "past_paper")
        .group_by(Question.id, Question.stem, Source.code)
        .having(wrong_user_expr >= min_wrong_users)
        .order_by(
            wrong_user_expr.desc(),
            (func.coalesce(func.sum(func.iif(UserAnswer.is_correct, 0, 1)), 0) * 1.0
             / func.count(UserAnswer.id)).desc(),
        )
        .limit(limit)
    )

    out: list[FrequentlyWrongRow] = []
    for qid, stem, src, uniq, wrong_users, attempts, wrong in db.execute(stmt).all():
        out.append(
            FrequentlyWrongRow(
                question_id=qid,
                stem_preview=(stem or "")[:140],
                source_code=src,
                distinct_users=uniq,
                wrong_users=wrong_users or 0,
                attempts=attempts,
                wrong_attempts=wrong,
                wrong_pct=round(wrong * 100.0 / attempts, 1) if attempts else 0.0,
            )
        )
    return out


class CohortUserInfo(BaseModel):
    user_id: int
    username: str
    is_admin: bool
    allowed_sources: str | None = None


@router.get("/users/{user_id}", response_model=CohortUserInfo)
def cohort_user_info(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> CohortUserInfo:
    """Single-user lookup, used by the FE read-only dashboard banner."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    return CohortUserInfo(
        user_id=u.id,
        username=u.username,
        is_admin=bool(u.is_admin),
        allowed_sources=u.allowed_sources,
    )


class ResetPasswordResponse(BaseModel):
    user_id: int
    username: str
    new_password: str


@router.post("/users/{user_id}/reset-password", response_model=ResetPasswordResponse)
def cohort_reset_password(
    user_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ResetPasswordResponse:
    """Reset a user's password to the system default and force change on next login."""
    u = db.get(User, user_id)
    if u is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "User not found")
    u.password_hash = hash_password(settings.default_password)
    u.must_change_password = True
    db.commit()
    return ResetPasswordResponse(
        user_id=u.id, username=u.username, new_password=settings.default_password
    )


class CohortProjectionRow(BaseModel):
    user_id: int
    username: str
    answered: int
    noreview_lo: float    # 最壞打算 (不複習 + 保守下界)
    noreview_mid: float
    review_lo: float
    review_mid: float


@router.get("/projections", response_model=list[CohortProjectionRow])
def cohort_projections(
    coverage: float = Query(0.65, ge=0.0, le=1.0),
    pass_mark: float = Query(60.0, ge=0.0, le=100.0),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[CohortProjectionRow]:
    """每位使用者的預期得分,排序為最壞打算 (不複習保守下界) 由低到高。
    排除非核心考生 (EXCLUDED_USER_IDS),預測對他們不適用。"""
    from backend.routers.lab_dashboard import EXCLUDED_USER_IDS, score_projection

    uids = [
        uid for (uid,) in db.execute(
            select(distinct(UserAnswer.user_id))
        ).all()
    ]
    out: list[CohortProjectionRow] = []
    for uid in uids:
        if uid in EXCLUDED_USER_IDS:
            continue
        u = db.get(User, uid)
        if u is None:
            continue
        r = score_projection(
            coverage=coverage, pass_mark=pass_mark, exam_delta=0.0, db=db, user=u
        )
        out.append(CohortProjectionRow(
            user_id=uid,
            username=u.username,
            answered=sum(t.seen for t in r.tags),
            noreview_lo=r.total_noreview.lo,
            noreview_mid=r.total_noreview.mid,
            review_lo=r.total_review.lo,
            review_mid=r.total_review.mid,
        ))
    out.sort(key=lambda x: x.noreview_lo)
    return out


@router.get("/overview", response_model=CohortOverview)
def cohort_overview(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> CohortOverview:
    """Cohort-wide summary numbers + top 5 active users."""
    visible_q = _visible_question_ids_subq()
    now = datetime.now(tz=timezone.utc)
    seven_days_ago = now - timedelta(days=7)

    total_users = db.execute(select(func.count(User.id))).scalar_one()

    total_attempts, total_correct = db.execute(
        select(
            func.count(UserAnswer.id),
            func.coalesce(func.sum(func.iif(UserAnswer.is_correct, 1, 0)), 0),
        ).where(UserAnswer.question_id.in_(visible_q))
    ).one()

    active_7d = db.execute(
        select(func.count(distinct(UserAnswer.user_id))).where(
            UserAnswer.question_id.in_(visible_q),
            UserAnswer.answered_at >= seven_days_ago,
        )
    ).scalar_one()

    # Distinct (user_id, day) pairs ≈ session count.
    # SQLite: date(answered_at) gives YYYY-MM-DD.
    sessions = db.execute(
        select(
            func.count(
                distinct(
                    func.printf("%d-%s", UserAnswer.user_id, func.date(UserAnswer.answered_at))
                )
            )
        ).where(UserAnswer.question_id.in_(visible_q))
    ).scalar_one()

    # Top 5 most-active users (by attempt count).
    top_stmt = (
        select(
            User.username,
            func.count(UserAnswer.id).label("attempts"),
            func.coalesce(func.sum(func.iif(UserAnswer.is_correct, 1, 0)), 0).label("correct"),
        )
        .join(UserAnswer, UserAnswer.user_id == User.id)
        .where(UserAnswer.question_id.in_(visible_q))
        .group_by(User.id, User.username)
        .order_by(func.count(UserAnswer.id).desc())
        .limit(5)
    )
    top_users = []
    for name, attempts, correct in db.execute(top_stmt).all():
        top_users.append({
            "username": name,
            "attempts": attempts,
            "accuracy": round(correct / attempts, 4) if attempts else 0.0,
        })

    return CohortOverview(
        total_users=total_users,
        active_users_7d=active_7d,
        total_attempts=total_attempts or 0,
        overall_accuracy=round((total_correct or 0) / total_attempts, 4)
        if total_attempts else 0.0,
        average_attempts_per_user=round(total_attempts / total_users, 1)
        if total_users else 0.0,
        sessions_estimated=sessions or 0,
        top_active_users=top_users,
    )
