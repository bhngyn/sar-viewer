"""Unit tests for shared/storage.py (LocalCache).

Tests must run entirely offline and not touch data/. All temporary files are
created under tmp_path (pytest fixture) so nothing leaks into the repo.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from shared.storage import LocalCache

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_cache(tmp_path: Path) -> LocalCache:
    """Create a LocalCache rooted under tmp_path/cache."""
    root = tmp_path / "cache"
    root.mkdir()
    return LocalCache(root=root)


# ---------------------------------------------------------------------------
# put / get / has
# ---------------------------------------------------------------------------


def test_put_bytes_returns_path(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    key = "abc123"
    result = cache.put(key, b"hello")
    assert result.exists()
    assert result.read_bytes() == b"hello"


def test_put_path_copies_file(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    src = tmp_path / "src.bin"
    src.write_bytes(b"binary content")
    key = "filekey"
    stored = cache.put(key, src)
    assert stored.exists()
    assert stored.read_bytes() == b"binary content"


def test_put_path_copies_directory(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    src_dir = tmp_path / "src_dir"
    src_dir.mkdir()
    (src_dir / "a.txt").write_text("content a")
    (src_dir / "b.txt").write_text("content b")
    key = "dirkey"
    stored = cache.put(key, src_dir)
    assert stored.is_dir()
    assert (stored / "a.txt").read_text() == "content a"
    assert (stored / "b.txt").read_text() == "content b"


def test_put_is_idempotent(tmp_path: Path) -> None:
    """Calling put twice with the same key must not overwrite existing data."""
    cache = make_cache(tmp_path)
    key = "idempotent"
    cache.put(key, b"first")
    cache.put(key, b"second")  # should be a no-op
    result = cache.get(key)
    assert result is not None
    assert result.read_bytes() == b"first"


def test_get_returns_none_for_missing(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    assert cache.get("nosuchkey") is None


def test_get_returns_path_for_existing(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.put("existing", b"data")
    result = cache.get("existing")
    assert result is not None
    assert result.read_bytes() == b"data"


def test_has_false_for_missing(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    assert cache.has("missing") is False


def test_has_true_after_put(tmp_path: Path) -> None:
    cache = make_cache(tmp_path)
    cache.put("present", b"x")
    assert cache.has("present") is True


# ---------------------------------------------------------------------------
# Content-hash determinism
# ---------------------------------------------------------------------------


def test_hash_inputs_deterministic() -> None:
    """Same inputs always produce the same hex digest."""
    h1 = LocalCache.hash_inputs("scene-uuid", "grd")
    h2 = LocalCache.hash_inputs("scene-uuid", "grd")
    assert h1 == h2


def test_hash_inputs_different_for_different_inputs() -> None:
    h1 = LocalCache.hash_inputs("scene-a", "grd")
    h2 = LocalCache.hash_inputs("scene-b", "grd")
    assert h1 != h2


def test_hash_inputs_accepts_bytes() -> None:
    h1 = LocalCache.hash_inputs(b"raw-bytes", "str-part")
    h2 = LocalCache.hash_inputs(b"raw-bytes", "str-part")
    assert h1 == h2


def test_hash_inputs_order_matters() -> None:
    """Order of parts is significant — hash(a, b) != hash(b, a)."""
    h1 = LocalCache.hash_inputs("first", "second")
    h2 = LocalCache.hash_inputs("second", "first")
    assert h1 != h2


def test_hash_inputs_returns_hex_string() -> None:
    h = LocalCache.hash_inputs("anything")
    # SHA-256 produces 64 hex chars.
    assert len(h) == 64
    int(h, 16)  # must be valid hex; raises ValueError if not


# ---------------------------------------------------------------------------
# Shard structure
# ---------------------------------------------------------------------------


def test_shard_prefix_used(tmp_path: Path) -> None:
    """Cache entries are stored under root/<first-2-chars>/<key>."""
    cache = make_cache(tmp_path)
    key = "abcdef1234"
    stored = cache.put(key, b"data")
    assert stored.parent.name == "ab"


# ---------------------------------------------------------------------------
# Audit log — off by default
# ---------------------------------------------------------------------------


def test_audit_log_not_created_when_disabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When AUDIT_LOG_ENABLED is not set, no audit.log must be created."""
    monkeypatch.delenv("AUDIT_LOG_ENABLED", raising=False)
    cache = LocalCache(root=tmp_path / "cache", audit_log_path=tmp_path / "audit.log")
    (tmp_path / "cache").mkdir()
    cache.put("k", b"v")
    cache.get("k")
    cache.get("missing")
    assert not (tmp_path / "audit.log").exists()


def test_audit_log_created_on_get_when_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With AUDIT_LOG_ENABLED=true, get() appends a JSONL line."""
    import json

    monkeypatch.setenv("AUDIT_LOG_ENABLED", "true")
    audit_path = tmp_path / "audit.log"
    cache = LocalCache(root=tmp_path / "cache", audit_log_path=audit_path)
    (tmp_path / "cache").mkdir()

    # miss → cache_miss event
    cache.get("no-such-key")
    assert audit_path.exists()
    lines = audit_path.read_text().strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["event"] == "cache_miss"
    assert record["key"] == "no-such-key"

    # hit → cache_hit event
    cache.put("hit-key", b"data")
    cache.get("hit-key")
    lines = audit_path.read_text().strip().splitlines()
    # put() does not log; only get() logs
    hit_records = [json.loads(line) for line in lines if json.loads(line)["event"] == "cache_hit"]
    assert len(hit_records) == 1
    assert hit_records[0]["key"] == "hit-key"
