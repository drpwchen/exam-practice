"""Admin-only: append question summary as a callout to a 52Medicine note."""

import re
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.deps import get_db, require_admin
from backend.models import Question, QuestionTag, Source, Tag, User, VaultAppend

router = APIRouter(prefix="/api/vault", tags=["vault"])


class VaultSearchHit(BaseModel):
    path: str
    rel_path: str
    title: str


class VaultAppendRequest(BaseModel):
    question_id: int
    note_rel_path: str  # path relative to vault root
    user_comment: str | None = None


@router.get("/search", response_model=list[VaultSearchHit])
def search_vault(
    q: str = Query(..., min_length=1),
    db: Session = Depends(get_db),
    _: User = Depends(require_admin),
) -> list[VaultSearchHit]:
    """Simple filename-based search under 52Medicine. Matches partial filename
    case-insensitively. Returns up to 30 hits."""
    root = settings.vault_root / "52Medicine"
    if not root.exists():
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR, f"Vault root missing: {root}"
        )
    needle = q.lower()
    hits: list[VaultSearchHit] = []
    for p in root.rglob("*.md"):
        if needle in p.stem.lower():
            rel = p.relative_to(settings.vault_root).as_posix()
            hits.append(VaultSearchHit(path=str(p), rel_path=rel, title=p.stem))
            if len(hits) >= 30:
                break
    return hits


@router.post("/append")
def append_to_note(
    body: VaultAppendRequest,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
) -> dict:
    target = (settings.vault_root / body.note_rel_path).resolve()
    # Safety: must stay under vault root
    if not str(target).startswith(str(settings.vault_root.resolve())):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Path escapes vault root")
    if not target.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Note not found: {body.note_rel_path}")

    q = db.get(Question, body.question_id)
    if not q:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Question not found")

    src_code = db.execute(select(Source.code).where(Source.id == q.source_id)).scalar_one()
    tag_names = [
        n for (n,) in db.execute(
            select(Tag.name).join(QuestionTag, QuestionTag.tag_id == Tag.id)
            .where(QuestionTag.question_id == q.id)
        ).all()
    ]

    # Build the callout
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    options_block = "\n".join(
        f"> - ({l}) {getattr(q, f'option_{l.lower()}') or ''}"
        for l in ["A", "B", "C", "D", "E"]
        if getattr(q, f"option_{l.lower()}", None)
    )
    callout_lines = [
        f"\n> [!note] 考題練習 {today} | {src_code}",
        f"> **領域:** {' '.join(tag_names) if tag_names else '—'}",
        "> ",
        f"> **題目:** {q.stem}",
        "> ",
        options_block,
        "> ",
        f"> **答案:** {q.correct_letter}",
    ]
    if q.explanation:
        callout_lines += ["> ", f"> **詳解:** {q.explanation[:600]}"]
    if q.reference_page:
        callout_lines += [f"> **參考:** {q.reference_page}"]
    if body.user_comment:
        callout_lines += ["> ", f"> **心得:** {body.user_comment}"]
    callout_lines += [f"> *(來源 ref: {q.source_ref})*"]
    callout_block = "\n".join(callout_lines) + "\n"

    # Append (don't overwrite). Add a leading blank line if needed.
    existing = target.read_text(encoding="utf-8")
    sep = "\n" if not existing.endswith("\n") else ""
    target.write_text(existing + sep + callout_block, encoding="utf-8")

    db.add(
        VaultAppend(
            question_id=q.id,
            appended_by=user.id,
            vault_note_path=str(target),
            callout_block=callout_block,
        )
    )
    db.commit()
    return {"ok": True, "appended_to": body.note_rel_path, "bytes_added": len(callout_block)}
