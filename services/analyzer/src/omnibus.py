"""Conradsen omnibus likelihood-ratio test for SAR intensity time series.

Reference
---------
Conradsen, K., Nielsen, A. A., & Skriver, H. (2016). "Determining the points
of change in time series of polarimetric SAR data." IEEE TGRS 54(5).

Adapted for single-channel intensity (no polarimetric covariance matrix).
The GEE community tutorial linked from phase-2-brief.md §2.1 was used as the
algorithmic crib — this is a *numpy* port (no Earth Engine dependency).

Model
-----
Each pixel intensity ``I_t`` at time ``t`` is assumed Gamma-distributed with
shape parameter ``n`` (the equivalent number of looks; ``enl`` arg, default
4.4 for S1 GRDH) and scale that may vary over time. The omnibus null is
"the scale parameter is constant across all k observations".

The likelihood-ratio statistic for k observations reduces to::

    Q = k^(k*n) * prod_i(I_i)^n / (sum_i I_i)^(k*n)

Under H0, ``-2 * ln(Q)`` is approximately χ² distributed with ``k - 1``
degrees of freedom (Wilks). The "approximately" matters at small k —
phase-2-brief.md §2.3 budgets up to 5x the nominal false-positive rate.

The Gamma scale parameter is the per-look mean. We pass ``intensities`` as
**linear σ⁰** (NOT dB), because dB values can be negative whereas the
Gamma distribution requires positives. Callers convert dB → linear via
``10**(db/10)``.
"""

from __future__ import annotations

import numpy as np
import structlog
from scipy import stats

log: structlog.BoundLogger = structlog.get_logger(__name__)

# Equivalent number of looks for Sentinel-1 GRDH IW after multilooking.
# Source: ESA Sentinel-1 product spec. The pyroSAR GRD pipeline applies a
# 7x7 Refined Lee speckle filter which further increases ENL; 4.4 is the
# pre-filter conservative number.  Tests may override this.
DEFAULT_ENL_GRDH: float = 4.4


def omnibus_test(
    intensities_linear: np.ndarray,
    enl: float = DEFAULT_ENL_GRDH,
) -> tuple[float, float]:
    """Return ``(log_Q, p_value)`` for the omnibus LRT.

    Parameters
    ----------
    intensities_linear:
        1-D numpy array of strictly-positive intensity values (linear σ⁰,
        not dB). Length k ≥ 2.
    enl:
        Equivalent number of looks. Default ``DEFAULT_ENL_GRDH``.

    Returns
    -------
    (log_Q, p_value)
        ``log_Q`` is the natural log of the LRT statistic; ``p_value`` is
        the right-tail χ²(k-1) probability of observing
        ``-2 * log_Q`` or larger under H0 (no change).

    Raises
    ------
    ValueError
        If ``intensities_linear`` is shorter than 2 elements, contains
        non-positive values, or is non-finite.
    """
    intensities = np.asarray(intensities_linear, dtype=np.float64)
    if intensities.ndim != 1:
        raise ValueError(f"intensities must be 1-D, got shape {intensities.shape}")
    k = intensities.shape[0]
    if k < 2:
        raise ValueError(f"omnibus needs at least 2 observations, got {k}")
    if not np.all(np.isfinite(intensities)):
        raise ValueError("intensities contain non-finite values")
    if np.any(intensities <= 0.0):
        raise ValueError("intensities must be strictly positive (linear σ⁰)")
    if enl <= 0.0:
        raise ValueError(f"enl must be positive, got {enl}")

    n = float(enl)
    sum_i = float(np.sum(intensities))
    sum_log = float(np.sum(np.log(intensities)))

    # log(Q) = k*n*log(k) + n*sum(log I_i) - k*n*log(sum I_i)
    log_q = k * n * np.log(k) + n * sum_log - k * n * np.log(sum_i)

    # ``log_q`` should be ≤ 0 under any sample because the LRT compares a
    # restricted vs. unrestricted likelihood; numerical noise can put it
    # epsilon > 0, which we clip.
    chi2_stat = -2.0 * min(log_q, 0.0)

    p_value: float = float(stats.chi2.sf(chi2_stat, df=k - 1))
    return float(log_q), p_value


def db_to_linear(db: np.ndarray | float) -> np.ndarray:
    """Convert σ⁰ in dB to linear units.  ``10 ** (db / 10)``.

    Always returns a numpy array (even for scalar input) so downstream
    callers can rely on a consistent type.
    """
    return np.asarray(np.power(10.0, np.asarray(db, dtype=np.float64) / 10.0))
