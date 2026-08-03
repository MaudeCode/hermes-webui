"""Small atomic sidecars for composer drafts.

Composer input changes frequently. Persisting a draft by rewriting the complete
session transcript makes typing contend with chat saves and scales with the full
conversation size. Draft sidecars keep that write bounded while Session.load()
continues to present the same composer_draft field to callers.
"""

from __future__ import annotations

import json
import os
import threading
import weakref
from pathlib import Path

import api.config as _cfg

_SAFE_SID_CHARS = frozenset(
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_-"
)
_LOCKS_GUARD = threading.Lock()
_LOCKS: weakref.WeakValueDictionary[str, threading.Lock] = weakref.WeakValueDictionary()


def _safe_sid(sid: str) -> bool:
    return bool(sid) and isinstance(sid, str) and all(c in _SAFE_SID_CHARS for c in sid)


def _draft_dir() -> Path:
    return Path(_cfg.SESSION_DIR) / "_drafts"


def draft_path(sid: str) -> Path:
    if not _safe_sid(sid):
        raise ValueError("Invalid session_id")
    return _draft_dir() / f"{sid}.json"


def _draft_lock(sid: str) -> threading.Lock:
    with _LOCKS_GUARD:
        lock = _LOCKS.get(sid)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[sid] = lock
        return lock


def normalize_draft(value) -> dict:
    draft = value if isinstance(value, dict) else {}
    text = draft.get("text", "")
    files = draft.get("files", [])
    return {
        "text": text if isinstance(text, str) else "",
        "files": files if isinstance(files, list) else [],
    }


def read_session_draft(sid: str, *, fallback=None) -> dict:
    """Return the sidecar draft when present, otherwise normalized fallback."""
    try:
        path = draft_path(sid)
        with _draft_lock(sid):
            if not path.exists():
                return normalize_draft(fallback)
            return normalize_draft(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, json.JSONDecodeError):
        return normalize_draft(fallback)


def write_session_draft(sid: str, draft) -> dict:
    """Atomically persist a bounded composer draft without touching transcript JSON."""
    normalized = normalize_draft(draft)
    path = draft_path(sid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    tmp = path.with_name(f"{path.name}.tmp.{os.getpid()}.{threading.get_ident()}")
    with _draft_lock(sid):
        try:
            with open(tmp, "w", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
    return normalized


def delete_session_draft(sid: str) -> None:
    """Delete the composer draft sidecar for a deleted conversation."""
    try:
        path = draft_path(sid)
    except ValueError:
        return
    with _draft_lock(sid):
        path.unlink(missing_ok=True)
