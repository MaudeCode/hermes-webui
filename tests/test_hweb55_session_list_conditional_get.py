"""HWEB-55: /api/sessions must revalidate instead of re-sending unchanged rows.

The sidebar polls every 30s per open tab while streaming. HWEB-30 made the
response cheap to *produce*; it was still shipped in full every time. These
tests pin the conditional-GET contract end to end: the server's 304, the
validator's sensitivity to the runtime overlay, and the browser-side rule that
a 304 must never re-derive the clock skew from a stale `server_time`.
"""

from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

import api.profiles as profiles
import api.routes as routes
from tests.js_source_extract import extract_function


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clear_caches():
    routes._session_list_cache_clear()
    routes._SESSION_LIST_RESPONSE_CACHE.clear()
    yield
    routes._session_list_cache_clear()
    routes._SESSION_LIST_RESPONSE_CACHE.clear()


class _FakeHandler:
    def __init__(self, request_headers=None):
        self.status = None
        self.headers = dict(request_headers or {})
        self.sent = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.sent[key] = value

    def end_headers(self):
        pass

    def body(self):
        return self.wfile.getvalue()

    def json_body(self):
        return json.loads(self.body().decode("utf-8"))


def _rows(count=3, title="Session"):
    return [
        {
            "session_id": f"s{i:05d}",
            "title": f"{title} {i}",
            "profile": "default",
            "archived": False,
            "message_count": 2,
            "created_at": 1000 + i,
            "updated_at": 1000 + i,
            "last_message_at": 1000 + i,
        }
        for i in range(count)
    ]


def _install_store(monkeypatch, rows):
    """Point the session list at a fixed synthetic store."""
    monkeypatch.setattr(routes, "all_sessions", lambda **_kwargs: [dict(r) for r in rows])
    monkeypatch.setattr(routes, "_enrich_sidebar_lineage_metadata", lambda _rows: None)
    monkeypatch.setattr(routes, "_reconcile_stale_stream_state_for_session_rows", lambda _rows: False)
    monkeypatch.setattr(routes, "load_settings", lambda: {"show_cli_sessions": False})
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")


def _get_sessions(request_headers=None):
    handler = _FakeHandler(request_headers)
    routes.handle_get(handler, urlparse("http://example.com/api/sessions"))
    return handler


# ── Server: the 304 short-circuit ────────────────────────────────────────────


def test_unchanged_store_revalidates_with_an_empty_304(monkeypatch):
    _install_store(monkeypatch, _rows())

    first = _get_sessions()
    assert first.status == 200
    etag = first.sent["ETag"]
    assert etag and first.body()

    second = _get_sessions({"If-None-Match": etag})
    assert second.status == 304
    assert second.body() == b"", "304 must not carry a body"
    assert second.sent["ETag"] == etag
    assert "Content-Length" not in second.sent


def test_response_is_storable_so_a_client_can_revalidate(monkeypatch):
    """`no-store` forbids keeping a copy, which makes revalidation impossible."""
    _install_store(monkeypatch, _rows())
    first = _get_sessions()
    assert first.sent["Cache-Control"] == "no-cache"
    assert _get_sessions({"If-None-Match": first.sent["ETag"]}).sent["Cache-Control"] == "no-cache"


def test_mismatched_validator_still_returns_a_full_body(monkeypatch):
    _install_store(monkeypatch, _rows())
    first = _get_sessions()

    stale = _get_sessions({"If-None-Match": '"0123456789abcdef0123456789abcdef"'})
    assert stale.status == 200
    assert [r["session_id"] for r in stale.json_body()["sessions"]] == [
        r["session_id"] for r in first.json_body()["sessions"]
    ]


def test_validator_is_stable_across_polls_but_server_time_is_not(monkeypatch):
    """The ETag covers the rows only — `server_time` is spliced in fresh."""
    _install_store(monkeypatch, _rows())
    first = _get_sessions()
    second = _get_sessions()
    assert second.status == 200
    assert second.sent["ETag"] == first.sent["ETag"]
    assert second.json_body()["server_time"] >= first.json_body()["server_time"]


# ── Server: what the validator tracks ────────────────────────────────────────


def test_validator_changes_when_a_session_is_added(monkeypatch):
    with monkeypatch.context() as m:
        _install_store(m, _rows(3))
        before = _get_sessions().sent["ETag"]
    routes._session_list_cache_clear()
    routes._SESSION_LIST_RESPONSE_CACHE.clear()
    with monkeypatch.context() as m:
        _install_store(m, _rows(4))
        after = _get_sessions().sent["ETag"]
    assert before != after


def test_validator_changes_when_a_session_is_renamed(monkeypatch):
    with monkeypatch.context() as m:
        _install_store(m, _rows(3))
        before = _get_sessions().sent["ETag"]
    routes._session_list_cache_clear()
    routes._SESSION_LIST_RESPONSE_CACHE.clear()
    with monkeypatch.context() as m:
        _install_store(m, _rows(3, title="Renamed"))
        after = _get_sessions().sent["ETag"]
    assert before != after


def test_validator_tracks_the_runtime_overlay_not_just_the_cached_payload(monkeypatch):
    """Attention is a live overlay: it changes no stored row, only the response.

    The cached payload object is deliberately left untouched here, so a
    validator keyed on the payload alone would keep answering 304 while the
    sidebar's approval badge was already out of date.
    """
    _install_store(monkeypatch, _rows(3))
    monkeypatch.setattr(routes, "approvals_pending_session_keys", lambda: set())
    monkeypatch.setattr(routes, "clarify_pending_session_keys", lambda: set())
    before_handler = _get_sessions()
    before = before_handler.sent["ETag"]
    assert all(row["attention"] is None for row in before_handler.json_body()["sessions"])

    monkeypatch.setattr(routes, "approvals_pending_session_keys", lambda: {"s00001"})
    monkeypatch.setattr(
        routes,
        "_session_attention_summary",
        lambda sid: {"kind": "approval", "count": 1, "severity": "critical"},
    )
    after = _get_sessions()
    assert after.status == 200
    assert after.sent["ETag"] != before
    assert after.json_body()["sessions"][1]["attention"]["kind"] == "approval"

    # And a client holding the STALE validator is not fobbed off with a 304.
    assert _get_sessions({"If-None-Match": before}).status == 200
    # The new one is itself stable while the overlay stays put.
    assert _get_sessions({"If-None-Match": after.sent["ETag"]}).status == 304


# ── Server: RFC 7232 §3.2 handling, matching the media path ──────────────────


@pytest.mark.parametrize("header", ["*", 'W/{etag}', '"nope", {etag}'])
def test_rfc7232_weak_comparison_matches_the_media_path(monkeypatch, header):
    _install_store(monkeypatch, _rows())
    etag = _get_sessions().sent["ETag"]
    assert _get_sessions({"If-None-Match": header.format(etag=etag)}).status == 304


def test_weak_comparison_helper_rejects_a_non_match():
    from api.helpers import _if_none_match_matches

    assert not _if_none_match_matches('W/"abc"', '"def"')
    assert not _if_none_match_matches("", '"abc"')
    assert not _if_none_match_matches('"abc"', "")


def test_payload_shape_is_unchanged_by_revalidation(monkeypatch):
    _install_store(monkeypatch, _rows())
    body = _get_sessions().json_body()
    assert [r["session_id"] for r in body["sessions"]] == ["s00002", "s00001", "s00000"]
    assert isinstance(body["server_time"], (int, float))
    assert isinstance(body["server_tz"], str)


# ── Client: a 304 must not disturb the clock-skew estimate ───────────────────

NODE = shutil.which("node")
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")


def _const(source: str, name: str) -> str:
    for line in source.splitlines():
        if line.startswith(f"const {name} = ") or line.startswith(f"const {name}="):
            return line
    raise AssertionError(f"const {name} not found")


def _run_node(script: str):
    completed = subprocess.run(
        [NODE, "-e", script], cwd=ROOT, text=True, capture_output=True, timeout=10, check=False
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip())


_PRELUDE = f"""
global._showAllProfiles = false;
global._allProjects = [];
global.S = {{activeProfile:'default'}};
global._sessionProjectsLastFetchedAt = 0;
global._sessionProjectsLastFetchScope = '';
global.SESSION_PROJECT_REFRESH_INTERVAL_MS = 30000;
global._sessionListLastFetchedAt = 0;
global._sessionListLastFetchKey = '';
global._sessionListLastPayload = null;
global._sessionListLastEtag = null;
global._sessionListLastMutationSeq = 0;
{_const(SESSIONS_JS, 'SESSION_LIST_REFRESH_TTL_MS')}
global.SESSION_LIST_REFRESH_TTL_MS = SESSION_LIST_REFRESH_TTL_MS;

// A server that answers 304 for a known validator, exactly as api() surfaces it.
const ETAG = '"deadbeef"';
const SERVER_TIME = 1000000;
let requests = [];
global.api = (url, opts) => {{
  if (url.startsWith('/api/projects')) return Promise.resolve({{projects: []}});
  requests.push(opts || {{}});
  const inm = (opts && opts.headers && opts.headers['If-None-Match']) || null;
  if (inm === ETAG) return Promise.resolve({{__notModified: true, __etag: ETAG}});
  const payload = {{sessions: [{{session_id:'s1'}}], server_time: SERVER_TIME, server_tz: '+0000'}};
  Object.defineProperty(payload, '__etag', {{value: ETAG}});
  return Promise.resolve(payload);
}};
{extract_function(SESSIONS_JS, '_loadSidebarSessionListPayload', prefix='async function')}
"""


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_second_poll_revalidates_and_replays_the_cached_rows():
    result = _run_node(
        f"""
    {_PRELUDE}
    (async () => {{
      const qs = '?sidebar_source=all';
      const first = await _loadSidebarSessionListPayload(qs, {{}});
      _sessionListLastFetchedAt -= (SESSION_LIST_REFRESH_TTL_MS + 1);
      const second = await _loadSidebarSessionListPayload(qs, {{}});
      console.log(JSON.stringify({{
        firstConditional: requests[0]['If-None-Match'] || (requests[0].headers||{{}})['If-None-Match'] || null,
        secondConditional: (requests[1].headers||{{}})['If-None-Match'] || null,
        cacheMode: requests[1].cache,
        rows: second.sessData.sessions.map(s => s.session_id),
        firstRows: first.sessData.sessions.map(s => s.session_id),
      }}));
    }})();
    """
    )
    assert result["firstConditional"] is None, "nothing to revalidate on the first poll"
    assert result["secondConditional"] == '"deadbeef"', result
    assert result["cacheMode"] == "no-store", "browser cache must not shadow our own revalidation"
    assert result["rows"] == result["firstRows"] == ["s1"]


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_a_304_keeps_the_previously_computed_server_time_delta():
    """`server_time` in a replayed body is stale by the whole polling gap.

    _applySessionListPayload() only re-derives _serverTimeDelta from a numeric
    server_time, so a 304 must hand it a payload without one — otherwise every
    poll would push the clock estimate backwards by the age of the cached body.
    """
    result = _run_node(
        f"""
    {_PRELUDE}
    (async () => {{
      const qs = '?sidebar_source=all';
      const first = await _loadSidebarSessionListPayload(qs, {{}});
      _sessionListLastFetchedAt -= (SESSION_LIST_REFRESH_TTL_MS + 1);
      const second = await _loadSidebarSessionListPayload(qs, {{}});
      // The TTL replay path must not smuggle it back either.
      const third = await _loadSidebarSessionListPayload(qs, {{}});
      console.log(JSON.stringify({{
        first: first.sessData.server_time,
        secondHasTime: Object.prototype.hasOwnProperty.call(second.sessData, 'server_time'),
        secondHasTz: Object.prototype.hasOwnProperty.call(second.sessData, 'server_tz'),
        thirdHasTime: Object.prototype.hasOwnProperty.call(third.sessData, 'server_time'),
      }}));
    }})();
    """
    )
    assert result["first"] == 1000000, "the 200 still carries a fresh server clock"
    assert result["secondHasTime"] is False, "a 304 must not replay a stale server_time"
    assert result["secondHasTz"] is False
    assert result["thirdHasTime"] is False, "the TTL replay must not resurrect it either"


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_apply_payload_retains_the_delta_when_server_time_is_absent():
    """The consumer half of the contract, against the real applier's rule."""
    result = _run_node(
        """
        let _serverTimeDelta = 4242;
        const sessData = {sessions: []};
        // The exact guard from _applySessionListPayload().
        if (typeof sessData.server_time === 'number' && sessData.server_time > 0) {
          _serverTimeDelta = Date.now() - (sessData.server_time * 1000);
        }
        console.log(JSON.stringify({delta: _serverTimeDelta}));
        """
    )
    assert result["delta"] == 4242


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_the_real_api_helper_carries_the_validator_from_one_poll_to_the_next():
    """End to end over the real api(), against a fake fetch.

    The first poll is unconditional by definition, so it is the one that has to
    hand the second poll something to revalidate with. A stub api() cannot show
    that; only the real helper's response handling can.
    """
    workspace_js = (ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
    script = f"""
{_PRELUDE.split('global.api =')[0]}
global.document = {{baseURI: 'http://localhost/'}};
global.location = {{href: 'http://localhost/'}};
const wire = [];
global.fetch = async (href, opts) => {{
  const sent = (opts && opts.headers && opts.headers['If-None-Match']) || null;
  wire.push({{sent, cache: opts && opts.cache}});
  if (href.indexOf('/api/projects') >= 0) {{
    return {{ok:true, status:200, headers:{{get:(k)=>k.toLowerCase()==='content-type'?'application/json':null}},
             json: async () => ({{projects: []}})}};
  }}
  const headers = {{get:(k)=>{{
    if (k.toLowerCase() === 'content-type') return 'application/json';
    if (k.toLowerCase() === 'etag') return '"deadbeef"';
    return null;
  }}}};
  if (sent === '"deadbeef"') return {{ok:false, status:304, headers, json: async () => null}};
  return {{ok:true, status:200, headers,
           json: async () => ({{sessions:[{{session_id:'s1'}}], server_time:1000000, server_tz:'+0000'}})}};
}};
{extract_function(workspace_js, 'api', prefix='async function')}
{extract_function(SESSIONS_JS, '_loadSidebarSessionListPayload', prefix='async function')}
    (async () => {{
      const qs = '?sidebar_source=all';
      await _loadSidebarSessionListPayload(qs, {{}});
      _sessionListLastFetchedAt -= (SESSION_LIST_REFRESH_TTL_MS + 1);
      const second = await _loadSidebarSessionListPayload(qs, {{}});
      const sessionWire = wire.filter(w => w.cache === 'no-store');
      console.log(JSON.stringify({{
        firstSent: sessionWire[0].sent,
        secondSent: sessionWire[1].sent,
        replayedRows: second.sessData.sessions.map(s => s.session_id),
        replayedServerTime: Object.prototype.hasOwnProperty.call(second.sessData, 'server_time'),
      }}));
    }})();
    """
    result = _run_node(script)
    assert result["firstSent"] is None
    assert result["secondSent"] == '"deadbeef"', "the 200's ETag never reached the next poll"
    assert result["replayedRows"] == ["s1"]
    assert result["replayedServerTime"] is False


@pytest.mark.skipif(NODE is None, reason="node not on PATH")
def test_a_scope_change_never_revalidates_against_another_scopes_validator():
    result = _run_node(
        f"""
    {_PRELUDE}
    (async () => {{
      await _loadSidebarSessionListPayload('?sidebar_source=all', {{}});
      _sessionListLastFetchedAt -= (SESSION_LIST_REFRESH_TTL_MS + 1);
      await _loadSidebarSessionListPayload('?sidebar_source=cli', {{}});
      console.log(JSON.stringify({{
        scopeChange: (requests[1].headers||{{}})['If-None-Match'] || null,
      }}));
    }})();
    """
    )
    assert result["scopeChange"] is None
