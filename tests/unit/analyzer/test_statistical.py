"""Statistical detector — robust z + DBSCAN + kind classification."""

from __future__ import annotations

import uuid
from pathlib import Path

import numpy as np

from services.analyzer.src.baseline import build_baseline
from services.analyzer.src.statistical import run_statistical

from .conftest import synthetic_db_stack, write_synthetic_db_cog


class TestStatisticalDetector:
    def test_bright_patch_detected_as_new(self, tmp_path: Path) -> None:
        """Inject a +10 dB patch → exactly one anomaly with kind='new' covering the patch."""
        rng_seed = 7
        stack = synthetic_db_stack(12, shape=(64, 64), mean_db=-12.0, std_db=1.0, seed=rng_seed)
        # Baseline scenes: stack[:11]; new scene: stack[11] with the patch.
        baseline_frames = stack[:11]
        new_frame = stack[11].copy()
        # 5x5 bright patch at rows 30:35, cols 40:45
        patch_rows = slice(30, 35)
        patch_cols = slice(40, 45)
        new_frame[patch_rows, patch_cols] += 10.0  # +10 dB

        baseline_paths = []
        for i, frame in enumerate(baseline_frames):
            p = tmp_path / f"baseline_{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            baseline_paths.append(p)
        new_path = tmp_path / "new.tif"
        write_synthetic_db_cog(new_path, new_frame)

        baseline = build_baseline(
            uuid.uuid4(),
            baseline_paths,
            [uuid.uuid4() for _ in baseline_paths],
            tmp_path / "baseline",
        )

        anomalies, clusters = run_statistical(
            new_scene_cog=new_path,
            new_scene_id=uuid.uuid4(),
            aoi_id=uuid.uuid4(),
            baseline=baseline,
            # Use a small min_samples because the synthetic raster has a tiny
            # pixel area (10 m * 10 m = 100 m²) and the default scales by 50 m²
            # which would give min_samples=5 already — but make it explicit.
            dbscan_min_samples=4,
            dbscan_eps_px=2.0,
        )
        assert len(anomalies) >= 1, "expected at least one anomaly from the bright patch"
        # At least one cluster should overlap the injected patch.
        any_match = False
        for cluster in clusters:
            mask_in_patch = (
                (cluster.pixel_rows >= 30)
                & (cluster.pixel_rows < 35)
                & (cluster.pixel_cols >= 40)
                & (cluster.pixel_cols < 45)
            )
            if np.any(mask_in_patch):
                any_match = True
                # Inspect the matching anomaly's kind.
                matching = next(a for a in anomalies if a.id == cluster.anomaly_id)
                assert matching.kind == "new", (
                    f"bright patch should classify as 'new', got {matching.kind}"
                )
                assert matching.score > 3.5, matching.score
                break
        assert any_match, "no cluster overlapped the injected bright patch"

    def test_no_change_no_anomaly(self, tmp_path: Path) -> None:
        """Pure-noise baseline + new scene → no anomalies (or only chance ones)."""
        stack = synthetic_db_stack(13, shape=(64, 64), mean_db=-12.0, std_db=1.0, seed=42)
        baseline_paths = []
        for i, frame in enumerate(stack[:12]):
            p = tmp_path / f"b{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            baseline_paths.append(p)
        new_path = tmp_path / "new.tif"
        write_synthetic_db_cog(new_path, stack[12])

        baseline = build_baseline(
            uuid.uuid4(),
            baseline_paths,
            [uuid.uuid4() for _ in baseline_paths],
            tmp_path / "baseline",
        )
        anomalies, _ = run_statistical(
            new_scene_cog=new_path,
            new_scene_id=uuid.uuid4(),
            aoi_id=uuid.uuid4(),
            baseline=baseline,
            dbscan_min_samples=12,  # require sizeable clusters to count
            dbscan_eps_px=1.5,
        )
        # 64*64 = 4096 pixels; at z>3.5 we expect ≈ 4096 * 2*(1-Phi(3.5)) ≈ 2
        # flagged pixels by chance, which won't cluster. Allow at most 1.
        assert len(anomalies) <= 1, len(anomalies)

    def test_missing_baseline_paths_raise(self, tmp_path: Path) -> None:
        """Baseline without median_db_cog/mad_db_cog (legacy scalar-only) is rejected."""
        from datetime import UTC, datetime

        import pytest

        from shared.models import Baseline

        b = Baseline(
            aoi_id=uuid.uuid4(),
            scene_ids=[],
            median_db=-12.0,
            mad_db=1.5,
            n_obs=10,
            computed_at=datetime.now(UTC),
            # median_db_cog / mad_db_cog deliberately None
        )
        new_path = tmp_path / "new.tif"
        write_synthetic_db_cog(new_path, np.zeros((8, 8), dtype=np.float32))

        with pytest.raises(ValueError):
            run_statistical(
                new_scene_cog=new_path,
                new_scene_id=uuid.uuid4(),
                aoi_id=uuid.uuid4(),
                baseline=b,
            )
