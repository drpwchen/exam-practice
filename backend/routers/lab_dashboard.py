"""Lab dashboard — 預期得分落點預測.

GET /api/lab/score-projection?coverage=0.65&pass_mark=60

Model (per major tag i):
  weight_i   真實出題比例 = EXAM_WEIGHT_YEAR past_paper tag share, with manual
             overrides (AST→6%, PAIN→3%; rest normalised to fill the rest).
  c_i        coverage = clamp(coverage_base × pool_count_i / mean, 0.20, 0.95).
             題庫該科題多 → 我們押中原題機率高 → c_i 高。
  a_first_i  初答正確率 (每題第一次作答) → 代表「新題會對的機率」。
  a_master_i 第二次以後正確率 → 代表「複習狀態下記得時的命中率」。
  R_i        已練題在考試日 (6/13) 的平均 FSRS retrievability (不複習情境)。

  命中部分 (比例 c_i):  p_hit = R × a_master + (1−R) × 0.2   (不複習)
                        p_hit = 0.9 × a_master + 0.1 × 0.2    (複習到位)
  未命中部分 (1−c_i):   a_first  (新題,複習無影響)
  單科得分率 s_i = c_i × p_hit + (1−c_i) × a_first
  總分 = Σ weight_i × s_i × 100

區間: 每科 s_i 套 Wilson 95% 半寬 (依該科作答數),合成總分保守/中位/樂觀。
"""

from datetime import datetime, timedelta, timezone
from math import sqrt

TAIPEI = timezone(timedelta(hours=8))

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fsrs import Rating
from pydantic import BaseModel
from sqlalchemy import func, select

from backend.fsrs_service import _scheduler, _state_to_card
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
    UserQuestionState,
)

# 公開給全體,僅排除 source-restricted / 非核心考生
EXCLUDED_USER_IDS: set[int] = {43, 44, 45, 46}

PAST_PAPER_CODE = "past_paper"
EXAM_WEIGHT_YEAR = 114  # use this past_paper year's tag distribution as exam-weight proxy

# Manual weight overrides (user-decided): tag.name → fraction of the exam.
# Pin these; the rest are normalised to fill 1 − Σoverride.
WEIGHT_OVERRIDES: dict[str, float] = {"AST": 0.06, "PAIN": 0.03}
# 顯示在該科旁的配分修改說明 (tag.name → note)
WEIGHT_NOTES: dict[str, str] = {
    "AST": "因應 Pain 增加,修正配分 8→6",
    "PAIN": "114 無此領域,預測今年配 3 分",
}

GUESS = 0.20            # 五選一盲猜
REVIEW_R = 0.90         # 複習到位時假設的記憶留存率
WILSON_Z = 1.96         # 95% CI


def _exam_date() -> datetime:
    """考試日 (settings.exam_date, ISO YYYY-MM-DD) as a UTC datetime."""
    y, m, d = (int(x) for x in settings.exam_date.split("-"))
    return datetime(y, m, d, 0, 0, tzinfo=TAIPEI).astimezone(timezone.utc)


EXAM_DATE = _exam_date()

router = APIRouter(prefix="/api/lab", tags=["lab"])


def require_projection_user(user: User = Depends(get_current_user)) -> User:
    if user.id in EXCLUDED_USER_IDS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return user


def _source_id(db: Session, code: str) -> int:
    return db.execute(select(Source.id).where(Source.code == code)).scalar_one()


def _exam_weights(db: Session) -> dict[int, float]:
    """tag-id → exam fraction. EXAM_WEIGHT_YEAR past_paper tag share with
    manual overrides applied."""
    past_paper_id = _source_id(db, PAST_PAPER_CODE)
    rows = db.execute(
        select(Tag.id, Tag.name, func.count(func.distinct(Question.id)))
        .join(QuestionTag, QuestionTag.tag_id == Tag.id)
        .join(Question, Question.id == QuestionTag.question_id)
        .where(
            Tag.level == "major",
            Question.source_id == past_paper_id,
            Question.year == EXAM_WEIGHT_YEAR,
        )
        .group_by(Tag.id)
    ).all()
    total = sum(n for _, _, n in rows) or 1
    raw = {tid: (name, n / total) for tid, name, n in rows}

    override_by_id = {
        tid: WEIGHT_OVERRIDES[name]
        for tid, (name, _) in raw.items()
        if name in WEIGHT_OVERRIDES
    }
    override_sum = sum(override_by_id.values())
    rest_sum = sum(
        frac for tid, (_, frac) in raw.items() if tid not in override_by_id
    ) or 1.0
    scale = (1.0 - override_sum) / rest_sum

    weights: dict[int, float] = {}
    for tid, (name, frac) in raw.items():
        weights[tid] = override_by_id.get(tid, frac * scale)
    return weights


def _retrievability(stability: float | None, last_review_at: datetime | None) -> float | None:
    """FSRS-6 R(t) at EXAM_DATE. None when card not yet stabilised."""
    if stability is None or last_review_at is None or stability <= 0:
        return None
    last = last_review_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    t_days = max(0.0, (EXAM_DATE - last).total_seconds() / 86400.0)
    return (1.0 + t_days / (9.0 * stability)) ** -1


def _simulated_r_review(s: UserQuestionState) -> float | None:
    """照 FSRS 到期進度複習到考前,回傳考試日的 retrievability。

    從現在到 6/13,每當題目到期 (due ≤ 考試日) 就在到期時補複習一次 (評 Good);
    逾期題立即補複習。複習會增加 stability、推後 due,直到下次 due 落在考後。
    最近剛複習、due 已在考後的題不動 (照進度本就不需再複習)。
    """
    if s.stability is None or s.last_review_at is None:
        return None
    card = _state_to_card(s)
    now = datetime.now(tz=timezone.utc)
    guard = 0
    while card.due is not None and guard < 30:
        due = card.due
        if due.tzinfo is None:
            due = due.replace(tzinfo=timezone.utc)
        if due > EXAM_DATE:
            break
        review_dt = due if due > now else now  # 逾期 → 現在補複習
        card, _ = _scheduler.review_card(
            card, Rating.Good, review_datetime=review_dt, review_duration=0
        )
        guard += 1
    return _retrievability(card.stability, card.last_review)


def _wilson_halfwidth(p: float, n: int) -> float:
    """Half-width of the Wilson 95% interval around p with sample n."""
    if n <= 0:
        return 0.30  # no data → very wide band
    z = WILSON_Z
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = (z * sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / denom
    lo = max(0.0, centre - half)
    hi = min(1.0, centre + half)
    return (hi - lo) / 2.0


class ProjectionTag(BaseModel):
    tag_id: int
    tag_name: str
    tag_short_zh: str | None
    sort_order: int
    weight: float            # 0..1 exam share
    score_points: float      # weight × 100, max attainable from this tag
    weight_note: str | None  # 配分手動修改說明 (僅 AST/PAIN)
    pool_count: int          # 該科題庫題數 (for coverage)
    coverage: float          # c_i, 0..1
    a_first: float | None    # 初答正確率
    a_master: float | None   # 第二次以後正確率
    r_exam: float | None     # 平均 retrievability at 6/13 (不複習)
    seen: int                # distinct questions attempted in tag
    attempts: int
    s_noreview: float        # 單科得分率 (不複習)
    s_review: float          # 單科得分率 (複習到位)
    contrib_noreview: float  # weight × s_noreview × 100
    contrib_review: float    # weight × s_review × 100


class ProjectionBand(BaseModel):
    lo: float
    mid: float
    hi: float


class ProjectionResult(BaseModel):
    coverage_base: float
    pass_mark: float
    exam_date: str
    total_noreview: ProjectionBand
    total_review: ProjectionBand
    tags: list[ProjectionTag]


@router.get("/score-projection", response_model=ProjectionResult)
def score_projection(
    coverage: float = Query(0.65, ge=0.0, le=1.0, description="題庫覆蓋真實考題的基準比例"),
    pass_mark: float = Query(60.0, ge=0.0, le=100.0),
    exam_delta: float = Query(0.0, ge=-0.10, le=0.10, description="考試時正確率相對練習的增減(百分點/100)"),
    db: Session = Depends(get_db),
    user: User = Depends(require_projection_user),
) -> ProjectionResult:
    weights = _exam_weights(db)
    past_paper_id = _source_id(db, PAST_PAPER_CODE)

    # --- per-tag question count (for coverage) ---
    pool_counts = dict(
        db.execute(
            select(QuestionTag.tag_id, func.count(func.distinct(Question.id)))
            .join(Question, Question.id == QuestionTag.question_id)
            .join(Tag, Tag.id == QuestionTag.tag_id)
            .where(Tag.level == "major", Question.source_id == past_paper_id)
            .group_by(QuestionTag.tag_id)
        ).all()
    )
    mean_pool = (sum(pool_counts.values()) / len(pool_counts)) if pool_counts else 1.0

    # 分數預估用單一 past_paper 來源;權重(配分分布)與 coverage(題量)由同一
    # 來源計算。
    pool_qids = select(Question.id).where(Question.source_id == past_paper_id)

    # --- first-attempt timestamp per question for this user ---
    ua_prior = (
        select(
            UserAnswer.question_id,
            func.min(UserAnswer.answered_at).label("first_at"),
        )
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.question_id.in_(pool_qids),
        )
        .group_by(UserAnswer.question_id)
        .subquery()
    )

    # first-attempt accuracy per tag
    first_rows = db.execute(
        select(
            QuestionTag.tag_id,
            func.count(UserAnswer.id),
            func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
        )
        .join(ua_prior, ua_prior.c.question_id == UserAnswer.question_id)
        .join(QuestionTag, QuestionTag.question_id == UserAnswer.question_id)
        .join(Tag, Tag.id == QuestionTag.tag_id)
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.answered_at == ua_prior.c.first_at,
            Tag.level == "major",
        )
        .group_by(QuestionTag.tag_id)
    ).all()
    first_by_tag = {tid: (n, c or 0) for tid, n, c in first_rows}

    # 2nd-or-later attempt accuracy per tag (master / retention)
    master_rows = db.execute(
        select(
            QuestionTag.tag_id,
            func.count(UserAnswer.id),
            func.sum(func.iif(UserAnswer.is_correct, 1, 0)),
        )
        .join(ua_prior, ua_prior.c.question_id == UserAnswer.question_id)
        .join(QuestionTag, QuestionTag.question_id == UserAnswer.question_id)
        .join(Tag, Tag.id == QuestionTag.tag_id)
        .where(
            UserAnswer.user_id == user.id,
            UserAnswer.answered_at > ua_prior.c.first_at,
            Tag.level == "major",
        )
        .group_by(QuestionTag.tag_id)
    ).all()
    master_by_tag = {tid: (n, c or 0) for tid, n, c in master_rows}

    # global fallbacks
    gf_n = sum(n for n, _ in first_by_tag.values())
    gf_c = sum(c for _, c in first_by_tag.values())
    a_first_global = (gf_c / gf_n) if gf_n else GUESS
    gm_n = sum(n for n, _ in master_by_tag.values())
    gm_c = sum(c for _, c in master_by_tag.values())
    a_master_global = (gm_c / gm_n) if gm_n else a_first_global

    # per-tag retrievability (seen questions with FSRS state).
    # r_noreview: 現狀記憶自然衰減到考試日。
    # r_review:   照 FSRS 到期進度複習到考前後,考試日的記憶 (逐題模擬)。
    state_rows = db.execute(
        select(QuestionTag.tag_id, UserQuestionState)
        .join(QuestionTag, QuestionTag.question_id == UserQuestionState.question_id)
        .join(Tag, Tag.id == QuestionTag.tag_id)
        .where(
            UserQuestionState.user_id == user.id,
            Tag.level == "major",
            UserQuestionState.question_id.in_(pool_qids),
        )
    ).all()
    r_nr_by_tag: dict[int, list[float]] = {}
    r_rv_by_tag: dict[int, list[float]] = {}
    for tid, st in state_rows:
        r_nr = _retrievability(st.stability, st.last_review_at)
        if r_nr is None:
            continue
        r_rv = _simulated_r_review(st)
        r_nr_by_tag.setdefault(tid, []).append(r_nr)
        r_rv_by_tag.setdefault(tid, []).append(max(r_rv, r_nr) if r_rv is not None else r_nr)

    # distinct seen per tag
    seen_rows = dict(
        db.execute(
            select(QuestionTag.tag_id, func.count(func.distinct(UserAnswer.question_id)))
            .join(QuestionTag, QuestionTag.question_id == UserAnswer.question_id)
            .join(Tag, Tag.id == QuestionTag.tag_id)
            .where(
                UserAnswer.user_id == user.id,
                Tag.level == "major",
                UserAnswer.question_id.in_(pool_qids),
            )
            .group_by(QuestionTag.tag_id)
        ).all()
    )

    tag_meta = db.execute(
        select(Tag.id, Tag.name, Tag.short_zh, Tag.sort_order).where(Tag.level == "major")
    ).all()

    tags_out: list[ProjectionTag] = []
    tot_nr = tot_nr_lo = tot_nr_hi = 0.0
    tot_rv = tot_rv_lo = tot_rv_hi = 0.0
    # 區間用各科 Wilson 半寬線性相加(保守):假設各科誤差正相關(同一人臨場
    # 狀態/複習投入是共同因子,狀態差易全科同向偏壞),作為「最壞打算」底線。

    for tid, name, short_zh, sort_order in tag_meta:
        weight = weights.get(tid, 0.0)
        pool_n = pool_counts.get(tid, 0)
        c_i = min(0.95, max(0.20, coverage * pool_n / mean_pool)) if mean_pool else coverage

        fn, fc = first_by_tag.get(tid, (0, 0))
        a_first = (fc / fn) if fn else a_first_global
        mn, mc = master_by_tag.get(tid, (0, 0))
        a_master = (mc / mn) if mn else a_master_global
        # 考試 vs 練習正確率調整(使用者自設,預設 0)
        if exam_delta:
            a_first = min(1.0, max(0.0, a_first + exam_delta))
            a_master = min(1.0, max(0.0, a_master + exam_delta))

        rs_nr = r_nr_by_tag.get(tid, [])
        r_exam = (sum(rs_nr) / len(rs_nr)) if rs_nr else None
        r_nr_used = r_exam if r_exam is not None else 0.0

        # 複習情境:逐題 FSRS 模擬到考前的平均 R。無 state 的科 fallback 到
        # max(現狀, 目標留存 0.9);並保證 ≥ 不複習(複習絕不使記憶下降)。
        rs_rv = r_rv_by_tag.get(tid, [])
        r_rv_used = (sum(rs_rv) / len(rs_rv)) if rs_rv else max(r_nr_used, REVIEW_R)
        r_rv_used = max(r_rv_used, r_nr_used)

        # 命中題「忘記」時退化到初答水平(基礎知識仍在),而非盲猜。
        # 對只刷一遍、沒建立 FSRS 複習軌跡的人,避免低估不複習實力。
        forget_floor = max(a_first, GUESS)
        # 複習後「記得」的正確率不應低於初見(二次正確率若低於初答多為樣本噪音)。
        a_master_eff = max(a_master, a_first)
        p_hit_nr = r_nr_used * a_master_eff + (1 - r_nr_used) * forget_floor
        p_hit_rv = r_rv_used * a_master_eff + (1 - r_rv_used) * forget_floor
        s_nr = c_i * p_hit_nr + (1 - c_i) * a_first
        # 最終防線:複習絕不低於不複習(防極端低樣本的單調性反轉)。
        s_rv = max(c_i * p_hit_rv + (1 - c_i) * a_first, s_nr)

        attempts = fn + mn
        half = _wilson_halfwidth((s_nr + s_rv) / 2, attempts)

        contrib_nr = weight * s_nr * 100
        contrib_rv = weight * s_rv * 100
        tot_nr += contrib_nr
        tot_rv += contrib_rv
        tot_nr_lo += weight * max(0.0, s_nr - half) * 100
        tot_nr_hi += weight * min(1.0, s_nr + half) * 100
        tot_rv_lo += weight * max(0.0, s_rv - half) * 100
        tot_rv_hi += weight * min(1.0, s_rv + half) * 100

        tags_out.append(ProjectionTag(
            tag_id=tid,
            tag_name=f"{sort_order:02d} {name}",
            tag_short_zh=short_zh,
            sort_order=sort_order,
            weight=round(weight, 4),
            score_points=round(weight * 100, 2),
            weight_note=WEIGHT_NOTES.get(name),
            pool_count=pool_n,
            coverage=round(c_i, 4),
            a_first=round(a_first, 4) if fn else None,
            a_master=round(a_master, 4) if mn else None,
            r_exam=round(r_exam, 4) if r_exam is not None else None,
            seen=seen_rows.get(tid, 0),
            attempts=attempts,
            s_noreview=round(s_nr, 4),
            s_review=round(s_rv, 4),
            contrib_noreview=round(contrib_nr, 2),
            contrib_review=round(contrib_rv, 2),
        ))

    tags_out.sort(key=lambda r: r.sort_order)
    return ProjectionResult(
        coverage_base=coverage,
        pass_mark=pass_mark,
        exam_date=EXAM_DATE.astimezone(TAIPEI).strftime("%Y-%m-%d"),
        total_noreview=ProjectionBand(lo=round(tot_nr_lo, 1), mid=round(tot_nr, 1), hi=round(tot_nr_hi, 1)),
        total_review=ProjectionBand(lo=round(tot_rv_lo, 1), mid=round(tot_rv, 1), hi=round(tot_rv_hi, 1)),
        tags=tags_out,
    )
