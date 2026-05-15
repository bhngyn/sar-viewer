"""End-to-end: baseline → statistical → omnibus on a synthetic pipeline."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from services.analyzer.src.baseline import build_baseline
from services.analyzer.src.detect import detect_anomalies

from .conftest import synthetic_db_stack, write_synthetic_db_cog


class TestDetectIntegration:
    def test_bright_patch_end_to_end_confirmed(self, tmp_path: Path) -> None:
        """12 baseline + 1 new (with bright patch) → 1 anomaly, confirmed_omnibus=True."""
        n_baseline = 12
        baseline_stack = synthetic_db_stack(
            n_baseline, shape=(48, 48), mean_db=-12.0, std_db=1.0, seed=11
        )

        # Build baseline COGs.
        baseline_paths: list[Path] = []
        baseline_scene_ids = []
        baseline_dates: list[datetime] = []
        t0 = datetime(2025, 1, 1, tzinfo=UTC)
        for i, frame in enumerate(baseline_stack):
            p = tmp_path / f"b{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            baseline_paths.append(p)
            baseline_scene_ids.append(uuid.uuid4())
            baseline_dates.append(t0 + timedelta(days=i * 12))

        # New scene with a +10 dB patch.
        new_frame = synthetic_db_stack(1, shape=(48, 48), mean_db=-12.0, std_db=1.0, seed=99)[0]
        new_frame[20:25, 20:25] += 10.0
        new_path = tmp_path / "new.tif"
        write_synthetic_db_cog(new_path, new_frame)

        baseline = build_baseline(
            uuid.uuid4(),
            baseline_paths,
            baseline_scene_ids,
            tmp_path / "baseline",
        )

        anomalies, ts_by_id = detect_anomalies(
            aoi_id=uuid.uuid4(),
            new_scene_cog=new_path,
            new_scene_id=uuid.uuid4(),
            baseline=baseline,
            timeseries=list(zip(baseline_dates, baseline_paths, strict=True)),
            alpha=1e-4,
        )

        assert anomalies, "expected at least one anomaly from the +10 dB patch"
        # At least one anomaly should be omnibus-confirmed.
        assert any(
            a.confirmed_omnibus for a in anomalies
        ), "omnibus should confirm the +10 dB bright patch"
        # Time series should be populated for every anomaly.
        for a in anomalies:
            assert str(a.id) in ts_by_id
            assert len(ts_by_id[str(a.id)]) >= 2

    def test_no_timeseries_means_no_confirmation(self, tmp_path: Path) -> None:
        """Without timeseries, anomalies are detected but never confirmed."""
        baseline_stack = synthetic_db_stack(10, shape=(32, 32), seed=3)
        baseline_paths: list[Path] = []
        for i, frame in enumerate(baseline_stack):
            p = tmp_path / f"b{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            baseline_paths.append(p)

        # New scene with a large bright patch.
        new_frame = baseline_stack[0].copy()
        new_frame[10:15, 10:15] += 10.0
        new_path = tmp_path / "new.tif"
        write_synthetic_db_cog(new_path, new_frame)

        baseline = build_baseline(
            uuid.uuid4(),
            baseline_paths,
            [uuid.uuid4() for _ in baseline_paths],
            tmp_path / "baseline",
        )
        anomalies, _ = detect_anomalies(
            aoi_id=uuid.uuid4(),
            new_scene_cog=new_path,
            new_scene_id=uuid.uuid4(),
            baseline=baseline,
            timeseries=None,
            alpha=1e-4,
        )
        # Should detect, but never confirm without time series.
        assert anomalies
        assert not any(a.confirmed_omnibus for a in anomalies)
