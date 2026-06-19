"""Idempotent seed: users (from Excel), sources, tags (major).

Run: python -m pipeline.seed_users
"""

from pathlib import Path

import bcrypt
import openpyxl
import yaml
from sqlalchemy import select

from backend.config import settings
from backend.db import Base, SessionLocal, engine
from backend.models import Source, Tag, User

SEEDS = Path(__file__).parent.parent / "seeds"


def ensure_schema() -> None:
    Base.metadata.create_all(engine)


def seed_users() -> int:
    wb = openpyxl.load_workbook(settings.users_xlsx, data_only=True)
    ws = wb.worksheets[0]
    default_hash = bcrypt.hashpw(
        settings.default_password.encode("utf-8"), bcrypt.gensalt()
    ).decode("utf-8")
    added = 0
    with SessionLocal() as s:
        existing = {u.username for u in s.scalars(select(User))}
        for row in ws.iter_rows(min_row=2, max_col=2, values_only=True):
            name = (row[1] or "").strip() if row[1] else ""
            if not name or name in existing:
                continue
            s.add(
                User(
                    username=name,
                    password_hash=default_hash,
                    is_admin=(name == settings.admin_username),
                    must_change_password=True,
                )
            )
            existing.add(name)
            added += 1
        s.commit()
    return added


def seed_sources() -> int:
    data = yaml.safe_load((SEEDS / "sources.yaml").read_text(encoding="utf-8"))
    added = 0
    with SessionLocal() as s:
        existing = {x.code for x in s.scalars(select(Source))}
        for row in data:
            if row["code"] in existing:
                continue
            s.add(
                Source(
                    code=row["code"],
                    display_name=row["display_name"],
                    description=row.get("description"),
                )
            )
            added += 1
        s.commit()
    return added


def seed_major_tags() -> tuple[int, int]:
    """UPSERT major tags. Returns (added, updated)."""
    data = yaml.safe_load((SEEDS / "tags_major.yaml").read_text(encoding="utf-8"))
    added = updated = 0
    with SessionLocal() as s:
        by_name = {t.name: t for t in s.scalars(select(Tag).where(Tag.level == "major"))}
        for row in data:
            existing = by_name.get(row["code"])
            if existing is None:
                s.add(
                    Tag(
                        name=row["code"],
                        level="major",
                        parent_id=None,
                        is_official=True,
                        display_name=row.get("display_name"),
                        short_zh=row.get("short_zh"),
                        sort_order=row.get("sort_order", 999),
                    )
                )
                added += 1
            else:
                changed = False
                if existing.display_name != row.get("display_name"):
                    existing.display_name = row.get("display_name")
                    changed = True
                if existing.sort_order != row.get("sort_order", 999):
                    existing.sort_order = row.get("sort_order", 999)
                    changed = True
                if existing.short_zh != row.get("short_zh"):
                    existing.short_zh = row.get("short_zh")
                    changed = True
                if changed:
                    updated += 1
        s.commit()
    return added, updated


def main() -> None:
    ensure_schema()
    n_src = seed_sources()
    n_tag_add, n_tag_upd = seed_major_tags()
    n_user = seed_users()
    print(
        f"Seeded: sources +{n_src}, "
        f"major tags +{n_tag_add}/upd {n_tag_upd}, users +{n_user}"
    )


if __name__ == "__main__":
    main()
