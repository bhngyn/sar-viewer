# Analyzer service — runs the baseline/statistical/omnibus pipeline.
# OPSEC §6: non-root user, no host network.
FROM python:3.11-slim AS base

# rasterio needs libexpat/libtiff/libgeos at runtime; the wheel bundles GDAL.
# We still install the minimum system deps to keep startup snappy.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libexpat1 \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 analyzer
USER analyzer
WORKDIR /home/analyzer/app

# uv-based install (deps are managed at the workspace root).
ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=analyzer:analyzer services/analyzer/pyproject.toml ./pyproject.toml
RUN pip install --user \
        "numpy>=1.26" \
        "scipy>=1.13" \
        "scikit-learn>=1.4" \
        "rasterio>=1.3" \
        "rio-cogeo>=5.0" \
        "pydantic>=2.7" \
        "structlog>=24.1"

COPY --chown=analyzer:analyzer shared /home/analyzer/app/shared
COPY --chown=analyzer:analyzer services/analyzer /home/analyzer/app/services/analyzer

ENV PYTHONPATH=/home/analyzer/app
# Phase 2 ship as a library, not a long-running server — Celery worker
# imports services.analyzer.src.detect.detect_anomalies directly.
CMD ["python", "-c", "import services.analyzer.src.detect as d; print('analyzer ready')"]
