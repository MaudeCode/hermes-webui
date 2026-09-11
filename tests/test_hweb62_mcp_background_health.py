"""Regression tests for HWEB-62 — background health checks for MCP servers."""
import json
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from api import mcp_health
from api.routes import (
    _handle_mcp_servers_list,
    _handle_mcp_tools_list,
    _mcp_runtime_status_by_name,
    _server_summary,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean_health_state():
    mcp_health.reset()
    yield
    _join_health_threads()
    mcp_health.reset()


def _join_health_threads(timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    for thread in list(threading.enumerate()):
        if thread.name.startswith("mcp-health-"):
            thread.join(max(0.0, deadline - time.monotonic()))


def _make_handler(path="/api/mcp/servers"):
    h = MagicMock()
    h.path = path
    h.command = "GET"
    return h


def _payload(handler):
    return json.loads(handler.wfile.write.call_args[0][0].decode("utf-8"))


def _runtime(servers, *, agent_statuses=None):
    """Run the real ``_mcp_runtime_status_by_name`` against a config, no agent."""
    with patch("api.routes.get_active_hermes_home", return_value=object()), \
         patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}):
        if agent_statuses is None:
            return _mcp_runtime_status_by_name(servers)
        fake = MagicMock()
        fake.get_mcp_status.return_value = agent_statuses
        with patch.dict("sys.modules", {"tools": MagicMock(), "tools.mcp_tool": fake}):
            return _mcp_runtime_status_by_name(servers)


class TestProbeVerdicts:
    def test_auth_failure_is_distinguishable_from_a_server_that_is_down(self):
        """401 and 500 must not collapse: one needs a token, the other needs a restart."""
        from urllib import error as urllib_error

        def raising(code):
            def _open(request):
                raise urllib_error.HTTPError(request.full_url, code, "no", {}, None)
            return _open

        with patch("api.mcp_health._urlopen", raising(401)):
            assert mcp_health.probe_server("a", {"url": "https://x/mcp"}) == ("needs_auth", "HTTP 401")
        with patch("api.mcp_health._urlopen", raising(500)):
            assert mcp_health.probe_server("a", {"url": "https://x/mcp"}) == ("unhealthy", "HTTP 500")

    def test_transport_failure_and_timeout_are_unhealthy_with_a_reason(self):
        from urllib import error as urllib_error

        with patch("api.mcp_health._urlopen",
                   side_effect=urllib_error.URLError(ConnectionRefusedError())):
            assert mcp_health.probe_server("a", {"url": "https://x/mcp"}) == ("unhealthy", "unreachable")
        with patch("api.mcp_health._urlopen",
                   side_effect=urllib_error.URLError(TimeoutError())):
            assert mcp_health.probe_server("a", {"url": "https://x/mcp"}) == ("unhealthy", "timed out")

    def test_non_http_url_scheme_is_rejected_without_being_opened(self):
        with patch("api.mcp_health._urlopen") as opener:
            state, detail = mcp_health.probe_server("a", {"url": "file:///etc/passwd"})
        assert (state, detail) == ("unhealthy", "unsupported url scheme")
        opener.assert_not_called()

    def test_stdio_command_that_does_not_exist_is_unhealthy(self):
        with patch("api.mcp_health.shutil.which", return_value=None):
            state, detail = mcp_health.probe_server("a", {"command": "/opt/nope/mcp-thing"})
        assert state == "unhealthy"
        assert detail == "command not found: mcp-thing"

    def test_stdio_command_that_exists_is_not_spawned_and_stays_unknown(self):
        with patch("api.mcp_health.shutil.which", return_value="/usr/bin/true"):
            assert mcp_health.probe_server("a", {"command": "true"})[0] == "unknown"

    def test_a_2xx_that_is_not_an_mcp_initialize_result_is_not_called_healthy(self):
        """An HTML login page or proxy catch-all answers 200 and serves no tools."""
        cases = {
            b"<html><body>Please sign in</body></html>": ("unknown", "HTTP 200, not an MCP response"),
            b"": ("unknown", "HTTP 200, not an MCP response"),
            b'{"ok": true}': ("unknown", "HTTP 200, not an MCP response"),
            b'{"jsonrpc":"2.0","id":1,"result":{"tools":[]}}':
                ("unknown", "HTTP 200, unrecognized initialize result"),
            b'{"jsonrpc":"2.0","id":1,"error":{"code":-32600,"message":"nope"}}':
                ("unhealthy", "HTTP 200, initialize rejected"),
        }
        for body, expected in cases.items():
            assert self._probe_body(body) == expected, body

    def test_a_real_initialize_result_is_healthy_over_json_and_sse(self):
        result = b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18","serverInfo":{"name":"x"}}}'
        assert self._probe_body(result) == ("healthy", "HTTP 200")
        sse = b"event: message\ndata: " + result + b"\n\n"
        assert self._probe_body(sse) == ("healthy", "HTTP 200")

    @staticmethod
    def _response(body: bytes, code: int = 200, headers: dict | None = None):
        response = MagicMock()
        response.status = code
        response.headers = headers or {}
        response.read.return_value = body
        response.__enter__ = lambda self: self
        response.__exit__ = lambda *a: False
        return response

    @classmethod
    def _probe_body(cls, body: bytes, code: int = 200):
        response = cls._response(body, code)
        with patch("api.mcp_health._urlopen", return_value=response):
            verdict = mcp_health.probe_server("a", {"url": "https://x/mcp"})
        # Bounded read: a probe must never pull an unbounded body into memory.
        response.read.assert_called_once_with(mcp_health._MAX_PROBE_BODY_BYTES)
        return verdict

    _INIT_OK = b'{"jsonrpc":"2.0","id":1,"result":{"protocolVersion":"2025-06-18"}}'

    def test_a_session_opened_by_the_probe_is_terminated_on_every_exit(self):
        """A stateful server allocates a session per initialize; we must not leak one per probe."""
        initialize = self._response(self._INIT_OK, headers={"Mcp-Session-Id": "sess-123"})
        terminate = self._response(b"")
        with patch("api.mcp_health._urlopen", side_effect=[initialize, terminate]) as opener:
            verdict = mcp_health.probe_server("a", {
                "url": "https://x/mcp", "headers": {"Authorization": "Bearer t"},
            })
        assert verdict == ("healthy", "HTTP 200")
        assert opener.call_count == 2
        delete = opener.call_args_list[1].args[0]
        assert delete.get_method() == "DELETE"
        assert delete.full_url == "https://x/mcp"
        assert delete.get_header("Mcp-session-id") == "sess-123"
        assert delete.get_header("Authorization") == "Bearer t"

        # No session header → nothing to terminate, and no extra request.
        with patch("api.mcp_health._urlopen", return_value=self._response(self._INIT_OK)) as opener:
            mcp_health.probe_server("a", {"url": "https://x/mcp"})
        assert opener.call_count == 1

        # A server that refuses DELETE (405) does not change the verdict.
        from urllib import error as urllib_error
        initialize = self._response(self._INIT_OK, headers={"Mcp-Session-Id": "sess-456"})
        refused = urllib_error.HTTPError("https://x/mcp", 405, "no", {}, None)
        with patch("api.mcp_health._urlopen", side_effect=[initialize, refused]):
            assert mcp_health.probe_server("a", {"url": "https://x/mcp"}) == ("healthy", "HTTP 200")

    def test_probe_refuses_redirects_so_the_bearer_token_never_leaves_the_host(self):
        """urllib copies request headers onto a redirect; following one would leak the token."""
        assert mcp_health._OPENER.open.__self__ is mcp_health._OPENER
        handler = next(h for h in mcp_health._OPENER.handlers
                       if isinstance(h, mcp_health._NoRedirect))
        assert handler.redirect_request(None, None, 302, "", {}, "https://evil.example/") is None
        # An unfollowed redirect is inconclusive, not a health verdict.
        assert mcp_health._status_result(302) == ("unknown", "HTTP 302")


class TestRuntimeStatusMapFold:
    def test_failing_health_check_is_reported_unhealthy_and_surfaced_by_the_endpoint(self):
        servers = {"web": {"url": "https://web.example/mcp"}}
        with patch("api.mcp_health.probe_server", return_value=("unhealthy", "HTTP 503")):
            _runtime(servers)
            _join_health_threads()
            runtime = _runtime(servers)
        assert runtime["web"]["health"] == "unhealthy"
        assert runtime["web"]["health_detail"] == "HTTP 503"

        h = _make_handler()
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value=runtime):
            _handle_mcp_servers_list(h)
        row = _payload(h)["servers"][0]
        assert row["name"] == "web"
        assert row["health"] == "unhealthy"
        assert row["health_detail"] == "HTTP 503"

    def test_needs_auth_and_down_stay_separate_states_through_the_endpoint(self):
        servers = {
            "expired": {"url": "https://expired.example/mcp"},
            "down": {"url": "https://down.example/mcp"},
        }
        verdicts = {"expired": ("needs_auth", "HTTP 401"), "down": ("unhealthy", "unreachable")}
        with patch("api.mcp_health.probe_server", side_effect=lambda n, c: verdicts[n]):
            _runtime(servers)
            _join_health_threads()
            runtime = _runtime(servers)

        h = _make_handler()
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value=runtime):
            _handle_mcp_servers_list(h)
        by_name = {row["name"]: row for row in _payload(h)["servers"]}
        assert by_name["expired"]["health"] == "needs_auth"
        assert by_name["down"]["health"] == "unhealthy"
        assert by_name["expired"]["health"] != by_name["down"]["health"]

    def test_a_probe_that_raises_leaves_that_server_known_and_spares_the_others(self):
        servers = {
            "boom": {"url": "https://boom.example/mcp"},
            "fine": {"url": "https://fine.example/mcp"},
        }

        def probe(name, cfg):
            if name == "boom":
                raise RuntimeError("probe exploded")
            return ("healthy", "HTTP 200")

        with patch("api.mcp_health.probe_server", side_effect=probe):
            _runtime(servers)
            _join_health_threads()
            runtime = _runtime(servers)
        assert runtime["boom"]["health"] == "unknown"
        assert runtime["boom"]["health_detail"] == "health check failed"
        assert runtime["fine"]["health"] == "healthy"

    def test_connected_stdio_server_is_healthy_but_expired_credentials_still_win(self):
        servers = {
            "local": {"command": "mcp-local"},
            "remote": {"url": "https://remote.example/mcp"},
        }
        verdicts = {
            "local": ("unknown", "stdio server not probed"),
            "remote": ("needs_auth", "HTTP 403"),
        }
        agent = [
            {"name": "local", "connected": True, "tools": 4},
            {"name": "remote", "connected": True, "tools": 2},
        ]
        with patch("api.mcp_health.probe_server", side_effect=lambda n, c: verdicts[n]):
            _runtime(servers, agent_statuses=agent)
            _join_health_threads()
            runtime = _runtime(servers, agent_statuses=agent)
        assert runtime["local"]["health"] == "healthy"
        assert runtime["remote"]["health"] == "needs_auth"

    def test_a_stale_connected_flag_never_overrules_a_probe_that_saw_a_failure(self):
        """``connected`` can be stale; it may only upgrade "unknown", never a failed probe."""
        servers = {"web": {"url": "https://web.example/mcp"}}
        agent = [{"name": "web", "connected": True, "tools": 3}]
        with patch("api.mcp_health.probe_server", return_value=("unhealthy", "HTTP 503")):
            _runtime(servers, agent_statuses=agent)
            _join_health_threads()
            runtime = _runtime(servers, agent_statuses=agent)
        assert runtime["web"]["connected"] is True
        assert runtime["web"]["health"] == "unhealthy"

    def test_health_rides_the_existing_runtime_map_with_no_second_status_path(self):
        """The endpoint's health output is fully determined by the runtime status map."""
        servers = {"web": {"url": "https://web.example/mcp"}}
        injected = {
            "web": {
                "name": "web",
                "connected": False,
                "health": "needs_auth",
                "health_detail": "HTTP 401",
                "health_checked_at": 1234.0,
            }
        }
        h = _make_handler()
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value=injected) as runtime_map, \
             patch("api.mcp_health.probe_server") as probe:
            _handle_mcp_servers_list(h)
        row = _payload(h)["servers"][0]
        assert row["health"] == "needs_auth"
        assert row["health_detail"] == "HTTP 401"
        assert row["health_checked_at"] == 1234.0
        runtime_map.assert_called_once()
        # No independent health lookup happens outside that map.
        probe.assert_not_called()

    def test_tool_rows_and_server_rows_agree_because_both_read_one_map(self):
        servers = {"web": {"url": "https://web.example/mcp"}}
        with patch("api.mcp_health.probe_server", return_value=("needs_auth", "HTTP 401")):
            _runtime(servers)
            _join_health_threads()
            runtime = _runtime(servers)
        h = _make_handler("/api/mcp/tools")
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value=runtime):
            _handle_mcp_tools_list(h)
        assert _payload(h)["unavailable_servers"] == ["web"]


class TestSchedulingIsBackgroundAndBounded:
    def test_disabled_server_is_never_health_checked(self):
        servers = {
            "off": {"url": "https://off.example/mcp", "enabled": False},
            "on": {"url": "https://on.example/mcp"},
        }
        probed = []
        with patch("api.mcp_health.probe_server",
                   side_effect=lambda n, c: probed.append(n) or ("healthy", "HTTP 200")):
            _runtime(servers)
            _join_health_threads()
            runtime = _runtime(servers)
        assert probed == ["on"]
        assert runtime["off"]["health"] == "not_checked"
        assert runtime["on"]["health"] == "healthy"

    def test_toggling_a_server_off_clears_its_stale_verdict(self):
        servers = {"web": {"url": "https://web.example/mcp"}}
        with patch("api.mcp_health.probe_server", return_value=("unhealthy", "unreachable")):
            _runtime(servers)
            _join_health_threads()
        assert mcp_health.snapshot()["web"]["health"] == "unhealthy"

        disabled = {"web": {"url": "https://web.example/mcp", "enabled": False}}
        with patch("api.mcp_health.probe_server", return_value=("unhealthy", "unreachable")):
            runtime = _runtime(disabled)
            _join_health_threads()
        assert mcp_health.snapshot() == {}
        assert runtime["web"]["health"] == "not_checked"

    def test_editing_a_server_drops_the_old_verdict_instead_of_waiting_out_the_interval(self):
        """Same name, different url — a different server, so not the same verdict."""
        before = {"web": {"url": "https://old.example/mcp"}}
        after = {"web": {"url": "https://new.example/mcp"}}
        seen = []

        def probe(name, cfg):
            seen.append(cfg["url"])
            return ("unhealthy", "unreachable") if "old" in cfg["url"] else ("healthy", "HTTP 200")

        with patch("api.mcp_health.probe_server", side_effect=probe):
            _runtime(before)
            _join_health_threads()
            assert _runtime(before)["web"]["health"] == "unhealthy"
            # Well inside HEALTH_INTERVAL_S: the edit must still force a re-probe.
            runtime = _runtime(after)
            _join_health_threads()
            runtime = _runtime(after)
        assert seen == ["https://old.example/mcp", "https://new.example/mcp"]
        assert runtime["web"]["health"] == "healthy"

    def test_a_same_named_server_with_different_credentials_does_not_inherit_health(self):
        """Two profiles can define "web" with different tokens; verdicts must not cross."""
        profile_a = {"web": {"url": "https://web.example/mcp", "headers": {"Authorization": "Bearer a"}}}
        profile_b = {"web": {"url": "https://web.example/mcp", "headers": {"Authorization": "Bearer b"}}}
        with patch("api.mcp_health.probe_server", return_value=("needs_auth", "HTTP 401")):
            _runtime(profile_a)
            _join_health_threads()
            assert _runtime(profile_a)["web"]["health"] == "needs_auth"
            # Switching profiles must not report profile A's verdict for B's server.
            assert _runtime(profile_b)["web"]["health"] == "unknown"

    def test_a_probe_that_finishes_after_a_config_change_is_not_published_as_the_new_health(self):
        before = {"web": {"url": "https://old.example/mcp"}}
        after = {"web": {"url": "https://new.example/mcp"}}
        release = threading.Event()
        entered = threading.Event()

        def hang(name, cfg):
            if "old" in cfg["url"]:
                entered.set()
                release.wait(10)
                return ("healthy", "HTTP 200")
            return ("unhealthy", "unreachable")

        try:
            with patch("api.mcp_health.probe_server", side_effect=hang):
                _runtime(before)
                assert entered.wait(5)
                # Config changes while the old probe is still in flight.
                assert _runtime(after)["web"]["health"] == "unknown"
                release.set()
                _join_health_threads()
                # The obsolete probe published, but it measured the old server.
                assert _runtime(after)["web"]["health"] == "unknown"
                _join_health_threads()
                assert _runtime(after)["web"]["health"] == "unhealthy"
        finally:
            release.set()
            _join_health_threads()

    def test_pending_is_derived_from_the_rows_not_from_a_separate_in_flight_check(self):
        """A probe that finishes between reading the rows and checking in-flight must still re-read."""
        servers = {"web": {"url": "https://web.example/mcp"}}
        # The rows say "no verdict yet" while nothing is in flight any more:
        # exactly the window a fast 401 lands in.
        no_verdict = {"web": {"name": "web", "health": "unknown", "health_detail": "",
                              "health_checked_at": None}}
        assert mcp_health.in_flight() == set()
        h = _make_handler()
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value=no_verdict):
            _handle_mcp_servers_list(h)
        assert _payload(h)["health_pending"] is True

        # A *settled* unknown (stdio not probed, protocol mismatch) is not pending.
        settled = {"web": {"name": "web", "health": "unknown", "health_detail": "HTTP 405",
                           "health_checked_at": 1234.0}}
        h = _make_handler()
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": servers}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value=settled):
            _handle_mcp_servers_list(h)
        assert _payload(h)["health_pending"] is False

        # A disabled server never counts as pending.
        off = {"web": {"url": "https://web.example/mcp", "enabled": False}}
        h = _make_handler()
        with patch("api.routes.get_active_hermes_home", return_value=object()), \
             patch("api.routes.get_config_for_profile_home", return_value={"mcp_servers": off}), \
             patch("api.routes._mcp_runtime_status_by_name", return_value={}):
            _handle_mcp_servers_list(h)
        assert _payload(h)["health_pending"] is False

    def test_the_endpoint_tells_the_panel_to_read_back_while_a_probe_is_in_flight(self):
        servers = {"slow": {"url": "https://slow.example/mcp"}}
        release = threading.Event()
        entered = threading.Event()

        def hang(name, cfg):
            entered.set()
            release.wait(10)
            return ("unhealthy", "timed out")

        try:
            with patch("api.mcp_health.probe_server", side_effect=hang), \
                 patch("api.routes.get_active_hermes_home", return_value=object()), \
                 patch("api.routes.get_config_for_profile_home",
                       return_value={"mcp_servers": servers}):
                h = _make_handler()
                _handle_mcp_servers_list(h)
                assert entered.wait(5)
                assert _payload(h)["health_pending"] is True
                release.set()
                _join_health_threads()
                h2 = _make_handler()
                _handle_mcp_servers_list(h2)
            assert _payload(h2)["health_pending"] is False
        finally:
            release.set()
            _join_health_threads()

    def test_endpoints_do_not_block_on_an_in_flight_check(self):
        """A hung probe must not hold up /api/mcp/servers or /api/mcp/tools."""
        servers = {"slow": {"url": "https://slow.example/mcp"}}
        release = threading.Event()
        entered = threading.Event()

        def hang(name, cfg):
            entered.set()
            release.wait(10)
            return ("unhealthy", "timed out")

        try:
            with patch("api.mcp_health.probe_server", side_effect=hang), \
                 patch("api.routes.get_active_hermes_home", return_value=object()), \
                 patch("api.routes.get_config_for_profile_home",
                       return_value={"mcp_servers": servers}):
                started = time.monotonic()
                _handle_mcp_servers_list(_make_handler())
                _handle_mcp_tools_list(_make_handler("/api/mcp/tools"))
                elapsed = time.monotonic() - started
                assert entered.wait(5), "probe never started in the background"
                assert "slow" in mcp_health.in_flight()
                assert elapsed < 1.0, f"endpoints blocked for {elapsed:.2f}s on a hung probe"
        finally:
            release.set()
            _join_health_threads()

    def test_a_slow_server_cannot_accumulate_overlapping_checks(self):
        servers = {"slow": {"url": "https://slow.example/mcp"}}
        release = threading.Event()
        entered = threading.Event()
        starts = []
        lock = threading.Lock()

        def hang(name, cfg):
            with lock:
                starts.append(name)
            entered.set()
            release.wait(10)
            return ("unhealthy", "timed out")

        try:
            with patch("api.mcp_health.probe_server", side_effect=hang):
                for _ in range(25):
                    mcp_health.refresh_and_read(servers)
                assert entered.wait(5), "probe never started in the background"
                assert starts == ["slow"], f"overlapping probes accumulated: {starts}"
                assert len([th for th in threading.enumerate()
                            if th.name.startswith("mcp-health-")]) == 1
        finally:
            release.set()
            _join_health_threads()

    def test_the_check_interval_is_bounded_so_polling_does_not_reprobe_every_request(self):
        servers = {"web": {"url": "https://web.example/mcp"}}
        calls = []
        with patch("api.mcp_health.probe_server",
                   side_effect=lambda n, c: calls.append(n) or ("healthy", "HTTP 200")):
            for _ in range(10):
                mcp_health.refresh_and_read(servers)
                _join_health_threads()
            assert calls == ["web"], "a re-probe ran inside the interval"

            # Age the last start past the interval; exactly one more probe runs.
            with mcp_health._LOCK:
                started, fingerprint = mcp_health._STARTED_AT["web"]
                mcp_health._STARTED_AT["web"] = (
                    started - mcp_health.HEALTH_INTERVAL_S - 1, fingerprint)
            mcp_health.refresh_and_read(servers)
            _join_health_threads()
        assert calls == ["web", "web"]
        assert mcp_health.HEALTH_INTERVAL_S > 0


class TestServerSummaryAndPanel:
    def test_summary_reports_not_checked_for_disabled_and_invalid_servers(self):
        disabled = _server_summary("off", {"url": "https://x/mcp", "enabled": False}, {})
        invalid = _server_summary("bad", {}, {})
        broken = _server_summary("worse", "not-a-dict", {})
        assert disabled["health"] == "not_checked"
        assert invalid["health"] == "not_checked"
        assert broken["health"] == "not_checked"

    def test_panel_badges_only_the_two_actionable_verdicts(self):
        js = (ROOT / "static/panels.js").read_text(encoding="utf-8")
        assert "function _mcpHealthBadge" in js
        assert "${_mcpHealthBadge(s)}" in js
        assert "mcp_health_unhealthy" in js
        assert "mcp_health_needs_auth" in js
        # Bounded re-read for a cold cache, never an unbounded poll loop.
        assert "MCP_HEALTH_REREADS" in js
        assert "_mcpHealthRereads>=MCP_HEALTH_REREADS" in js
        assert "_scheduleMcpHealthReread(r.health_pending)" in js
        assert "setInterval" not in js.split("function loadMcpServers")[1][:1500]
        i18n = (ROOT / "static/i18n.js").read_text(encoding="utf-8")
        assert "mcp_health_unhealthy:" in i18n
        assert "mcp_health_needs_auth:" in i18n
        css = (ROOT / "static/style.css").read_text(encoding="utf-8")
        assert ".mcp-health-unhealthy" in css
        assert ".mcp-health-needs_auth" in css

    def test_out_of_scope_features_did_not_ship(self):
        """HWEB-62 is health checks only: no usage overlay, deep links, or drag-in import."""
        js = (ROOT / "static/panels.js").read_text(encoding="utf-8")
        routes = (ROOT / "api/routes.py").read_text(encoding="utf-8")
        assert "hermes://" not in js
        assert "hermes://" not in routes
        assert "mcp-cost-overlay" not in js
        assert "mcp_usage_30d" not in js
