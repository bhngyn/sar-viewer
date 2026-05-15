"""FastAPI entrypoint for the api service.

Phase 0: only the /health endpoint is wired. The job, AOI, and result routes
are implemented in Phase 1.
"""

from __future__ import annotations

from fastapi import FastAPI

app = FastAPI(
    title="sar-viewer api",
    version="0.1.0",
    description="SAR/OSINT investigation tool for human rights researchers.",
)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Phase 0 acceptance and docker-compose healthchecks."""
    return {"status": "ok"}
