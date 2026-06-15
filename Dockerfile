# syntax=docker/dockerfile:1
#
# One image that serves the whole platform: FastAPI (API) + the built React app,
# single-origin. Code only — the corpus data is NEVER baked in; it lives on a
# mounted volume/disk (see docs/deployment.md). Build context excludes data/ via
# .dockerignore.

# ---- Stage 1: build the React app ----
FROM node:20-slim AS frontend
WORKDIR /app/apps/review
COPY apps/review/package.json apps/review/package-lock.json ./
RUN npm ci
COPY apps/review/ ./
RUN npm run build

# ---- Stage 2: Python runtime ----
FROM python:3.12-slim AS runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    FLORACCO_SERVE_STATIC=1
WORKDIR /app
# rsync: needed to upload the corpus onto the disk (receiving end) and by
# deploy/reset_demo.sh. Not in python:slim by default.
RUN apt-get update && apt-get install -y --no-install-recommends rsync \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv
# Install only the locked dependencies (not the project itself); we run the code
# from the copied workflows/ dir via PYTHONPATH, so no packaging step is needed.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project
COPY workflows/ ./workflows/
COPY --from=frontend /app/apps/review/dist ./apps/review/dist
EXPOSE 8000
# Bind the port the host assigns (Render sets $PORT; defaults to 8000 locally).
# `sh -c … exec` lets ${PORT} expand while keeping uvicorn as PID 1 for clean
# shutdown. One worker keeps SQLite writes serialized (correct + simplest for a pilot).
CMD ["sh", "-c", "exec .venv/bin/uvicorn workflows.review_server:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
