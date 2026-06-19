"""Per-request access logging — records user_id for every authenticated request.

Goal: forensic recovery if user_answers (or any user table) gets wiped, the log
preserves enough state to re-enter what users did.

Two channels:
1. Generic middleware → JSONL line per request: ts, user_id, method, path, status.
2. answer_endpoint hook → JSONL line per /api/practice/answer with the resolved
   (question_id, selected_letter, is_correct). Called from practice.submit_answer
   after the DB row is created.

Both write to ./data/access.log (rotated by size — see RotatingFileHandler below).
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import time
from pathlib import Path
from typing import Any

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from backend.auth import decode_access_token

LOG_PATH = Path("data/access.log")
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

_logger = logging.getLogger("exam_access")
_logger.setLevel(logging.INFO)
_logger.propagate = False
if not _logger.handlers:
    # Rotate at 50 MB, keep 14 historic files (≈ 2 weeks under normal load).
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=50 * 1024 * 1024, backupCount=14, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(handler)


def _user_id_from_request(request: Request) -> int | None:
    auth = request.headers.get("authorization")
    if not auth or not auth.lower().startswith("bearer "):
        token = request.query_params.get("token")
    else:
        token = auth.split(None, 1)[1]
    if not token:
        return None
    try:
        payload = decode_access_token(token)
    except Exception:
        return None
    if not payload:
        return None
    try:
        return int(payload["sub"])
    except (KeyError, TypeError, ValueError):
        return None


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Logs each /api/* request with user_id resolved from the JWT."""

    async def dispatch(self, request: Request, call_next):
        # Only log API calls — skip static asset / SPA shell requests.
        path = request.url.path
        if not path.startswith("/api/"):
            return await call_next(request)
        started = time.time()
        response: Response = await call_next(request)
        elapsed_ms = int((time.time() - started) * 1000)
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
            "kind": "req",
            "user_id": _user_id_from_request(request),
            "ip": request.client.host if request.client else None,
            "method": request.method,
            "path": path,
            "status": response.status_code,
            "ms": elapsed_ms,
        }
        try:
            _logger.info(json.dumps(entry, ensure_ascii=False))
        except Exception:
            # Logging must never break the response.
            pass
        return response


def log_answer(
    *,
    user_id: int,
    question_id: int,
    selected_letter: str,
    correct_letter: str,
    is_correct: bool,
    confidence: int,
    time_ms: int,
    shuffle_seed: int,
) -> None:
    """Structured log line for /api/practice/answer submissions.

    Called from practice.submit_answer AFTER the DB INSERT succeeds. If the DB
    is later lost, replaying these lines reconstructs user_answers.
    """
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "kind": "answer",
        "user_id": user_id,
        "qid": question_id,
        "sel": selected_letter,
        "cor": correct_letter,
        "ok": is_correct,
        "conf": confidence,
        "ms": time_ms,
        "seed": shuffle_seed,
    }
    try:
        _logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass


def log_next(
    *,
    user_id: int,
    mode: str,
    source_code: str,
    tag_ids: list[int] | None,
    exclude_count: int,
    priority_count: int,
    returned_qid: int | None,
    due_at_iso: str | None,
    state: int | None,
    step: int | None,
) -> None:
    """Structured log line for /api/practice/next.

    Captures enough state to debug Pool-A cycling issues: which qid was
    returned, its FSRS state at the time of selection, and the server's
    view of due_at vs the wall-clock at log time.  Lets a postmortem
    answer 'why was this card served when its due_at was in the future?'.
    """
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "kind": "next",
        "user_id": user_id,
        "mode": mode,
        "src": source_code,
        "tags": tag_ids or [],
        "excl": exclude_count,
        "pri": priority_count,
        "qid": returned_qid,
        "due_at": due_at_iso,
        "state": state,
        "step": step,
    }
    try:
        _logger.info(json.dumps(entry, ensure_ascii=False))
    except Exception:
        pass
