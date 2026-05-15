"""Smoke tests for the Phase 0 shared models.

These exist so the pytest harness has something to run, and so any future
agent that touches `shared/models.py` gets immediate feedback if they
break the interface contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from shared.models import AOI, Anomaly, Baseline, Job, Scene


def _now() -> datetime:
    return datetime.now(UTC)


def test_aoi_roundtrip() -> None:
    aoi = AOI(
        id=uuid4(),
        geometry={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
        created_at=_now(),
        label="test-aoi",
    )
    AOI.model_validate_json(aoi.model_dump_json())


def test_scene_roundtrip() -> None:
    scene = Scene(
        id=uuid4(),
        platform="S1",
        product_type="GRD",
        sensing_start=_now(),
        sensing_end=_now(),
        polarization="VV",
        orbit_direction="ASCENDING",
        footprint={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    )
    Scene.model_validate_json(scene.model_dump_json())


def test_job_roundtrip() -> None:
    job = Job(
        id=uuid4(),
        aoi_id=uuid4(),
        kind="search",
        status="queued",
        created_at=_now(),
    )
    Job.model_validate_json(job.model_dump_json())


def test_baseline_roundtrip() -> None:
    baseline = Baseline(
        aoi_id=uuid4(),
        scene_ids=[uuid4() for _ in range(3)],
        median_db=-12.5,
        mad_db=1.8,
        n_obs=12,
        computed_at=_now(),
    )
    Baseline.model_validate_json(baseline.model_dump_json())


def test_anomaly_roundtrip() -> None:
    anomaly = Anomaly(
        id=uuid4(),
        aoi_id=uuid4(),
        scene_id=uuid4(),
        bbox=(0.0, 0.0, 1.0, 1.0),
        score=4.2,
        kind="new",
    )
    parsed = Anomaly.model_validate_json(anomaly.model_dump_json())
    assert parsed.confirmed_omnibus is False
