"""Admin-only endpoints: OCR review queue, direct question edit."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.deps import get_db, require_admin
from backend.models import Question, QuestionRevision, User

router = APIRouter(prefix="/api/admin", tags=["admin"])


class ReviewQuestionRow(BaseModel):
    id: int
    source_code: str
    source_ref: str
    stem: str
    correct_letter: str
    needs_review: bool
    ocr_status: str
    has_explanation: bool


class QuestionEditRequest(BaseModel):
    stem: str | None = None
    option_a: str | None = None
    option_b: str | None = None
    option_c: str | None = None
    option_d: str | None = None
    option_e: str | None = None
    correct_letter: str | None = None
    explanation: str | None = None
    reference_page: str | None = None
    dispute_note: str | None = None
    needs_review: bool | None = None
    ocr_status: str | None = None
    reason: str | None = None


@router.get("/review-queue", response_model=list[ReviewQuestionRow])
def review_queue(
    needs_review_only: bool = Query(
        False,
        description="if true, return only rows manually flagged needs_review=True",
    ),
    limit: int = Query(200, ge=1, le=2000),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[ReviewQuestionRow]:
    """Questions flagged for review or missing key data.

    Default ordering puts manually-flagged rows (needs_review=True) FIRST so
    they don't get buried under the bulk of `ocr_status='auto'` rows.
    """
    from backend.models import Source
    stmt = (
        select(Question, Source.code)
        .join(Source, Source.id == Question.source_id)
    )
    # Queue contains ONLY questions manually flagged via error reports (needs_review=True).
    # ocr_status='auto' and missing explanation are no longer review triggers — users see
    # the source PDF image directly, so OCR/explanation gaps don't need admin attention
    # unless someone reports a problem.
    stmt = stmt.where(Question.needs_review == True)  # noqa: E712
    stmt = stmt.order_by(Question.id).limit(limit)
    rows = db.execute(stmt).all()
    return [
        ReviewQuestionRow(
            id=q.id,
            source_code=src,
            source_ref=q.source_ref,
            stem=q.stem,
            correct_letter=q.correct_letter,
            needs_review=q.needs_review,
            ocr_status=q.ocr_status,
            has_explanation=bool(q.explanation),
        )
        for q, src in rows
    ]


@router.patch("/questions/{question_id}")
def edit_question(
    question_id: int,
    body: QuestionEditRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")

    diff: dict[str, list[str]] = {}
    fields = (
        "stem option_a option_b option_c option_d option_e "
        "correct_letter explanation reference_page dispute_note needs_review ocr_status"
    ).split()
    for f in fields:
        new_val = getattr(body, f)
        if new_val is None:
            continue
        old_val = getattr(q, f)
        if str(old_val) != str(new_val):
            diff[f] = [str(old_val) if old_val is not None else "", str(new_val)]
            setattr(q, f, new_val)

    if diff:
        # Mark as reviewed when admin edits
        if body.ocr_status is None:
            q.ocr_status = "reviewed"
        q.updated_at = datetime.now(tz=timezone.utc)
        db.add(
            QuestionRevision(
                question_id=q.id,
                edited_by=user.id,
                diff_json=json.dumps(diff, ensure_ascii=False),
                reason=body.reason,
            )
        )
    db.commit()
    return {"ok": True, "fields_changed": list(diff.keys())}


