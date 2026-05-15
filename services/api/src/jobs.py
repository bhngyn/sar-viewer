"""Job management routes for the SAR-OSINT API.

Routes
------
POST  /jobs          Create a new job and dispatch it to Celery.
GET   /jobs/{id}     Fetch a single job by id.
GET   /jobs          List jobs, optionally filtered by aoi_id and/or status.

All three routes return ``Job`` Pydantic models (shared/models.py) so the
client always gets the same schema regardless of where the data comes from.

Celery task dispatch uses ``apply_async`` with the job id and params as
positional arguments.  The task name matches ``JobKind`` exactly (``search``,
``fetch``, ``process``, ``analyze``, ``fuse``).
"""

from __future__ import annotations

import uuid
from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.api.src.database import create_job, get_job, init_db, list_jobs
from shared.models import Job, JobKind, JobStatus

logger: structlog.BoundLogger = structlog.get_logger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])


# ──────────────────────────────────────────────────────────────────────────────
# Request / response schemas
# ──────────────────────────────────────────────────────────────────────────────


class CreateJobRequest(BaseModel):
    """Request body for POST /jobs."""

    aoi_id: uuid.UUID
    kind: JobKind
    params: dict[str, Any] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


def _row_to_job(row: Any) -> Job:
    """Convert a JobRow ORM object to a Pydantic Job model."""
    return Job(
        id=uuid.UUID(row.id),
        aoi_id=uuid.UUID(row.aoi_id),
        kind=row.kind,
        status=row.status,
        created_at=row.created_at,
        started_at=row.started_at,
        completed_at=row.completed_at,
        error=row.error,
    )


def _dispatch(job_id: str, kind: str, params: dict[str, Any]) -> None:
    """Send the task to Celery.

    Import is deferred so that the API service can start even when the Celery
    broker is not yet available (useful in development).  The route handler
    will return 503 if Celery is unavailable.
    """
    from services.worker.src.app import app as celery_app

    celery_app.send_task(kind, args=[job_id, params])


# ──────────────────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────────────────


@router.post("", response_model=Job, status_code=202)
def create_job_route(body: CreateJobRequest) -> Job:
    """Create a new job and enqueue it for processing.

    Returns the newly created ``Job`` with ``status="queued"``.  Clients
    should poll ``GET /jobs/{id}`` to track progress.
    """
    job_id = str(uuid.uuid4())
    log = logger.bind(job_id=job_id, kind=body.kind, aoi_id=str(body.aoi_id))
    log.info("job.create")

    try:
        row = create_job(
            job_id=job_id,
            aoi_id=str(body.aoi_id),
            kind=body.kind,
            params=body.params,
        )
    except Exception as exc:
        log.error("job.db_error", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Database error: {exc}") from exc

    try:
        _dispatch(job_id, body.kind, body.params)
    except Exception as exc:
        # Celery not reachable — mark job as error immediately.
        log.error("job.dispatch_error", error=str(exc))
        raise HTTPException(status_code=503, detail=f"Broker unavailable: {exc}") from exc

    log.info("job.queued")
    return _row_to_job(row)


@router.get("/{job_id}", response_model=Job)
def get_job_route(job_id: uuid.UUID) -> Job:
    """Return a single job by id."""
    row = get_job(str(job_id))
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
    return _row_to_job(row)


@router.get("", response_model=list[Job])
def list_jobs_route(
    aoi_id: uuid.UUID | None = Query(default=None, description="Filter by AOI id"),  # noqa: B008
    status: JobStatus | None = Query(default=None, description="Filter by job status"),  # noqa: B008
) -> list[Job]:
    """Return all jobs, optionally filtered by aoi_id and/or status."""
    rows = list_jobs(
        aoi_id=str(aoi_id) if aoi_id is not None else None,
        status=status,
    )
    return [_row_to_job(r) for r in rows]


# ──────────────────────────────────────────────────────────────────────────────
# Application startup
# ──────────────────────────────────────────────────────────────────────────────


def on_startup() -> None:
    """Initialise the jobs table on API startup (idempotent).

    Called from the FastAPI lifespan hook in main.py.
    """
    init_db()
    logger.info("jobs_db.ready")
