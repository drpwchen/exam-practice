"""Source / tag listing endpoints."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.deps import get_current_user, get_db
from backend.models import Source, Tag, User
from backend.schemas import SourceOut, TagOut

router = APIRouter(prefix="/api", tags=["meta"])


@router.get("/meta/db-info")
def db_info() -> dict:
    """Public (no auth) — title bar / intro can render this on first load."""
    if settings.demo_relative_dates:
        # Live demo: always a fresh window relative to today.
        today = date.today()
        db_updated = today.isoformat()
        exam = (today + timedelta(days=60)).isoformat()
    else:
        db_updated = settings.db_updated_date
        exam = settings.exam_date
    return {
        "db_updated_date": db_updated,
        "exam_date": exam,
        "exam_seconds_per_q": settings.exam_seconds_per_q,
        "cohort_wrongbook_public": settings.cohort_wrongbook_public,
    }


@router.get("/sources", response_model=list[SourceOut])
def list_sources(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[SourceOut]:
    return [
        SourceOut(
            id=s.id, code=s.code, display_name=s.display_name, description=s.description
        )
        for s in db.scalars(select(Source).order_by(Source.id))
    ]


@router.get("/tags", response_model=list[TagOut])
def list_tags(
    db: Session = Depends(get_db), _: User = Depends(get_current_user)
) -> list[TagOut]:
    return [
        TagOut(
            id=t.id, name=t.name, level=t.level, parent_id=t.parent_id,
            is_official=t.is_official,
            display_name=t.display_name,
            short_zh=t.short_zh,
            sort_order=t.sort_order,
        )
        for t in db.scalars(select(Tag).order_by(Tag.level, Tag.sort_order, Tag.name))
    ]
