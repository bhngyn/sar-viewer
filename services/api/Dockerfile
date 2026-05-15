# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Phase 1: api service deps (added celery[redis], sqlalchemy for job dispatch).
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "pydantic>=2.7" \
        "structlog>=24.1" \
        "shapely>=2.0" \
        "pyproj>=3.6" \
        "celery[redis]>=5.4" \
        "sqlalchemy>=2.0"

# Non-root user per CLAUDE.md §6 OPSEC profile.
RUN useradd --create-home --uid 10001 appuser
USER appuser

COPY --chown=appuser:appuser shared /app/shared
COPY --chown=appuser:appuser services/__init__.py /app/services/__init__.py
COPY --chown=appuser:appuser services/api /app/services/api
COPY --chown=appuser:appuser services/worker/src /app/services/worker/src
COPY --chown=appuser:appuser services/worker/__init__.py /app/services/worker/__init__.py

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "services.api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
