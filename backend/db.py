from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import DateTime, TypeDecorator, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from backend.config import settings


class Base(DeclarativeBase):
    pass


class UtcDateTime(TypeDecorator):
    """DateTime that always round-trips through SQLite as UTC-aware.

    SQLite drops tzinfo when storing the underlying TIMESTAMP, so without
    this decorator everything we read back is naive — Pydantic then emits
    a no-offset ISO string and browsers interpret it as local time. Force
    UTC on both write and read so the JSON contract is unambiguous.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, _):
        if value is None:
            return value
        if value.tzinfo is not None:
            value = value.astimezone(timezone.utc)
        return value.replace(tzinfo=None)

    def process_result_value(self, value, _):
        if value is None:
            return value
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value


_db_path = settings.database_url.removeprefix("sqlite:///")
Path(_db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    echo=False,
    future=True,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_session() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
