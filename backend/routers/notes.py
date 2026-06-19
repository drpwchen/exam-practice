"""Per-user notes on questions."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.deps import get_current_user, get_db
from backend.models import Question, User, UserNote
from backend.schemas import NoteOut, NoteUpsert

router = APIRouter(prefix="/api/notes", tags=["notes"])


def _to_out(n: UserNote) -> NoteOut:
    return NoteOut(id=n.id, question_id=n.question_id, body=n.body, updated_at=n.updated_at)


@router.get("/{question_id}", response_model=NoteOut | None)
def get_note(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NoteOut | None:
    n = db.execute(
        select(UserNote).where(
            UserNote.user_id == user.id, UserNote.question_id == question_id
        )
    ).scalar_one_or_none()
    return _to_out(n) if n else None


@router.put("/{question_id}", response_model=NoteOut)
def upsert_note(
    question_id: int,
    body: NoteUpsert,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> NoteOut:
    if not db.get(Question, question_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    existing = db.execute(
        select(UserNote).where(
            UserNote.user_id == user.id, UserNote.question_id == question_id
        )
    ).scalar_one_or_none()
    if existing is None:
        existing = UserNote(user_id=user.id, question_id=question_id, body=body.body)
        db.add(existing)
    else:
        existing.body = body.body
    db.commit()
    db.refresh(existing)
    return _to_out(existing)


@router.delete("/{question_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> None:
    db.execute(
        UserNote.__table__.delete().where(
            UserNote.user_id == user.id, UserNote.question_id == question_id
        )
    )
    db.commit()
