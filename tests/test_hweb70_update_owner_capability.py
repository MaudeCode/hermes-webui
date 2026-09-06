"""HWEB-70: owner-only update controls are explained before non-owners try them.

Server: ``/api/auth/status`` exposes ``can_manage_server`` from the same helper
that guards ``OPERATOR_ONLY_PATHS``. Frontend: apply / force / clear-lock stay
disabled unless that capability is exactly ``true``.
"""
from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

import api.auth as auth
import api.passkeys as passkeys
import api.routes as routes
from tests.js_source_extract import extract_function

ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


class _Handler:
    def __init__(self, cookie=None):
        self.headers = {"Host": "localhost:8787"}
        if cookie:
            self.headers["Cookie"] = f"hermes_session={cookie}"
        self.request = SimpleNamespace()
        self.wfile = io.BytesIO()
        self.status = None
        self.sent_headers = []

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent_headers.append((key, value))

    def end_headers(self):
        pass

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _auth_enabled(monkeypatch, session_info):
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "is_oidc_auth_enabled", lambda: True)
    monkeypatch.setattr(auth, "is_trusted_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "_passkey_feature_flag_enabled", lambda: False)
    monkeypatch.setattr(auth, "get_password_hash", lambda: None)
    monkeypatch.setattr(auth, "parse_cookie", lambda _h: "cookie" if session_info else None)
    monkeypatch.setattr(auth, "verify_session", lambda _c: bool(session_info))
    monkeypatch.setattr(auth, "ensure_trusted_auth_session", lambda _h: session_info)
    monkeypatch.setattr(passkeys, "registered_credentials", lambda: [])


def _status(handler):
    routes.handle_get(handler, urlparse("http://example.com/api/auth/status"))
    assert handler.status == 200
    return handler.json_body()


# ── shared helper ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "session_info, expected",
    [
        (None, False),
        ({"auth_type": None, "username": None, "bound_profile": None}, True),
        ({"auth_type": "oidc", "username": "alice", "bound_profile": "alice"}, False),
        ({"auth_type": "oidc", "username": "d", "bound_profile": "default"}, False),
        ({"auth_type": "trusted", "username": "ops", "bound_profile": "ops"}, False),
    ],
)
def test_helper_with_auth_enabled(monkeypatch, session_info, expected):
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    assert auth.session_can_manage_server(session_info) is expected


def test_helper_with_auth_disabled_is_owner_for_everyone(monkeypatch):
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    assert auth.session_can_manage_server(None) is True
    assert auth.session_can_manage_server({"bound_profile": "alice"}) is True


# ── /api/auth/status and the route guard agree ───────────────────────────────


@pytest.mark.parametrize("bound", ["alice", "default"])
def test_bound_session_reports_false_and_is_still_denied_directly(monkeypatch, bound):
    info = {"auth_type": "oidc", "username": "user@example.com", "bound_profile": bound}
    _auth_enabled(monkeypatch, info)

    assert _status(_Handler("cookie"))["can_manage_server"] is False

    for path in ("/api/updates/apply", "/api/updates/force", "/api/updates/clear_lock"):
        handler = _Handler("cookie")
        assert auth.check_auth(handler, SimpleNamespace(path=path, query="")) is False
        assert handler.status == 403
        assert handler.json_body() == {"error": "Owner session required"}


def test_unbound_owner_session_reports_true_and_passes_guard(monkeypatch):
    info = {"auth_type": None, "username": None, "bound_profile": None}
    _auth_enabled(monkeypatch, info)
    monkeypatch.setattr(auth, "trusted_session_allows_active_profile", lambda _i: True)

    assert _status(_Handler("cookie"))["can_manage_server"] is True
    handler = _Handler("cookie")
    assert auth.check_auth(handler, SimpleNamespace(path="/api/updates/apply", query="")) is True
    assert handler.status is None


def test_unauthenticated_with_auth_enabled_reports_false(monkeypatch):
    _auth_enabled(monkeypatch, None)
    payload = _status(_Handler())
    assert payload["logged_in"] is False
    assert payload["can_manage_server"] is False


def test_auth_disabled_reports_true(monkeypatch):
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "is_oidc_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "is_trusted_auth_enabled", lambda: False)
    monkeypatch.setattr(auth, "_passkey_feature_flag_enabled", lambda: False)
    monkeypatch.setattr(auth, "get_password_hash", lambda: None)
    monkeypatch.setattr(passkeys, "registered_credentials", lambda: [])
    monkeypatch.setattr(routes, "load_settings", lambda: {})

    payload = _status(_Handler())
    assert payload["auth_enabled"] is False
    assert payload["can_manage_server"] is True


# ── frontend harness ─────────────────────────────────────────────────────────

_FUNCTIONS = [
    ("_showUpdateBanner", "function"),
    ("_updateMutationAllowed", "function"),
    ("_renderUpdateCapability", "function"),
    ("_syncUpdateCapability", "async function"),
    ("_noteUpdateForbidden", "async function"),
    ("_i18nUpdateText", "function"),
    ("_isUpdateApplyNetworkError", "function"),
    ("_formatUpdateApplyExceptionMessage", "function"),
    ("applyUpdates", "async function"),
    ("_showUpdateError", "function"),
    ("applyClearUpdateLock", "async function"),
    ("forceUpdate", "async function"),
]

_HARNESS = r"""
const scenario = JSON.parse(process.argv[1]);
const authResponses = scenario.auth.slice();
const updateResponses = scenario.updates.slice();

function el(extra) { return Object.assign({ disabled: false, textContent: '', style: { display: '' }, dataset: {} }, extra || {}); }
const dom = {
  updateBanner: { classList: { classes: new Set(), add(c) { this.classes.add(c); }, remove(c) { this.classes.delete(c); } } },
  updateMsg: el(),
  updateError: el({ style: { display: 'none' } }),
  updateOwnerNote: el({ style: { display: 'none' } }),
  btnApplyUpdate: el({ textContent: 'Update Now' }),
  btnForceUpdate: el({ style: { display: 'none' }, textContent: 'Force update', dataset: { target: 'webui' } }),
  btnClearUpdateLock: el({ style: { display: 'none' }, textContent: 'Clear lock', dataset: { target: 'webui' } }),
  btnUpdatePermissionRetry: el({ style: { display: 'none' } }),
};
const apiCalls = [];
const waitCalls = [];
const toasts = [];

global.window = { _updateApplyInFlight: false, _clearLockInFlight: false, _updateData: scenario.updateData };
global.sessionStorage = { removeItem() {}, setItem() {} };
global.$ = (id) => dom[id] || null;
global.api = async (path, opts) => {
  if (path === '/api/auth/status') {
    const res = authResponses.length > 1 ? authResponses.shift() : authResponses[0];
    if (res && res.throwMessage) throw new Error(res.throwMessage);
    return res;
  }
  apiCalls.push({ path, body: JSON.parse(opts.body) });
  const res = updateResponses.shift() || { ok: true };
  if (res.httpStatus) { const e = new Error(res.message || 'HTTP ' + res.httpStatus); e.status = res.httpStatus; throw e; }
  return res;
};
global._readHealthServerIdentity = async () => 'baseline';
global._waitForServerThenReload = (opts) => waitCalls.push(opts);
global.showToast = (message) => toasts.push(message);
global.showConfirmDialog = async () => true;
global._renderLockManualInstruction = () => {};
global._formatUpdateTargetStatus = (label, t) => (t && t.behind > 0 ? label : '');
global._formatManualUpdateInstruction = () => '';
global._renderUpdateWhatsNewLinks = () => {};
global._hideUpdateSummaryPanel = () => {};
global.setTimeout = (cb) => { cb(); return 1; };
global.clearTimeout = () => {};

__FUNCTIONS__

(async () => {
  for (const action of scenario.actions) {
    if (action === 'banner') _showUpdateBanner(scenario.updateData);
    else if (action === 'settle') await new Promise((r) => setImmediate(r));
    else if (action === 'apply') await applyUpdates();
    else if (action === 'force') await forceUpdate(dom.btnForceUpdate);
    else if (action === 'clearLock') await applyClearUpdateLock(dom.btnClearUpdateLock);
    else if (action === 'retry') await _syncUpdateCapability();
    else throw new Error('unknown action ' + action);
  }
  console.log(JSON.stringify({
    apiCalls,
    waitCalls: waitCalls.length,
    canManage: window._updateCanManage === undefined ? 'undefined' : window._updateCanManage,
    applyDisabled: dom.btnApplyUpdate.disabled,
    applyText: dom.btnApplyUpdate.textContent,
    forceDisabled: dom.btnForceUpdate.disabled,
    clearLockDisabled: dom.btnClearUpdateLock.disabled,
    retryDisplay: dom.btnUpdatePermissionRetry.style.display,
    noteDisplay: dom.updateOwnerNote.style.display,
    noteText: dom.updateOwnerNote.textContent,
    errorText: dom.updateError.textContent,
    inFlight: window._updateApplyInFlight,
    lockInFlight: window._clearLockInFlight,
  }));
})().catch((error) => { console.error(error.stack || String(error)); process.exit(1); });
"""


def _run(actions, *, auth=None, updates=None, update_data=None):
    if NODE is None:
        pytest.skip("node not available")
    functions = "\n".join(extract_function(UI_JS, name, prefix) for name, prefix in _FUNCTIONS)
    scenario = {
        "actions": actions,
        "auth": auth if auth is not None else [{"can_manage_server": True}],
        "updates": updates or [],
        "updateData": update_data or {"webui": {"behind": 2}, "agent": {"behind": 1}},
    }
    result = subprocess.run(
        [NODE, "-e", _HARNESS.replace("__FUNCTIONS__", functions), json.dumps(scenario)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"node harness failed: {result.stderr or result.stdout}")
    return json.loads(result.stdout.strip().splitlines()[-1])


def _mutations(out):
    return [c["path"] for c in out["apiCalls"] if c["path"].startswith("/api/updates/")]


def _assert_locked_out(out):
    assert out["applyDisabled"] is True
    assert out["forceDisabled"] is True
    assert out["clearLockDisabled"] is True
    assert out["noteDisplay"] == "block"


def test_bound_session_sees_explanation_and_sends_no_mutation():
    out = _run(["banner", "settle", "apply", "force", "clearLock"], auth=[{"can_manage_server": False}])
    assert _mutations(out) == []
    _assert_locked_out(out)
    assert "owner session" in out["noteText"]
    assert out["retryDisplay"] == "none"
    assert out["inFlight"] is False and out["lockInFlight"] is False


def test_owner_session_keeps_existing_multi_target_apply():
    out = _run(["banner", "settle", "apply"], updates=[{"ok": True}, {"ok": True}])
    assert [c["body"]["target"] for c in out["apiCalls"]] == ["agent", "webui"]
    assert out["waitCalls"] == 1
    assert out["noteDisplay"] == "none"


def test_owner_force_and_clear_lock_still_send_requests():
    out = _run(["banner", "settle", "force", "clearLock"], updates=[{"ok": True}, {"ok": True}])
    assert _mutations(out) == ["/api/updates/force", "/api/updates/clear_lock"]
    assert out["waitCalls"] == 2


@pytest.mark.parametrize("status", [{}, {"can_manage_server": "true"}, {"can_manage_server": None}, None])
def test_missing_or_non_boolean_capability_never_enables(status):
    out = _run(["banner", "settle", "apply", "force", "clearLock"], auth=[status])
    assert _mutations(out) == []
    _assert_locked_out(out)
    assert out["canManage"] is False


def test_failed_capability_fetch_disables_and_retry_recovers():
    out = _run(["banner", "settle"], auth=[{"throwMessage": "Failed to fetch"}])
    _assert_locked_out(out)
    assert out["canManage"] == "error"
    assert out["retryDisplay"] == ""
    assert "Could not confirm" in out["noteText"]

    out = _run(["banner", "settle", "retry", "apply"], auth=[{"throwMessage": "Failed to fetch"}, {"can_manage_server": True}], updates=[{"ok": True}, {"ok": True}])
    assert len(_mutations(out)) == 2
    assert out["noteDisplay"] == "none"
    assert out["retryDisplay"] == "none"


def test_loading_state_keeps_controls_disabled_until_answer():
    out = _run(["banner"], auth=[{"can_manage_server": True}])
    assert out["canManage"] == "undefined"
    _assert_locked_out(out)
    assert "Checking" in out["noteText"]


def test_permission_lost_between_banner_and_click_blocks_apply():
    out = _run(["banner", "settle", "apply"], auth=[{"can_manage_server": True}, {"can_manage_server": False}])
    assert _mutations(out) == []
    _assert_locked_out(out)
    assert out["applyText"] == "Update Now"
    assert out["inFlight"] is False


@pytest.mark.parametrize("action, path", [("apply", "/api/updates/apply"), ("force", "/api/updates/force"), ("clearLock", "/api/updates/clear_lock")])
def test_stale_capability_then_403_disables_and_explains(action, path):
    # banner -> true, pre-mutation recheck -> true, post-403 re-read -> false
    auth = [{"can_manage_server": True}, {"can_manage_server": True}, {"can_manage_server": False}]
    out = _run(["banner", "settle", action], auth=auth, updates=[{"httpStatus": 403, "message": "Owner session required"}])
    assert _mutations(out) == [path]
    assert out["waitCalls"] == 0
    assert out["canManage"] is False
    _assert_locked_out(out)
    assert "owner session" in out["noteText"]
    assert "Owner session required" in out["errorText"]
    assert out["inFlight"] is False and out["lockInFlight"] is False


def test_non_403_failure_reset_reenables_only_for_owner():
    out = _run(["banner", "settle", "apply"], updates=[{"httpStatus": 500, "message": "boom"}])
    assert out["canManage"] is True
    assert out["applyDisabled"] is False
    assert out["inFlight"] is False


@pytest.mark.parametrize("action", ["apply", "force", "clearLock"])
def test_403_from_another_gate_does_not_lock_out_a_still_owner(action):
    """A CSRF/origin 403 must not be mistaken for loss of owner permission."""
    out = _run(["banner", "settle", action], updates=[{"httpStatus": 403, "message": "CSRF token mismatch"}])
    assert out["canManage"] is True
    assert out["applyDisabled"] is False
    assert out["forceDisabled"] is False
    assert out["clearLockDisabled"] is False
    assert out["noteDisplay"] == "none"
    assert "CSRF token mismatch" in out["errorText"]
    assert out["inFlight"] is False and out["lockInFlight"] is False


@pytest.mark.parametrize("status", [{"can_manage_server": False}, {"throwMessage": "Failed to fetch"}])
def test_manual_only_banner_shows_no_permission_copy(status):
    """No in-app apply target means no owner note and no retry button (Codex P2)."""
    manual_only = {"webui": {"behind": 1, "manual_update": True, "no_git": True}, "agent": None}
    out = _run(["banner", "settle"], auth=[status], update_data=manual_only)
    assert out["noteDisplay"] == "none"
    assert out["noteText"] == ""
    assert out["retryDisplay"] == "none"
    assert out["applyDisabled"] is True
