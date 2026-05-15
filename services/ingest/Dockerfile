# syntax=docker/dockerfile:1
# Ingest service — CDSE OAuth2, STAC search, Sentinel Hub preview, OData download.
#
# OPSEC: runs as a non-root user (uid 1000); no --privileged flag; no host
# networking. Per CLAUDE.md §6.

FROM python:3.11-slim AS base

# Install uv for fast dependency installs inside the container.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Create a non-root user that matches a reasonable host UID.
RUN groupadd --gid 1000 ingest && \
    useradd --uid 1000 --gid ingest --no-create-home --shell /bin/false ingest

# ── Build stage: install Python dependencies ──────────────────────────────────
FROM base AS builder

# Copy workspace root pyproject so uv resolves the shared workspace.
COPY pyproject.toml uv.lock ./
COPY shared/ ./shared/
COPY services/ingest/pyproject.toml ./services/ingest/pyproject.toml

# Install the ingest package and its dependencies into an isolated venv.
RUN uv sync --no-dev --package ingest --frozen

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM base AS runtime

# Copy the installed virtual environment from the builder.
COPY --from=builder /app/.venv /app/.venv

# Copy source code.
COPY shared/ ./shared/
COPY services/ingest/src/ ./services/ingest/src/
COPY services/ingest/migrations/ ./services/ingest/migrations/
COPY services/ingest/alembic.ini ./services/ingest/alembic.ini

# Ensure the data directories exist and are writable by the ingest user.
RUN mkdir -p data/cache data/derived && chown -R ingest:ingest data/

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER ingest

# Default command: run DB migrations then start an interactive shell (the
# actual entry point will be set by docker-compose per job type).
CMD ["python", "-m", "services.ingest.src"]
