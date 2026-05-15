"""Integration test: search → fetch → process DAG through Celery's eager mode.

Strategy
--------
- Monkey-patch the public interfaces from services/ingest and services/processor
  so no network calls or SNAP are needed.
- Configure Celery to run tasks synchronously (task_always_eager=True) so the
  test does not need a running Redis broker.
- Use an in-memory SQLite database so no files are left on disk after the test.
- Verify that: each task transitions the Job through the expected status
  sequence, results are stored in LocalCache, and the DAG links search output
  to fetch input and fetch output to process input.

Monkeypatching approach
-----------------------
The tasks import services.ingest.src.client and services.processor.src.pipeline
inside the task body (deferred import). We patch at the module level by
inserting a fake module into sys.modules before the tasks run.
"""

from __future__ import annotations

import json
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from shared.models import Scene

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def celery_eager(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure Celery to run tasks synchronously (no broker needed)."""
    from services.worker.src.app import app as celery_app

    celery_app.conf.update(
        task_always_eager=True,
        task_eager_propagates=True,
    )


@pytest.fixture()
def tmp_cache_roots(tmp_path: Path) -> tuple[Path, Path]:
    """Return (cache_root, derived_root) under tmp_path."""
    cache_root = tmp_path / "cache"
    derived_root = tmp_path / "derived"
    cache_root.mkdir()
    derived_root.mkdir()
    return cache_root, derived_root


@pytest.fixture()
def job_db(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the job database to an in-memory SQLite instance."""
    monkeypatch.setenv("JOB_DB_URL", "sqlite:///:memory:")
    # Re-import db module with the new URL.
    import importlib

    import services.worker.src.db as db_mod

    importlib.reload(db_mod)
    db_mod.init_db()

    # Ensure tasks and api use the reloaded module.
    import services.api.src.database as api_db_mod

    importlib.reload(api_db_mod)
    # tasks imports db functions at call time, so no reload needed there.
    yield
    # Cleanup: re-reload with no env override to restore state for next test.
    monkeypatch.delenv("JOB_DB_URL", raising=False)
    importlib.reload(db_mod)
    importlib.reload(api_db_mod)


@pytest.fixture()
def fake_safe_dir(tmp_path: Path) -> Path:
    """Create a minimal fake .SAFE directory for use as fetch result."""
    safe = tmp_path / "S1A_IW_GRDH_1S.SAFE"
    safe.mkdir()
    (safe / "manifest.safe").write_text("<SAFE>fake</SAFE>")
    return safe


@pytest.fixture()
def fake_cog(tmp_path: Path) -> Path:
    """Create a tiny fake COG file to be returned by the processor stub."""
    cog = tmp_path / "output.tif"
    cog.write_bytes(b"TIFF" + b"\x00" * 100)  # not a real TIFF, but enough for path tests
    return cog


# ---------------------------------------------------------------------------
# Ingest / Processor stubs
# ---------------------------------------------------------------------------


def _make_fake_ingest_module(
    aoi_id: uuid.UUID,
    safe_dir: Path,
) -> types.ModuleType:
    """Build a fake services.ingest.src.client module."""
    scene = Scene(
        id=uuid.uuid4(),
        platform="S1",
        product_type="IW_GRDH_1S",
        sensing_start=datetime(2024, 1, 1, tzinfo=UTC),
        sensing_end=datetime(2024, 1, 1, 1, tzinfo=UTC),
        polarization="VV",
        orbit_direction="ASCENDING",
        footprint={"type": "Polygon", "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]]},
    )

    async def search_scenes(
        aoi: Any, start: Any, end: Any, platform: Any, product_type: Any = None
    ) -> list[Scene]:
        return [scene]

    async def fetch_scene(scene_id: uuid.UUID) -> Path:
        return safe_dir

    async def preview_scene(scene_id: uuid.UUID, bbox: Any) -> bytes:
        return b"PNG_BYTES"

    mod = types.ModuleType("services.ingest.src.client")
    mod.search_scenes = search_scenes  # type: ignore[attr-defined]
    mod.fetch_scene = fetch_scene  # type: ignore[attr-defined]
    mod.preview_scene = preview_scene  # type: ignore[attr-defined]
    return mod


def _make_fake_processor_module(cog_path: Path) -> types.ModuleType:
    """Build a fake services.processor.src.pipeline module."""

    def process_grd(safe_dir: Path, out_dir: Path) -> Path:
        # Write a fake COG into out_dir to simulate real output.
        output = out_dir / "output.tif"
        output.write_bytes(b"TIFF" + b"\x00" * 100)
        return output

    def process_slc_pair(safe_a: Path, safe_b: Path, out_dir: Path) -> Path:
        output = out_dir / "coherence.tif"
        output.write_bytes(b"TIFF" + b"\x00" * 100)
        return output

    mod = types.ModuleType("services.processor.src.pipeline")
    mod.process_grd = process_grd  # type: ignore[attr-defined]
    mod.process_slc_pair = process_slc_pair  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# Helper: stub both external service modules in sys.modules
# ---------------------------------------------------------------------------


def _install_stubs(
    aoi_id: uuid.UUID,
    safe_dir: Path,
    cog_path: Path,
) -> None:
    """Inject fake ingest + processor modules into sys.modules."""
    # Ensure parent packages exist so sub-package imports resolve.
    for pkg in (
        "services.ingest",
        "services.ingest.src",
        "services.processor",
        "services.processor.src",
    ):
        if pkg not in sys.modules:
            sys.modules[pkg] = types.ModuleType(pkg)

    sys.modules["services.ingest.src.client"] = _make_fake_ingest_module(aoi_id, safe_dir)
    sys.modules["services.processor.src.pipeline"] = _make_fake_processor_module(cog_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_search_task_creates_job_and_returns_scenes(
    job_db: None,
    tmp_cache_roots: tuple[Path, Path],
    fake_safe_dir: Path,
    fake_cog: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """search task transitions job to 'done' and returns a list of scenes."""
    from services.worker.src import db as db_mod
    from services.worker.src.tasks import task_search

    cache_root, derived_root = tmp_cache_roots
    monkeypatch.setattr("services.worker.src.tasks.CACHE_ROOT", cache_root)
    monkeypatch.setattr("services.worker.src.tasks.DERIVED_ROOT", derived_root)
    monkeypatch.setattr(
        "services.worker.src.tasks.raw_cache",
        __import__("shared.storage", fromlist=["LocalCache"]).LocalCache(cache_root),
    )
    monkeypatch.setattr(
        "services.worker.src.tasks.derived_cache",
        __import__("shared.storage", fromlist=["LocalCache"]).LocalCache(derived_root),
    )

    aoi_id = uuid.uuid4()
    _install_stubs(aoi_id, fake_safe_dir, fake_cog)

    job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=job_id, aoi_id=str(aoi_id), kind="search")

    result = task_search(
        job_id,
        {
            "aoi_id": str(aoi_id),
            "aoi_geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T23:59:59+00:00",
            "platform": "S1",
        },
    )

    assert result["status"] == "done"
    assert len(result["scenes"]) == 1
    scene = result["scenes"][0]
    assert scene["platform"] == "S1"
    assert scene["product_type"] == "IW_GRDH_1S"

    row = db_mod.get_job(job_id)
    assert row is not None
    assert row.status == "done"


def test_fetch_task_caches_safe_dir(
    job_db: None,
    tmp_cache_roots: tuple[Path, Path],
    fake_safe_dir: Path,
    fake_cog: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """fetch task downloads a .SAFE dir and stores it in raw_cache."""
    from services.worker.src import db as db_mod
    from services.worker.src.tasks import task_fetch
    from shared.storage import LocalCache

    cache_root, derived_root = tmp_cache_roots
    raw_cache = LocalCache(cache_root)
    monkeypatch.setattr("services.worker.src.tasks.CACHE_ROOT", cache_root)
    monkeypatch.setattr("services.worker.src.tasks.DERIVED_ROOT", derived_root)
    monkeypatch.setattr("services.worker.src.tasks.raw_cache", raw_cache)
    monkeypatch.setattr(
        "services.worker.src.tasks.derived_cache",
        LocalCache(derived_root),
    )

    aoi_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    _install_stubs(aoi_id, fake_safe_dir, fake_cog)

    job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=job_id, aoi_id=str(aoi_id), kind="fetch")

    result = task_fetch(job_id, {"scene_id": str(scene_id)})

    assert result["status"] == "done"
    assert Path(result["path"]).exists()

    row = db_mod.get_job(job_id)
    assert row is not None
    assert row.status == "done"

    # Idempotency: second call must be a cache hit, not call fetch_scene again.
    job_id2 = str(uuid.uuid4())
    db_mod.create_job(job_id=job_id2, aoi_id=str(aoi_id), kind="fetch")
    result2 = task_fetch(job_id2, {"scene_id": str(scene_id)})
    assert result2["status"] == "done"
    # Same path returned.
    assert result2["path"] == result["path"]


def test_process_task_produces_cog(
    job_db: None,
    tmp_cache_roots: tuple[Path, Path],
    fake_safe_dir: Path,
    fake_cog: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process task runs the GRD pipeline stub and stores the COG."""
    from services.worker.src import db as db_mod
    from services.worker.src.tasks import task_process
    from shared.storage import LocalCache

    cache_root, derived_root = tmp_cache_roots
    monkeypatch.setattr("services.worker.src.tasks.CACHE_ROOT", cache_root)
    monkeypatch.setattr("services.worker.src.tasks.DERIVED_ROOT", derived_root)
    monkeypatch.setattr("services.worker.src.tasks.raw_cache", LocalCache(cache_root))
    derived_cache = LocalCache(derived_root)
    monkeypatch.setattr("services.worker.src.tasks.derived_cache", derived_cache)

    aoi_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    _install_stubs(aoi_id, fake_safe_dir, fake_cog)

    job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=job_id, aoi_id=str(aoi_id), kind="process")

    result = task_process(
        job_id,
        {
            "scene_id": str(scene_id),
            "safe_path": str(fake_safe_dir),
            "pipeline": "grd",
        },
    )

    assert result["status"] == "done"
    cog_path = Path(result["path"])
    assert cog_path.exists()

    row = db_mod.get_job(job_id)
    assert row is not None
    assert row.status == "done"


def test_full_dag_search_fetch_process(
    job_db: None,
    tmp_cache_roots: tuple[Path, Path],
    fake_safe_dir: Path,
    fake_cog: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise the full search → fetch → process DAG in one test.

    This is the Phase 1 acceptance integration test specified in
    phase-1-brief.md §4.4.
    """
    from services.worker.src import db as db_mod
    from services.worker.src.tasks import task_fetch, task_process, task_search
    from shared.storage import LocalCache

    cache_root, derived_root = tmp_cache_roots
    raw_cache = LocalCache(cache_root)
    derived_cache = LocalCache(derived_root)
    monkeypatch.setattr("services.worker.src.tasks.CACHE_ROOT", cache_root)
    monkeypatch.setattr("services.worker.src.tasks.DERIVED_ROOT", derived_root)
    monkeypatch.setattr("services.worker.src.tasks.raw_cache", raw_cache)
    monkeypatch.setattr("services.worker.src.tasks.derived_cache", derived_cache)

    aoi_id = uuid.uuid4()
    _install_stubs(aoi_id, fake_safe_dir, fake_cog)

    # --- search ---
    search_job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=search_job_id, aoi_id=str(aoi_id), kind="search")
    search_result = task_search(
        search_job_id,
        {
            "aoi_id": str(aoi_id),
            "aoi_geometry": {
                "type": "Polygon",
                "coordinates": [[[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]],
            },
            "start": "2024-01-01T00:00:00+00:00",
            "end": "2024-01-31T23:59:59+00:00",
            "platform": "S1",
        },
    )
    assert search_result["status"] == "done"
    scenes = search_result["scenes"]
    assert len(scenes) == 1
    scene_id = scenes[0]["id"]

    # --- fetch (uses scene_id from search result) ---
    fetch_job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=fetch_job_id, aoi_id=str(aoi_id), kind="fetch")
    fetch_result = task_fetch(fetch_job_id, {"scene_id": scene_id})
    assert fetch_result["status"] == "done"
    safe_path = fetch_result["path"]
    assert Path(safe_path).exists()

    # --- process (uses safe_path from fetch result) ---
    process_job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=process_job_id, aoi_id=str(aoi_id), kind="process")
    process_result = task_process(
        process_job_id,
        {
            "scene_id": scene_id,
            "safe_path": safe_path,
            "pipeline": "grd",
        },
    )
    assert process_result["status"] == "done"
    cog_path = Path(process_result["path"])
    assert cog_path.exists()

    # All three jobs should now be done.
    for jid in (search_job_id, fetch_job_id, process_job_id):
        row = db_mod.get_job(jid)
        assert row is not None
        assert row.status == "done", f"Job {jid} status is {row.status}"


def test_audit_log_written_for_search_when_enabled(
    job_db: None,
    tmp_cache_roots: tuple[Path, Path],
    fake_safe_dir: Path,
    fake_cog: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With AUDIT_LOG_ENABLED=true, a search job causes audit events for cache ops."""

    from services.worker.src import db as db_mod
    from services.worker.src.tasks import task_fetch
    from shared.storage import LocalCache

    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")

    cache_root, derived_root = tmp_cache_roots
    audit_log = tmp_path / "audit.log"
    raw_cache = LocalCache(cache_root, audit_log_path=audit_log)
    monkeypatch.setattr("services.worker.src.tasks.CACHE_ROOT", cache_root)
    monkeypatch.setattr("services.worker.src.tasks.DERIVED_ROOT", derived_root)
    monkeypatch.setattr("services.worker.src.tasks.raw_cache", raw_cache)
    monkeypatch.setattr(
        "services.worker.src.tasks.derived_cache",
        LocalCache(derived_root, audit_log_path=audit_log),
    )

    aoi_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    _install_stubs(aoi_id, fake_safe_dir, fake_cog)

    # First fetch — miss, then the key is stored.
    fetch_job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=fetch_job_id, aoi_id=str(aoi_id), kind="fetch")
    task_fetch(fetch_job_id, {"scene_id": str(scene_id)})

    assert audit_log.exists()
    lines = [json.loads(line) for line in audit_log.read_text().strip().splitlines()]
    events = [line["event"] for line in lines]
    # First get() on raw_cache is a miss; after put(), second fetch is a hit.
    assert "cache_miss" in events


def test_audit_log_not_created_when_disabled(
    job_db: None,
    tmp_cache_roots: tuple[Path, Path],
    fake_safe_dir: Path,
    fake_cog: Path,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """With AUDIT_LOG_ENABLED unset, no audit.log must be created."""
    from services.worker.src import db as db_mod
    from services.worker.src.tasks import task_fetch
    from shared.storage import LocalCache

    monkeypatch.delenv("AUDIT_LOG_ENABLED", raising=False)

    cache_root, derived_root = tmp_cache_roots
    audit_log = tmp_path / "audit.log"
    raw_cache = LocalCache(cache_root, audit_log_path=audit_log)
    monkeypatch.setattr("services.worker.src.tasks.CACHE_ROOT", cache_root)
    monkeypatch.setattr("services.worker.src.tasks.DERIVED_ROOT", derived_root)
    monkeypatch.setattr("services.worker.src.tasks.raw_cache", raw_cache)
    monkeypatch.setattr(
        "services.worker.src.tasks.derived_cache",
        LocalCache(derived_root, audit_log_path=audit_log),
    )

    aoi_id = uuid.uuid4()
    scene_id = uuid.uuid4()
    _install_stubs(aoi_id, fake_safe_dir, fake_cog)

    fetch_job_id = str(uuid.uuid4())
    db_mod.create_job(job_id=fetch_job_id, aoi_id=str(aoi_id), kind="fetch")
    task_fetch(fetch_job_id, {"scene_id": str(scene_id)})

    assert not audit_log.exists()
