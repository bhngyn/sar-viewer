# Fusion service — combines S2 basemap with SAR products into fused COGs.
# OPSEC §6: non-root user, no host network.
FROM python:3.11-slim AS base

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libexpat1 \
        libfreetype6 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10002 fusion
USER fusion
WORKDIR /home/fusion/app

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=fusion:fusion services/fusion/pyproject.toml ./pyproject.toml
RUN pip install --user \
        "numpy>=1.26" \
        "rasterio>=1.3" \
        "rio-cogeo>=5.0" \
        "pillow>=10.0" \
        "matplotlib>=3.8" \
        "pydantic>=2.7" \
        "structlog>=24.1"

COPY --chown=fusion:fusion shared /home/fusion/app/shared
COPY --chown=fusion:fusion services/fusion /home/fusion/app/services/fusion

ENV PYTHONPATH=/home/fusion/app
# Phase 2 ship as a library — Celery worker imports services.fusion.src.fuse.fuse.
CMD ["python", "-c", "import services.fusion.src.fuse as f; print('fusion ready')"]
