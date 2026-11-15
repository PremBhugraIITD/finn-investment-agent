# Single-service image: builds the mobile frontend, then serves it and the API from FastAPI.

# ---- Stage 1: production frontend bundle (tsc typecheck + vite build -> /web/dist)
FROM node:22-slim AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# ---- Stage 2: API + seeded demo data + built frontend
# Python minor version matches the one the pinned requirements were tested on (3.14).
FROM python:3.14-slim
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    FINN_CACHE_DIR=/tmp/finn-cache
WORKDIR /app

# Exact pinned versions; wheels only, so a missing wheel fails the build instead of compiling.
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --only-binary=:all: -r backend/requirements.txt

COPY backend/ backend/
# Committed fallback snapshot (prices, NSE filings, model outputs, parsed documents, research results)
COPY data/seed/ data/seed/
COPY --from=web /web/dist frontend/dist

# Run unprivileged; the runtime cache lives in /tmp (ephemeral on Render by design).
# COPY keeps host file modes (some are 0600), so make the tree world-readable for the unprivileged user.
RUN chmod -R a+rX /app \
    && useradd --create-home --uid 10001 finn \
    && mkdir -p /tmp/finn-cache \
    && chown -R finn:finn /tmp/finn-cache
USER finn

WORKDIR /app/backend
EXPOSE 8000
# Render injects $PORT; bind all interfaces so its port check passes. exec -> proper SIGTERM handling.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
