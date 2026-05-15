"""Analyzer package.

Public surface used by services/worker/src/tasks.py and downstream callers:

    from services.analyzer.src.baseline import build_baseline
    from services.analyzer.src.detect import detect_anomalies
    from services.analyzer.src.rgb import build_multitemporal_rgb
"""

from services.analyzer.src.baseline import build_baseline
from services.analyzer.src.detect import detect_anomalies
from services.analyzer.src.rgb import build_multitemporal_rgb

__all__ = ["build_baseline", "build_multitemporal_rgb", "detect_anomalies"]
