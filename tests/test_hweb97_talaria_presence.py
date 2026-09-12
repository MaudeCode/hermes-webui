"""HWEB-97: genuine WebUI activity decides Talaria alert eligibility."""

import io
import json
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from api import talaria_relay
from api.talaria_relay import (
    PRESENCE_LEASE_SECONDS,
    RelayConfig,
    RelayPairingError,
    TalariaRelayPublisher,
    update_presence,
)

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


@pytest.fixture(scope="session", autouse=True)
def test_server():
    """Unit-level module: no HTTP server needed."""


@pytest.fixture(autouse=True)
def presence_registry(monkeypatch):
    talaria_relay._presence.clear()
    clock = {"now": 1000.0}
    monkeypatch.setattr(talaria_relay, "_presence_clock", lambda: clock["now"])
    from api import profiles

    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    yield clock
    talaria_relay._presence.clear()


@contextmanager
def _active_runs(monkeypatch, sessions: dict[str, str]):
    """Register one running stream per session id, tagged with its profile."""
    from api import config, models

    monkeypatch.setattr(
        models,
        "get_session",
        lambda sid, metadata_only: type("S", (), {"title": sid, "profile": sessions[sid]})(),
    )
    with config.ACTIVE_RUNS_LOCK:
        previous = dict(config.ACTIVE_RUNS)
        config.ACTIVE_RUNS.clear()
        for index, sid in enumerate(sessions):
            config.ACTIVE_RUNS[f"stream-{sid}"] = {
                "stream_id": f"stream-{sid}",
                "session_id": sid,
                "started_at": index + 1,
            }
    try:
        yield
    finally:
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()
            config.ACTIVE_RUNS.update(previous)


def _publisher(tmp_path, *, opener=None, profiles=None):
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(
        Ed25519PrivateKey.generate().private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    )
    config = RelayConfig(
        "https://relay.example",
        "https://hermes.example",
        "key",
        key_path,
        profiles or {"default": {"identity": "", "profile_id": "prf_default"}},
    )
    return TalariaRelayPublisher(config, **({"opener": opener} if opener else {}))


def _eligibility(publisher, profile):
    states = publisher.build_states(profile)
    assert states, "expected at least one published state"
    return [state.get("alertEligible", "omitted") for state in states]


# ── Lease semantics ─────────────────────────────────────────────────────────


def test_fresh_tab_without_input_leaves_alerts_eligible(tmp_path, monkeypatch):
    with _active_runs(monkeypatch, {"s1": "default"}):
        assert _eligibility(_publisher(tmp_path), "default") == ["omitted"]


def test_qualifying_input_mutes_profile_for_ninety_seconds(tmp_path, monkeypatch, presence_registry):
    publisher = _publisher(tmp_path)
    with _active_runs(monkeypatch, {"s1": "default", "s2": "default"}):
        assert update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default") == {
            "ok": True,
            "lease_seconds": PRESENCE_LEASE_SECONDS,
        }
        assert _eligibility(publisher, "default") == [False, False]
        presence_registry["now"] += PRESENCE_LEASE_SECONDS - 1
        assert _eligibility(publisher, "default") == [False, False]
        # Still open, focused, connected and streaming: nothing renewed server-side.
        presence_registry["now"] += 1
        assert _eligibility(publisher, "default") == ["omitted", "omitted"]
        assert talaria_relay._presence == {}


def test_server_ignores_client_supplied_expiry(presence_registry):
    update_presence(
        {"tab_id": "tab-aaaaaaaa", "active": True, "expires_at": 10**12, "duration": 10**6, "ts": 0},
        profile="default",
    )
    assert talaria_relay._presence[("default", "tab-aaaaaaaa")] == presence_registry["now"] + PRESENCE_LEASE_SECONDS


def test_revocation_clears_only_that_tab(tmp_path, monkeypatch, presence_registry):
    publisher = _publisher(tmp_path)
    with _active_runs(monkeypatch, {"s1": "default"}):
        update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default")
        update_presence({"tab_id": "tab-bbbbbbbb", "active": True}, profile="default")
        assert update_presence({"tab_id": "tab-aaaaaaaa", "active": False}, profile="default") == {
            "ok": True,
            "lease_seconds": 0,
        }
        assert _eligibility(publisher, "default") == [False]
        update_presence({"tab_id": "tab-bbbbbbbb", "active": False}, profile="default")
        assert _eligibility(publisher, "default") == ["omitted"]


def test_one_tab_expiring_cannot_clear_another_fresh_tab(tmp_path, monkeypatch, presence_registry):
    publisher = _publisher(tmp_path)
    with _active_runs(monkeypatch, {"s1": "default"}):
        update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default")
        presence_registry["now"] += 50
        update_presence({"tab_id": "tab-bbbbbbbb", "active": True}, profile="default")
        presence_registry["now"] += 45  # tab a expired, tab b has 45s left
        assert _eligibility(publisher, "default") == [False]
        assert set(talaria_relay._presence) == {("default", "tab-bbbbbbbb")}
        presence_registry["now"] += 45
        assert _eligibility(publisher, "default") == ["omitted"]


def test_profiles_do_not_suppress_each_other(tmp_path, monkeypatch):
    publisher = _publisher(
        tmp_path,
        profiles={
            "alice": {"identity": "", "profile_id": "prf_alice"},
            "bob": {"identity": "", "profile_id": "prf_bob"},
        },
    )
    with _active_runs(monkeypatch, {"a1": "alice", "b1": "bob"}):
        update_presence({"tab_id": "tab-alice000", "active": True}, profile="alice")
        assert _eligibility(publisher, "alice") == [False]
        assert _eligibility(publisher, "bob") == ["omitted"]
        update_presence({"tab_id": "tab-alice000", "active": False}, profile="bob")
        assert _eligibility(publisher, "alice") == [False], "another profile cannot revoke this lease"


def test_renamed_root_profile_shares_the_default_scope(tmp_path, monkeypatch):
    from api import profiles

    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name in ("default", "kinni"))
    publisher = _publisher(tmp_path)
    with _active_runs(monkeypatch, {"s1": "default"}):
        update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="kinni")
        assert _eligibility(publisher, "default") == [False]


# ── Failure modes default to eligible ───────────────────────────────────────


@pytest.mark.parametrize(
    "body",
    [
        None,
        [],
        {},
        {"tab_id": "tab-aaaaaaaa"},
        {"active": True},
        {"tab_id": "tab-aaaaaaaa", "active": "yes"},
        {"tab_id": "tab-aaaaaaaa", "active": 1},
        {"tab_id": "short", "active": True},
        {"tab_id": "x" * 65, "active": True},
        {"tab_id": "tab aaaaaaaa", "active": True},
        {"tab_id": "tab/../../a", "active": True},
        {"tab_id": 12345678, "active": True},
    ],
)
def test_malformed_heartbeat_is_rejected_without_a_lease(body):
    with pytest.raises(RelayPairingError) as excinfo:
        update_presence(body, profile="default")
    assert excinfo.value.status == 400
    assert talaria_relay._presence == {}


@pytest.mark.parametrize("profile", ["", "   ", None])
def test_missing_profile_scope_is_rejected_without_a_lease(profile):
    with pytest.raises(RelayPairingError) as excinfo:
        update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile=profile)
    assert excinfo.value.status == 403
    assert talaria_relay._presence == {}


def test_registry_is_bounded_and_eviction_restores_eligibility(tmp_path, monkeypatch):
    publisher = _publisher(tmp_path)
    with _active_runs(monkeypatch, {"s1": "default"}):
        update_presence({"tab_id": "tab-victim00", "active": True}, profile="default")
        for index in range(talaria_relay._PRESENCE_MAX_LEASES):
            update_presence({"tab_id": f"tab-flood-{index:06d}", "active": True}, profile="flood")
        assert len(talaria_relay._presence) == talaria_relay._PRESENCE_MAX_LEASES
        assert ("default", "tab-victim00") not in talaria_relay._presence
        assert _eligibility(publisher, "default") == ["omitted"]


def test_presence_lookup_failure_defaults_to_eligible(tmp_path, monkeypatch):
    def boom(_profile):
        raise RuntimeError("registry unavailable")

    monkeypatch.setattr(talaria_relay, "profile_has_presence", boom)
    with _active_runs(monkeypatch, {"s1": "default"}):
        assert _eligibility(_publisher(tmp_path), "default") == ["omitted"]


def test_restart_forgets_every_lease(tmp_path, monkeypatch):
    update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default")
    talaria_relay._presence.clear()  # a new process starts empty
    with _active_runs(monkeypatch, {"s1": "default"}):
        assert _eligibility(_publisher(tmp_path), "default") == ["omitted"]


def test_incompatible_relay_falls_back_to_eligible_snapshots(tmp_path, monkeypatch, caplog):
    bodies = []

    @contextmanager
    def opener(request, timeout):
        body = json.loads(request.data)
        bodies.append(body)
        if any("alertEligible" in state for state in body["states"]):
            import urllib.error

            raise urllib.error.HTTPError(request.full_url, 400, "unknown field", {}, io.BytesIO(b""))
        yield type("Response", (), {"status": 200})()

    publisher = _publisher(tmp_path, opener=opener)
    with _active_runs(monkeypatch, {"s1": "default"}):
        update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default")
        with caplog.at_level("WARNING", logger="api.talaria_relay"):
            publisher.publish_snapshot()
        assert [s.get("alertEligible", "omitted") for body in bodies for s in body["states"]] == [False, "omitted"]
        assert bodies[0]["states"][0]["revision"] == bodies[1]["states"][0]["revision"]
        assert "rejected alertEligible" in caplog.text
        # The lease is still fresh, but this publisher no longer stamps the field.
        assert _eligibility(publisher, "default") == ["omitted"]
        publisher.publish_snapshot()
        assert len(bodies) == 3


def test_retryable_relay_failure_does_not_strip_alert_eligibility(tmp_path, monkeypatch):
    bodies = []

    @contextmanager
    def opener(request, timeout):
        import urllib.error

        bodies.append(json.loads(request.data))
        raise urllib.error.HTTPError(request.full_url, 503, "busy", {}, io.BytesIO(b""))

    publisher = _publisher(tmp_path, opener=opener)
    with _active_runs(monkeypatch, {"s1": "default"}):
        update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default")
        with pytest.raises(talaria_relay._RelayHTTPError):
            publisher.publish_snapshot()
        assert len(bodies) == 1
        assert publisher._alert_eligibility_supported is True
        assert _eligibility(publisher, "default") == [False]


# ── Authenticated route ─────────────────────────────────────────────────────


class _FakeHeaders(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _RouteFakeHandler:
    def __init__(self, payload):
        body = json.dumps(payload).encode()
        self.headers = _FakeHeaders({"Host": "server.example", "Content-Length": str(len(body))})
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.request = SimpleNamespace()
        self.status = None
        self.client_address = ("127.0.0.1", 12345)

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        pass

    def end_headers(self):
        pass

    def json_body(self):
        return json.loads(self.wfile.getvalue())


def _post_presence(payload):
    from api import routes

    handler = _RouteFakeHandler(payload)
    routes.handle_post(handler, SimpleNamespace(path="/api/talaria/presence", query=""))
    return handler


def test_route_scopes_lease_to_the_request_profile(monkeypatch):
    from api import profiles

    profiles.set_request_profile("member")
    try:
        handler = _post_presence({"tab_id": "tab-aaaaaaaa", "active": True})
    finally:
        profiles.clear_request_profile()
    assert handler.status == 200
    assert handler.json_body() == {"ok": True, "lease_seconds": PRESENCE_LEASE_SECONDS}
    assert set(talaria_relay._presence) == {("member", "tab-aaaaaaaa")}


def test_route_prefers_the_bound_auth_profile(monkeypatch):
    from api import auth, profiles

    monkeypatch.setattr(auth, "ensure_trusted_auth_session", lambda handler: {"bound_profile": "ops"})
    profiles.set_request_profile("member")
    try:
        handler = _post_presence({"tab_id": "tab-aaaaaaaa", "active": True})
    finally:
        profiles.clear_request_profile()
    assert handler.status == 200
    assert set(talaria_relay._presence) == {("ops", "tab-aaaaaaaa")}


def test_route_rejects_malformed_heartbeat():
    handler = _post_presence({"tab_id": "nope", "active": True})
    assert handler.status == 400
    assert talaria_relay._presence == {}


def test_route_revokes_lease():
    update_presence({"tab_id": "tab-aaaaaaaa", "active": True}, profile="default")
    handler = _post_presence({"tab_id": "tab-aaaaaaaa", "active": False})
    assert handler.status == 200
    assert talaria_relay._presence == {}


def test_presence_route_requires_auth_like_every_api_route():
    from api.auth import is_public_path

    assert not is_public_path("/api/talaria/presence")


# ── Browser module ──────────────────────────────────────────────────────────


def test_shell_loads_and_precaches_presence_module():
    index = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    sw = (ROOT / "static" / "sw.js").read_text(encoding="utf-8")
    workspace = (ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
    assert index.index('src="static/presence.js?v=__WEBUI_VERSION__"') > index.index("csrfToken:")
    assert "'./static/presence.js' + VQ" in sw
    assert "window.HermesPresence) await window.HermesPresence.settle();" in workspace


_PRESENCE_HARNESS = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const calls = [];
const log = [];
let now = 100000;
Date.now = () => now;
const docListeners = {}, winListeners = {};
const document = {
  visibilityState: 'visible',
  focused: true,
  baseURI: 'http://localhost:8787/hermes/',
  hasFocus() { return this.focused; },
  addEventListener(type, fn) { (docListeners[type] = docListeners[type] || []).push(fn); },
};
const pending = [];
global.location = { href: 'http://localhost:8787/hermes/' };
global.fetch = (url, opts) => {
  calls.push({ url, method: opts.method, keepalive: opts.keepalive, body: JSON.parse(opts.body) });
  return new Promise((resolve) => pending.push(resolve));
};
global.window = {
  crypto: { randomUUID: () => 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee' },
  addEventListener(type, fn) { (winListeners[type] = winListeners[type] || []).push(fn); },
};
global.document = document;
global.AbortSignal = undefined;
new Function('document', 'window', 'fetch', src)(document, window, global.fetch);
const fire = (type, trusted) => (docListeners[type] || []).forEach((fn) => fn({ type, isTrusted: trusted }));
const fireWin = (type) => (winListeners[type] || []).forEach((fn) => fn({ type, isTrusted: true }));
const tick = () => new Promise((resolve) => setImmediate(resolve));
// Answer every outstanding request and let queued ones start, until quiet.
const flush = async () => { do { while (pending.length) pending.shift()({ ok: true }); await tick(); } while (pending.length); };
const count = (label) => log.push([label, calls.length]);

(async () => {
  await tick(); count('load');
  fire('keydown', false); await flush(); count('untrusted keydown');
  fire('scroll', true); fire('mousemove', true); fire('focus', true); await flush(); count('scroll/mousemove/focus');
  document.visibilityState = 'hidden'; fire('keydown', true); await flush(); count('hidden keydown');
  document.visibilityState = 'visible'; document.focused = false; fire('pointerdown', true); await flush(); count('unfocused pointerdown');
  document.focused = true;
  fire('keydown', true); await tick(); count('qualifying keydown');
  let settled = false;
  window.HermesPresence.settle().then(() => { settled = true; });
  await tick();
  const settledBeforeResponse = settled;
  await flush();
  const settledAfterResponse = settled;
  now += 5000; fire('pointerdown', true); await flush(); count('pointerdown within throttle');
  now += 10000; fire('wheel', true); await flush(); count('wheel after throttle');
  document.visibilityState = 'hidden'; fire('visibilitychange', true); await flush(); count('hidden revoke');
  fire('visibilitychange', true); await flush(); count('hidden again');
  document.visibilityState = 'visible';
  now += 1000; fire('keydown', true); await flush(); count('keydown right after revoke');
  fireWin('blur'); await flush(); count('blur revoke');
  fire('pointerdown', true); await flush(); count('pointerdown after blur');
  fireWin('pagehide'); await flush(); count('pagehide revoke');
  // A revocation must wait for the renewal in flight ahead of it.
  fire('keydown', true); await tick(); const renewalInFlight = calls.length;
  document.visibilityState = 'hidden'; fire('visibilitychange', true); await tick(); const revokeWhileRenewing = calls.length;
  await flush(); count('revoke after renewal answered');
  process.stdout.write(JSON.stringify({
    log, calls, settledBeforeResponse, settledAfterResponse, renewalInFlight, revokeWhileRenewing,
    tabId: window.HermesPresence.tabId,
    listeners: Object.keys(docListeners).sort(),
  }));
})();
"""


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_browser_module_renews_only_on_trusted_input_in_a_visible_focused_tab():
    with tempfile.NamedTemporaryFile("w", suffix=".cjs", encoding="utf-8", dir=ROOT, delete=False) as script:
        script.write(_PRESENCE_HARNESS)
        script_path = Path(script.name)
    try:
        result = subprocess.run(
            [NODE, str(script_path), str(ROOT / "static" / "presence.js")],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        script_path.unlink(missing_ok=True)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout)

    assert dict(out["log"]) == {
        "load": 0,
        "untrusted keydown": 0,
        "scroll/mousemove/focus": 0,
        "hidden keydown": 0,
        "unfocused pointerdown": 0,
        "qualifying keydown": 1,
        "pointerdown within throttle": 1,
        "wheel after throttle": 2,
        "hidden revoke": 3,
        "hidden again": 3,
        "keydown right after revoke": 4,
        "blur revoke": 5,
        "pointerdown after blur": 6,
        "pagehide revoke": 7,
        "revoke after renewal answered": 9,
    }
    assert out["renewalInFlight"] == 8
    assert out["revokeWhileRenewing"] == 8, "revocation must not overtake the in-flight renewal"
    assert out["listeners"] == ["keydown", "pointerdown", "visibilitychange", "wheel"]
    tab_id = out["tabId"]
    assert tab_id == "aaaaaaaabbbbccccddddeeeeeeeeeeee"
    assert {call["url"] for call in out["calls"]} == {"http://localhost:8787/hermes/api/talaria/presence"}
    assert all(call["method"] == "POST" and call["body"]["tab_id"] == tab_id for call in out["calls"])
    assert [(call["body"]["active"], call["keepalive"]) for call in out["calls"]] == [
        (True, False),
        (True, False),
        (False, True),
        (True, False),
        (False, True),
        (True, False),
        (False, True),
        (True, False),
        (False, True),
    ]
    assert all(set(call["body"]) == {"tab_id", "active"} for call in out["calls"])
    assert out["settledBeforeResponse"] is False
    assert out["settledAfterResponse"] is True
