"""Add a single user (idempotent on username) with optional source restrictions.

Defaults match the existing `pipeline/seed_users.py` flow: password =
`settings.default_password`, `must_change_password=True` so the user sets
their own on first login. Override via flags only if needed.

Usage examples:

    # Default password, restrict to past_paper only, force change on first login:
    .venv/Scripts/python.exe -m tools.add_user --username "alice" \
        --allowed-sources "past_paper" --must-change-password

    # Explicit password (bypass default):
    .venv/Scripts/python.exe -m tools.add_user --username "x" --password "MyPass123"

    # No source restriction (full access — equivalent to existing seed users):
    .venv/Scripts/python.exe -m tools.add_user --username "x" --must-change-password

If the user already exists, the script reports it and exits 0 without
touching the row. Use `tools/reset_admin_pw.py` to change a password.
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import select

from backend.auth import hash_password
from backend.config import settings
from backend.db import SessionLocal
from backend.models import Source, User


def _validate_allowed_sources(value: str | None) -> str | None:
    """Validate allowed_sources is a CSV of known source codes."""
    if value is None:
        return None
    codes = [c.strip() for c in value.split(",") if c.strip()]
    if not codes:
        return None
    with SessionLocal() as db:
        known = {s.code for s in db.scalars(select(Source))}
    bad = [c for c in codes if c not in known]
    if bad:
        sys.exit(f"unknown source code(s): {bad}; known: {sorted(known)}")
    return ",".join(codes)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--username", required=True)
    ap.add_argument("--password", default=None, help=f"explicit password; defaults to settings.default_password ({settings.default_password!r})")
    ap.add_argument(
        "--allowed-sources",
        default=None,
        help="comma-separated source codes (e.g. 'past_paper'); omit for full access",
    )
    ap.add_argument(
        "--must-change-password",
        action="store_true",
        help="force user to set a new password on first login",
    )
    ap.add_argument("--admin", action="store_true", help="grant is_admin=True")
    args = ap.parse_args()

    allowed_sources = _validate_allowed_sources(args.allowed_sources)
    password = args.password or settings.default_password

    with SessionLocal() as db:
        existing = db.execute(
            select(User).where(User.username == args.username)
        ).scalar_one_or_none()
        if existing is not None:
            print(
                f"user already exists: {args.username} (id={existing.id}, "
                f"is_admin={existing.is_admin}, allowed_sources={existing.allowed_sources!r})"
            )
            print("nothing changed; use tools/reset_admin_pw.py to rotate password")
            return 0
        user = User(
            username=args.username,
            password_hash=hash_password(password),
            is_admin=args.admin,
            must_change_password=args.must_change_password,
            allowed_sources=allowed_sources,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(
            f"created user: {user.username} (id={user.id}, is_admin={user.is_admin}, "
            f"must_change_password={user.must_change_password}, "
            f"allowed_sources={user.allowed_sources!r})"
        )
        # Always print on its own line so calling shell can capture cleanly.
        print(f"PASSWORD: {password}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
