"""Pytest configuration for ingest integration tests.

All tests use VCR cassettes — no live network calls are made in CI.
The cassettes are pre-recorded YAML files in ``cassettes/``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# Directory that holds VCR cassette YAML files.
CASSETTES_DIR = Path(__file__).parent / "cassettes"


@pytest.fixture(autouse=True)
def _no_live_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Guard: ensure tests cannot accidentally make live network calls.

    When vcrpy is active it intercepts httpx calls.  This fixture acts as a
    belt-and-suspenders check that the CDSE env vars are not set (which would
    cause real calls if vcrpy were misconfigured).
    """
    # Remove real credentials so that any accidental live call fails loudly.
    monkeypatch.delenv("CDSE_CLIENT_ID", raising=False)
    monkeypatch.delenv("CDSE_CLIENT_SECRET", raising=False)
    # Keep SH_INSTANCE_ID controlled to a test value.
    monkeypatch.setenv("SH_INSTANCE_ID", "test-instance-id")
