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


def test_conclusively_empty_journal_leaves_the_transcript_untouched():
    """A sealed journal with no visible output is not an inconclusive read."""
    session_id = "hweb13_empty"
    stream_id = "hweb13_stream_empty"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "done", {})

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


def test_invisible_journal_keeps_the_stream_id_on_a_retry_hook():
    """A journal that is not yet visible must not lose its only lookup key.

    `active_stream_id` is the only way back to the run journal.  On a
    delayed-visibility filesystem the journal appears moments later, so hand the
    stream id to the existing lazy-retry hook rather than clearing it.
    """
    session_id = "hweb13_invisible"
    stream_id = "hweb13_stream_invisible"
    session = _dead_session(session_id, stream_id)
    # No journal events at all yet — the file has not become visible.

    assert _recover_dead_run_journal(session, stream_id) is True

    marker = session.messages[-1]
    assert marker["_pending_journal_recovery"] is True
    assert marker["_journal_retry_stream_id"] == stream_id
    assert marker["_journal_retry_attempts"] == 0

    # The armed marker is what the read-side self-heal looks for, and the retry
    # recovers the output once the journal lands.
    assert models._session_has_pending_journal_retry(session) is True
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Late but real output."})
    assert models._retry_journal_recovery_in_place(session) is True
    assert _visible(session) == ["Late but real output."]


def test_repeated_historical_prose_is_not_claimed_by_an_unrelated_row():
    """Journal prose matching an older answer must not be swallowed by it.

    Without pending metadata there is no turn boundary, so session-wide content
    dedupe could claim an unrelated historical assistant row — the dead run's
    output and its interruption marker would both vanish.
    """
    session_id = "hweb13_repeat_prose"
    stream_id = "hweb13_stream_repeat_prose"
    repeated = "The migration is already applied; nothing further to do."
    session = _dead_session(
        session_id,
        stream_id,
        messages=[
            {"role": "user", "content": "Is the migration applied?", "timestamp": 1},
            {"role": "assistant", "content": repeated, "timestamp": 2},
            {"role": "user", "content": "Check again after the restart", "timestamp": 3},
        ],
    )
    append_run_event(session_id, stream_id, "interim_assistant", {"text": repeated})

    assert _recover_dead_run_journal(session, stream_id) is True
    assert _visible(session) == [repeated]
    assert len([m for m in session.messages if m.get("type") == "interrupted"]) == 1


def test_recovered_prefix_is_settled_once_not_replayed():
    """Recovering a visible prefix must not arm a cumulative replay.

    `token` events aggregate, so replaying a grown journal yields "Hello" where
    the first pass yielded "Hel". The content deduper cannot match the two, so
    both rows would land in `messages` and in `context_messages`, feeding the
    next model turn duplicated partial prose.
    """
    session_id = "hweb13_prefix"
    stream_id = "hweb13_stream_prefix"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "token", {"text": "Hel"})

    assert _recover_dead_run_journal(session, stream_id) is True
    assert _visible(session) == ["Hel"]
    marker = session.messages[-1]
    assert marker["type"] == "interrupted"
    assert "_pending_journal_recovery" not in marker
    assert models._session_has_pending_journal_retry(session) is False

    # The journal grows afterwards. No retry is armed, so nothing replays it and
    # the transcript keeps exactly one assistant row for this run.
    append_run_event(session_id, stream_id, "token", {"text": "lo world."})
    append_run_event(session_id, stream_id, "done", {})
    models._retry_journal_recovery_in_place(session)

    assert _visible(session) == ["Hel"]
    assistant_context = [
        m for m in session.context_messages if m.get("role") == "assistant"
    ]
    assert len(assistant_context) == 1, "cumulative replay duplicated prose into context"


def test_terminal_journal_marker_is_final():
    """A run with a terminal event is settled — no retry hook, no churn."""
    session_id = "hweb13_terminal_marker"
    stream_id = "hweb13_stream_terminal_marker"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "All of the answer."})
    append_run_event(session_id, stream_id, "cancel", {})

    assert _recover_dead_run_journal(session, stream_id) is True
    marker = session.messages[-1]
    assert marker["type"] == "interrupted"
    assert "_pending_journal_recovery" not in marker
    assert models._session_has_pending_journal_retry(session) is False


def test_recovery_never_reads_an_unbounded_journal(monkeypatch):
    """Recovery runs on session APIs under the per-session lock.

    A run journal has no size cap, so every recovery reader must go through the
    bounded window rather than parsing the whole file.
    """
    import api.run_journal as run_journal

    session_id = "hweb13_bounded"
    stream_id = "hweb13_stream_bounded"
    session = _dead_session(session_id, stream_id)
    _journal_a_full_turn(session_id, stream_id)

    # The unbounded reader is what stalls the endpoint — nothing on the recovery
    # path may reach it, including the pending-turn repair path's readers.
    def _forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must not call the unbounded read_run_events()")

    monkeypatch.setattr(run_journal, "read_run_events", _forbidden)

    windows = []
    real_tail = run_journal.read_run_event_tail

    def _record(sid, rid, **kwargs):
        windows.append((kwargs.get("max_bytes"), kwargs.get("max_rows")))
        return real_tail(sid, rid, **kwargs)

    monkeypatch.setattr(run_journal, "read_run_event_tail", _record)

    assert _recover_dead_run_journal(session, stream_id) is True
    assert _visible(session) == [
        "The clear path never reads the journal.",
        "So the prose is dropped on refresh.",
    ]
    assert windows, "recovery did not go through the bounded reader"
    assert all(
        limit == (models._RECOVERY_JOURNAL_MAX_BYTES, models._RECOVERY_JOURNAL_MAX_ROWS)
        for limit in windows
    )


def test_pending_turn_repair_is_bounded_too(monkeypatch):
    """The pre-existing pending path shares the same readers and the same cap."""
    import api.run_journal as run_journal

    session_id = "hweb13_bounded_pending"
    stream_id = "hweb13_stream_bounded_pending"
    session = _dead_session(session_id, stream_id)
    session.pending_user_message = "Trace the regression again"
    session.pending_started_at = time.time() - 300
    session.save()
    _journal_a_full_turn(session_id, stream_id)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("recovery must not call the unbounded read_run_events()")

    monkeypatch.setattr(run_journal, "read_run_events", _forbidden)

    assert models._apply_core_sync_or_error_marker(
        session,
        models.SESSION_DIR / "missing-core.json",
        stream_id_for_recheck=stream_id,
    ) is True
    assert _visible(session) == [
        "The clear path never reads the journal.",
        "So the prose is dropped on refresh.",
    ]


def test_output_free_terminal_failure_still_records_an_outcome():
    """A cancel/error journal with no visible output must still settle the turn.

    The run really did stop, and this is the last read before the stream id — the
    only key back to the journal — is cleared, so returning silently would leave
    a persisted user turn with no outcome and no way to recover one.
    """
    session_id = "hweb13_silent_cancel"
    stream_id = "hweb13_stream_silent_cancel"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "cancel", {})

    assert _recover_dead_run_journal(session, stream_id) is True
    marker = session.messages[-1]
    assert marker["type"] == "interrupted"
    assert marker["_error"] is True
    assert marker["_recovered_stream_id"] == stream_id


def test_late_tool_complete_settles_the_already_recovered_card():
    """A `tool_complete` in a later replay must settle the existing card.

    Any armed retry re-runs `_append_journaled_partial_output` with
    `dedupe_existing=True`. The dedupe skips the already-materialized card, so
    without tracking the persisted dict the completion has nothing to apply to
    and the card stays running forever.
    """
    session_id = "hweb13_late_tool"
    stream_id = "hweb13_stream_late_tool"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Running the search."})
    append_run_event(
        session_id,
        stream_id,
        "tool",
        {"name": "terminal", "preview": "rg _journal_tool_already_present", "args": {"command": "rg x"}},
    )

    assert _recover_dead_run_journal(session, stream_id) is True
    assert [t["done"] for t in session.tool_calls] == [False]

    append_run_event(
        session_id,
        stream_id,
        "tool_complete",
        {"name": "terminal", "duration": 1.25, "is_error": False, "preview": "3 matches"},
    )
    append_run_event(session_id, stream_id, "done", {})
    models._append_journaled_partial_output(session, stream_id, dedupe_existing=True)

    assert len(session.tool_calls) == 1, "the replay duplicated the tool card"
    card = session.tool_calls[0]
    assert card["done"] is True
    assert card["duration"] == 1.25
    assert card["is_error"] is False
    assert card["preview"] == "3 matches"


def test_oversized_terminal_row_still_recovers(monkeypatch):
    """One JSONL row larger than the recovery window must not read as empty.

    `read_run_event_tail` seeks to `size - max_bytes` and trims through the next
    newline, so a window landing inside an oversized row returns nothing. An
    `apperror` embedding a whole terminal session payload is exactly that shape,
    and treating it as eventless would clear the stream id and lose the error.
    """
    session_id = "hweb13_oversized"
    stream_id = "hweb13_stream_oversized"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Work before the error."})
    append_run_event(
        session_id,
        stream_id,
        "apperror",
        {
            "session_id": session_id,
            # Padding stands in for a large embedded terminal session payload.
            "bulk": "x" * 4096,
            "session": {
                "session_id": session_id,
                "messages": [
                    {"role": "user", "content": "Trace the regression"},
                    {"role": "assistant", "content": "Provider rejected the request.", "_error": True},
                ],
            },
        },
    )

    # Shrink the window below that row so the trim consumes the whole read.
    monkeypatch.setattr(models, "_RECOVERY_JOURNAL_MAX_BYTES", 1024)

    assert _recover_dead_run_journal(session, stream_id) is True
    assert session.messages[-1]["content"] == "Provider rejected the request."
    assert session.messages[-1]["_error"] is True


def test_one_recovery_uses_a_single_journal_snapshot(monkeypatch):
    """Replay and terminal classification must see the same journal.

    Reading separately is a TOCTOU: a journal advancing mid-call lets replay see
    a prefix while classification sees a later `done`, which suppresses the
    interruption marker and then clears the stream id — presenting a turn as
    successful while its tail is silently missing.
    """
    session_id = "hweb13_snapshot"
    stream_id = "hweb13_stream_snapshot"
    session = _dead_session(session_id, stream_id)
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Only the prefix."})

    real_read = models._read_run_journal_for_recovery
    reads = {"n": 0}

    def _advancing_read(sid, rid):
        # Simulate the journal becoming visible mid-call: every read after the
        # first also sees a terminal `done` the replay never got.
        reads["n"] += 1
        if reads["n"] > 1:
            append_run_event(sid, rid, "done", {})
        return real_read(sid, rid)

    monkeypatch.setattr(models, "_read_run_journal_for_recovery", _advancing_read)

    assert _recover_dead_run_journal(session, stream_id) is True
    assert reads["n"] == 1, f"recovery took {reads['n']} journal snapshots, expected 1"
    assert _visible(session) == ["Only the prefix."]
    # Classification saw the same prefix replay did, so the turn is still marked
    # interrupted rather than silently presented as complete.
    assert [m for m in session.messages if m.get("type") == "interrupted"]


def test_recovery_marker_does_not_bubble_an_old_session(monkeypatch):
    """A recovery marker is dated by its run, not by when it was read.

    `Session.compact()` derives `last_message_at` from the newest message
    timestamp and the sidebar sorts on it, so a `time.time()` marker would move
    an old conversation to the top merely by loading it — defeating the caller's
    deliberate `save(touch_updated_at=False)`.
    """
    session_id = "hweb13_recency"
    stream_id = "hweb13_stream_recency"
    long_ago = time.time() - (30 * 24 * 3600)
    session = _dead_session(
        session_id,
        stream_id,
        messages=[{"role": "user", "content": "An old question", "timestamp": int(long_ago)}],
    )
    append_run_event(
        session_id, stream_id, "interim_assistant",
        {"text": "An old, interrupted answer."}, created_at=long_ago,
    )
    append_run_event(session_id, stream_id, "cancel", {}, created_at=long_ago + 1)

    assert _recover_dead_run_journal(session, stream_id) is True

    marker = next(m for m in session.messages if m.get("type") == "interrupted")
    assert marker["timestamp"] <= int(long_ago) + 5, "marker dated by read time, not run time"
    assert session.compact()["last_message_at"] <= int(long_ago) + 5


def test_arrival_decision_uses_the_captured_snapshot(monkeypatch):
    """An empty captured snapshot must fail closed and keep the stream id.

    `_journal_is_still_arriving()` takes its own fresh view of the file. If the
    journal becomes visible between that call and the captured snapshot's checks,
    it reports "settled" while everything else still sees the empty snapshot —
    and the stream id, the only key back to the journal, is dropped without the
    output ever being replayed.
    """
    session_id = "hweb13_arrival"
    stream_id = "hweb13_stream_arrival"
    session = _dead_session(session_id, stream_id)

    # The captured snapshot is empty, but the journal lands (with a terminal
    # event) before the arrival check runs — so a fresh view says "settled".
    def _settled_now(_session, _stream_id):
        return False

    monkeypatch.setattr(models, "_journal_is_still_arriving", _settled_now)

    assert _recover_dead_run_journal(session, stream_id) is True
    marker = session.messages[-1]
    assert marker["_pending_journal_recovery"] is True
    assert marker["_journal_retry_stream_id"] == stream_id

    # The retry still reaches the output that arrived late.
    append_run_event(session_id, stream_id, "interim_assistant", {"text": "Arrived late."})
    append_run_event(session_id, stream_id, "done", {})
    assert models._retry_journal_recovery_in_place(session) is True
    assert _visible(session) == ["Arrived late."]
