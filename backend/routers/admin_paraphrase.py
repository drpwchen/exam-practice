"""Admin endpoints for reviewing paraphrase variants.

Two-tier flow: generation script writes status='pending' rows, validation
script auto-flips clean ones to 'approved' and obvious ones to 'rejected',
leaving the borderline cases as 'pending'. Admins triage the residual
pending queue here, plus override any auto-decision when needed.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from backend.deps import get_db, require_admin
from backend.models import Question, QuestionParaphrase, User

router = APIRouter(prefix="/api/admin/paraphrase", tags=["admin", "paraphrase"])


# ─────────────── Schemas ───────────────


class ParaphraseSummary(BaseModel):
    id: int
    question_id: int
    variant_idx: int
    status: str
    judge_verdict: str | None
    created_at: datetime
    reviewed_at: datetime | None
    stem_preview: str  # first ~80 chars


class ParaphraseDetail(BaseModel):
    id: int
    question_id: int
    variant_idx: int
    status: str
    judge_verdict: str | None
    validation_notes: str | None
    generator: str
    model_id: str | None
    prompt_version: str | None
    created_at: datetime
    reviewed_at: datetime | None
    reviewed_by: int | None
    # Variant text
    stem: str
    option_a: str
    option_b: str
    option_c: str
    option_d: str
    option_e: str | None
    # Original (for diff view)
    original_stem: str
    original_option_a: str
    original_option_b: str
    original_option_c: str
    original_option_d: str
    original_option_e: str | None
    correct_letter: str


class ParaphraseEdit(BaseModel):
    stem: str = Field(min_length=1)
    option_a: str = Field(min_length=1)
    option_b: str = Field(min_length=1)
    option_c: str = Field(min_length=1)
    option_d: str = Field(min_length=1)
    option_e: str | None = None


class CountsResponse(BaseModel):
    pending: int
    approved: int
    rejected: int
    total: int
    questions_with_any: int  # distinct questions that have at least one variant


# ─────────────── Endpoints ───────────────


@router.get("/counts", response_model=CountsResponse)
def counts(
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> CountsResponse:
    counts_by_status = dict(
        db.execute(
            select(
                QuestionParaphrase.status, func.count(QuestionParaphrase.id)
            ).group_by(QuestionParaphrase.status)
        ).all()
    )
    distinct_qs = db.execute(
        select(func.count(func.distinct(QuestionParaphrase.question_id)))
    ).scalar_one()
    return CountsResponse(
        pending=counts_by_status.get("pending", 0),
        approved=counts_by_status.get("approved", 0),
        rejected=counts_by_status.get("rejected", 0),
        total=sum(counts_by_status.values()),
        questions_with_any=distinct_qs or 0,
    )


@router.get("/queue", response_model=list[ParaphraseSummary])
def queue(
    status_filter: str = Query("pending", pattern="^(pending|approved|rejected|all)$"),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[ParaphraseSummary]:
    stmt = select(QuestionParaphrase)
    if status_filter != "all":
        stmt = stmt.where(QuestionParaphrase.status == status_filter)
    # pending first, then by id so the queue is stable across reloads
    stmt = stmt.order_by(
        case((QuestionParaphrase.status == "pending", 0), else_=1),
        QuestionParaphrase.id,
    ).limit(limit).offset(offset)
    rows = db.execute(stmt).scalars().all()
    return [
        ParaphraseSummary(
            id=r.id,
            question_id=r.question_id,
            variant_idx=r.variant_idx,
            status=r.status,
            judge_verdict=r.judge_verdict,
            created_at=r.created_at,
            reviewed_at=r.reviewed_at,
            stem_preview=(r.stem[:80] + "…") if len(r.stem) > 80 else r.stem,
        )
        for r in rows
    ]


def _detail(db: Session, paraphrase_id: int) -> ParaphraseDetail:
    p = db.get(QuestionParaphrase, paraphrase_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paraphrase not found")
    q = db.get(Question, p.question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent question gone")
    return ParaphraseDetail(
        id=p.id,
        question_id=p.question_id,
        variant_idx=p.variant_idx,
        status=p.status,
        judge_verdict=p.judge_verdict,
        validation_notes=p.validation_notes,
        generator=p.generator,
        model_id=p.model_id,
        prompt_version=p.prompt_version,
        created_at=p.created_at,
        reviewed_at=p.reviewed_at,
        reviewed_by=p.reviewed_by,
        stem=p.stem,
        option_a=p.option_a,
        option_b=p.option_b,
        option_c=p.option_c,
        option_d=p.option_d,
        option_e=p.option_e,
        original_stem=q.stem,
        original_option_a=q.option_a,
        original_option_b=q.option_b,
        original_option_c=q.option_c,
        original_option_d=q.option_d,
        original_option_e=q.option_e,
        correct_letter=q.correct_letter,
    )


@router.get("/{paraphrase_id}", response_model=ParaphraseDetail)
def get_one(
    paraphrase_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> ParaphraseDetail:
    return _detail(db, paraphrase_id)


def _set_status(
    db: Session, paraphrase_id: int, new_status: str, admin: User
) -> ParaphraseDetail:
    p = db.get(QuestionParaphrase, paraphrase_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paraphrase not found")
    p.status = new_status
    p.reviewed_at = datetime.now(tz=timezone.utc)
    p.reviewed_by = admin.id
    db.commit()
    return _detail(db, paraphrase_id)


@router.post("/{paraphrase_id}/approve", response_model=ParaphraseDetail)
def approve(
    paraphrase_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ParaphraseDetail:
    return _set_status(db, paraphrase_id, "approved", admin)


@router.post("/{paraphrase_id}/reject", response_model=ParaphraseDetail)
def reject(
    paraphrase_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ParaphraseDetail:
    return _set_status(db, paraphrase_id, "rejected", admin)


@router.post("/{paraphrase_id}/edit", response_model=ParaphraseDetail)
def edit_and_approve(
    paraphrase_id: int,
    body: ParaphraseEdit,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
) -> ParaphraseDetail:
    p = db.get(QuestionParaphrase, paraphrase_id)
    if not p:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Paraphrase not found")
    q = db.get(Question, p.question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Parent question gone")
    # Sanity: option_e must exist on variant iff it exists on the original.
    if bool(q.option_e) != bool(body.option_e):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "option_e presence must match the original question",
        )
    p.stem = body.stem.strip()
    p.option_a = body.option_a.strip()
    p.option_b = body.option_b.strip()
    p.option_c = body.option_c.strip()
    p.option_d = body.option_d.strip()
    p.option_e = body.option_e.strip() if body.option_e else None
    p.status = "approved"
    p.reviewed_at = datetime.now(tz=timezone.utc)
    p.reviewed_by = admin.id
    p.validation_notes = (
        (p.validation_notes or "") + f"\nADMIN_EDIT by uid={admin.id}"
    ).strip()
    db.commit()
    return _detail(db, paraphrase_id)
