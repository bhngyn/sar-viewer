# services/processor/Dockerfile
#
# Slim Python image for the SAR processor worker.
# SNAP/GPT is NOT installed here; it runs in the companion snap sidecar
# (services/processor/snap.Dockerfile) and is invoked via subprocess over
# the shared ./data/scratch volume.
#
# Non-root user and no --privileged per OPSEC §6.

FROM python:3.11-slim AS base

# System dependencies required by GDAL/rasterio
RUN apt-get update && apt-get install -y --no-install-recommends \
        gdal-bin \
        libgdal-dev \
        libproj-dev \
        libgeos-dev \
        build-essential \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Install uv for fast dependency resolution
COPY --from=ghcr.io/astral-sh/uv:0.4 /uv /usr/local/bin/uv

WORKDIR /app

# Copy processor-specific pyproject first for layer caching
COPY services/processor/pyproject.toml services/processor/pyproject.toml

# Install Python dependencies (no dev extras)
RUN uv pip install --system --no-cache \
        pyroSAR \
        rasterio \
        rio-cogeo \
        numpy \
        structlog

# Copy shared models (needed for type annotations)
COPY shared/ shared/

# Copy processor source
COPY services/processor/src/ services/processor/src/

# Create non-root user
RUN groupadd -r processor && useradd -r -g processor processor

# Scratch directory for SNAP intermediate products (mounted from host)
RUN mkdir -p /scratch && chown processor:processor /scratch

USER processor

CMD ["python", "-m", "services.processor.src.worker"]
