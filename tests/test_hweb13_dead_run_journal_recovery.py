"""HWEB-13: recover prose from a dead run journal when pending state is nil.

`_apply_core_sync_or_error_marker` only repairs a turn while
``pending_user_message`` still holds the prompt.  A run abandoned with lost
worker bookkeeping has already had its pending state cleared, so
`_clear_stale_stream_state` used to drop the ``active_stream_id`` — the only
key into the run journal — without ever reading it.
"""

import time

import pytest

import api.models as models
import api.routes as routes
from api.models import Session, _recover_dead_run_journal
from api.run_journal import append_run_event


@pytest.fixture(autouse=True)
def _isolate_session_state(tmp_path, monkeypatch):
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    models.SESSIONS.clear()
    with routes.STREAMS_LOCK:
        routes.STREAMS.clear()
    yield
    models.SESSIONS.clear()


def _dead_session(session_id, stream_id, *, messages=None):
    """A settled session still pointing at a run whose worker is gone."""
    session = Session(
        session_id=session_id,
        title="Dead run",
        messages=list(messages if messages is not None else [
            {"role": "user", "content": "Trace the regression", "timestamp": 1},
        ]),
        context_messages=[
            {"role": "user", "content": "Trace the regression", "timestamp": 1},
        ],
        active_stream_id=stream_id,
    )
    # The defining condition of this ticket: pending state was already cleared.
    session.pending_user_message = None
    session.pending_attachments = []
    session.pending_started_at = None
    session.pending_user_source = None
    session.save()
    return session


def _journal_a_full_turn(session_id, stream_id):
    append_run_event(session_id, stream_id, "reasoning", {"text": "Reading the stale branch."})
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "The clear path never reads the journal."})
    append_run_event(
        session_id,
        stream_id,
        "tool",
        {"name": "terminal", "preview": "rg _clear_stale_stream_state", "args": {"command": "rg x"}},
    )
    append_run_event(session_id, stream_id, "tool_complete", {"name": "terminal", "duration": 0.2})
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "So the prose is dropped on refresh."})


def _visible(session):
    return [
        m.get("content")
        for m in session.messages
        if m.get("_recovered_from_run_journal") and m.get("content")
    ]


def test_lost_worker_run_recovers_prose_reasoning_tools_and_one_marker(monkeypatch):
    session_id = "hweb13_lost_worker"
    stream_id = "hweb13_stream_a"
    session = _dead_session(session_id, stream_id)
    _journal_a_full_turn(session_id, stream_id)
    monkeypatch.setattr(routes, "get_session", lambda sid, **_kw: session)

    assert routes._clear_stale_stream_state(session) is True

    assert _visible(session) == [
        "The clear path never reads the journal.",
        "So the prose is dropped on refresh.",
    ]
    assert session.messages[1].get("reasoning") == "Reading the stale branch."
    assert [t["name"] for t in session.tool_calls] == ["terminal"]
    assert session.tool_calls[0]["done"] is True
    markers = [m for m in session.messages if m.get("type") == "interrupted"]
    assert len(markers) == 1
    assert markers[0]["interruption_cause"] == "lost_worker_bookkeeping"
    assert "recovered from the run journal" in markers[0]["content"]
    assert session.active_stream_id is None

    models.SESSIONS.clear()
    reloaded = models.get_session(session_id)
    assert _visible(reloaded) == _visible(session)
    assert reloaded.active_stream_id is None
    # The settled payload's activity scene is projected from tool_calls anchored
    # to a recovered assistant owner; without a valid anchor the scene is empty.
    anchor_idx = reloaded.tool_calls[0]["assistant_msg_idx"]
    assert reloaded.messages[anchor_idx]["_recovered_from_run_journal"] is True
    assert reloaded.messages[anchor_idx]["role"] == "assistant"


def test_repeated_reads_do_not_duplicate_recovered_content():
    session_id = "hweb13_repeat"
    stream_id = "hweb13_stream_repeat"
    session = _dead_session(session_id, stream_id)
    _journal_a_full_turn(session_id, stream_id)

    assert _recover_dead_run_journal(session, stream_id) is True
    first = list(session.messages)
    first_tools = list(session.tool_calls)

    assert _recover_dead_run_journal(session, stream_id) is False
    assert session.messages == first
    assert session.tool_calls == first_tools


def test_successor_run_recovers_only_its_own_journal():
    session_id = "hweb13_successor"
    first_stream = "hweb13_run_one"
    second_stream = "hweb13_run_two"
    session = _dead_session(session_id, first_stream)
    _journal_a_full_turn(session_id, first_stream)
    assert _recover_dead_run_journal(session, first_stream) is True

    append_run_event(session_id, second_stream, "interim_assistant", {"text": "Second run output."})
    assert _recover_dead_run_journal(session, second_stream) is True

    assert _visible(session) == [
        "The clear path never reads the journal.",
        "So the prose is dropped on refresh.",
        "Second run output.",
    ]
    assert len([m for m in session.messages if m.get("type") == "interrupted"]) == 2


def test_cancelled_dead_run_recovers_output_with_marker():
    session_id = "hweb13_cancel"
    stream_id = "hweb13_stream_cancel"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Partial work before cancel."})
    append_run_event(session_id, stream_id, "cancel", {})

    assert _recover_dead_run_journal(session, stream_id) is True
    assert _visible(session) == ["Partial work before cancel."]
    assert len([m for m in session.messages if m.get("type") == "interrupted"]) == 1


def test_completed_dead_run_recovers_output_without_marker():
    session_id = "hweb13_completed"
    stream_id = "hweb13_stream_done"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Finished answer."})
    append_run_event(session_id, stream_id, "done", {})

    assert _recover_dead_run_journal(session, stream_id) is True
    assert _visible(session) == ["Finished answer."]
    assert not [m for m in session.messages if m.get("type") == "interrupted"]


def test_errored_dead_run_materializes_the_gateway_error():
    session_id = "hweb13_error"
    stream_id = "hweb13_stream_error"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Work before the error."})
    append_run_event(
        session_id,
        stream_id,
        "apperror",
        {
            "session_id": session_id,
            "session": {
                "session_id": session_id,
                "messages": [
                    {"role": "user", "content": "Trace the regression"},
                    {"role": "assistant", "content": "Provider rejected the request.", "_error": True},
                ],
            },
        },
    )

    assert _recover_dead_run_journal(session, stream_id) is True
    assert _visible(session) == [
        "Work before the error.",
        "Provider rejected the request.",
    ]
    # The specific gateway error replaces the generic interruption marker.
    assert not [m for m in session.messages if m.get("type") == "interrupted"]
    assert session.messages[-1]["content"] == "Provider rejected the request."
    assert session.messages[-1]["_error"] is True


def test_empty_journal_leaves_the_transcript_untouched():
    session_id = "hweb13_empty"
    stream_id = "hweb13_stream_empty"
    session = _dead_session(session_id, stream_id)

    assert _recover_dead_run_journal(session, stream_id) is False
    assert len(session.messages) == 1


def test_live_worker_journal_is_left_alone(monkeypatch):
    session_id = "hweb13_live"
    stream_id = "hweb13_stream_live"
    session = _dead_session(session_id, stream_id)
    _journal_a_full_turn(session_id, stream_id)
    monkeypatch.setattr(routes, "get_session", lambda sid, **_kw: session)
    with routes.STREAMS_LOCK:
        routes.STREAMS[stream_id] = object()
    try:
        assert routes._clear_stale_stream_state(session) is False
    finally:
        with routes.STREAMS_LOCK:
            routes.STREAMS.pop(stream_id, None)

    assert _visible(session) == []
    assert session.active_stream_id == stream_id


def test_detached_live_worker_journal_is_left_alone(monkeypatch):
    """No SSE channel but live worker bookkeeping is a detached run, not a dead one."""
    import api.config as config

    session_id = "hweb13_detached"
    stream_id = "hweb13_stream_detached"
    session = _dead_session(session_id, stream_id)
    _journal_a_full_turn(session_id, stream_id)
    monkeypatch.setattr(routes, "get_session", lambda sid, **_kw: session)
    with config.ACTIVE_RUNS_LOCK:
        config.ACTIVE_RUNS[stream_id] = {"started_at": time.time()}
    try:
        assert routes._clear_stale_stream_state(session) is False
    finally:
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.pop(stream_id, None)

    assert _visible(session) == []
    assert session.active_stream_id == stream_id


def test_compression_snapshot_run_is_not_recovered():
    session_id = "hweb13_snapshot"
    stream_id = "hweb13_stream_snapshot"
    session = _dead_session(session_id, stream_id)
    session.pre_compression_snapshot = True
    _journal_a_full_turn(session_id, stream_id)

    assert _recover_dead_run_journal(session, stream_id) is False
    assert _visible(session) == []


def test_pending_turns_still_route_through_core_sync_repair(monkeypatch):
    """The pending path keeps its own repair; recovery must not double-append."""
    session_id = "hweb13_pending"
    stream_id = "hweb13_stream_pending"
    session = _dead_session(session_id, stream_id)
    session.pending_user_message = "Trace the regression again"
    session.pending_started_at = time.time() - 300
    session.save()
    _journal_a_full_turn(session_id, stream_id)
    monkeypatch.setattr(routes, "get_session", lambda sid, **_kw: session)

    assert routes._clear_stale_stream_state(session) is True

    assert _visible(session) == [
        "The clear path never reads the journal.",
        "So the prose is dropped on refresh.",
    ]
    assert len([m for m in session.messages if m.get("type") == "interrupted"]) == 1
    assert session.pending_user_message is None
