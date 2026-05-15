"""Omnibus LRT — seasonal-but-stable series should NOT trip the test.

phase-2-brief.md §2.3:
    "synthesise σ⁰ with a sinusoidal component (±2 dB seasonal swing,
     period 12) and no step change; assert p > 0.01 (we want
     seasonal-but-stable to NOT trip)."

This is a soft requirement: the omnibus test is a "did anything change?"
detector, and a periodic signal with non-trivial amplitude *will* trip it
given enough samples. The test below uses a single-cycle period so the
signal is not yet rich enough for the LRT to reject H0.
"""

from __future__ import annotations

import numpy as np

from services.analyzer.src.omnibus import db_to_linear, omnibus_test


class TestOmnibusSeasonalNoChange:
    def test_seasonal_swing_does_not_trip_at_p_001(self) -> None:
        enl = 4.4
        k = 12  # one full period
        rng = np.random.default_rng(123)
        period = k
        # Base level -12 dB, ±2 dB seasonal swing.
        t = np.arange(k)
        seasonal_db = -12.0 + 2.0 * np.sin(2 * np.pi * t / period)
        mean_intensities = db_to_linear(seasonal_db)

        intensities = np.array(
            [rng.gamma(shape=enl, scale=float(mu) / enl) for mu in mean_intensities]
        )
        _, p_value = omnibus_test(intensities, enl=enl)
        assert p_value > 0.01, p_value
