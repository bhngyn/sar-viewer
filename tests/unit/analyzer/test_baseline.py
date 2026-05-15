"""Baseline tests — per-pixel median + MAD + scalar summaries + COG output."""

from __future__ import annotations

import math
import subprocess
import uuid
from pathlib import Path

import numpy as np
import rasterio

from services.analyzer.src.baseline import MAD_SCALE, build_baseline

from .conftest import synthetic_db_stack, write_synthetic_db_cog


class TestBuildBaseline:
    def test_summary_scalars_match_distribution(self, tmp_path: Path) -> None:
        """12 Gaussian-noise scenes around -12 dB → AOI-mean median ≈ -12 dB."""
        n_scenes = 12
        stack = synthetic_db_stack(n_scenes, shape=(64, 64), mean_db=-12.0, std_db=2.0, seed=0)
        cog_paths = []
        scene_ids = []
        for i, frame in enumerate(stack):
            p = tmp_path / f"scene_{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            cog_paths.append(p)
            scene_ids.append(uuid.uuid4())

        out_dir = tmp_path / "baseline"
        baseline = build_baseline(uuid.uuid4(), cog_paths, scene_ids, out_dir)

        assert baseline.n_obs == 12
        assert math.isfinite(baseline.median_db)
        assert math.isfinite(baseline.mad_db)
        assert abs(baseline.median_db - (-12.0)) < 0.5, baseline.median_db
        # Theoretical MAD of Gaussian = std * (1 / 1.4826) ≈ std * 0.6745
        expected_mad = 2.0 / MAD_SCALE
        assert abs(baseline.mad_db - expected_mad) < 0.3, baseline.mad_db

    def test_outputs_are_valid_cogs(self, tmp_path: Path) -> None:
        """Output rasters pass `rio cogeo validate`."""
        stack = synthetic_db_stack(10, shape=(64, 64), seed=1)
        cog_paths = []
        for i, frame in enumerate(stack):
            p = tmp_path / f"scene_{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            cog_paths.append(p)
        scene_ids = [uuid.uuid4() for _ in cog_paths]

        out_dir = tmp_path / "baseline"
        baseline = build_baseline(uuid.uuid4(), cog_paths, scene_ids, out_dir)

        assert baseline.median_db_cog is not None
        assert baseline.mad_db_cog is not None
        median_path = Path(baseline.median_db_cog)
        mad_path = Path(baseline.mad_db_cog)
        assert median_path.exists()
        assert mad_path.exists()

        # rio-cogeo's validate is exposed as a CLI; invoking it via the
        # entry-point keeps the test honest (we want to verify the produced
        # file is actually a valid COG, not just that we wrote a TIFF).
        for path in (median_path, mad_path):
            result = subprocess.run(
                ["uv", "run", "rio", "cogeo", "validate", str(path)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, (
                f"rio cogeo validate failed for {path}:\n{result.stdout}\n{result.stderr}"
            )

    def test_per_pixel_arrays_match_baseline(self, tmp_path: Path) -> None:
        """The per-pixel COGs encode the actual median/MAD per pixel."""
        stack = synthetic_db_stack(10, shape=(32, 32), mean_db=-15.0, std_db=1.0, seed=2)
        cog_paths = []
        for i, frame in enumerate(stack):
            p = tmp_path / f"scene_{i:02d}.tif"
            write_synthetic_db_cog(p, frame)
            cog_paths.append(p)
        scene_ids = [uuid.uuid4() for _ in cog_paths]

        out_dir = tmp_path / "baseline"
        baseline = build_baseline(uuid.uuid4(), cog_paths, scene_ids, out_dir)
        assert baseline.median_db_cog is not None
        with rasterio.open(baseline.median_db_cog) as src:
            median_arr = src.read(1)

        # Compute the truth per-pixel median directly from the stack.
        truth = np.nanmedian(stack, axis=0).astype(np.float32)
        assert np.allclose(median_arr, truth, atol=1e-3)

    def test_empty_inputs_raise(self, tmp_path: Path) -> None:
        import pytest

        with pytest.raises(ValueError):
            build_baseline(uuid.uuid4(), [], [], tmp_path)

    def test_length_mismatch_raises(self, tmp_path: Path) -> None:
        import pytest

        frame = synthetic_db_stack(1, shape=(8, 8))[0]
        p = write_synthetic_db_cog(tmp_path / "s.tif", frame)
        with pytest.raises(ValueError):
            build_baseline(uuid.uuid4(), [p], [uuid.uuid4(), uuid.uuid4()], tmp_path)
