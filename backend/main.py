"""FastAPI entrypoint. Run: uvicorn backend.main:app --reload"""

from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from backend.access_log import AccessLogMiddleware
from backend.auth import decode_access_token
from backend.config import settings
from backend.db import Base, engine
from backend.deps import oauth2_scheme
from backend.limiter import limiter
from backend.routers import (
    admin,
    admin_cohort,
    admin_paraphrase,
    auth,
    dashboard,
    error_reports,
    history,
    lab_dashboard,
    meta,
    notes,
    overrides,
    practice,
    preferences,
    questions,
    vault,
    wrong_bank,
)

# Make sure the schema exists (idempotent for dev; production uses alembic)
Base.metadata.create_all(engine)

app = FastAPI(title="exam-practice", version="0.1.0", debug=settings.debug)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)
app.add_middleware(AccessLogMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(meta.router)
app.include_router(practice.router)
app.include_router(notes.router)
app.include_router(preferences.router)
app.include_router(overrides.router)
app.include_router(dashboard.router)
app.include_router(admin.router)
app.include_router(admin_cohort.router)
app.include_router(admin_paraphrase.router)
app.include_router(vault.router)
app.include_router(questions.router)
app.include_router(history.router)
app.include_router(wrong_bank.router)
app.include_router(error_reports.router)
app.include_router(lab_dashboard.router)

# Question images served under /api/static/images/<filename>.
# Auth-protected: accepts Bearer header OR ?token= query (img tags can't send headers).
images_dir = Path("data/question_images")
images_dir.mkdir(parents=True, exist_ok=True)


@app.get("/api/static/images/{filename}")
def serve_question_image(
    filename: str,
    token: str | None = Query(None),
    bearer: str | None = Depends(oauth2_scheme),
):
    actual = bearer or token
    if not actual or not decode_access_token(actual):
        raise HTTPException(status_code=401, detail="Auth required")
    safe = Path(filename).name  # path-traversal guard
    full = images_dir / safe
    if not full.is_file():
        raise HTTPException(status_code=404)
    return FileResponse(full)


@app.get("/api/healthz")
def healthz() -> dict:
    return {"ok": True, "version": app.version}


# Serve built frontend (Vite dist) at root if available. In dev (Vite running
# on :5173) this directory is absent and we fall through to API-only behavior.
_dist = Path("frontend/dist")
if _dist.is_dir():
    # Mount the assets/ subtree first (hashed JS/CSS bundles)
    app.mount(
        "/assets",
        StaticFiles(directory=str(_dist / "assets")),
        name="frontend-assets",
    )

    _index = _dist / "index.html"
    _root_files = {p.name for p in _dist.iterdir() if p.is_file()}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):  # type: ignore[no-redef]
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        # Serve files that physically exist at dist root (favicon, manifest, sw etc.)
        if full_path in _root_files:
            return FileResponse(_dist / full_path)
        # Otherwise return SPA shell for client-side routing
        return FileResponse(_index)
