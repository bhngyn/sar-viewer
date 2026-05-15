# syntax=docker/dockerfile:1.7
FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install only what the api service needs in Phase 0.
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "pydantic>=2.7" \
        "structlog>=24.1" \
        "shapely>=2.0" \
        "pyproj>=3.6"

# Non-root user per CLAUDE.md §6 OPSEC profile.
RUN useradd --create-home --uid 10001 appuser
USER appuser

COPY --chown=appuser:appuser shared /app/shared
COPY --chown=appuser:appuser services/api/src /app/services/api/src

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

CMD ["uvicorn", "services.api.src.main:app", "--host", "0.0.0.0", "--port", "8000"]
