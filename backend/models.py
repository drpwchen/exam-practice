"""All ORM models. SQLite via SQLAlchemy 2.0 (Mapped style).

Two ownership tiers:
- Shared (admin-edits-only): users, sources, tags, questions, *_revisions, *_correction_log
- Per-user (always WHERE user_id=current): user_answers, user_question_state, user_notes
"""

from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Use UtcDateTime instead of bare DateTime so values round-trip through SQLite
# with explicit UTC tzinfo (otherwise Pydantic serialises naive datetimes as
# tz-less ISO strings and the browser misreads them as local time).
from backend.db import Base, UtcDateTime as DateTime


def utcnow() -> datetime:
    return datetime.now(tz=timezone.utc)


# ─────────────────────────── Identity & sources ───────────────────────────


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # Comma-separated source codes (e.g. "past_paper") this user may
    # access. NULL = full access (default; backward-compatible with all
    # existing rows). Non-NULL = strict allowlist; any source whose `code`
    # is not in this list is hidden from the user (returns 403 on direct
    # access, filtered out of dashboards/wrong-bank/practice).
    allowed_sources: Mapped[str | None] = mapped_column(String(64), nullable=True)

    def has_source(self, code: str) -> bool:
        """True if the user is allowed to see/practise questions from `code`.

        NULL `allowed_sources` (the default) means full access.
        """
        if not self.allowed_sources:
            return True
        return code in {c.strip() for c in self.allowed_sources.split(",") if c.strip()}


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)


# ─────────────────────────── Tags ───────────────────────────


class Tag(Base):
    __tablename__ = "tags"
    __table_args__ = (
        UniqueConstraint("name", "parent_id", name="uq_tag_name_parent"),
        CheckConstraint("level IN ('major','minor')", name="ck_tag_level"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    level: Mapped[str] = mapped_column(String(8), nullable=False)
    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey("tags.id", ondelete="SET NULL")
    )
    is_official: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    display_name: Mapped[str | None] = mapped_column(String(256))
    short_zh: Mapped[str | None] = mapped_column(String(64))
    sort_order: Mapped[int] = mapped_column(
        Integer, default=999, nullable=False, server_default="999"
    )


class QuestionTag(Base):
    __tablename__ = "question_tags"

    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


# ─────────────────────────── Questions (shared) ───────────────────────────


class Question(Base):
    __tablename__ = "questions"
    __table_args__ = (
        UniqueConstraint("source_id", "source_ref", name="uq_question_source_ref"),
        CheckConstraint(
            "ocr_status IN ('auto','reviewed','manual')", name="ck_q_ocr_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"), nullable=False
    )
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)

    stem: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    option_e: Mapped[str | None] = mapped_column(Text)
    correct_letter: Mapped[str] = mapped_column(String(5), nullable=False)
    is_multi: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    reference_page: Mapped[str | None] = mapped_column(String(64))

    has_image: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_status: Mapped[str] = mapped_column(String(16), default="auto", nullable=False)
    needs_review: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_archived: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    median_time_ms: Mapped[int | None] = mapped_column(Integer)

    # When the official exam answer differs from what the explanation PDF says,
    # store a human-readable note here so the UI can surface the discrepancy.
    # Example: "學會公告答案為E，詳解為A，以公告答案為主"
    dispute_note: Mapped[str | None] = mapped_column(Text)

    # 0-indexed page in the source PDF where this question's stem begins.
    # Populated by the past_paper parser.
    source_page: Mapped[int | None] = mapped_column(Integer)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )

    images: Mapped[list["QuestionImage"]] = relationship(
        back_populates="question", cascade="all, delete-orphan"
    )


class QuestionImage(Base):
    __tablename__ = "question_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    filename: Mapped[str] = mapped_column(String(256), nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    display_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    # Heuristic: True if image was on a continuation page (詳解 figure shown
    # only after answering); False if on the question's own stem page (e.g.
    # an X-ray that is part of the question itself).
    is_explanation: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )
    # If the image visually follows a specific option's explanation text, store
    # the option letter (A-E) here so the UI can render image-per-option layout.
    option_letter: Mapped[str | None] = mapped_column(String(1))

    question: Mapped[Question] = relationship(back_populates="images")


class QuestionRevision(Base):
    """Full edit history. Diff stored as JSON text."""

    __tablename__ = "question_revisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    edited_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    edited_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    diff_json: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)


class QuestionParaphrase(Base):
    """Paraphrased variants of a question's stem and options.

    Variants preserve meaning but reword surface form, simulating how the
    real exam may rephrase a question while keeping the same answer. The
    `correct_letter` is NOT stored here — it always comes from the parent
    Question. Option letters are paired by position with the original
    (variant.option_a corresponds to question.option_a).
    """

    __tablename__ = "question_paraphrases"
    __table_args__ = (
        UniqueConstraint(
            "question_id", "variant_idx", name="uq_paraphrase_q_variant"
        ),
        CheckConstraint(
            "status IN ('pending','approved','rejected')",
            name="ck_paraphrase_status",
        ),
        Index("ix_paraphrase_q_status", "question_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    variant_idx: Mapped[int] = mapped_column(Integer, nullable=False)

    stem: Mapped[str] = mapped_column(Text, nullable=False)
    option_a: Mapped[str] = mapped_column(Text, nullable=False)
    option_b: Mapped[str] = mapped_column(Text, nullable=False)
    option_c: Mapped[str] = mapped_column(Text, nullable=False)
    option_d: Mapped[str] = mapped_column(Text, nullable=False)
    option_e: Mapped[str | None] = mapped_column(Text)

    generator: Mapped[str] = mapped_column(
        String(32), nullable=False, default="claude"
    )
    model_id: Mapped[str | None] = mapped_column(String(64))
    prompt_version: Mapped[str | None] = mapped_column(String(32))

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending"
    )
    # Free-form notes from validation: regex-check failures, judge verdict, etc.
    validation_notes: Mapped[str | None] = mapped_column(Text)
    judge_verdict: Mapped[str | None] = mapped_column(String(16))  # SAME/CHANGED/UNSURE

    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reviewed_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )


class QuestionCorrectionLog(Base):
    """Non-admin proposals queue. Admin approve/reject."""

    __tablename__ = "question_correction_log"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','approved','rejected')", name="ck_qcl_status"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    suggested_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    approved_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    field: Mapped[str] = mapped_column(String(32), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


# ─────────────────────────── Per-user data ───────────────────────────


class UserAnswer(Base):
    __tablename__ = "user_answers"
    __table_args__ = (
        Index("ix_ua_user_q_time", "user_id", "question_id", "answered_at"),
        CheckConstraint("confidence IN (1,2,3)", name="ck_ua_confidence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    answered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    selected_letter: Mapped[str] = mapped_column(String(5), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[int] = mapped_column(Integer, nullable=False)  # 1=guess..3=sure
    time_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    shuffle_seed: Mapped[int] = mapped_column(Integer, nullable=False)
    fsrs_rating: Mapped[int] = mapped_column(Integer, nullable=False)  # Rating value


class UserQuestionState(Base):
    """One row per (user, question). FSRS-6 card state + manual override."""

    __tablename__ = "user_question_state"
    __table_args__ = (Index("ix_uqs_user_due", "user_id", "due_at"),)

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True
    )

    # FSRS-6 card fields
    stability: Mapped[float | None] = mapped_column(Float)
    difficulty: Mapped[float | None] = mapped_column(Float)
    state: Mapped[int] = mapped_column(Integer, default=1, nullable=False)  # State enum
    step: Mapped[int | None] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_review_at: Mapped[datetime | None] = mapped_column(DateTime)
    reps: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lapses: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    # Manual override / soft pin
    priority_boost: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    is_starred: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Wrong-bank dismiss flag: user manually removed this from 錯題本.
    # Auto-resurrects on next wrong answer (see practice.submit_answer).
    dismissed_from_wrong_bank: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default="0"
    )


class UserNote(Base):
    __tablename__ = "user_notes"
    __table_args__ = (
        UniqueConstraint("user_id", "question_id", name="uq_user_note_user_q"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class UserPreferences(Base):
    """Per-user UI preferences (server-side, cross-device).

    Currently holds only `practice_setup` (last-used picker form values
    serialised as JSON: source, mode, tag_ids, limit, default_confidence,
    exclude_mastered, shuffle). Add more JSON-blob fields here rather than
    new tables when prefs grow.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    practice_setup: Mapped[str | None] = mapped_column(Text)  # JSON blob
    exam_goal: Mapped[str | None] = mapped_column(Text)       # JSON: {target_date, target_count}
    # JSON: {request_retention: float, max_new_per_session: int} — FSRS study
    # tuning. NULL → defaults (see preferences router STUDY_CONFIG_DEFAULTS).
    study_config: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, onupdate=utcnow
    )


class VaultAppend(Base):
    """Audit log of admin's 'add to vault' actions, with rollback callout block."""

    __tablename__ = "vault_appends"

    id: Mapped[int] = mapped_column(primary_key=True)
    question_id: Mapped[int] = mapped_column(
        ForeignKey("questions.id", ondelete="CASCADE"), nullable=False
    )
    appended_by: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    vault_note_path: Mapped[str] = mapped_column(String(512), nullable=False)
    appended_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    callout_block: Mapped[str] = mapped_column(Text, nullable=False)
