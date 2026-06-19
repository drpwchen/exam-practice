"""FSRS-6 wrapper: rating mapping + state persistence + scheduling."""

from datetime import datetime, timezone

from fsrs import Card, Rating, Scheduler, State

from backend.models import UserQuestionState

# Default desired retention (FSRS-6 library default is 0.9). Per-user overrides
# live in UserPreferences.study_config and are applied via _get_scheduler().
DEFAULT_RETENTION = 0.9

# Scheduler is immutable per desired_retention, so cache one instance per
# distinct retention value (rounded) instead of rebuilding on every review.
_scheduler_cache: dict[float, Scheduler] = {}


def _get_scheduler(request_retention: float | None = None) -> Scheduler:
    """Return a (cached) FSRS-6 Scheduler for the given desired retention.

    None → library/default retention. Other FSRS params stay at defaults
    (do not tune until post-exam)."""
    r = round(float(request_retention if request_retention is not None else DEFAULT_RETENTION), 3)
    sched = _scheduler_cache.get(r)
    if sched is None:
        sched = Scheduler(desired_retention=r)
        _scheduler_cache[r] = sched
    return sched


# Default-retention scheduler. Kept as a module-level singleton for callers that
# need a baseline scheduler (e.g. lab_dashboard projections); per-review tuning
# goes through _get_scheduler().
_scheduler = _get_scheduler()


def rating_from_answer(
    is_correct: bool,
    confidence: int,         # 1=guess, 2=some, 3=sure
    time_ms: int,
    median_ms_for_question: int | None,
) -> Rating:
    """Map answer outcome to FSRS rating.

    Wrong → Again. Correct: confidence 1=Hard (lucky guess), 2=Good, 3=Easy if fast.
    """
    if not is_correct:
        return Rating.Again
    if confidence == 1:
        return Rating.Hard
    if confidence == 2:
        return Rating.Good
    # confidence == 3 (sure)
    if median_ms_for_question and time_ms < 0.7 * median_ms_for_question:
        return Rating.Easy
    return Rating.Good


def _state_to_card(s: UserQuestionState) -> Card:
    """Reconstruct a fsrs.Card from our DB row. Empty/new state → fresh Card."""
    if s.last_review_at is None or s.stability is None:
        return Card()
    return Card(
        card_id=s.question_id,
        state=State(s.state),
        step=s.step,
        stability=s.stability,
        difficulty=s.difficulty,
        due=s.due_at.replace(tzinfo=timezone.utc) if s.due_at and s.due_at.tzinfo is None else s.due_at,
        last_review=(
            s.last_review_at.replace(tzinfo=timezone.utc)
            if s.last_review_at and s.last_review_at.tzinfo is None
            else s.last_review_at
        ),
    )


def _card_to_state(card: Card, s: UserQuestionState) -> None:
    s.state = int(card.state)
    s.step = card.step
    s.stability = card.stability
    s.difficulty = card.difficulty
    s.due_at = card.due
    s.last_review_at = card.last_review


def rebuild_state_from_history(
    db,
    user_id: int,
    question_id: int,
    request_retention: float | None = None,
) -> bool:
    """Replay all remaining UserAnswer rows for (user, question) and rebuild
    UserQuestionState from scratch. Used after deleting one or more answers
    so the FSRS state reflects the **remaining** history (not the original).

    Returns True if state row exists after rebuild, False if dropped (no
    remaining answers).
    """
    from sqlalchemy import select  # local import to avoid module-load cost
    from backend.models import Question, UserAnswer, UserQuestionState

    rows = db.execute(
        select(UserAnswer)
        .where(UserAnswer.user_id == user_id, UserAnswer.question_id == question_id)
        .order_by(UserAnswer.answered_at)
    ).scalars().all()

    state = db.execute(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user_id,
            UserQuestionState.question_id == question_id,
        )
    ).scalar_one_or_none()

    if not rows:
        if state is not None:
            db.delete(state)
        return False

    # Reset state to fresh; preserve user-set fields (is_starred, priority_boost,
    # dismissed_from_wrong_bank) so re-derivation doesn't lose manual flags.
    if state is None:
        state = UserQuestionState(user_id=user_id, question_id=question_id)
        db.add(state)
    state.state = 0
    state.step = None
    state.stability = None
    state.difficulty = None
    state.due_at = None
    state.last_review_at = None
    state.reps = 0
    state.lapses = 0

    median = db.execute(
        select(Question.median_time_ms).where(Question.id == question_id)
    ).scalar_one_or_none()

    for ua in rows:
        review_dt = ua.answered_at
        if review_dt.tzinfo is None:
            review_dt = review_dt.replace(tzinfo=timezone.utc)
        apply_answer(
            state,
            is_correct=bool(ua.is_correct),
            confidence=ua.confidence,
            time_ms=ua.time_ms,
            median_ms=median,
            review_dt=review_dt,
            request_retention=request_retention,
        )
    return True


def apply_answer(
    state: UserQuestionState,
    is_correct: bool,
    confidence: int,
    time_ms: int,
    median_ms: int | None,
    review_dt: datetime | None = None,
    request_retention: float | None = None,
) -> tuple[Rating, datetime | None]:
    """Apply a review to (mutates) state. Returns (rating, new due_at).

    request_retention sets the user's desired memory-retention target for the
    FSRS scheduler (higher → shorter intervals → more frequent reviews).
    None → DEFAULT_RETENTION."""
    card = _state_to_card(state)
    rating = rating_from_answer(is_correct, confidence, time_ms, median_ms)
    scheduler = _get_scheduler(request_retention)
    new_card, _log = scheduler.review_card(
        card,
        rating,
        review_datetime=review_dt or datetime.now(tz=timezone.utc),
        review_duration=time_ms,
    )
    _card_to_state(new_card, state)
    state.reps = (state.reps or 0) + 1
    if not is_correct:
        state.lapses = (state.lapses or 0) + 1
    return rating, new_card.due
