"""FastAPI entrypoint for the api service.

Phase 0: only the /health endpoint was wired.
Phase 1: jobs router added (/jobs POST, GET /{id}, GET list).

The jobs table is initialised on startup via the lifespan hook so the API
never starts without the schema in place.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.api.src.jobs import on_startup
from services.api.src.jobs import router as jobs_router


@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
    """Run startup hooks before the server accepts requests."""
    on_startup()
    yield


app = FastAPI(
    title="sar-viewer api",
    version="0.1.0",
    description="SAR/OSINT investigation tool for human rights researchers.",
    lifespan=lifespan,
)

app.include_router(jobs_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe used by Phase 0 acceptance and docker-compose healthchecks."""
    return {"status": "ok"}
