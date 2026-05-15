"""Pydantic v2 models for the sar-viewer data contracts.

These interfaces are frozen in Phase 0 per CLAUDE.md §5. Any change must go
through the interface-change protocol described in CLAUDE.md §4.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

GeoJSON = dict[str, Any]


class AOI(BaseModel):
    """An area of interest drawn by the investigator."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    id: UUID
    geometry: GeoJSON = Field(description="GeoJSON Polygon geometry")
    created_at: datetime
    label: str | None = None


class Scene(BaseModel):
    """A Sentinel-1 or Sentinel-2 scene as returned by CDSE STAC search."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    platform: Literal["S1", "S2"]
    product_type: str
    sensing_start: datetime
    sensing_end: datetime
    polarization: str | None = None
    orbit_direction: Literal["ASCENDING", "DESCENDING"] | None = None
    footprint: GeoJSON


JobKind = Literal["search", "fetch", "process", "analyze", "fuse"]
JobStatus = Literal["queued", "running", "done", "error"]


class Job(BaseModel):
    """A unit of work in the processing DAG."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    aoi_id: UUID
    kind: JobKind
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None


class Baseline(BaseModel):
    """A statistical baseline (median + MAD of sigma-0 in dB) for an AOI.

    Phase 0 declared scalar ``median_db`` / ``mad_db`` (AOI-mean summaries).
    Phase 2 (phase-2-brief.md §1.1.1) extends this additively with two
    optional path fields pointing to per-pixel COG rasters. The scalar
    fields remain for quick numeric summaries; the COG paths carry the
    full per-pixel detail required for statistical anomaly detection.
    """

    model_config = ConfigDict(extra="forbid")

    aoi_id: UUID
    scene_ids: list[UUID]
    median_db: float
    mad_db: float
    n_obs: int
    computed_at: datetime
    # Phase 2 additive — path strings, relative to project root or absolute.
    # ``None`` means the AOI-mean scalars are the only available summary
    # (backward compatibility with Phase 0/1 callers that built scalar-only
    # baselines).
    median_db_cog: str | None = None
    mad_db_cog: str | None = None


AnomalyKind = Literal["new", "missing", "intensity_change"]


class Anomaly(BaseModel):
    """A region flagged as anomalous against the baseline."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    aoi_id: UUID
    scene_id: UUID
    bbox: tuple[float, float, float, float]
    score: float
    kind: AnomalyKind
    confirmed_omnibus: bool = False
