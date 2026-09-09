"""Crash-safe WebUI turn journal helpers.

The journal is deliberately tiny: one JSONL file per session, append-only events,
and read helpers that tolerate malformed lines. Recovery and repair can then
reason about submitted turns without depending on in-memory stream state.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable

try:  # pragma: no cover - fcntl is unavailable on Windows.
    import fcntl as _fcntl
except ImportError:  # pragma: no cover
    _fcntl = None

TURN_JOURNAL_DIR_NAME = "_turn_journal"
_TERMINAL_EVENTS = {"completed", "interrupted"}
_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_STREAM_TURN_CACHE_MAX = 4096
_TURN_JOURNAL_RETENTION_DAYS_ENV = "HERMES_WEBUI_TURN_JOURNAL_RETENTION_DAYS"
_TURN_JOURNAL_DEFAULT_RETENTION_DAYS = 14.0
_STREAM_TURN_CACHE: "OrderedDict[tuple[str, str], str]" = OrderedDict()
_STREAM_TURN_CACHE_LOCK = threading.Lock()


def _default_session_dir() -> Path:
    from api.models import SESSION_DIR

    return Path(SESSION_DIR)


def _journal_path(session_id: str, session_dir: Path | None = None) -> Path:
    sid = str(session_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or not _SESSION_ID_RE.fullmatch(sid):
        raise ValueError("invalid session_id")
    root = Path(session_dir) if session_dir is not None else _default_session_dir()
    return root / TURN_JOURNAL_DIR_NAME / f"{sid}~{os.getpid()}.jsonl"


def _make_turn_id() -> str:
    return f"{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}-{uuid.uuid4().hex[:12]}"


@contextmanager
def _journal_file_lock(file_obj):
    """Serialize multi-process journal writes when advisory locks exist.

    ``O_APPEND`` keeps normal same-process appends simple, but a long JSONL event
    can exceed POSIX's small atomic-write boundary.  On Unix, take an advisory
    lock around the single event write+fsync so two WebUI worker processes cannot
    interleave large submitted-message payloads into corrupted JSONL.  Platforms
    without ``fcntl`` keep the previous best-effort append behavior.
    """
    if _fcntl is None:
        yield
        return
    _fcntl.flock(file_obj.fileno(), _fcntl.LOCK_EX)
    try:
        yield
    finally:
        _fcntl.flock(file_obj.fileno(), _fcntl.LOCK_UN)


def append_turn_journal_event(
    session_id: str,
    event: dict,
    *,
    session_dir: Path | None = None,
) -> dict:
    """Append one turn journal event and fsync it before returning.

    The returned event is the exact payload written, with default ``version``,
    ``session_id``, ``turn_id``, and ``created_at`` fields filled in.
    """
    if not isinstance(event, dict):
        raise TypeError("event must be a dict")
    event_name = str(event.get("event") or "").strip()
    if not event_name:
        raise ValueError("event is required")
    payload = dict(event)
    payload.setdefault("version", 1)
    payload["session_id"] = str(session_id)
    payload.setdefault("turn_id", _make_turn_id())
    payload.setdefault("created_at", time.time())
    if event_name in _TERMINAL_EVENTS:
        payload.setdefault("terminal", True)

    path = _journal_path(session_id, session_dir=session_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    # A directory entry only needs to be made durable when this process creates
    # its pid-scoped shard. Re-fsyncing the directory after every append adds no
    # durability (the entry already exists) and was a measurable per-event tax.
    created_shard = not path.exists()
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    fd = os.open(path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "a", encoding="utf-8") as fh:
        with _journal_file_lock(fh):
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    o_directory = getattr(os, "O_DIRECTORY", None)
    if created_shard and o_directory is not None:
        try:
            dir_fd = os.open(path.parent, o_directory)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
    stream_id = str(payload.get("stream_id") or "").strip()
    turn_id = str(payload.get("turn_id") or "").strip()
    if stream_id and turn_id:
        key = (str(path.parent.parent.resolve()), f"{session_id}:{stream_id}")
        with _STREAM_TURN_CACHE_LOCK:
            _STREAM_TURN_CACHE[key] = turn_id
            _STREAM_TURN_CACHE.move_to_end(key)
            while len(_STREAM_TURN_CACHE) > _STREAM_TURN_CACHE_MAX:
                _STREAM_TURN_CACHE.popitem(last=False)
    return payload


def read_turn_journal(session_id: str, *, session_dir: Path | None = None) -> dict:
    """Read a session journal, merging all pid-scoped shards and returning valid events plus malformed lines."""
    sid = str(session_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or not _SESSION_ID_RE.fullmatch(sid):
        raise ValueError("invalid session_id")
    root = Path(session_dir) if session_dir is not None else _default_session_dir()
    journal_dir = root / TURN_JOURNAL_DIR_NAME
    events: list[dict] = []
    malformed: list[dict] = []
    # Collect pid-scoped shards ({sid}~{pid}.jsonl) plus legacy ({sid}.jsonl).
    # The ~ separator cannot appear in session IDs (_SESSION_ID_RE allows only [A-Za-z0-9_.-]),
    # so the glob is unambiguous even for dotted-numeric session IDs like "sess.123".
    shards: list[Path] = list(journal_dir.glob(f"{sid}~*.jsonl")) if journal_dir.exists() else []
    legacy = journal_dir / f"{sid}.jsonl"
    if legacy.exists():
        shards.append(legacy)
    if not shards:
        return {"session_id": str(session_id), "events": [], "malformed": []}
    for shard in shards:
        try:
            lines = shard.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            continue
        for line_no, raw in enumerate(lines, start=1):
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                malformed.append({"line": line_no, "raw": raw, "shard": shard.name})
                continue
            if isinstance(event, dict):
                events.append(event)
            else:
                malformed.append({"line": line_no, "raw": raw, "shard": shard.name})
    def _safe_ts(e):
        try:
            return float(e.get("created_at") or 0)
        except (ValueError, TypeError):
            return 0.0
    events.sort(key=_safe_ts)
    return {"session_id": str(session_id), "events": events, "malformed": malformed}


def derive_turn_journal_states(events: Iterable[dict]) -> tuple[dict[str, dict], list[dict]]:
    '''Return the latest event per ``turn_id`` and any terminal-collision entries.

    The first element is the latest event per turn_id (same overwrite-by-timestamp
    behaviour as before).  The second element is a list of collision records, one
    per turn_id that had more than one terminal event.  Each collision record
    contains ``turn_id`` and the ``events`` list (in ascending created_at order).

    A collision means the same logical turn recorded both ``completed`` and
    ``interrupted`` terminal events -- the derived state still picks the latest
    by timestamp, but callers can now detect and audit the double-terminal
    situation explicitly rather than having it silently collapse.
    '''
    states: dict[str, dict] = {}
    # Collect all terminal events per turn_id to detect collisions
    terminal_events: dict[str, list[dict]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        turn_id = str(event.get('turn_id') or '').strip()
        if not turn_id:
            continue
        # Track terminal events for collision detection
        if is_terminal_turn_event(event):
            terminal_events.setdefault(turn_id, []).append(event)
        # Existing latest-by-timestamp derivation
        previous = states.get(turn_id)
        if previous is None or float(event.get('created_at') or 0) >= float(previous.get('created_at') or 0):
            states[turn_id] = event

    # Build collision list: turn_ids with more than one terminal event
    collisions = [
        {'turn_id': tid, 'events': sorted(evts, key=lambda e: float(e.get('created_at') or 0))}
        for tid, evts in terminal_events.items()
        if len(evts) > 1
    ]
    return states, collisions

def _latest_turn_id_for_stream(events: Iterable[dict], stream_id: str) -> str | None:
    stream = str(stream_id or "").strip()
    if not stream:
        return None
    latest: str | None = None
    for event in events:
        if not isinstance(event, dict):
            continue
        if str(event.get("stream_id") or "") != stream:
            continue
        turn_id = str(event.get("turn_id") or "").strip()
        if turn_id:
            latest = turn_id
    return latest


def append_turn_journal_event_for_stream(
    session_id: str,
    stream_id: str,
    event: dict,
    *,
    session_dir: Path | None = None,
) -> dict:
    """Append a lifecycle event for the turn associated with ``stream_id``."""
    payload = dict(event)
    payload["stream_id"] = str(stream_id)
    if not payload.get("turn_id"):
        root = Path(session_dir) if session_dir is not None else _default_session_dir()
        key = (str(root.resolve()), f"{session_id}:{stream_id}")
        with _STREAM_TURN_CACHE_LOCK:
            turn_id = _STREAM_TURN_CACHE.get(key)
            if turn_id:
                _STREAM_TURN_CACHE.move_to_end(key)
        if not turn_id:
            # Restart / cache-eviction fallback: scan durable history once, then
            # append_turn_journal_event repopulates the bounded process cache.
            journal = read_turn_journal(session_id, session_dir=session_dir)
            turn_id = _latest_turn_id_for_stream(journal.get("events") or [], stream_id)
        if turn_id:
            payload["turn_id"] = turn_id
    return append_turn_journal_event(session_id, payload, session_dir=session_dir)


def iter_turn_journal_session_ids(session_dir: Path) -> list[str]:
    journal_dir = Path(session_dir) / TURN_JOURNAL_DIR_NAME
    if not journal_dir.exists():
        return []
    session_ids: set[str] = set()
    for path in journal_dir.glob("*.jsonl"):
        if not path.is_file():
            continue
        stem = path.stem  # e.g. "sid-1~12345" or "sid-1"
        tilde = stem.find("~")
        if tilde > 0:
            session_ids.add(stem[:tilde])
        else:
            session_ids.add(stem)
    return sorted(session_ids)


def delete_turn_journal(session_id: str, *, session_dir: Path | None = None) -> int:
    """Remove every turn-journal shard for ``session_id``.

    Deletes both the pid-scoped shards (``{sid}~{pid}.jsonl``) written by
    :func:`append_turn_journal_event` and the legacy single-file form
    (``{sid}.jsonl``) that :func:`read_turn_journal` still merges. Returns the
    number of files removed. Invalid/empty ids and a missing journal directory
    are treated as a no-op so callers can invoke this unconditionally on delete.
    """
    sid = str(session_id or "").strip()
    # Reject "."/".." for parity with delete_run_journal — the regex permits
    # dots, and a traversal id has no legitimate use here.
    if sid in (".", "..") or not sid or "/" in sid or "\\" in sid or not _SESSION_ID_RE.fullmatch(sid):
        return 0
    root = Path(session_dir) if session_dir is not None else _default_session_dir()
    root_key = str(root.resolve())
    with _STREAM_TURN_CACHE_LOCK:
        for key in [
            key for key in _STREAM_TURN_CACHE
            if key[0] == root_key and key[1].startswith(f"{sid}:")
        ]:
            _STREAM_TURN_CACHE.pop(key, None)
    journal_dir = root / TURN_JOURNAL_DIR_NAME
    if not journal_dir.exists():
        return 0
    removed = 0
    shards = list(journal_dir.glob(f"{sid}~*.jsonl"))
    legacy = journal_dir / f"{sid}.jsonl"
    if legacy.exists():
        shards.append(legacy)
    for shard in shards:
        try:
            shard.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        except OSError:
            # Best-effort cleanup; the caller logs the overall delete outcome.
            pass
    return removed


def _retention_seconds_from_env() -> float:
    raw = os.environ.get(
        _TURN_JOURNAL_RETENTION_DAYS_ENV,
        str(_TURN_JOURNAL_DEFAULT_RETENTION_DAYS),
    )
    try:
        days = float(raw)
    except (TypeError, ValueError):
        days = _TURN_JOURNAL_DEFAULT_RETENTION_DAYS
    return max(0.0, days) * 24 * 60 * 60


def _session_sidecar_is_intact(session_id: str, root: Path) -> bool:
    """True when the session's live sidecar exists and parses as a mapping.

    ``docs/rfcs/turn-journal.md`` gates pruning on sidecar/index recovery having
    no findings. Running the full audit from here would invert the dependency —
    ``api.session_recovery`` imports this module — and rescan the whole state
    directory on every pass. So check the one condition that makes the journal
    the sole surviving evidence for a session: a ``{sid}.json`` that is absent
    or unreadable means the session is awaiting repair, and its journal is what
    the repair would be built from.
    """
    try:
        payload = json.loads((root / f"{session_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    return isinstance(payload, dict)


def _session_is_prunable(session_id: str, root: Path) -> bool:
    """True only on positive evidence that nothing still needs this journal.

    Fails closed on every uncertainty, because each one is a case the recovery
    audit exists to catch:

    * an id ``read_turn_journal`` rejects, or a shard it cannot read;
    * a malformed line — a crash-torn event is exactly the evidence recovery
      flags for manual review, and deleting it destroys the only record;
    * a nonterminal turn, which the startup audit still reports as pending;
    * a missing or unparseable live sidecar, i.e. a session awaiting repair.
    """
    try:
        journal = read_turn_journal(session_id, session_dir=root)
    except (ValueError, OSError):
        return False
    if journal.get("malformed"):
        return False
    states, _ = derive_turn_journal_states(journal.get("events") or [])
    if any(not is_terminal_turn_event(event) for event in states.values()):
        return False
    return _session_sidecar_is_intact(session_id, root)


def _release_expired_shard(path: Path, expected_mtime: float, own_suffix: str) -> bool:
    """Reclaim one expired shard's bytes. Returns ``True`` when it did.

    A shard this process owns is truncated, not unlinked.
    :func:`append_turn_journal_event` reopens the path on every call, so
    unlinking races an appender that has already opened the old inode: its event
    would land in an unlinked file and vanish. Truncating under the same
    advisory lock the appender takes cannot lose a write — an ``O_APPEND``
    writer that was blocked on the lock simply resumes at offset 0 — and the
    mtime recheck under that lock drops the whole attempt if an append landed
    between the scan and here. The bytes are reclaimed either way; only the
    now-empty inode stays, and ``delete_turn_journal`` releases that with the
    session.

    Shards carrying another pid have no appender in this process, so they are
    unlinked outright.
    """
    if not path.name.endswith(own_suffix):
        try:
            path.unlink()
            return True
        except OSError:
            return False
    try:
        with open(path, "r+b") as fh:
            with _journal_file_lock(fh):
                if os.fstat(fh.fileno()).st_mtime != expected_mtime:
                    return False
                fh.truncate(0)
    except OSError:
        return False
    return True


def prune_stale_turn_journals(
    *,
    session_dir: Path | None = None,
    now: float | None = None,
    retention_seconds: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Reclaim settled turn-journal shards nothing has appended to in the window.

    The run journal's retention keys off a terminal run event; the turn journal
    has no such per-file marker, because a shard stays open for as long as its
    session might submit another turn. So a session's shards are expired only
    when both hold:

    * no shard of that session has been written to inside the retention window;
    * :func:`_session_is_prunable` finds positive evidence the session is
      settled — judged on the *merged* journal, because a turn submitted under
      one pid can be completed under another and half a turn read alone looks
      pending.

    Expired shards from dead processes are deleted; the running process's own
    shard is truncated in place under the appender's lock, so a long-running
    server reclaims its own storage without racing a write. See
    :func:`_release_expired_shard`.

    Returns ``{"examined", "pruned", "bytes_reclaimed"}`` counted in shards.
    ``dry_run`` counts without touching anything.
    """
    root = Path(session_dir) if session_dir is not None else _default_session_dir()
    journal_dir = root / TURN_JOURNAL_DIR_NAME
    retention = float(
        _retention_seconds_from_env()
        if retention_seconds is None
        else max(0.0, retention_seconds)
    )
    cutoff = float(now if now is not None else time.time()) - retention
    result = {"examined": 0, "pruned": 0, "bytes_reclaimed": 0}
    if not journal_dir.is_dir():
        return result

    own_suffix = f"~{os.getpid()}.jsonl"
    by_session: dict[str, list[tuple[Path, os.stat_result]]] = {}
    for path in sorted(journal_dir.glob("*.jsonl")):
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            continue
        result["examined"] += 1
        stem = path.stem  # "sid~12345" or legacy "sid"
        tilde = stem.find("~")
        session_id = stem[:tilde] if tilde > 0 else stem
        by_session.setdefault(session_id, []).append((path, stat))

    for session_id, shards in by_session.items():
        if any(stat.st_mtime >= cutoff for _, stat in shards):
            continue
        if not _session_is_prunable(session_id, root):
            continue
        for path, stat in shards:
            if not stat.st_size:
                continue
            if not dry_run and not _release_expired_shard(
                path, stat.st_mtime, own_suffix
            ):
                continue
            result["pruned"] += 1
            result["bytes_reclaimed"] += int(stat.st_size)
    return result


def is_terminal_turn_event(event: dict) -> bool:
    return str((event or {}).get("event") or "") in _TERMINAL_EVENTS
