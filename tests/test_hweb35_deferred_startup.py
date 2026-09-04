"""HWEB-35: the listening socket must bind before startup recovery runs.

Session recovery, agent dependency repair and watcher/plugin startup all used to
run in front of ``QuietHTTPServer(...)``, so a restart could leave the port
unreachable for as long as a full session scan — worst case, a pip install with
a 120s timeout. They now run on a background thread behind the bind, and only
requests that read recovered session state wait on a readiness event.

Each test drives the real ``server.main()`` with a gate wedged into recovery, so
the assertions are about the production startup ordering rather than a stand-in.
"""

from __future__ import annotations

import json
import socket
import threading
import time
import urllib.error
import urllib.request

import pytest

# Bound for the 503 path: the readiness wait is shortened so a test observes the
# timeout in well under a second instead of the production 10s.
GATE_WAIT_SECONDS = 0.5


class _Boot:
    """Handle on one in-process server started from ``server.main()``."""

    def __init__(self, recovery_error):
        self.recovery_error = recovery_error
        self.recovery_started = threading.Event()
        self.release_recovery = threading.Event()
        self.deferred_finished = threading.Event()
        self.auto_install_calls = []
        self.httpd = None
        self.main_thread = None

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_address[1]}"

    def connect(self, timeout=5):
        return socket.create_connection(
            ("127.0.0.1", self.httpd.server_address[1]), timeout=timeout
        )

    def get(self, path, timeout=10):
        """Return ``(status, headers, body_text)``, treating 4xx/5xx as results."""
        try:
            with urllib.request.urlopen(self.base + path, timeout=timeout) as resp:
                return resp.status, dict(resp.headers), resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read().decode("utf-8", "replace")


@pytest.fixture
def boot_server(monkeypatch):
    """Start ``server.main()`` on an ephemeral port with recovery held open.

    Every startup side effect that reaches outside the process (crash hooks, fd
    limits, credential chmod, watcher, plugins, drain threads) is stubbed; the
    bind, the readiness event and the deferred-startup ordering are real.
    """
    import server

    from api import background_process, config, gateway_watcher, plugins
    from api import session_lifecycle, session_recovery, startup, talaria_relay

    boots = []

    def _start(*, recovery_error=None):
        boot = _Boot(recovery_error)

        def fake_recovery(*_args, **_kwargs):
            boot.recovery_started.set()
            assert boot.release_recovery.wait(timeout=30), "recovery gate never released"
            if boot.recovery_error is not None:
                raise boot.recovery_error
            return {"restored": 0, "scanned": 0}

        monkeypatch.setattr(session_recovery, "recover_all_sessions_on_startup", fake_recovery)
        monkeypatch.setattr(config, "verify_hermes_imports", lambda: (True, [], {}))
        monkeypatch.setattr(config, "print_startup_config", lambda *a, **k: None)
        monkeypatch.setattr(
            startup, "auto_install_agent_deps", lambda: boot.auto_install_calls.append(1)
        )
        monkeypatch.setattr(server, "install_crash_visibility", lambda *a, **k: None)
        monkeypatch.setattr(server, "_ignore_sigpipe", lambda: None)
        monkeypatch.setattr(server, "_raise_fd_soft_limit", lambda *a, **k: {})
        monkeypatch.setattr(server, "fix_credential_permissions", lambda: None)
        # The readiness gate sits behind auth; auth itself is not under test.
        monkeypatch.setattr(server, "check_auth", lambda handler, parsed: True)
        monkeypatch.setattr(gateway_watcher, "start_watcher", lambda *a, **k: None)
        monkeypatch.setattr(gateway_watcher, "stop_watcher", lambda *a, **k: None)
        monkeypatch.setattr(background_process, "start_drain_thread", lambda *a, **k: False)
        monkeypatch.setattr(background_process, "stop_drain_thread", lambda *a, **k: None)
        monkeypatch.setattr(background_process, "start_session_channel_reaper", lambda *a, **k: False)
        monkeypatch.setattr(background_process, "stop_session_channel_reaper", lambda *a, **k: None)
        monkeypatch.setattr(session_lifecycle, "drain_all_on_shutdown", lambda *a, **k: None)
        monkeypatch.setattr(plugins, "load_plugins", lambda *a, **k: None)
        # Last call in run_deferred_startup — signals the thread ran to the end.
        monkeypatch.setattr(
            talaria_relay,
            "start_talaria_relay_publisher",
            lambda *a, **k: boot.deferred_finished.set(),
        )

        real_server_cls = server.QuietHTTPServer

        def capture(*args, **kwargs):
            boot.httpd = real_server_cls(*args, **kwargs)
            return boot.httpd

        monkeypatch.setattr(server, "QuietHTTPServer", capture)
        monkeypatch.setattr(server, "HOST", "127.0.0.1")
        monkeypatch.setattr(server, "PORT", 0)  # ephemeral; real port read back off httpd
        monkeypatch.setattr(startup, "STARTUP_READY", threading.Event())
        monkeypatch.setattr(startup, "STARTUP_WAIT_SECONDS", GATE_WAIT_SECONDS)

        boot.main_thread = threading.Thread(target=server.main, daemon=True)
        boot.main_thread.start()
        deadline = time.monotonic() + 15
        while boot.httpd is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert boot.httpd is not None, "server.main() never bound a socket"
        boots.append(boot)
        return boot

    yield _start

    for boot in boots:
        boot.release_recovery.set()
        if boot.httpd is not None:
            boot.httpd.shutdown()
        if boot.main_thread is not None:
            boot.main_thread.join(timeout=15)


def test_port_accepts_connection_before_recovery_completes(boot_server):
    """The bind must not wait on the session scan."""
    from api import startup

    boot = boot_server()
    assert boot.recovery_started.wait(timeout=15), "deferred startup never reached recovery"
    assert not startup.STARTUP_READY.is_set(), "readiness set while recovery still running"

    with boot.connect() as sock:
        assert sock is not None

    status, _headers, body = boot.get("/health", timeout=5)
    assert status == 200, f"/health must answer during recovery, got {status}: {body}"
    assert json.loads(body).get("status") == "ok"


def test_recovery_dependent_request_503s_during_startup_then_succeeds(boot_server):
    """/api/sessions waits on the readiness bound, then 503s instead of hanging."""
    from api import startup

    boot = boot_server()
    assert boot.recovery_started.wait(timeout=15), "deferred startup never reached recovery"

    started = time.monotonic()
    status, headers, body = boot.get("/api/sessions", timeout=10)
    elapsed = time.monotonic() - started
    assert status == 503, f"expected 503 while recovery runs, got {status}: {body}"
    assert headers.get("Retry-After") == "5", f"missing Retry-After: {headers}"
    assert json.loads(body).get("phase") == "session recovery"
    assert GATE_WAIT_SECONDS <= elapsed < 8, (
        f"request must wait the readiness bound then answer, not hang; took {elapsed:.2f}s"
    )

    boot.release_recovery.set()
    assert startup.STARTUP_READY.wait(timeout=15), "readiness never set after recovery"

    status, _headers, body = boot.get("/api/sessions", timeout=15)
    assert status == 200, f"expected success once recovery finished, got {status}: {body}"


def test_auto_install_not_invoked_when_agent_imports_resolve(boot_server):
    """The pip path stays behind an actual import failure."""
    boot = boot_server()
    boot.release_recovery.set()
    assert boot.deferred_finished.wait(timeout=20), "deferred startup did not run to completion"
    assert boot.auto_install_calls == [], "auto_install_agent_deps ran with all imports resolving"


def test_server_still_serves_when_background_recovery_raises(boot_server):
    """A raising recovery must release readiness, not wedge every /api/ request."""
    from api import startup

    boot = boot_server(recovery_error=RuntimeError("simulated recovery failure HWEB-35"))
    assert boot.recovery_started.wait(timeout=15), "deferred startup never reached recovery"
    boot.release_recovery.set()
    assert startup.STARTUP_READY.wait(timeout=15), "readiness never set after recovery raised"

    status, _headers, body = boot.get("/api/sessions", timeout=15)
    assert status == 200, f"server must keep serving after recovery raised, got {status}: {body}"


def test_gate_fails_open_when_no_deferred_startup_was_armed():
    """No recovery pass in flight means nothing to wait for.

    A process that never calls start_deferred_startup() — a test driving Handler
    directly, an embedder calling into the routes — must not stall every /api/
    request for the full readiness bound before 503ing.
    """
    from urllib.parse import urlparse

    from api import startup

    assert startup.STARTUP_READY.is_set(), (
        "readiness must default to set so an unarmed process serves immediately"
    )
    started = time.monotonic()
    assert startup.await_startup_ready(None, urlparse("/api/sessions")) is True
    assert time.monotonic() - started < 1, "unarmed gate must not wait on the readiness bound"
