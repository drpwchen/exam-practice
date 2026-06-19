from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    secret_key: str = "dev-secret-change-me-32-bytes-min-length"
    jwt_algorithm: str = "HS256"
    jwt_expire_days: int = 7

    database_url: str = "sqlite:///./data/app.db"

    # Comma-separated list of allowed CORS origins. Default keeps dev working.
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # Toggle FastAPI debug & verbose error responses. MUST be False in prod.
    debug: bool = False

    # Admin bootstrap credentials — override via env in any real deployment.
    admin_username: str = "admin"
    default_password: str = "changeme"
    users_xlsx: Path = Path("./data/users.xlsx")

    # Directory holding the source PDFs for the past-paper (考古題) pipeline.
    past_paper_dir: Path = Path("./source_pdfs")
    past_paper_years: str = "112,113,114"
    # 題庫最近更新日（ISO YYYY-MM-DD）。Single source of truth for both
    # Dashboard 「題庫更新日期」 and Intro 第一頁。Bump when a new batch of
    # questions is loaded.
    db_updated_date: str = "2026-06-11"
    # 考試日期（ISO YYYY-MM-DD），用於倒數與分數預測。Override via env.
    # (Demo default is a future date so the countdown + goal date-picker work;
    # set your own exam date before using.)
    exam_date: str = "2026-09-13"
    # Pace target for the in-question timer colour (seconds per question) — set
    # to your exam's pace = total minutes × 60 ÷ number of questions. The timer
    # turns amber at 0.75× and red at 1× this value. Default 48 = a 200-question
    # / 160-minute paper.
    exam_seconds_per_q: int = 48
    # Demo-only: when True, meta serves dates relative to *today* so the live
    # demo always shows a fresh window — db_updated_date = today, exam_date =
    # today + 60 days. Leave False for a real deployment (uses the values above).
    demo_relative_dates: bool = False
    # 「大家的錯題本」(cohort 常錯題公開頁) 是否對非 admin 開放。
    cohort_wrongbook_public: bool = True

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"

    # Obsidian vault root (optional — only used by the vault append feature).
    vault_root: Path = Path("./vault")

    @property
    def past_paper_years_list(self) -> list[int]:
        return [int(y.strip()) for y in self.past_paper_years.split(",") if y.strip()]

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
