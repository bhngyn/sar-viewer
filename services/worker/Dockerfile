# syntax=docker/dockerfile:1.7
# Phase 1 — real Celery worker replacing the Phase 0 placeholder.
# Runs as non-root per CLAUDE.md §6 OPSEC profile.

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime dependencies for the worker.
# celery[redis] brings in redis-py and kombu; sqlalchemy handles job state.
RUN pip install --no-cache-dir \
        "celery[redis]>=5.4" \
        "pydantic>=2.7" \
        "sqlalchemy>=2.0" \
        "structlog>=24.1"

# Non-root user per OPSEC profile.
RUN useradd --create-home --uid 10002 appuser
USER appuser

# Copy shared models (needed by tasks) and the worker package.
COPY --chown=appuser:appuser shared /app/shared
COPY --chown=appuser:appuser services/worker/src /app/services/worker/src

# data/ is mounted as a volume at runtime (see docker-compose.yml).
# Create the expected directories so the container starts cleanly even
# if the volume is not yet mounted.
RUN mkdir -p /app/data/cache /app/data/derived

CMD ["celery", "-A", "services.worker.src.app", "worker", "--loglevel=info", "--concurrency=2"]
