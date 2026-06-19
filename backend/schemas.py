"""Pydantic request/response models for the API."""

from datetime import datetime

from pydantic import BaseModel, Field


# ─────────────── Auth ───────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: "UserInfo"


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str = Field(min_length=8)


class UserInfo(BaseModel):
    id: int
    username: str
    is_admin: bool
    must_change_password: bool
    # Comma-separated source codes this user may see, or null for full access.
    # Frontend uses this to hide forbidden source pickers / cells / tabs.
    allowed_sources: str | None = None


# ─────────────── Sources / Tags ───────────────


class SourceOut(BaseModel):
    id: int
    code: str
    display_name: str
    description: str | None


class TagOut(BaseModel):
    id: int
    name: str
    level: str
    parent_id: int | None
    is_official: bool
    display_name: str | None = None
    short_zh: str | None = None
    sort_order: int = 999


# ─────────────── Practice ───────────────


class OptionDisplay(BaseModel):
    display: str  # the shuffled letter the user sees (A..E)
    text: str


class ImageRef(BaseModel):
    url: str
    option_letter: str | None = None  # A-E if image is tied to a specific option


class NextQuestionResponse(BaseModel):
    answer_token: str  # signed JWT carrying question_id + shuffle seed + issued time
    question_id: int
    source_code: str
    stem: str
    options: list[OptionDisplay]  # already shuffled
    stem_image_urls: list[str]          # shown DURING practice (X-rays etc.)
    explanation_image_urls: list[str]   # back-compat: flat list of all explanation image URLs
    explanation_images: list[ImageRef]  # richer: each image carries optional option_letter
    tags: list[str]
    has_user_note: bool
    is_starred: bool
    priority_boost: float
    # Source-specific labels for the practice header
    year: int | None = None             # only for past_paper
    question_label: str | None = None   # e.g., "112 年 第 7 題" or None
    # Set when the served stem/options come from a paraphrase variant rather
    # than the original question. The Feedback view uses these to offer a
    # 「對照原題」 panel without an extra round-trip.
    paraphrase_id: int | None = None
    paraphrase_variant_idx: int | None = None
    original_stem: str | None = None
    original_options_text: dict[str, str] | None = None
    # Multi-select questions: frontend renders checkboxes and submits a set of
    # display letters. Scoring is exact-match (all-correct-or-wrong).
    is_multi: bool = False


class AnswerSubmit(BaseModel):
    answer_token: str
    # Single-select: A-E string. Multi-select: concatenated sorted set "ACE".
    # Empty string allowed only for multi-select "abstain".
    selected_display: str = Field(pattern=r"^[A-E]{0,5}$")
    confidence: int = Field(ge=1, le=3)
    time_ms: int = Field(ge=0)


class AnswerResponse(BaseModel):
    answer_id: int
    correct_letter: str   # the original (pre-shuffle) correct letter
    correct_display: str  # the post-shuffle letter the user should have clicked
    selected_display: str
    selected_letter: str  # the original letter the user effectively chose
    is_correct: bool
    explanation: str | None              # original (A-E) labels
    explanation_remapped: str | None     # (A-E) re-labelled to match shuffled display order
    reference_page: str | None
    fsrs_rating: int
    next_due: datetime | None
    # Original options in canonical A-E order (so Feedback can render in
    # original layout even after shuffle, matching the explanation's letters)
    original_options: dict[str, str]
    was_shuffled: bool
    source_info: str | None = None  # human-readable source label (e.g. "112年解題.pdf")
    dispute_note: str | None = None  # official vs explanation answer discrepancy


# ─────────────── Notes ───────────────


class NoteUpsert(BaseModel):
    body: str


class NoteOut(BaseModel):
    id: int
    question_id: int
    body: str
    updated_at: datetime


# ─────────────── Override / Star ───────────────


class OverrideUpdate(BaseModel):
    priority_boost: float | None = Field(default=None, ge=0.1, le=10.0)
    is_starred: bool | None = None


class OverrideOut(BaseModel):
    question_id: int
    priority_boost: float
    is_starred: bool


# ─────────────── Study config (FSRS tuning) ───────────────


class StudyConfig(BaseModel):
    # Desired memory-retention target for FSRS scheduling. Higher → shorter
    # intervals → more frequent reviews. Clamped to a safe band.
    request_retention: float = Field(default=0.9, ge=0.80, le=0.97)
    # Cap on brand-new cards introduced per practice session.
    max_new_per_session: int = Field(default=20, ge=5, le=40)


LoginResponse.model_rebuild()
