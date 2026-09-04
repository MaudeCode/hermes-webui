"""Regression coverage for restart-safety run lifecycle reporting."""

import io
import time
from types import SimpleNamespace

import pytest


def test_health_counts_active_runs_even_when_no_sse_streams():
    """A worker run can outlive its SSE channel; health must expose the run."""
    from api import config, routes

    with config.STREAMS_LOCK:
        config.STREAMS.clear()
    with config.ACTIVE_RUNS_LOCK:
        config.ACTIVE_RUNS.clear()
        config.ACTIVE_RUNS["stream-1"] = {
            "stream_id": "stream-1",
            "session_id": "session-1",
            "workspace": "/private/workspace",
            "started_at": time.time() - 42,
            "phase": "running",
        }

    try:
        stream_check = routes._streams_lock_health()
        run_check = routes._run_lifecycle_health()

        assert stream_check["active_streams"] == 0
        assert run_check["active_runs"] == 1
        assert run_check["oldest_run_age_seconds"] >= 40
        run = run_check["runs"][0]
        assert "session_id" not in run
        assert "stream_id" not in run
        assert "workspace" not in run
    finally:
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()


def test_run_registry_unregister_records_last_finished_time():
    """Guards need a grace window after the last real worker exits."""
    from api import config

    with config.ACTIVE_RUNS_LOCK:
        config.ACTIVE_RUNS.clear()
        config.LAST_RUN_FINISHED_AT = None
    config.register_stream_owner("stream-2", "session-2")

    config.register_active_run("stream-2", session_id="session-2", phase="starting")
    with config.ACTIVE_RUNS_LOCK:
        assert "stream-2" in config.ACTIVE_RUNS
    assert config.stream_owner_session_id("stream-2") == "session-2"

    config.unregister_active_run("stream-2")

    with config.ACTIVE_RUNS_LOCK:
        assert "stream-2" not in config.ACTIVE_RUNS
        assert isinstance(config.LAST_RUN_FINISHED_AT, float)
    assert config.stream_owner_session_id("stream-2") is None


def test_health_degrades_for_blocked_admission_without_exposing_session():
    from api import config, routes

    with config.ACTIVE_RUNS_LOCK:
        config.ACTIVE_RUNS.clear()
        config.ACTIVE_RUNS["blocked-stream"] = {
            "stream_id": "blocked-stream",
            "session_id": "private-session",
            "workspace": "/private/workspace",
            "started_at": time.time() - 10,
            "phase": "waiting_for_session_lock",
            "lock_wait_started_at": time.time() - 3,
            "owner_thread_native_id": 123,
        }
    try:
        health = routes._run_lifecycle_health()
    finally:
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()

    assert health["status"] == "degraded"
    assert health["degraded_runs"] == 1
    assert health["runs"][0]["degraded"] is True
    assert health["runs"][0]["lock_wait_age_seconds"] >= 2
    assert "session_id" not in health["runs"][0]
    assert "workspace" not in health["runs"][0]


def test_admission_timeout_marks_only_its_owned_turn_stale():
    from api.streaming import _mark_chat_admission_timeout_stale

    owned = type("Session", (), {"active_stream_id": "owned", "pending_started_at": 123.0})()
    replaced = type("Session", (), {"active_stream_id": "newer", "pending_started_at": 456.0})()

    assert _mark_chat_admission_timeout_stale(owned, "owned") is True
    assert owned.pending_started_at is None
    assert _mark_chat_admission_timeout_stale(replaced, "old") is False
    assert replaced.pending_started_at == 456.0


def test_route_admission_lock_is_bounded_and_visible_to_health():
    import threading
    from api import config, routes

    lock = threading.Lock()
    lock.acquire()
    try:
        with routes._bounded_chat_admission_lock("private-session", lock, 0.02) as acquired:
            assert acquired is False
        health = routes._run_lifecycle_health()
        assert health["status"] == "degraded"
        assert health["recent_admission_timeout"] is True
        assert health["active_runs"] == 0
    finally:
        lock.release()
        config.LAST_CHAT_ADMISSION_TIMEOUT_AT = None
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()


@pytest.mark.parametrize("entrypoint", ["stream", "wakeup", "sync"])
def test_all_chat_entrypoints_timeout_on_held_session_lock(
    monkeypatch, tmp_path, entrypoint
):
    import threading
    from api import config, routes

    session = SimpleNamespace(
        session_id=f"bounded-{entrypoint}",
        pre_compression_snapshot=False,
        active_stream_id=None,
        pending_user_message=None,
        workspace=str(tmp_path),
        model="test-model",
        model_provider=None,
        title="Bounded",
        messages=[],
    )
    held = threading.Lock()
    held.acquire()
    monkeypatch.setattr(routes, "_CHAT_LOCK_WAIT_SECONDS", 0.02)
    monkeypatch.setattr(routes, "_get_session_agent_lock", lambda _sid: held)
    monkeypatch.setattr(routes, "_agent_runtime_barrier_response", lambda **_kwargs: None)
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(
        routes, "_resolve_chat_workspace_with_recovery", lambda *_args: str(tmp_path)
    )
    monkeypatch.setattr(routes, "resolve_trusted_workspace", lambda value: value)
    monkeypatch.setattr(
        routes, "_read_profile_model_config", lambda *_args: (None, None, {})
    )
    monkeypatch.setattr(
        routes,
        "_resolve_compatible_session_model_state",
        lambda *_args, **_kwargs: ("test-model", None, False),
    )
    try:
        if entrypoint == "stream":
            response = routes._start_chat_stream_for_session(
                session,
                msg="hello",
                attachments=[],
                workspace=str(tmp_path),
                model="test-model",
            )
            status = response["_status"]
        elif entrypoint == "wakeup":
            response = routes.start_session_turn(session.session_id, "hello")
            status = response["_status"]
        else:
            handler = SimpleNamespace(
                wfile=io.BytesIO(),
                send_response=lambda value: setattr(handler, "status", value),
                send_header=lambda *_args: None,
                end_headers=lambda: None,
            )
            routes._handle_chat_sync(
                handler,
                {"session_id": session.session_id, "message": "hello"},
            )
            response = __import__("json").loads(handler.wfile.getvalue())
            status = handler.status
    finally:
        held.release()
        config.LAST_CHAT_ADMISSION_TIMEOUT_AT = None
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()

    assert status == 409
    assert response["code"] == "chat_admission_timeout"
    assert response["retryable"] is True


def test_public_health_exposes_retained_admission_timeout(monkeypatch):
    from api import config, routes

    captured = {}
    config.note_chat_admission_timeout()
    monkeypatch.setattr(
        routes, "_streams_lock_health", lambda: {"status": "ok", "active_streams": 0}
    )
    monkeypatch.setattr(routes, "_accept_loop_health", lambda _handler: {"status": "ok"})
    monkeypatch.setattr(
        routes,
        "j",
        lambda _handler, payload, status=200: captured.update(
            payload=payload, status=status
        ),
    )
    try:
        routes._handle_health(None, SimpleNamespace(query=""))
    finally:
        config.LAST_CHAT_ADMISSION_TIMEOUT_AT = None

    assert captured["status"] == 503
    assert captured["payload"]["recent_admission_timeout"] is True
    assert "admission_timeout_age_seconds" in captured["payload"]


def test_worker_startup_lock_timeout_is_bounded_and_retained(monkeypatch):
    import threading
    from api import config, routes, streaming

    lock = threading.Lock()
    lock.acquire()
    config.register_active_run("worker-timeout", session_id="private")
    monkeypatch.setattr(streaming, "_CHAT_LOCK_WAIT_SECONDS", 0.02)
    try:
        with streaming._try_acquire_worker_session_lock(
            "worker-timeout", lock, "prestream_save"
        ) as acquired:
            assert acquired is False
        assert routes._run_lifecycle_health()["status"] == "degraded"
        assert config.LAST_CHAT_ADMISSION_TIMEOUT_AT is not None
    finally:
        lock.release()
        config.unregister_active_run("worker-timeout")
        config.LAST_CHAT_ADMISSION_TIMEOUT_AT = None


def test_post_execution_writeback_timeout_is_not_retryable():
    from api.routes import _chat_writeback_timeout_response

    response = _chat_writeback_timeout_response()
    assert response["_status"] == 503
    assert response["retryable"] is False
    assert response["outcome_unknown"] is True


def test_health_only_admission_waiter_never_blocks_worker_start():
    from api import config, routes

    config.register_active_run(
        "admission-observation",
        session_id="shared-session",
        phase="waiting_for_session_lock",
        health_only=True,
        attachable=False,
        profile=None,
    )
    try:
        assert routes._active_run_stream_for_session("shared-session") is None
    finally:
        config.unregister_active_run("admission-observation")
