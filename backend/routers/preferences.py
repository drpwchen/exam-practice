"""Per-user server-side UI preferences (cross-device)."""

import json

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.deps import get_current_user, get_db
from backend.models import User, UserPreferences
from backend.schemas import StudyConfig

router = APIRouter(prefix="/api/me/preferences", tags=["preferences"])

# FSRS study tuning defaults (mirrors StudyConfig field defaults).
STUDY_CONFIG_DEFAULTS = {"request_retention": 0.9, "max_new_per_session": 20}


def load_study_config(db: Session, user_id: int) -> dict:
    """Return the user's FSRS study config as a plain dict, falling back to
    STUDY_CONFIG_DEFAULTS for any missing/invalid value. Importable by the
    practice flow + question picker so tuning is applied consistently."""
    pref = db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user_id)
    ).scalar_one_or_none()
    raw: dict = {}
    if pref is not None and pref.study_config:
        try:
            parsed = json.loads(pref.study_config)
            if isinstance(parsed, dict):
                raw = parsed
        except ValueError:
            raw = {}
    merged = {**STUDY_CONFIG_DEFAULTS, **{k: raw[k] for k in STUDY_CONFIG_DEFAULTS if k in raw}}
    # Validate/clamp through the schema so out-of-range stored values can't leak.
    try:
        return StudyConfig(**merged).model_dump()
    except Exception:
        return dict(STUDY_CONFIG_DEFAULTS)


@router.get("/practice-setup")
def get_practice_setup(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict | None:
    pref = db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    ).scalar_one_or_none()
    if pref is None or not pref.practice_setup:
        return None
    try:
        return json.loads(pref.practice_setup)
    except ValueError:
        return None


@router.put("/practice-setup")
def put_practice_setup(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be an object")
    pref = db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    ).scalar_one_or_none()
    serialised = json.dumps(payload, ensure_ascii=False)
    if pref is None:
        pref = UserPreferences(user_id=user.id, practice_setup=serialised)
        db.add(pref)
    else:
        pref.practice_setup = serialised
    db.commit()
    return {"ok": True}


def _load_goals(pref: UserPreferences | None) -> dict:
    """Returns nested {source_code: {target_date, target_count, set_at_pool}}.

    Migrates legacy flat shape `{target_date, target_count, ...}` into
    `{past_paper: {...}}` on read so old saves remain functional.
    """
    if pref is None or not pref.exam_goal:
        return {}
    try:
        data = json.loads(pref.exam_goal)
    except ValueError:
        return {}
    if not isinstance(data, dict):
        return {}
    if "target_date" in data and "target_count" in data:
        # legacy flat shape — assume past_paper
        return {"past_paper": data}
    return data


@router.get("/exam-goal")
def get_exam_goal(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Returns nested {source_code: {target_date, target_count, set_at_pool}} or {}."""
    pref = db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    ).scalar_one_or_none()
    return _load_goals(pref)


@router.put("/exam-goal")
def put_exam_goal(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> dict:
    """Per-source goal management.

    Body shape:
      {source_code, target_date, target_count, set_at_pool}  → set goal for that source
      {source_code, clear: true}                              → clear that source's goal
      {clear_all: true}                                       → wipe all goals
    """
    if not isinstance(payload, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Body must be an object")
    pref = db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    ).scalar_one_or_none()
    goals = _load_goals(pref)

    if payload.get("clear_all"):
        goals = {}
    else:
        sc = payload.get("source_code")
        if not isinstance(sc, str) or not sc:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, "source_code is required"
            )
        if payload.get("clear"):
            goals.pop(sc, None)
        else:
            td = payload.get("target_date")
            tc = payload.get("target_count")
            if not isinstance(td, str) or not isinstance(tc, int) or tc <= 0:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    "Need {source_code, target_date: YYYY-MM-DD, target_count: positive int}",
                )
            entry = {"target_date": td, "target_count": tc}
            if isinstance(payload.get("set_at_pool"), int):
                entry["set_at_pool"] = payload["set_at_pool"]
            goals[sc] = entry

    serialised = json.dumps(goals, ensure_ascii=False) if goals else None
    if pref is None:
        pref = UserPreferences(user_id=user.id, exam_goal=serialised)
        db.add(pref)
    else:
        pref.exam_goal = serialised
    db.commit()
    return {"ok": True, "goals": goals}


@router.get("/study-config", response_model=StudyConfig)
def get_study_config(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StudyConfig:
    """Return the user's FSRS study config (request_retention + new-card cap),
    filling defaults for any missing/invalid value."""
    return StudyConfig(**load_study_config(db, user.id))


@router.put("/study-config", response_model=StudyConfig)
def put_study_config(
    payload: StudyConfig,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> StudyConfig:
    """Validate (range-clamp via StudyConfig) and persist the user's FSRS
    study config as JSON."""
    pref = db.execute(
        select(UserPreferences).where(UserPreferences.user_id == user.id)
    ).scalar_one_or_none()
    serialised = json.dumps(payload.model_dump(), ensure_ascii=False)
    if pref is None:
        pref = UserPreferences(user_id=user.id, study_config=serialised)
        db.add(pref)
    else:
        pref.study_config = serialised
    db.commit()
    return payload
