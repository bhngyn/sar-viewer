"""Colormap helpers — gray/viridis ok, jet banned."""

from __future__ import annotations

import numpy as np
import pytest

from services.fusion.src.colormap import apply_colormap, db_to_normalized


class TestApplyColormap:
    def test_gray_maps_zero_to_zero_and_one_to_white(self) -> None:
        arr = np.array([[0.0, 1.0]], dtype=np.float32)
        rgb = apply_colormap(arr, "gray")
        assert rgb.shape == (1, 2, 3)
        assert rgb.dtype == np.uint8
        # gray cmap: 0 → black, 1 → white.
        assert rgb[0, 0].tolist() == [0, 0, 0]
        assert rgb[0, 1].tolist() == [255, 255, 255]

    def test_viridis_returns_three_channels(self) -> None:
        arr = np.linspace(0.0, 1.0, 16, dtype=np.float32).reshape(4, 4)
        rgb = apply_colormap(arr, "viridis")
        assert rgb.shape == (4, 4, 3)
        # viridis at 0 is dark purple; at 1 is yellow.  Spot-check sanity.
        assert rgb[0, 0, 2] > 0  # purple has blue
        assert rgb[-1, -1, 0] > 200  # yellow has high red

    def test_jet_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="banned"):
            apply_colormap(np.zeros((2, 2), dtype=np.float32), "jet")

    def test_rainbow_also_banned(self) -> None:
        with pytest.raises(ValueError, match="banned"):
            apply_colormap(np.zeros((2, 2), dtype=np.float32), "rainbow")

    def test_unsupported_raises(self) -> None:
        with pytest.raises(ValueError, match="unsupported"):
            apply_colormap(np.zeros((2, 2), dtype=np.float32), "plasma")


class TestDbToNormalized:
    def test_in_range_scales_linearly(self) -> None:
        arr = np.array([[-25.0, -12.5, 0.0]], dtype=np.float32)
        norm = db_to_normalized(arr, low_db=-25.0, high_db=0.0)
        assert np.isclose(norm[0, 0], 0.0)
        assert np.isclose(norm[0, 1], 0.5)
        assert np.isclose(norm[0, 2], 1.0)

    def test_clamps_outliers(self) -> None:
        arr = np.array([[-100.0, 100.0]], dtype=np.float32)
        norm = db_to_normalized(arr)
        assert norm[0, 0] == 0.0
        assert norm[0, 1] == 1.0

    def test_nan_becomes_floor(self) -> None:
        arr = np.array([[np.nan, -25.0]], dtype=np.float32)
        norm = db_to_normalized(arr, low_db=-25.0, high_db=0.0)
        assert norm[0, 0] == 0.0
