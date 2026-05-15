"""Anomaly detection package.

Public surface used by services/worker/src/tasks.py:

    from services.analyzer.src.baseline import build_baseline
    from services.analyzer.src.detect import detect_anomalies
"""

from services.analyzer.src.baseline import build_baseline
from services.analyzer.src.detect import detect_anomalies

__all__ = ["build_baseline", "detect_anomalies"]
