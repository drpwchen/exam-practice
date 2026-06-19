"""Auth helpers: bcrypt + JWT + answer-token signing."""

from datetime import datetime, timedelta, timezone
from typing import Any

import bcrypt
from jose import JWTError, jwt

from backend.config import settings


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, extra: dict | None = None) -> str:
    now = datetime.now(tz=timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=settings.jwt_expire_days)).timestamp()),
        "typ": "access",
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("typ") != "access":
            return None
        return payload
    except JWTError:
        return None


# ───────────── Answer token (short-lived, anti-cheat) ─────────────


ANSWER_TOKEN_EXPIRE_SECONDS = 1800  # 30 min — long enough for slow readers


def create_answer_token(user_id: int, question_id: int, shuffle_seed: int) -> str:
    now = datetime.now(tz=timezone.utc)
    payload = {
        "uid": user_id,
        "qid": question_id,
        "seed": shuffle_seed,
        "iat": int(now.timestamp()),
        "exp": int(
            (now + timedelta(seconds=ANSWER_TOKEN_EXPIRE_SECONDS)).timestamp()
        ),
        "typ": "answer",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algorithm)


def decode_answer_token(token: str) -> dict | None:
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.jwt_algorithm])
        if payload.get("typ") != "answer":
            return None
        return payload
    except JWTError:
        return None
