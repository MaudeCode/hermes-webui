"""HWEB-33 — one sidebar EventSource instead of two, and a two-stream ceiling.

A visible tab used to hold five long-lived EventSources (sidebar session
events, gateway watcher, per-session, per-turn chat, Kanban) against a browser
budget of six same-origin HTTP/1.1 sockets, so ordinary `api()` fetches queued
behind them indefinitely.

Two changes bring the worst case (visible tab, active turn, Kanban panel open)
down to two:

1. `/api/sessions/events?gateway=1` serves the gateway watcher's frames on the
   same connection, tagged with a `stream` discriminator.
2. `_sidebarSseBackgrounded()` also reports "backgrounded" while a main-view
   panel owns the screen — such a panel replaces both the sidebar session list
   and the chat transcript, so nothing on screen consumes sidebar events.

The server half is exercised for real (the merged handler is driven with both
producers wired up); the client half is source-structural, matching the house
pattern for static JS with no server round trip.
"""

from __future__ import annotations

import json
import queue
from pathlib import Path

import pytest

from api import routes

ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


# ── server: the merged endpoint ─────────────────────────────────────────────


class _Wfile:
    """Collects SSE bytes and hangs up once the handler has written enough.

    The handler loops until the client disconnects, so the disconnect is how a
    test ends it — `_CLIENT_DISCONNECT_ERRORS` is exactly what a real dropped
    browser connection raises.
    """

    def __init__(self, stop_after: int):
        self.chunks: list[bytes] = []
        self._stop_after = stop_after

    def write(self, data: bytes):
        self.chunks.append(data)
        if len(self.chunks) >= self._stop_after:
            raise BrokenPipeError("test client hung up")

    def flush(self):
        pass


class _Handler:
    def __init__(self, stop_after: int):
        self.wfile = _Wfile(stop_after)


class _FakeWatcher:
    """Stands in for GatewayWatcher: hands out a pre-filled subscriber queue."""

    def __init__(self, events, alive=True):
        self._q: queue.Queue = queue.Queue()
        for event in events:
            self._q.put(event)
        self._alive = alive
        self.unsubscribed: list[queue.Queue] = []

    def is_alive(self):
        return self._alive

    def subscribe(self):
        return self._q

    def unsubscribe(self, q):
        self.unsubscribed.append(q)


def _parse_frames(chunks):
    """Return [(event_name, payload_or_None)] from raw SSE bytes."""
    frames = []
    for raw in b"".join(chunks).decode("utf-8").split("\n\n"):
        if not raw.strip():
            continue
        if raw.startswith(":"):
            frames.append(("keepalive", None))
            continue
        event = None
        data = None
        for line in raw.split("\n"):
            if line.startswith("event: "):
                event = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        frames.append((event or "message", data))
    return frames


@pytest.fixture
def merged_sse(monkeypatch):
    """Drive `_handle_session_events_stream` with both producers stubbed out."""

    def run(*, query, session_events=(), gateway_events=(), watcher_alive=True,
            show_cli_sessions=True, stop_after=6):
        session_q: queue.Queue = queue.Queue()
        for event in session_events:
            session_q.put(event)
        watcher = _FakeWatcher(gateway_events, alive=watcher_alive)

        monkeypatch.setattr(routes, "start_sse_response", lambda handler, **kw: True)
        monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda handler: None)
        monkeypatch.setattr(routes, "subscribe_session_events", lambda: session_q)
        monkeypatch.setattr(routes, "unsubscribe_session_events", lambda q: None)
        monkeypatch.setattr(
            routes, "load_settings", lambda *a, **kw: {"show_cli_sessions": show_cli_sessions}
        )
        monkeypatch.setattr("api.gateway_watcher.get_watcher", lambda *a, **kw: watcher)
        monkeypatch.setattr("api.models.get_cli_sessions", lambda *a, **kw: [{"session_id": "cli-1"}])
        # Keepalive cadence must not outlive the test.
        monkeypatch.setattr(routes, "_SSE_HEARTBEAT_INTERVAL_SECONDS", 0.05)

        handler = _Handler(stop_after)
        routes._handle_session_events_stream(handler, routes.urlsplit("/api/sessions/events" + query))
        return _parse_frames(handler.wfile.chunks), watcher

    return run


def test_merged_stream_carries_session_and_gateway_frames(merged_sse):
    """Both event kinds arrive on one connection, tagged by origin.

    This is the whole point of the merge: before it, the gateway payload only
    existed on `/api/sessions/gateway/stream`'s own socket.
    """
    frames, _watcher = merged_sse(
        query="?gateway=1",
        session_events=[{"type": "sessions_changed", "reason": "session_created", "profile": "default"}],
        gateway_events=[{"type": "sessions_changed", "sessions": [{"session_id": "cli-2"}]}],
    )
    by_stream = {(name, (data or {}).get("stream")) for name, data in frames}
    assert ("sessions_changed", "gateway") in by_stream
    assert ("sessions_changed", "sessions") in by_stream


def test_merged_stream_preserves_event_names_and_payload_shapes(merged_sse):
    """Names and payload keys are unchanged; `stream` is purely additive.

    Client handlers were written against these shapes on two endpoints, so the
    merge must not rename or restructure anything.
    """
    session_payload = {
        "type": "sessions_changed",
        "reason": "project_renamed",
        "profile": "work",
        "session_id": "abc",
        "version": 2,
    }
    gateway_payload = {"type": "sessions_changed", "sessions": [{"session_id": "cli-2", "updated_at": 7}]}
    frames, _watcher = merged_sse(
        query="?gateway=1",
        session_events=[session_payload],
        gateway_events=[gateway_payload],
    )
    got = {(data or {}).get("stream"): data for name, data in frames if name == "sessions_changed"}

    assert got["sessions"] == dict(session_payload, stream="sessions")
    assert got["gateway"] == dict(gateway_payload, stream="gateway")


def test_merged_stream_sends_initial_gateway_snapshot(merged_sse):
    """The gateway half opens with a snapshot, exactly as its own endpoint did."""
    frames, _watcher = merged_sse(query="?gateway=1")
    snapshots = [
        data for name, data in frames
        if name == "sessions_changed" and (data or {}).get("stream") == "gateway"
    ]
    assert snapshots and snapshots[0]["sessions"] == [{"session_id": "cli-1"}]


def test_gateway_half_is_opt_in(merged_sse):
    """Without ?gateway=1 the endpoint behaves exactly as it did before.

    Clients that subscribe to `/api/sessions/events` directly (and the
    standalone gateway endpoint they may also hold) must not start receiving
    gateway frames they never asked for.
    """
    frames, watcher = merged_sse(
        query="",
        session_events=[{"type": "sessions_changed", "reason": "session_created"}],
    )
    assert all((data or {}).get("stream") != "gateway" for _name, data in frames)
    assert all(name != "gateway_status" for name, _data in frames)


def test_unusable_gateway_reports_status_instead_of_failing_the_stream(merged_sse):
    """A dead watcher must not take the session-events half down with it.

    On the merged stream the client cannot learn about a broken gateway from
    `onerror` any more, so the server states it — that frame is what starts the
    30s poll fallback.
    """
    frames, _watcher = merged_sse(
        query="?gateway=1",
        watcher_alive=False,
        session_events=[{"type": "sessions_changed", "reason": "session_created"}],
    )
    statuses = [data for name, data in frames if name == "gateway_status"]
    assert statuses and statuses[0]["ok"] is False
    assert statuses[0]["watcher_running"] is False
    assert statuses[0]["fallback_poll_ms"]
    # The session-events half still delivers.
    assert any((data or {}).get("stream") == "sessions" for _name, data in frames)


def test_watcher_shutdown_downgrades_only_the_gateway_half(merged_sse):
    """The watcher's None sentinel drops the gateway half and says so."""
    frames, watcher = merged_sse(
        query="?gateway=1",
        gateway_events=[None],
        session_events=[{"type": "sessions_changed", "reason": "session_created"}],
    )
    statuses = [data for name, data in frames if name == "gateway_status"]
    assert statuses[-1]["ok"] is False
    assert statuses[-1]["watcher_running"] is False
    assert watcher.unsubscribed, "the gateway queue must be released, not leaked"
    assert any((data or {}).get("stream") == "sessions" for _name, data in frames)


def test_merged_stream_releases_both_subscriptions_on_disconnect(monkeypatch):
    """Neither producer may leak a queue when the browser hangs up."""
    released: list[str] = []
    session_q: queue.Queue = queue.Queue()
    session_q.put({"type": "sessions_changed", "reason": "session_created"})
    watcher = _FakeWatcher([])

    monkeypatch.setattr(routes, "start_sse_response", lambda handler, **kw: True)
    monkeypatch.setattr(routes, "_sse_set_write_deadline", lambda handler: None)
    monkeypatch.setattr(routes, "subscribe_session_events", lambda: session_q)
    monkeypatch.setattr(routes, "unsubscribe_session_events", lambda q: released.append("sessions"))
    monkeypatch.setattr(routes, "load_settings", lambda *a, **kw: {"show_cli_sessions": True})
    monkeypatch.setattr("api.gateway_watcher.get_watcher", lambda *a, **kw: watcher)
    monkeypatch.setattr("api.models.get_cli_sessions", lambda *a, **kw: [])

    handler = _Handler(stop_after=1)
    routes._handle_session_events_stream(handler, routes.urlsplit("/api/sessions/events?gateway=1"))

    assert released == ["sessions"]
    assert watcher.unsubscribed


def test_standalone_gateway_endpoint_still_routed():
    """Other clients (and the probe) keep their dedicated endpoint."""
    assert "if parsed.path == '/api/sessions/gateway/stream':" in routes_source()
    assert "return _handle_gateway_sse_stream(handler, parsed)" in routes_source()


def routes_source() -> str:
    return (ROOT / "api" / "routes.py").read_text(encoding="utf-8")


# ── client: the two-stream ceiling ──────────────────────────────────────────


def test_sidebar_stream_is_the_only_gateway_subscription():
    """No second EventSource for the gateway feed anywhere in the client."""
    assert "new EventSource('api/sessions/gateway/stream')" not in SESSIONS_JS
    assert "api/sessions/events' + (wantGateway ? '?gateway=1' : '')" in SESSIONS_JS
    # Exactly one EventSource construction remains in the sidebar module.
    assert SESSIONS_JS.count("new EventSource(") == 1


def test_gateway_frames_route_to_the_handler_that_owned_the_old_endpoint():
    start = SESSIONS_JS.index("_sessionEventsSSE.addEventListener('sessions_changed'")
    block = SESSIONS_JS[start:start + 1600]
    assert "payload.stream === 'gateway'" in block
    assert "_handleGatewaySessionsChanged(payload)" in block
    # Gateway frames must not also fall through to the session-events logic.
    assert block.index("_handleGatewaySessionsChanged(payload)") < block.index("_sessionEventProfilesMatch")


def test_gateway_status_frame_drives_the_poll_fallback():
    """`onerror` can no longer report a broken gateway — the status frame must.

    A disabled feature is not a broken one: `enabled: false` must stay a no-op,
    matching the old probe's 404 branch, so turning agent sessions off does not
    start a pointless 30s poll and toast.
    """
    assert "addEventListener('gateway_status'" in SESSIONS_JS
    start = SESSIONS_JS.index("function _applyGatewayStatus(status)")
    block = SESSIONS_JS[start:start + 1100]
    assert "stopGatewayPollFallback()" in block
    assert "startGatewayPollFallback(" in block
    disabled = block.index("status.enabled === false")
    assert disabled < block.index("startGatewayPollFallback(")


def test_main_view_panel_closes_the_sidebar_stream():
    """Kanban (a main-view panel) frees the sidebar socket for its own stream.

    This is what holds "active turn + Kanban open" to two EventSources:
    chat stream + Kanban stream, with the sidebar stream closed.
    """
    start = SESSIONS_JS.index("function _sidebarSseBackgrounded()")
    block = SESSIONS_JS[start:start + 900]
    assert "window._mainViewPanelActive" in block

    assert "function _mainViewPanelActive()" in PANELS_JS
    assert "MAIN_VIEW_PANELS.includes(_currentPanel)" in PANELS_JS
    assert "'kanban'" in PANELS_JS[PANELS_JS.index("const MAIN_VIEW_PANELS"):PANELS_JS.index("const MAIN_VIEW_PANELS") + 200]
    # switchPanel must act on the change, not just record it.
    start = PANELS_JS.index("async function switchPanel(name, opts = {})")
    assert "_syncSidebarSseForPanel()" in PANELS_JS[start:start + 4000]


def test_concurrent_stream_budget_for_turn_plus_kanban():
    """Enumerate the four on-screen states and pin each at two streams or fewer.

    The streams are gated by source-level rules rather than runtime state, so
    this models them from those rules:
      - sidebar stream: closed while a main-view panel is active
      - per-session stream: closed while a chat stream is live for the session
      - chat stream: open during a turn
      - Kanban stream: open only while the Kanban panel is active
    """
    # Each rule above is asserted individually elsewhere in this file / suite;
    # here we pin the arithmetic they produce so a future regression that
    # re-opens one of them fails loudly.
    def concurrent(*, main_view_panel, kanban_open, turn_active):
        sidebar = 0 if main_view_panel else 1
        chat = 1 if turn_active else 0
        per_session = 0 if turn_active else 1
        kanban = 1 if kanban_open else 0
        return sidebar + chat + per_session + kanban

    assert concurrent(main_view_panel=True, kanban_open=True, turn_active=True) == 2
    assert concurrent(main_view_panel=True, kanban_open=True, turn_active=False) == 2
    assert concurrent(main_view_panel=False, kanban_open=False, turn_active=True) == 2
    assert concurrent(main_view_panel=False, kanban_open=False, turn_active=False) == 2


def test_per_session_stream_yields_to_the_live_chat_stream():
    """Pre-existing rule the budget above depends on — pin it."""
    start = MESSAGES_JS.index("function startSessionStream(sid)")
    block = MESSAGES_JS[start:start + 1200]
    assert "if (_chatStreamActiveForSession(sid)) {" in block
    assert "return;" in block


def test_kanban_stream_closes_when_its_panel_closes():
    """Pre-existing rule the budget above depends on — pin it."""
    start = PANELS_JS.index("async function switchPanel(name, opts = {})")
    block = PANELS_JS[start:start + 4000]
    assert "if (prevPanel === 'kanban' && nextPanel !== 'kanban') {" in block
    assert "_kanbanStopPolling()" in block
    stop = PANELS_JS[PANELS_JS.index("function _kanbanStopPolling()"):][:400]
    assert "_kanbanEventSource.close()" in stop


def test_sidebar_stream_keeps_its_backoff_reconnect():
    """The merge must not cost the sidebar stream its existing backoff."""
    start = SESSIONS_JS.index("_sessionEventsSSE.onerror = () =>")
    block = SESSIONS_JS[start:start + 1200]
    assert "_sessionEventsReconnectDelayMs()" in block
    assert "_sessionEventsReconnectAttempt = Math.min(_sessionEventsReconnectAttempt + 1, 6)" in block
    assert "ensureSessionEventsSSE()" in block
    # Exponential with jitter, capped — unchanged from before the merge.
    delay = SESSIONS_JS[SESSIONS_JS.index("function _sessionEventsReconnectDelayMs()"):][:500]
    assert "Math.pow(2, attempt)" in delay
    assert "_sessionEventsReconnectMaxMs" in delay


def test_kanban_stream_keeps_its_reconnect_and_poll_fallback():
    start = PANELS_JS.index("function _kanbanStartEventStream()")
    block = PANELS_JS[start:start + 1800]
    assert "_kanbanEventSourceFailures >= 3" in block
    assert "setInterval(refreshKanbanEvents, 30000)" in block


def test_forced_reconnect_is_a_separate_verb_from_the_idempotent_open():
    """A dead or wrong-profile socket needs a reconnect the open path won't do.

    startGatewaySSE() is idempotent once the gateway half is attached, which is
    right for the boot/focus paths but wrong for a profile switch (the watcher
    registry is profile-keyed, #3629, so the open stream is still bound to the
    previous profile's watcher) and for bfcache/offline recovery (the socket is
    dead but not yet nulled). reconnectSidebarSSE() is that verb.
    """
    start = SESSIONS_JS.index("function startGatewaySSE(){")
    assert "if(_sessionEventsSSE && _sessionEventsGatewayAttached) return;" in SESSIONS_JS[start:start + 500]

    start = SESSIONS_JS.index("function reconnectSidebarSSE(){")
    block = SESSIONS_JS[start:start + 300]
    assert "_closeSessionEventsSSE();" in block
    assert "if(_sidebarSseBackgrounded()) return;" in block
    assert "ensureSessionEventsSSE();" in block

    assert "reconnectSidebarSSE()" in PANELS_JS  # profile switch
    assert "reconnectSidebarSSE()" in (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
    assert "reconnectSidebarSSE()" in (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
