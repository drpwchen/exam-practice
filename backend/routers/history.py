"""Per-user answer history + bulk/per-question reset."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import desc, func, select
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

router = APIRouter(prefix="/api/history", tags=["history"])


class HistoryRow(BaseModel):
    answer_id: int
    question_id: int
    source_code: str
    answered_at: datetime
    selected_letter: str
    is_correct: bool
    confidence: int
    time_ms: int
    fsrs_rating: int
    stem_preview: str
    tags: list[str]


class HistoryPage(BaseModel):
    rows: list[HistoryRow]
    total: int
    offset: int
    limit: int


@router.get("", response_model=HistoryPage)
def list_history(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> HistoryPage:
    total = db.execute(
        select(func.count(UserAnswer.id)).where(UserAnswer.user_id == user.id)
    ).scalar_one()

    rows = db.execute(
        select(UserAnswer, Question.stem, Source.code)
        .join(Question, Question.id == UserAnswer.question_id)
        .join(Source, Source.id == Question.source_id)
        .where(UserAnswer.user_id == user.id)
        .order_by(desc(UserAnswer.answered_at))
        .limit(limit)
        .offset(offset)
    ).all()

    out: list[HistoryRow] = []
    for ua, stem, src in rows:
        tag_names = [
            n for (n,) in db.execute(
                select(Tag.name)
                .join(QuestionTag, QuestionTag.tag_id == Tag.id)
                .where(QuestionTag.question_id == ua.question_id)
            ).all()
        ]
        out.append(
            HistoryRow(
                answer_id=ua.id,
                question_id=ua.question_id,
                source_code=src,
                answered_at=ua.answered_at,
                selected_letter=ua.selected_letter,
                is_correct=ua.is_correct,
                confidence=ua.confidence,
                time_ms=ua.time_ms,
                fsrs_rating=ua.fsrs_rating,
                stem_preview=(stem or "")[:80],
                tags=tag_names,
            )
        )
    return HistoryPage(rows=out, total=total, offset=offset, limit=limit)


@router.delete("/answer/{answer_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_answer(
    answer_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Delete a single answer row and rebuild the FSRS state from remaining
    attempts. Effect = "as if that one attempt never happened":
      - last attempt only → state drops back to never-answered
      - earlier attempt → state replays through the remaining sequence,
        landing on the same FSRS values you'd have without the deleted row
    """
    from backend.fsrs_service import rebuild_state_from_history

    ua = db.get(UserAnswer, answer_id)
    if not ua or ua.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Answer not found")
    qid = ua.question_id
    db.delete(ua)
    db.flush()
    rebuild_state_from_history(db, user.id, qid)
    db.commit()


@router.delete("/question/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def reset_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Wipe all of this user's answers for a question and reset FSRS state."""
    db.execute(
        UserAnswer.__table__.delete().where(
            UserAnswer.user_id == user.id, UserAnswer.question_id == question_id
        )
    )
    db.execute(
        UserQuestionState.__table__.delete().where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id == question_id,
        )
    )
    db.commit()


class BulkDeleteAnswers(BaseModel):
    answer_ids: list[int]


@router.post("/bulk-delete-answers")
def bulk_delete_answers(
    body: BulkDeleteAnswers,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not body.answer_ids:
        return {"deleted": 0}
    from backend.fsrs_service import rebuild_state_from_history

    # Capture affected question_ids BEFORE delete so we can rebuild state per qid
    affected_qids = list({
        qid for (qid,) in db.execute(
            select(UserAnswer.question_id).where(
                UserAnswer.user_id == user.id,
                UserAnswer.id.in_(body.answer_ids),
            )
        ).all()
    })
    deleted = db.execute(
        UserAnswer.__table__.delete().where(
            UserAnswer.user_id == user.id,
            UserAnswer.id.in_(body.answer_ids),
        )
    ).rowcount
    db.flush()
    for qid in affected_qids:
        rebuild_state_from_history(db, user.id, qid)
    db.commit()
    return {"deleted": deleted or 0}


@router.delete("/all", status_code=status.HTTP_204_NO_CONTENT)
def reset_all(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    """Wipe ALL of this user's history (answers + FSRS state). Useful for
    test accounts or starting fresh."""
    db.execute(UserAnswer.__table__.delete().where(UserAnswer.user_id == user.id))
    db.execute(
        UserQuestionState.__table__.delete().where(UserQuestionState.user_id == user.id)
    )
    db.commit()
