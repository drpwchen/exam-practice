"""Read a specific question by id (used for review-from-dashboard etc.)."""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from backend.deps import get_current_user, get_db
from backend.models import (
    Question,
    QuestionImage,
    QuestionTag,
    Source,
    Tag,
    User,
    UserAnswer,
    UserNote,
    UserQuestionState,
)
from backend.schemas import ImageRef

router = APIRouter(prefix="/api/questions", tags=["questions"])


class QuestionDetail(BaseModel):
    id: int
    source_code: str
    source_ref: str
    source_info: str | None = None      # human-readable PDF + page
    year: int | None
    question_label: str | None = None
    stem: str
    options: dict[str, str]
    correct_letter: str
    explanation: str | None
    reference_page: str | None
    stem_image_urls: list[str]
    explanation_image_urls: list[str]   # back-compat flat list
    explanation_images: list[ImageRef]  # with option_letter
    tags: list[str]
    dispute_note: str | None = None
    needs_review: bool
    ocr_status: str
    user_answer_count: int
    last_selected_letter: str | None
    last_was_correct: bool | None
    last_answered_at: datetime | None
    user_note: str | None
    is_starred: bool
    priority_boost: float


@router.get("/{question_id}", response_model=QuestionDetail)
def get_question(
    question_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> QuestionDetail:
    q = db.get(Question, question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")

    src_code = db.execute(select(Source.code).where(Source.id == q.source_id)).scalar_one()
    if not user.has_source(src_code):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "source not allowed for this user"
        )

    options: dict[str, str] = {}
    for letter in ["A", "B", "C", "D", "E"]:
        v = getattr(q, f"option_{letter.lower()}")
        if v:
            options[letter] = v

    img_rows = db.execute(
        select(QuestionImage)
        .where(QuestionImage.question_id == q.id)
        .order_by(QuestionImage.display_order)
    ).scalars().all()
    stem_imgs = [f"/api/static/images/{r.filename}" for r in img_rows if not r.is_explanation]
    expl_imgs = [f"/api/static/images/{r.filename}" for r in img_rows if r.is_explanation]
    expl_image_refs = [
        ImageRef(
            url=f"/api/static/images/{r.filename}",
            option_letter=r.option_letter,
        )
        for r in img_rows
        if r.is_explanation
    ]

    tag_names = [
        n for (n,) in db.execute(
            select(Tag.name)
            .join(QuestionTag, QuestionTag.tag_id == Tag.id)
            .where(QuestionTag.question_id == q.id)
            .order_by(Tag.sort_order)
        ).all()
    ]

    last_attempt = db.execute(
        select(UserAnswer)
        .where(UserAnswer.user_id == user.id, UserAnswer.question_id == q.id)
        .order_by(desc(UserAnswer.answered_at))
    ).scalars().first()
    attempt_count = db.execute(
        select(UserAnswer).where(
            UserAnswer.user_id == user.id, UserAnswer.question_id == q.id
        )
    ).scalars().all()
    note = db.execute(
        select(UserNote).where(
            UserNote.user_id == user.id, UserNote.question_id == q.id
        )
    ).scalar_one_or_none()
    state = db.execute(
        select(UserQuestionState).where(
            UserQuestionState.user_id == user.id,
            UserQuestionState.question_id == q.id,
        )
    ).scalar_one_or_none()

    # Build source_info + question_label
    source_info: str | None = None
    question_label: str | None = None
    if src_code == "past_paper" and "/Q" in q.source_ref:
        try:
            qnum = int(q.source_ref.split("/Q")[1])
            question_label = f"{q.year} 年 第 {qnum} 題"
            source_info = f"{q.year}年解題.pdf"
        except ValueError:
            pass

    return QuestionDetail(
        id=q.id,
        source_code=src_code,
        source_ref=q.source_ref,
        source_info=source_info,
        year=q.year,
        question_label=question_label,
        stem=q.stem,
        options=options,
        correct_letter=q.correct_letter,
        explanation=q.explanation,
        reference_page=q.reference_page,
        stem_image_urls=stem_imgs,
        explanation_image_urls=expl_imgs,
        explanation_images=expl_image_refs,
        tags=tag_names,
        dispute_note=q.dispute_note,
        needs_review=q.needs_review,
        ocr_status=q.ocr_status,
        user_answer_count=len(attempt_count),
        last_selected_letter=last_attempt.selected_letter if last_attempt else None,
        last_was_correct=last_attempt.is_correct if last_attempt else None,
        last_answered_at=last_attempt.answered_at if last_attempt else None,
        user_note=note.body if note else None,
        is_starred=bool(state and state.is_starred),
        priority_boost=state.priority_boost if state else 1.0,
    )
