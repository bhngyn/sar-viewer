"""Unit tests for pipeline.py — GRD and SLC pipeline entry points.

Tests mock the subprocess boundary (GPT invocation) and the COG conversion
step so that SNAP does not need to be installed in CI.  We verify:
- Correct GPT command-line argument construction.
- Correct handling of missing SAFE directories (FileNotFoundError).
- The pipeline calls convert_to_cog after GPT succeeds.
- MAX_SLC_PARALLEL is set to the expected value.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# We import the module under test after patching, to avoid import-time side effects.
from services.processor.src.pipeline import (
    MAX_SLC_PARALLEL,
    process_grd,
    process_slc_pair,
)

# ---------------------------------------------------------------------------
# Constant checks
# ---------------------------------------------------------------------------


def test_max_slc_parallel_is_two() -> None:
    """Per CLAUDE.md §5 Phase 1 Agent B, SLC jobs capped at 2 in parallel."""
    assert MAX_SLC_PARALLEL == 2


# ---------------------------------------------------------------------------
# process_grd tests
# ---------------------------------------------------------------------------


class TestProcessGrd:
    def test_raises_if_safe_dir_missing(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError, match="SAFE directory not found"):
            process_grd(tmp_path / "nonexistent.SAFE", tmp_path / "out")

    def test_calls_gpt_with_source_and_target(self, tmp_path: Path) -> None:
        safe_dir = tmp_path / "S1A_IW_GRD_1SDV_20240101T000000.SAFE"
        safe_dir.mkdir()
        out_dir = tmp_path / "out"

        fake_snap_out = MagicMock(spec=Path)
        fake_snap_out.exists.return_value = True
        fake_cog_out = tmp_path / "out" / "S1A_IW_GRD_1SDV_20240101T000000_GRD_sigma0_dB.tif"

        with (
            patch("subprocess.run") as mock_run,
            patch("services.processor.src.pipeline.convert_to_cog") as mock_cog,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")

            # Simulate GPT creating the snap_out file
            def side_effect_run(cmd: list[str], **kwargs: object) -> MagicMock:
                # The snap_out path is the -Ptarget= value
                target_arg = next(a for a in cmd if a.startswith("-Ptarget="))
                snap_out_path = Path(target_arg.split("=", 1)[1])
                snap_out_path.parent.mkdir(parents=True, exist_ok=True)
                snap_out_path.write_bytes(b"fake")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect_run
            mock_cog.return_value = fake_cog_out

            result = process_grd(safe_dir, out_dir)

        # GPT was called
        assert mock_run.called
        run_call = mock_run.call_args
        cmd = run_call[0][0]
        assert cmd[0] == "gpt"
        # Source is the SAFE directory
        source_args = [a for a in cmd if a.startswith("-Psource0=")]
        assert len(source_args) == 1
        assert source_args[0].endswith(str(safe_dir))
        # convert_to_cog was called
        assert mock_cog.called
        # Result is what convert_to_cog returned
        assert result == fake_cog_out

    def test_out_dir_created_automatically(self, tmp_path: Path) -> None:
        safe_dir = tmp_path / "test.SAFE"
        safe_dir.mkdir()
        out_dir = tmp_path / "deeply" / "nested" / "out"

        with (
            patch("subprocess.run") as mock_run,
            patch("services.processor.src.pipeline.convert_to_cog") as mock_cog,
        ):

            def side_effect_run(cmd: list[str], **kwargs: object) -> MagicMock:
                target_arg = next(a for a in cmd if a.startswith("-Ptarget="))
                snap_out_path = Path(target_arg.split("=", 1)[1])
                snap_out_path.parent.mkdir(parents=True, exist_ok=True)
                snap_out_path.write_bytes(b"fake")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect_run
            mock_cog.return_value = out_dir / "result.tif"

            process_grd(safe_dir, out_dir)

        assert out_dir.exists()

    def test_gpt_failure_raises(self, tmp_path: Path) -> None:
        safe_dir = tmp_path / "test.SAFE"
        safe_dir.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["gpt"], stderr="SNAP error"
            )
            with pytest.raises(subprocess.CalledProcessError):
                process_grd(safe_dir, tmp_path / "out")

    def test_output_name_contains_grd_sigma0_db(self, tmp_path: Path) -> None:
        """Output file name must include the product type and parameter."""
        safe_dir = tmp_path / "S1A_IW_GRD.SAFE"
        safe_dir.mkdir()
        captured_snap_out: list[Path] = []

        with (
            patch("subprocess.run") as mock_run,
            patch("services.processor.src.pipeline.convert_to_cog") as mock_cog,
        ):

            def side_effect_run(cmd: list[str], **kwargs: object) -> MagicMock:
                target_arg = next(a for a in cmd if a.startswith("-Ptarget="))
                snap_out_path = Path(target_arg.split("=", 1)[1])
                captured_snap_out.append(snap_out_path)
                snap_out_path.parent.mkdir(parents=True, exist_ok=True)
                snap_out_path.write_bytes(b"fake")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect_run
            mock_cog.return_value = tmp_path / "out" / "result.tif"

            process_grd(safe_dir, tmp_path / "out")

        assert captured_snap_out, "GPT was never called"
        snap_name = captured_snap_out[0].name
        assert "GRD" in snap_name
        assert "sigma0" in snap_name
        assert "dB" in snap_name


# ---------------------------------------------------------------------------
# process_slc_pair tests
# ---------------------------------------------------------------------------


class TestProcessSlcPair:
    def test_raises_if_safe_a_missing(self, tmp_path: Path) -> None:
        safe_b = tmp_path / "secondary.SAFE"
        safe_b.mkdir()
        with pytest.raises(FileNotFoundError, match="SAFE directory not found"):
            process_slc_pair(tmp_path / "nonexistent.SAFE", safe_b, tmp_path / "out")

    def test_raises_if_safe_b_missing(self, tmp_path: Path) -> None:
        safe_a = tmp_path / "reference.SAFE"
        safe_a.mkdir()
        with pytest.raises(FileNotFoundError, match="SAFE directory not found"):
            process_slc_pair(safe_a, tmp_path / "nonexistent.SAFE", tmp_path / "out")

    def test_calls_gpt_with_two_sources(self, tmp_path: Path) -> None:
        safe_a = tmp_path / "ref.SAFE"
        safe_b = tmp_path / "sec.SAFE"
        safe_a.mkdir()
        safe_b.mkdir()
        out_dir = tmp_path / "out"

        with (
            patch("subprocess.run") as mock_run,
            patch("services.processor.src.pipeline.convert_to_cog") as mock_cog,
        ):

            def side_effect_run(cmd: list[str], **kwargs: object) -> MagicMock:
                target_arg = next(a for a in cmd if a.startswith("-Ptarget="))
                snap_out_path = Path(target_arg.split("=", 1)[1])
                snap_out_path.parent.mkdir(parents=True, exist_ok=True)
                snap_out_path.write_bytes(b"fake")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect_run
            mock_cog.return_value = out_dir / "coherence.tif"

            process_slc_pair(safe_a, safe_b, out_dir)

        cmd = mock_run.call_args[0][0]
        source0_args = [a for a in cmd if a.startswith("-Psource0=")]
        source1_args = [a for a in cmd if a.startswith("-Psource1=")]
        assert len(source0_args) == 1
        assert len(source1_args) == 1
        assert source0_args[0].endswith(str(safe_a))
        assert source1_args[0].endswith(str(safe_b))

    def test_output_name_contains_coherence(self, tmp_path: Path) -> None:
        safe_a = tmp_path / "ref.SAFE"
        safe_b = tmp_path / "sec.SAFE"
        safe_a.mkdir()
        safe_b.mkdir()
        captured_snap_out: list[Path] = []

        with (
            patch("subprocess.run") as mock_run,
            patch("services.processor.src.pipeline.convert_to_cog") as mock_cog,
        ):

            def side_effect_run(cmd: list[str], **kwargs: object) -> MagicMock:
                target_arg = next(a for a in cmd if a.startswith("-Ptarget="))
                snap_out_path = Path(target_arg.split("=", 1)[1])
                captured_snap_out.append(snap_out_path)
                snap_out_path.parent.mkdir(parents=True, exist_ok=True)
                snap_out_path.write_bytes(b"fake")
                return MagicMock(returncode=0, stdout="", stderr="")

            mock_run.side_effect = side_effect_run
            mock_cog.return_value = tmp_path / "out" / "coherence.tif"

            process_slc_pair(safe_a, safe_b, tmp_path / "out")

        assert "coherence" in captured_snap_out[0].name

    def test_gpt_failure_raises(self, tmp_path: Path) -> None:
        safe_a = tmp_path / "ref.SAFE"
        safe_b = tmp_path / "sec.SAFE"
        safe_a.mkdir()
        safe_b.mkdir()

        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = subprocess.CalledProcessError(
                returncode=1, cmd=["gpt"], stderr="SNAP error"
            )
            with pytest.raises(subprocess.CalledProcessError):
                process_slc_pair(safe_a, safe_b, tmp_path / "out")
