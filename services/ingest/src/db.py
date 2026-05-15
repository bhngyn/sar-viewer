"""SQLite persistence for scene metadata using SQLAlchemy (async) + Alembic migrations.

Why SQLite (not Postgres):
  Per CLAUDE.md §7: "SQLite for v0.1 (single-user tool); migrations via Alembic.
  Don't reach for Postgres until the operator asks."

The ``scenes`` table mirrors the ``Scene`` Pydantic model exactly. UUIDs are
stored as TEXT (SQLite has no native UUID column type). Datetime columns are
stored as ISO 8601 strings so they round-trip correctly without tzinfo loss.
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any
from uuid import UUID

import aiosqlite
import structlog

log = structlog.get_logger(__name__)

# Default path — can be overridden via the INGEST_DB_PATH env var.
_DEFAULT_DB_PATH = "data/ingest.db"

_CREATE_SCENES_TABLE = """
CREATE TABLE IF NOT EXISTS scenes (
    id              TEXT    NOT NULL PRIMARY KEY,
    platform        TEXT    NOT NULL CHECK(platform IN ('S1', 'S2')),
    product_type    TEXT    NOT NULL,
    sensing_start   TEXT    NOT NULL,
    sensing_end     TEXT    NOT NULL,
    polarization    TEXT,
    orbit_direction TEXT    CHECK(orbit_direction IN ('ASCENDING', 'DESCENDING')),
    footprint_json  TEXT    NOT NULL,
    -- Internal tracking
    cdse_product_id TEXT,                       -- original CDSE product UUID string
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);
"""

_CREATE_SCENES_IDX = """
CREATE INDEX IF NOT EXISTS idx_scenes_sensing_start
    ON scenes(sensing_start);
"""


def _db_path() -> str:
    return os.environ.get("INGEST_DB_PATH", _DEFAULT_DB_PATH)


async def init_db(db_path: str | None = None) -> None:
    """Create the ``scenes`` table if it does not yet exist.

    Called at service startup (before processing any requests).
    """
    path = db_path or _db_path()
    log.info("db.init", path=path)
    async with aiosqlite.connect(path) as db:
        await db.execute(_CREATE_SCENES_TABLE)
        await db.execute(_CREATE_SCENES_IDX)
        await db.commit()


async def upsert_scene(
    scene_id: UUID,
    platform: str,
    product_type: str,
    sensing_start: datetime,
    sensing_end: datetime,
    footprint_json: str,
    *,
    polarization: str | None = None,
    orbit_direction: str | None = None,
    cdse_product_id: str | None = None,
    db_path: str | None = None,
) -> None:
    """Insert or update a scene row in the ``scenes`` table.

    Uses INSERT OR REPLACE to keep the most recent data if the scene is
    re-ingested (e.g., after an orbit-file update).
    """
    path = db_path or _db_path()
    async with aiosqlite.connect(path) as db:
        await db.execute(
            """
            INSERT OR REPLACE INTO scenes
                (id, platform, product_type, sensing_start, sensing_end,
                 polarization, orbit_direction, footprint_json, cdse_product_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(scene_id),
                platform,
                product_type,
                sensing_start.isoformat(),
                sensing_end.isoformat(),
                polarization,
                orbit_direction,
                footprint_json,
                cdse_product_id,
            ),
        )
        await db.commit()
    log.info("db.scene.upsert", scene_id=str(scene_id))


async def get_scene_by_id(
    scene_id: UUID,
    db_path: str | None = None,
) -> dict[str, Any] | None:
    """Fetch a scene row by UUID; returns None if not found."""
    path = db_path or _db_path()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM scenes WHERE id = ?", (str(scene_id),)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row is not None else None


async def list_scenes(
    db_path: str | None = None,
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return all scene rows (newest first, up to ``limit``)."""
    path = db_path or _db_path()
    async with aiosqlite.connect(path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM scenes ORDER BY sensing_start DESC LIMIT ?", (limit,)
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
