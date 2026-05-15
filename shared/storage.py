"""Local filesystem cache for raw scenes and derived COGs.

Interface is frozen for Phase 1 per phase-1-brief.md §4.2. Any change must
go through the interface-change protocol (stop and append to §8 of the brief).

Two canonical roots:
  - data/cache/    raw .SAFE downloads (managed by services/ingest)
  - data/derived/  processed COGs (managed by services/processor)

Keys are produced via hash_inputs() so identical inputs always resolve to the
same path, making all pipeline steps idempotent.

Audit log (when AUDIT_LOG_ENABLED=true):
  - cache hit  → {"event": "cache_hit",  "key": "<key>"}
  - cache miss → {"event": "cache_miss", "key": "<key>"}
  Events are appended to data/audit.log as JSONL. The log file is NOT created
  unless AUDIT_LOG_ENABLED=true; this avoids surprises for investigators who
  have not opted in to on-disk logging (CLAUDE.md §6).
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path


def _audit_enabled() -> bool:
    """Return True if the audit log feature flag is set."""
    return os.environ.get("AUDIT_LOG_ENABLED", "false").lower() in ("1", "true", "yes")


def _append_audit(event: dict[str, object], audit_log_path: Path) -> None:
    """Append one JSONL record to the audit log.

    Called only when AUDIT_LOG_ENABLED=true. The log file is created on first
    write. Each line is a valid JSON object terminated by a newline.
    """
    audit_log_path.parent.mkdir(parents=True, exist_ok=True)
    record = {"ts": datetime.now(UTC).isoformat(), **event}
    with audit_log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")


class LocalCache:
    """Content-addressed local filesystem cache.

    Parameters
    ----------
    root:
        Base directory for this cache. Files are stored under
        ``root/<first-2-chars-of-key>/<key>`` so that large caches do not
        degrade to a flat directory.
    audit_log_path:
        Optional path for the audit log. Defaults to ``root/../audit.log``
        (i.e. ``data/audit.log`` when root is ``data/cache`` or
        ``data/derived``).  Only written when AUDIT_LOG_ENABLED=true.
    """

    def __init__(
        self,
        root: Path,
        audit_log_path: Path | None = None,
    ) -> None:
        self._root = root
        # Resolve the audit log path relative to the parent of root (data/).
        self._audit_log_path: Path = (
            audit_log_path if audit_log_path is not None else root.parent / "audit.log"
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key_path(self, key: str) -> Path:
        """Return the filesystem path for a cache key.

        Two-level sharding: root/<aa>/<key> where <aa> is the first two
        characters of the key. This keeps individual directories manageable
        even with thousands of cached scenes.
        """
        shard = key[:2]
        return self._root / shard / key

    # ------------------------------------------------------------------
    # Public interface (frozen — see module docstring)
    # ------------------------------------------------------------------

    def put(self, key: str, data: bytes | Path) -> Path:
        """Store *data* under *key* and return the stored path.

        If *data* is a ``Path``, the source file (or directory tree) is copied
        into the cache. If *data* is ``bytes``, the bytes are written directly.

        The operation is idempotent: if the key already exists the existing
        path is returned without modification.
        """
        dest = self._key_path(key)
        if dest.exists():
            return dest
        dest.parent.mkdir(parents=True, exist_ok=True)

        if isinstance(data, bytes | bytearray):
            dest.write_bytes(data)
        else:
            # data is a Path — copy file or directory tree
            src = Path(data)
            if src.is_dir():
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)

        return dest

    def get(self, key: str) -> Path | None:
        """Return the path to a cached item, or ``None`` if not cached.

        Records a cache-hit or cache-miss event in the audit log when the
        feature flag is enabled.
        """
        dest = self._key_path(key)
        if dest.exists():
            if _audit_enabled():
                _append_audit({"event": "cache_hit", "key": key}, self._audit_log_path)
            return dest
        if _audit_enabled():
            _append_audit({"event": "cache_miss", "key": key}, self._audit_log_path)
        return None

    def has(self, key: str) -> bool:
        """Return ``True`` if *key* exists in the cache (no audit event)."""
        return self._key_path(key).exists()

    @staticmethod
    def hash_inputs(*parts: str | bytes) -> str:
        """Return a deterministic hex digest over the given parts.

        Combines all parts into a single SHA-256 digest so that the same
        logical inputs always produce the same cache key. This is the
        foundation for pipeline idempotency: compute the key up-front, check
        ``has()``, skip processing if cached.

        Example::

            key = LocalCache.hash_inputs(str(scene_id), "grd", aoi_id.hex)
        """
        h = hashlib.sha256()
        for part in parts:
            if isinstance(part, str):
                h.update(part.encode())
            else:
                h.update(part)
        return h.hexdigest()
