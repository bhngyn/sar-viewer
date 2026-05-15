"""Omnibus LRT — step change in intensity must be detected at alpha=1e-4."""

from __future__ import annotations

import numpy as np

from services.analyzer.src.omnibus import db_to_linear, omnibus_test


class TestOmnibusStepChange:
    def test_step_change_six_db_at_t15(self) -> None:
        """30-sample series with +6 dB step at t=15 → p < 1e-4."""
        enl = 4.4
        # Pre-change ~ -12 dB linear intensity.
        rng = np.random.default_rng(0)
        pre_mean = db_to_linear(-12.0)  # linear σ⁰
        post_mean = db_to_linear(-6.0)  # +6 dB → ~4x intensity

        pre_intensities = rng.gamma(shape=enl, scale=float(pre_mean) / enl, size=15)
        post_intensities = rng.gamma(shape=enl, scale=float(post_mean) / enl, size=15)
        series = np.concatenate([pre_intensities, post_intensities])

        _, p_value = omnibus_test(series, enl=enl)
        assert p_value < 1e-4, p_value

    def test_log_q_non_positive(self) -> None:
        """``log Q`` is always ≤ 0 for valid intensities (numerical sanity)."""
        enl = 4.4
        rng = np.random.default_rng(1)
        # A perfectly stable series.
        series = rng.gamma(shape=enl, scale=float(db_to_linear(-12.0)) / enl, size=30)
        log_q, p = omnibus_test(series, enl=enl)
        assert log_q <= 1e-9, log_q
        assert 0.0 <= p <= 1.0, p
