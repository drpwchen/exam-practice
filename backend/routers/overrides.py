"""Per-user manual frequency boost + star toggle."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.deps import get_current_user, get_db
from backend.models import Question, User, UserQuestionState
from backend.schemas import OverrideOut, OverrideUpdate

router = APIRouter(prefix="/api/overrides", tags=["overrides"])


def _to_out(s: UserQuestionState) -> OverrideOut:
    return OverrideOut(
        question_id=s.question_id,
        priority_boost=s.priority_boost,
        is_starred=s.is_starred,
    )


@router.get("/{question_id}", response_model=OverrideOut)
def get_override(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OverrideOut:
    s = db.execute(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id == question_id,
        )
    ).scalar_one_or_none()
    if s is None:
        # not yet answered → return defaults
        return OverrideOut(question_id=question_id, priority_boost=1.0, is_starred=False)
    return _to_out(s)


@router.put("/{question_id}", response_model=OverrideOut)
def update_override(
    question_id: int,
    body: OverrideUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> OverrideOut:
    if not db.get(Question, question_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    s = db.execute(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id == question_id,
        )
    ).scalar_one_or_none()
    if s is None:
        s = UserQuestionState(user_id=user.id, question_id=question_id)
        db.add(s)
    if body.priority_boost is not None:
        s.priority_boost = body.priority_boost
    if body.is_starred is not None:
        s.is_starred = body.is_starred
    db.commit()
    db.refresh(s)
    return _to_out(s)


class BulkOverride(BaseModel):
    question_ids: list[int]
    is_starred: bool | None = None
    priority_boost: float | None = None


@router.post("/bulk")
def bulk_update(
    body: BulkOverride,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not body.question_ids:
        return {"updated": 0}
    if body.is_starred is None and body.priority_boost is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nothing to update")
    if body.priority_boost is not None and not (0.5 <= body.priority_boost <= 5.0):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "priority_boost out of range")
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
            s = UserQuestionState(user_id=user.id, question_id=qid)
            db.add(s)
        if body.is_starred is not None:
            s.is_starred = body.is_starred
        if body.priority_boost is not None:
            s.priority_boost = body.priority_boost
        updated += 1
    db.commit()
    return {"updated": updated}
