"""Synchronous SQLite persistence layer for Job rows.

We use synchronous SQLAlchemy (not asyncio) here because Celery tasks run in
plain threads, not an asyncio event loop.  The async DB engine used by the API
layer is defined separately in services/api/src/database.py.

The database file path is controlled by the JOB_DB_URL env var so tests can
inject an in-memory SQLite URL (``sqlite:///:memory:``).

Schema is kept simple: a single ``jobs`` table mirroring the Job Pydantic
model.  Alembic manages migrations for the production database.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DB_URL = os.environ.get("JOB_DB_URL", "sqlite:///data/jobs.db")

# Thread-safe engine; connect_args for SQLite WAL mode so readers don't block.
_engine = create_engine(
    DB_URL,
    connect_args={"check_same_thread": False},
    echo=False,
)

SessionLocal: sessionmaker[Session] = sessionmaker(bind=_engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


class JobRow(Base):
    """ORM representation of a Job row.

    Uses SQLAlchemy 2.0 Mapped columns for full mypy compatibility.
    Mirrors the Pydantic ``Job`` model (shared/models.py) field for field.
    """

    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    aoi_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Stores the JSON-serialised result (e.g. list of Scene dicts for search,
    # path string for fetch/process). Not exposed in the public Job model; the
    # API reads it when building result detail endpoints (Phase 3).
    result: Mapped[str | None] = mapped_column(Text, nullable=True)


def init_db() -> None:
    """Create all tables if they do not exist.

    Called at worker startup and in tests.  In production the Alembic
    migration runs first; this function is a safety net for development.
    """
    Base.metadata.create_all(bind=_engine)


def _now() -> datetime:
    return datetime.now(UTC)


def create_job(
    job_id: str,
    aoi_id: str,
    kind: str,
    params: dict[str, Any] | None = None,
) -> JobRow:
    """Insert a new Job row with status=queued and return it."""
    row = JobRow(
        id=job_id,
        aoi_id=aoi_id,
        kind=kind,
        status="queued",
        created_at=_now(),
    )
    with SessionLocal() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
        # Detach from session so callers can use the object freely.
        session.expunge(row)
    return row


def get_job(job_id: str) -> JobRow | None:
    """Fetch a Job row by id, or ``None`` if not found."""
    with SessionLocal() as session:
        row = session.get(JobRow, job_id)
        if row is not None:
            session.expunge(row)
        return row


def list_jobs(
    aoi_id: str | None = None,
    status: str | None = None,
) -> list[JobRow]:
    """Return Job rows, optionally filtered by aoi_id and/or status."""
    stmt = select(JobRow)
    if aoi_id is not None:
        stmt = stmt.where(JobRow.aoi_id == aoi_id)
    if status is not None:
        stmt = stmt.where(JobRow.status == status)

    with SessionLocal() as session:
        rows = list(session.scalars(stmt))
        for row in rows:
            session.expunge(row)
        return rows


def mark_job_running(job_id: str) -> None:
    """Transition a job to status=running and stamp started_at."""
    with SessionLocal() as session:
        row = session.get(JobRow, job_id)
        if row is not None:
            row.status = "running"
            row.started_at = _now()
            session.commit()


def mark_job_done(job_id: str, result: str | None = None) -> None:
    """Transition a job to status=done, stamp completed_at, optionally store result."""
    with SessionLocal() as session:
        row = session.get(JobRow, job_id)
        if row is not None:
            row.status = "done"
            row.completed_at = _now()
            if result is not None:
                row.result = result
            session.commit()


def mark_job_error(job_id: str, error: str) -> None:
    """Transition a job to status=error, stamp completed_at, store error message."""
    with SessionLocal() as session:
        row = session.get(JobRow, job_id)
        if row is not None:
            row.status = "error"
            row.completed_at = _now()
            row.error = error
            session.commit()
