"""Simple error report system: users flag issues; admin reviews."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    desc,
    select,
    text,
)
from sqlalchemy.orm import Mapped, Session, mapped_column

from backend.db import Base, engine
from backend.deps import get_current_user, get_db, require_admin
from backend.models import Question, User

router = APIRouter(prefix="/api/error-reports", tags=["error-reports"])


# Inline-defined model so we don't have to migrate models.py
class ErrorReport(Base):
    __tablename__ = "error_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    question_id: Mapped[int | None] = mapped_column(ForeignKey("questions.id", ondelete="SET NULL"))
    category: Mapped[str] = mapped_column(String(32), default="general")  # general/parser/ui/answer
    body: Mapped[str] = mapped_column(Text, nullable=False)
    page_url: Mapped[str | None] = mapped_column(String(512))
    is_resolved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(tz=timezone.utc))


# Ensure table exists (idempotent)
Base.metadata.create_all(engine, tables=[ErrorReport.__table__])


class ReportSubmit(BaseModel):
    body: str = Field(min_length=2, max_length=2000)
    category: str = Field(default="general", pattern=r"^(general|parser|ui|answer|tag|other|qqe)$")
    question_id: int | None = None
    page_url: str | None = None


class ReportOut(BaseModel):
    id: int
    user_id: int | None
    username: str | None
    question_id: int | None
    category: str
    body: str
    page_url: str | None
    is_resolved: bool
    created_at: datetime


@router.post("", response_model=ReportOut)
def submit(
    body: ReportSubmit,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> ReportOut:
    q = None
    if body.question_id:
        q = db.get(Question, body.question_id)
        if not q:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")
    rep = ErrorReport(
        user_id=user.id,
        question_id=body.question_id,
        category=body.category,
        body=body.body,
        page_url=body.page_url,
    )
    db.add(rep)
    if q is not None:
        # Flag the question for admin review queue.
        q.needs_review = True
    db.commit()
    db.refresh(rep)
    return ReportOut(
        id=rep.id, user_id=rep.user_id, username=user.username,
        question_id=rep.question_id, category=rep.category, body=rep.body,
        page_url=rep.page_url, is_resolved=rep.is_resolved, created_at=rep.created_at,
    )


@router.get("", response_model=list[ReportOut])
def list_reports(
    only_open: bool = Query(False),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[ReportOut]:
    stmt = select(ErrorReport).order_by(desc(ErrorReport.created_at))
    if only_open:
        stmt = stmt.where(ErrorReport.is_resolved == False)  # noqa: E712
    rows = db.scalars(stmt).all()
    out = []
    user_lookup = {u.id: u.username for u in db.scalars(select(User))}
    for r in rows:
        out.append(ReportOut(
            id=r.id, user_id=r.user_id, username=user_lookup.get(r.user_id) if r.user_id else None,
            question_id=r.question_id, category=r.category, body=r.body,
            page_url=r.page_url, is_resolved=r.is_resolved, created_at=r.created_at,
        ))
    return out


@router.patch("/{report_id}/resolve")
def resolve(
    report_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> dict:
    r = db.get(ErrorReport, report_id)
    if not r:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    r.is_resolved = True
    db.flush()
    # Clear the question's needs_review flag iff no other unresolved reports remain.
    if r.question_id:
        remaining = db.scalar(
            select(ErrorReport.id)
            .where(ErrorReport.question_id == r.question_id)
            .where(ErrorReport.is_resolved == False)  # noqa: E712
            .limit(1)
        )
        if not remaining:
            q = db.get(Question, r.question_id)
            if q is not None:
                q.needs_review = False
    db.commit()
    return {"ok": True}
