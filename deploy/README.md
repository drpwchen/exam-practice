# Deployment

This app is a single FastAPI process that serves both the JSON API and the
built Vue SPA. SQLite (WAL mode) keeps it dependency-light — no external
database required.

## Option A — Docker (recommended)

```bash
cp env.example .env          # then edit SECRET_KEY etc. (see below)
docker compose up -d --build
# app is now on http://localhost:8000
```

The `data/` directory (SQLite DB + question images) is a named volume, so it
survives rebuilds. On first start with an empty volume the container seeds the
demo database; for a real deployment, import your own questions and remove the
seed step from the `Dockerfile` CMD.

## Option B — bare metal

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp env.example .env
python -m pipeline.seed_demo            # or import your own bank
cd frontend && npm ci --legacy-peer-deps && npm run build && cd ..
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --workers 4
```

Put it behind a reverse proxy (nginx / Caddy / Cloudflare Tunnel) that
terminates TLS and forwards to `127.0.0.1:8000`.

> SQLite WAL handles a few uvicorn workers fine for a study-group-sized user
> base. A single worker serializes slow requests; 2–4 workers is a good default.

## ⚠️ Security — read before exposing publicly

This stores **personal answer data for multiple users**. Minimum hardening:

| Do | Why |
|---|---|
| Generate a strong random `SECRET_KEY` | JWTs are signed with it; the example value is public |
| Strong per-user passwords + forced first-login change | prevents trivial account takeover |
| Serve over **HTTPS** only | credentials + tokens travel on every request |
| Keep the built-in **rate limiting** on `/api/auth/*` | slows brute-force |
| Never commit `.env` or `data/*.db` | already in `.gitignore` — keep it that way |
| Restrict the **admin** account | admin can edit the whole question bank |
| Back up `data/app.db` regularly | it is the single source of truth |

### Backups

The DB is one file. A nightly snapshot is enough:

```bash
sqlite3 data/app.db ".backup data/backups/app_$(date +%F).db"
```

Use SQLite's `.backup` (not a raw `cp`) so an in-flight WAL is checkpointed
into the copy.
