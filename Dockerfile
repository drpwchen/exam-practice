# ---- Stage 1: build the Vue frontend ----
FROM node:18-alpine AS frontend
WORKDIR /app/frontend
COPY frontend/package*.json ./
RUN npm ci --legacy-peer-deps
COPY frontend/ ./
RUN npm run build        # outputs to frontend/dist, served by the backend

# ---- Stage 2: python runtime ----
FROM python:3.11-slim AS runtime
WORKDIR /app

# system deps for PyMuPDF / Pillow wheels are bundled, so keep it slim
ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

COPY requirements.txt ./
RUN pip install -r requirements.txt

COPY backend/ ./backend/
COPY pipeline/ ./pipeline/
COPY tools/ ./tools/
COPY seeds/ ./seeds/
COPY demo/ ./demo/
# built SPA from stage 1
COPY --from=frontend /app/frontend/dist ./frontend/dist

# data dir is a mounted volume in production (DB + images live here)
RUN mkdir -p data
VOLUME ["/app/data"]

EXPOSE 8000

# On first run with an empty volume, seed the demo DB; then start the server.
# (For a real deployment, replace seed_demo with your own import + remove the seed step.)
CMD ["sh", "-c", "test -f data/app.db || python -m pipeline.seed_demo; uvicorn backend.main:app --host 0.0.0.0 --port 8000"]
