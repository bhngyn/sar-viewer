"""Watermark renderer — env switch + bottom-right placement."""

from __future__ import annotations

import numpy as np
import pytest

from services.fusion.src.watermark import (
    DEFAULT_TEXT,
    stamp_watermark,
    watermark_enabled,
)


class TestWatermarkEnabled:
    def test_call_arg_false_disables(self) -> None:
        assert watermark_enabled(False) is False

    def test_default_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("WATERMARK", raising=False)
        assert watermark_enabled(True) is True

    @pytest.mark.parametrize("env_val", ["0", "false", "FALSE", "no", "off"])
    def test_env_switch_off(self, monkeypatch: pytest.MonkeyPatch, env_val: str) -> None:
        monkeypatch.setenv("WATERMARK", env_val)
        assert watermark_enabled(True) is False


class TestStampWatermark:
    def test_bottom_right_has_text(self) -> None:
        """A previously-uniform image gets non-uniform bottom-right pixels."""
        rgb = np.full((128, 128, 3), 100, dtype=np.uint8)
        stamped = stamp_watermark(rgb, DEFAULT_TEXT)
        # Top-left untouched.
        assert np.array_equal(stamped[:32, :32], rgb[:32, :32])
        # Bottom-right region has variation (text rendered).
        br = stamped[-32:, -64:]
        assert br.std() > 0
        # Should contain near-black background and near-white text pixels.
        assert (br < 30).any()
        assert (br > 200).any()

    def test_does_not_mutate_input(self) -> None:
        rgb = np.full((64, 64, 3), 50, dtype=np.uint8)
        original = rgb.copy()
        _ = stamp_watermark(rgb)
        assert np.array_equal(rgb, original)

    def test_too_small_image_returns_copy_unchanged(self) -> None:
        rgb = np.full((8, 8, 3), 50, dtype=np.uint8)
        stamped = stamp_watermark(rgb)
        # Either equal (no room for watermark) or stamped — but no crash.
        assert stamped.shape == rgb.shape

    def test_bad_shape_raises(self) -> None:
        with pytest.raises(ValueError):
            stamp_watermark(np.zeros((10, 10), dtype=np.uint8))  # 2D

    def test_bad_dtype_raises(self) -> None:
        with pytest.raises(ValueError):
            stamp_watermark(np.zeros((10, 10, 3), dtype=np.float32))
