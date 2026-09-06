"""HWEB-39: saving one session must not cost a full-store index rewrite.

Covers the three properties the index write path has to keep:
  * concurrent saves coalesce into fewer rewrites than there are savers,
  * the serialized index is smaller for an identical logical payload,
  * an interrupted write leaves the previous index intact, never a partial one.
"""

import json
import threading
from types import SimpleNamespace

import pytest

from api import models

INDEX_ROWS = 2000


def _row(sid, updated_at, title=None):
    return {
        "session_id": sid,
        "title": title or sid,
        "updated_at": updated_at,
        "message_count": 1,
    }


def _session(sid, updated_at, title=None):
    entry = _row(sid, updated_at, title)
    return SimpleNamespace(session_id=sid, compact=lambda: dict(entry))


@pytest.fixture
def index_store(tmp_path, monkeypatch):
    """A synthetic 2000-session index wired up as the active session store."""
    index_path = tmp_path / "_index.json"
    rows = [_row(f"sid-{i}", i) for i in range(INDEX_ROWS)]
    index_path.write_text(json.dumps(rows), encoding="utf-8")
    all_ids = frozenset(row["session_id"] for row in rows)

    monkeypatch.setattr(models, "SESSION_DIR", tmp_path)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", index_path)
    monkeypatch.setattr(models, "SESSIONS", {})
    monkeypatch.setattr(models, "_persisted_session_ids_snapshot", lambda: all_ids)
    models._PARSED_INDEX_CACHE.clear()

    # The coalescing gate is module-global; leave it clean for the next test.
    models._INDEX_PENDING_UPDATES.clear()
    monkeypatch.setattr(models, "_INDEX_FLUSH_IN_PROGRESS", False)
    yield SimpleNamespace(path=index_path, rows=rows)
    models._INDEX_PENDING_UPDATES.clear()
    models._INDEX_FLUSH_IN_PROGRESS = False
    models._PARSED_INDEX_CACHE.clear()


def test_concurrent_saves_coalesce_into_fewer_index_rewrites(index_store, monkeypatch):
    savers = 8
    rewrites = []
    first_replace_entered = threading.Event()
    release_first_replace = threading.Event()
    real_replace = models._safe_replace

    def gated_replace(src, dst):
        rewrites.append(str(dst))
        if len(rewrites) == 1:
            first_replace_entered.set()
            assert release_first_replace.wait(timeout=10), "gate never released"
        real_replace(src, dst)

    monkeypatch.setattr(models, "_safe_replace", gated_replace)

    # Saver 0 becomes the flusher and parks inside its rewrite.
    flusher = threading.Thread(
        target=models._queue_session_index_update,
        args=([_session("sid-0", 9000)],),
    )
    flusher.start()
    assert first_replace_entered.wait(timeout=10), "flusher never reached the rewrite"

    # Savers 1..7 land while that rewrite is in flight.
    for i in range(1, savers):
        models._queue_session_index_update([_session(f"sid-{i}", 9000 + i)])

    release_first_replace.set()
    flusher.join(timeout=10)
    assert not flusher.is_alive()

    assert len(rewrites) < savers, f"{savers} concurrent saves cost {len(rewrites)} rewrites"

    # Coalescing must not drop anyone's row.
    persisted = {row["session_id"]: row for row in json.loads(index_store.path.read_bytes())}
    assert len(persisted) == INDEX_ROWS
    for i in range(savers):
        assert persisted[f"sid-{i}"]["updated_at"] == 9000 + i


def test_index_bytes_shrink_for_an_identical_logical_payload(index_store):
    models._write_session_index(updates=[_session("sid-0", 0, title="renamed")])

    written = index_store.path.read_bytes()
    entries = json.loads(written)
    pretty = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")

    assert len(written) < len(pretty)
    assert json.loads(pretty) == entries
    assert next(e for e in entries if e["session_id"] == "sid-0")["title"] == "renamed"


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_interrupted_index_write_leaves_the_previous_index_intact(
    index_store, monkeypatch, failure_point
):
    before = index_store.path.read_bytes()

    def boom(*_args, **_kwargs):
        raise OSError("simulated crash mid-write")

    if failure_point == "fsync":
        monkeypatch.setattr(models.os, "fsync", boom)
    else:
        monkeypatch.setattr(models, "_safe_replace", boom)

    with pytest.raises(OSError):
        models._write_session_index(updates=[_session("sid-0", 9999)])

    after = index_store.path.read_bytes()
    assert after == before
    assert json.loads(after) == index_store.rows
    assert not list(index_store.path.parent.glob("_index*.tmp.*"))
