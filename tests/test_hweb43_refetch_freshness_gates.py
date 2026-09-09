"""HWEB-43: freshness gates on session-list and panel refetching, plus api() dedupe.

Every test here drives the real extracted function under node so it asserts
observable behaviour (how many fetches/loads actually happened), not source text.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.js_source_extract import extract_function


ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
PANELS_JS = (ROOT / "static" / "panels.js").read_text(encoding="utf-8")
WORKSPACE_JS = (ROOT / "static" / "workspace.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _js(source: str, name: str) -> str:
    """Extract `name` whether it is declared async or not."""
    if f"async function {name}(" in source:
        return extract_function(source, name, prefix="async function")
    return extract_function(source, name)


def _const(source: str, name: str) -> str:
    for line in source.splitlines():
        if line.startswith(f"const {name} = ") or line.startswith(f"const {name}="):
            return line
    raise AssertionError(f"const {name} not found")


def _run_node(script: str, timeout: float = 10.0):
    completed = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout.strip())


# ── 1 + 5. Session list TTL ──────────────────────────────────────────────────

_SIDEBAR_PRELUDE = f"""
global._showAllProfiles = false;
global._allProjects = [];
global.S = {{activeProfile:'default'}};
global._sessionProjectsLastFetchedAt = 0;
global._sessionProjectsLastFetchScope = '';
global.SESSION_PROJECT_REFRESH_INTERVAL_MS = 30000;
global._sessionListLastFetchedAt = 0;
global._sessionListLastFetchKey = '';
global._sessionListLastPayload = null;
{_const(SESSIONS_JS, 'SESSION_LIST_REFRESH_TTL_MS')}
global.SESSION_LIST_REFRESH_TTL_MS = SESSION_LIST_REFRESH_TTL_MS;
let sessionFetches = 0;
let sessionTitle = 'first turn';
let holdSessions = null;
global.api = (url) => {{
  if (url.startsWith('/api/projects')) return Promise.resolve({{projects: []}});
  if (url.startsWith('/api/sessions')) {{
    sessionFetches += 1;
    const payload = {{sessions: [{{session_id:'s1', title: sessionTitle}}]}};
    if (holdSessions) return holdSessions.then(() => payload);
    return Promise.resolve(payload);
  }}
  return Promise.reject(new Error('unexpected endpoint ' + url));
}};
{_js(SESSIONS_JS, '_loadSidebarSessionListPayload')}
"""


def test_burst_of_turn_transitions_issues_one_session_fetch_per_ttl_window():
    """Stream start/apperror/done/terminal all render the sidebar; one fetch is enough."""
    script = f"""
    {_SIDEBAR_PRELUDE}
    (async () => {{
      const qs = '?sidebar_source=all';
      // Five back-to-back lifecycle renders, exactly as one turn transition emits them.
      for (let i = 0; i < 5; i++) await _loadSidebarSessionListPayload(qs, {{}});
      const gated = sessionFetches;
      // A caller that knows the data changed bypasses the window.
      await _loadSidebarSessionListPayload(qs, {{}}, {{force:true}});
      const afterForce = sessionFetches;
      // Once the window lapses, an ordinary render refetches again.
      _sessionListLastFetchedAt -= (SESSION_LIST_REFRESH_TTL_MS + 1);
      await _loadSidebarSessionListPayload(qs, {{}});
      console.log(JSON.stringify({{gated, afterForce, afterExpiry: sessionFetches}}));
    }})();
    """
    result = _run_node(script)
    assert result["gated"] == 1, result
    assert result["afterForce"] == 2, result
    assert result["afterExpiry"] == 3, result


def test_session_list_window_is_scoped_to_the_request_identity():
    """A different sidebar scope must never be served the previous scope's rows."""
    script = f"""
    {_SIDEBAR_PRELUDE}
    (async () => {{
      await _loadSidebarSessionListPayload('?sidebar_source=all', {{}});
      const afterFirst = sessionFetches;
      await _loadSidebarSessionListPayload('?sidebar_source=cli', {{}});
      const afterScopeChange = sessionFetches;
      global.S = {{activeProfile:'other'}};
      await _loadSidebarSessionListPayload('?sidebar_source=cli', {{}});
      console.log(JSON.stringify({{
        afterFirst, afterScopeChange, afterProfileChange: sessionFetches,
      }}));
    }})();
    """
    result = _run_node(script)
    assert result == {"afterFirst": 1, "afterScopeChange": 2, "afterProfileChange": 3}


def test_new_message_reaches_the_sidebar_without_waiting_for_the_ttl():
    """AC5: the forced path (SSE session event, pull-to-refresh) shows new data now."""
    script = f"""
    {_SIDEBAR_PRELUDE}
    (async () => {{
      const qs = '?sidebar_source=all';
      const before = await _loadSidebarSessionListPayload(qs, {{}});
      sessionTitle = 'a new message arrived';
      const gated = await _loadSidebarSessionListPayload(qs, {{}});
      const forced = await _loadSidebarSessionListPayload(qs, {{}}, {{force:true}});
      console.log(JSON.stringify({{
        before: before.sessData.sessions[0].title,
        gated: gated.sessData.sessions[0].title,
        forced: forced.sessData.sessions[0].title,
        fetches: sessionFetches,
      }}));
    }})();
    """
    result = _run_node(script)
    assert result["before"] == "first turn"
    assert result["gated"] == "first turn", "the window is what makes the force path meaningful"
    assert result["forced"] == "a new message arrived", result
    assert result["fetches"] == 2, result


def test_session_list_window_fails_closed_after_a_write():
    """api() stamps mutations; a snapshot older than the last write is not fresh."""
    script = f"""
    {_SIDEBAR_PRELUDE}
    (async () => {{
      const qs = '?sidebar_source=all';
      await _loadSidebarSessionListPayload(qs, {{}});
      const afterFirst = sessionFetches;
      await _loadSidebarSessionListPayload(qs, {{}});
      const stillGated = sessionFetches;
      // A POST just completed (renamed/archived/sent) — the snapshot predates it.
      globalThis.__apiLastMutationAt = Date.now() + 1;
      await _loadSidebarSessionListPayload(qs, {{}});
      console.log(JSON.stringify({{afterFirst, stillGated, afterWrite: sessionFetches}}));
    }})();
    """
    result = _run_node(script)
    assert result == {"afterFirst": 1, "stillGated": 1, "afterWrite": 2}


def test_a_response_already_in_flight_when_a_write_landed_is_not_cached_as_fresh():
    """Codex P2: the entry is stamped with the request's start, not its completion.

    A render queued behind an in-flight fetch previously saw that fetch's
    completion timestamp as newer than the write that landed mid-flight, so it
    replayed pre-mutation rows instead of issuing the replacement fetch.
    """
    script = f"""
    {_SIDEBAR_PRELUDE}
    let clock = 100000;
    Date.now = () => clock;
    (async () => {{
      const qs = '?sidebar_source=all';
      let release;
      holdSessions = new Promise(resolve => {{ release = resolve; }});
      const inFlight = _loadSidebarSessionListPayload(qs, {{}});   // started at 100000
      clock += 10;
      globalThis.__apiLastMutationAt = clock;   // archive/rename POST lands mid-flight
      clock += 10;
      release();                                // response arrives at 100020
      await inFlight;
      holdSessions = null;
      const afterFirst = sessionFetches;
      // The queued render must refetch: that payload was requested before the write.
      await _loadSidebarSessionListPayload(qs, {{}});
      console.log(JSON.stringify({{afterFirst, afterQueued: sessionFetches}}));
    }})();
    """
    result = _run_node(script)
    assert result == {"afterFirst": 1, "afterQueued": 2}


def test_an_out_of_order_older_response_does_not_overwrite_a_newer_snapshot():
    script = f"""
    {_SIDEBAR_PRELUDE}
    // A controlled clock so "started earlier" is unambiguous rather than a
    // same-millisecond tie.
    let clock = 100000;
    Date.now = () => clock;
    (async () => {{
      const qs = '?sidebar_source=all';
      let release;
      holdSessions = new Promise(resolve => {{ release = resolve; }});
      sessionTitle = 'older';
      const slow = _loadSidebarSessionListPayload(qs, {{}});
      await Promise.resolve();
      holdSessions = null;
      clock += 50;
      sessionTitle = 'newer';
      // A later request that started after `slow` but resolves before it.
      globalThis.__apiLastMutationAt = clock;
      await _loadSidebarSessionListPayload(qs, {{}});
      release();
      await slow;
      console.log(JSON.stringify({{cached: _sessionListLastPayload.sessions[0].title}}));
    }})();
    """
    assert _run_node(script) == {"cached": "newer"}


# ── 2. Panel switch freshness gate ───────────────────────────────────────────

def _panel_harness(body: str) -> str:
    return f"""
    const loads = {{}};
    const bump = (name) => {{ loads[name] = (loads[name] || 0) + 1; }};
    const noopEl = {{
      classList:{{add(){{}}, remove(){{}}, toggle(){{}}}},
      dataset:{{}},
      contains(){{ return false; }},
    }};
    global.document = {{
      querySelectorAll: () => [],
      querySelector: () => noopEl,
    }};
    global.$ = () => noopEl;
    global.S = {{activeProfile:'default', session:{{session_id:'sess-a'}}}};
    global._currentPanel = 'chat';
    global._currentSettingsSection = 'general';
    global._beforePanelSwitch = () => true;
    global._beginSettingsPanelSession = () => {{}};
    global._kanbanStopPolling = () => {{}};
    global._syncSidebarSseForPanel = () => {{}};
    global._syncSidebarAria = () => {{}};
    global._syncLogsAutoRefresh = () => {{}};
    global._syncSystemHealthMonitorVisibility = () => {{}};
    global._resyncChatSidebarAfterPanelSwitch = () => {{}};
    global.syncTopbar = () => {{}};
    global.syncAppTitlebar = () => {{}};
    global.switchSettingsSection = () => bump('settingsSection');
    global.loadSettingsPanel = async () => {{ bump('settings'); if (global.failSettings) globalThis.__apiFailureCount = (globalThis.__apiFailureCount || 0) + 1; }};
    global.loadCrons = async () => bump('tasks');
    global.loadKanban = async () => bump('kanban');
    global.loadSkills = async () => bump('skills');
    global.loadMemory = async () => bump('memory');
    global.loadWorkspacesPanel = async () => bump('workspaces');
    global.loadProfilesPanel = async () => bump('profiles');
    global.loadTodos = () => bump('todos');
    global.loadInsights = async () => bump('insights');
    global.loadLogs = async () => bump('logs');
    {_const(PANELS_JS, 'MAIN_VIEW_PANELS')}
    {_const(PANELS_JS, 'PANEL_DATA_TTL_MS')}
    const _panelDataLoadedAt = new Map();
    {_js(PANELS_JS, '_panelDataFreshnessKey')}
    {_js(PANELS_JS, '_panelDataIsFresh')}
    {_js(PANELS_JS, '_apiFailureCount')}
    {_js(PANELS_JS, '_markPanelDataLoaded')}
    global._revertSettingsPreview = () => {{}};
    global._hideSettingsPanel = () => {{}};
    {_js(PANELS_JS, '_discardSettings')}
    {_js(PANELS_JS, 'switchPanel')}
    {body}
    """


def test_toggling_between_two_panels_twice_loads_each_once():
    script = _panel_harness("""
    (async () => {
      await switchPanel('tasks');
      await switchPanel('skills');
      await switchPanel('tasks');
      await switchPanel('skills');
      console.log(JSON.stringify(loads));
    })();
    """)
    result = _run_node(script)
    assert result == {"tasks": 1, "skills": 1}, result


def test_panel_reloads_after_its_window_lapses_and_on_force():
    script = _panel_harness("""
    (async () => {
      await switchPanel('tasks');
      await switchPanel('chat');
      await switchPanel('tasks', {force:true});
      const afterForce = loads.tasks;
      await switchPanel('chat');
      for (const key of _panelDataLoadedAt.keys()) {
        const entry = _panelDataLoadedAt.get(key);
        entry.at -= (PANEL_DATA_TTL_MS + 1);
      }
      await switchPanel('tasks');
      console.log(JSON.stringify({afterForce, afterExpiry: loads.tasks}));
    })();
    """)
    result = _run_node(script)
    assert result == {"afterForce": 2, "afterExpiry": 3}


def test_a_failed_panel_load_is_not_cached_as_fresh():
    """Codex P2: the loaders swallow their own errors, so freshness must fail closed."""
    script = _panel_harness("""
    (async () => {
      global.loadCrons = async () => { bump('tasks'); globalThis.__apiFailureCount = (globalThis.__apiFailureCount || 0) + 1; };
      await switchPanel('tasks');
      await switchPanel('chat');
      await switchPanel('tasks');
      const afterFailures = loads.tasks;
      // Once a load succeeds, the window applies again.
      global.loadCrons = async () => bump('tasks');
      await switchPanel('chat');
      await switchPanel('tasks');
      const afterSuccess = loads.tasks;
      await switchPanel('chat');
      await switchPanel('tasks');
      console.log(JSON.stringify({afterFailures, afterSuccess, afterGated: loads.tasks}));
    })();
    """)
    result = _run_node(script)
    assert result == {"afterFailures": 2, "afterSuccess": 3, "afterGated": 3}


def test_a_failed_settings_load_is_not_cached_as_fresh():
    script = _panel_harness("""
    (async () => {
      global.failSettings = true;
      await switchPanel('settings');
      await switchPanel('chat');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('settings');
      const afterFailure = loads.settings;
      global.failSettings = false;
      await switchPanel('chat');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('settings');
      const afterSuccess = loads.settings;
      await switchPanel('chat');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('settings');
      console.log(JSON.stringify({afterFailure, afterSuccess, afterGated: loads.settings}));
    })();
    """)
    result = _run_node(script)
    assert result == {"afterFailure": 2, "afterSuccess": 3, "afterGated": 3}


def test_a_fire_and_forget_failure_after_the_loader_resolves_invalidates_freshness():
    """Codex P2: loadCrons() launches loadCronGatewayNotice() without awaiting it.

    A nested request that fails after the outer loader resolved would otherwise
    change the failure count too late to stop the stamp, pinning the panel's
    partial state for the whole window.
    """
    script = _panel_harness("""
    (async () => {
      global.loadCrons = async () => {
        bump('tasks');
        // The loader's own unawaited tail: a real request, so it lands a turn of
        // the event loop later — after switchPanel() has already stamped.
        setTimeout(() => {
          globalThis.__apiFailureCount = (globalThis.__apiFailureCount || 0) + 1;
        }, 0);
      };
      await switchPanel('tasks');
      await switchPanel('chat');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('tasks');
      const afterLateFailure = loads.tasks;
      // With a clean tail the window applies again: one load, then a gated re-entry.
      global.loadCrons = async () => bump('tasks');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('chat');
      await switchPanel('tasks');
      const afterCleanLoad = loads.tasks;
      await switchPanel('chat');
      await switchPanel('tasks');
      console.log(JSON.stringify({
        afterLateFailure, afterCleanLoad, afterGatedReentry: loads.tasks,
      }));
    })();
    """)
    assert _run_node(script) == {
        "afterLateFailure": 2,
        "afterCleanLoad": 3,
        "afterGatedReentry": 3,
    }


def test_kanban_is_never_gated_because_its_loader_restarts_polling():
    """Codex P2: switchPanel() stops kanban polling on exit; loadKanban() restarts it."""
    script = _panel_harness("""
    (async () => {
      let polling = false;
      global._kanbanStopPolling = () => { polling = false; };
      global.loadKanban = async () => { bump('kanban'); polling = true; };
      await switchPanel('kanban');
      await switchPanel('chat');
      const stoppedOnExit = polling;
      await switchPanel('kanban');
      console.log(JSON.stringify({stoppedOnExit, loads: loads.kanban, pollingOnReturn: polling}));
    })();
    """)
    assert _run_node(script) == {
        "stoppedOnExit": False,
        "loads": 2,
        "pollingOnReturn": True,
    }


def test_panel_freshness_is_keyed_by_the_active_workspace():
    """Codex P2: switchToWorkspace() mutates S.session.workspace without a new session."""
    script = _panel_harness("""
    (async () => {
      S.session = {session_id: 'sess-a', workspace: '/ws/a'};
      await switchPanel('workspaces');
      await switchPanel('chat');
      await switchPanel('workspaces');
      const sameWorkspace = loads.workspaces;
      // An in-place workspace switch on the SAME session.
      S.session.workspace = '/ws/b';
      await switchPanel('chat');
      await switchPanel('workspaces');
      console.log(JSON.stringify({sameWorkspace, afterWorkspaceSwitch: loads.workspaces}));
    })();
    """)
    assert _run_node(script) == {"sameWorkspace": 1, "afterWorkspaceSwitch": 2}


def test_expired_freshness_entries_are_evicted_instead_of_accumulating():
    """Codex P2: nothing else deletes from the map, so a long-lived tab grows it."""
    script = _panel_harness("""
    (async () => {
      for (let i = 0; i < 25; i++) {
        S.session = {session_id: 'sess-' + i, workspace: '/ws/' + i};
        await switchPanel('memory');
        await switchPanel('chat');
        // Age every entry past the window, as a long-lived tab would.
        for (const entry of _panelDataLoadedAt.values()) entry.at -= (PANEL_DATA_TTL_MS + 1);
      }
      console.log(JSON.stringify({entries: _panelDataLoadedAt.size}));
    })();
    """)
    result = _run_node(script)
    assert result["entries"] <= 2, result


def test_discarding_settings_forces_the_next_entry_to_reload():
    """Codex P2: _revertSettingsPreview() is a no-op, so the reload IS the revert."""
    script = _panel_harness("""
    (async () => {
      await switchPanel('settings');
      await switchPanel('chat');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('settings');
      const gated = loads.settings;
      // The user edits a non-autosaved field and clicks Discard. The form DOM still
      // holds the discarded values, so the next entry must refetch.
      _discardSettings();
      await switchPanel('chat');
      await new Promise(r => setTimeout(r, 0));
      await switchPanel('settings');
      console.log(JSON.stringify({gated, afterDiscard: loads.settings}));
    })();
    """)
    assert _run_node(script) == {"gated": 1, "afterDiscard": 2}


def test_settings_section_still_syncs_on_every_entry_while_its_fetch_is_gated():
    """Re-entering settings must re-apply the visible section even when data is fresh."""
    script = _panel_harness("""
    (async () => {
      await switchPanel('settings');
      await switchPanel('chat');
      await switchPanel('settings');
      console.log(JSON.stringify(loads));
    })();
    """)
    result = _run_node(script)
    assert result["settingsSection"] == 2, result
    assert result["settings"] == 1, result


def test_panel_freshness_is_keyed_by_session_not_just_profile():
    """Codex P2: loadMemory() fetches /api/memory?session_id=, so the key must carry it."""
    script = _panel_harness("""
    (async () => {
      await switchPanel('memory');
      await switchPanel('chat');
      await switchPanel('memory');
      const sameSession = loads.memory;
      S.session = {session_id: 'sess-b'};
      await switchPanel('chat');
      await switchPanel('memory');
      console.log(JSON.stringify({sameSession, afterSessionSwitch: loads.memory}));
    })();
    """)
    assert _run_node(script) == {"sameSession": 1, "afterSessionSwitch": 2}


def test_a_load_that_finishes_after_a_switch_does_not_stamp_the_new_identity():
    """Codex P2: the stamp is bound to the identity that started the load."""
    script = _panel_harness("""
    (async () => {
      let release;
      global.loadCrons = async () => {
        bump('tasks');
        await new Promise(resolve => { release = resolve; });
      };
      const slow = switchPanel('tasks');
      // The user switches profile while that load is still on the wire.
      S.activeProfile = 'other';
      release();
      await slow;
      global.loadCrons = async () => bump('tasks');
      // Profile B's tasks panel must NOT be marked fresh by profile A's response.
      await switchPanel('chat');
      await switchPanel('tasks');
      console.log(JSON.stringify({loads: loads.tasks}));
    })();
    """)
    assert _run_node(script) == {"loads": 2}


# ── 3 + 4. api() dedupe and idempotent-only network retry ────────────────────

_API_PRELUDE = f"""
global.document = {{baseURI:'http://example.test/'}};
global.location = {{href:'http://example.test/', pathname:'/', search:''}};
global.window = {{location: global.location}};
global.showToast = () => {{}};
{_js(WORKSPACE_JS, 'api')}
"""


def test_simultaneous_gets_for_the_same_url_issue_one_request():
    script = f"""
    {_API_PRELUDE}
    const calls = [];
    let release;
    const gate = new Promise(resolve => {{ release = resolve; }});
    global.fetch = (url) => {{
      calls.push(url);
      return gate.then(() => ({{
        ok:true,
        headers:{{get:()=>'application/json'}},
        json:()=>Promise.resolve({{n: calls.length}}),
        text:()=>Promise.resolve(''),
      }}));
    }};
    (async () => {{
      const both = Promise.all([api('/api/sessions?x=1'), api('/api/sessions?x=1')]);
      const other = api('/api/projects');
      release();
      const [a, b] = await both;
      await other;
      // Sequential calls must NOT reuse a settled entry.
      await api('/api/sessions?x=1');
      console.log(JSON.stringify({{calls, sameValue: a === b}}));
    }})();
    """
    result = _run_node(script)
    assert result["calls"] == [
        "http://example.test/api/sessions?x=1",
        "http://example.test/api/projects",
        "http://example.test/api/sessions?x=1",
    ], result
    assert result["sameValue"] is True, "concurrent callers must share the one response"


def test_concurrent_gets_with_a_caller_signal_are_not_shared():
    """Sharing a promise across AbortSignals would let one caller cancel another."""
    script = f"""
    {_API_PRELUDE}
    let calls = 0;
    let release;
    const gate = new Promise(resolve => {{ release = resolve; }});
    global.fetch = () => {{
      calls += 1;
      return gate.then(() => ({{
        ok:true, headers:{{get:()=>'application/json'}},
        json:()=>Promise.resolve({{}}), text:()=>Promise.resolve(''),
      }}));
    }};
    (async () => {{
      const both = Promise.all([
        api('/api/sessions', {{signal: new AbortController().signal}}),
        api('/api/sessions', {{signal: new AbortController().signal}}),
      ]);
      release();
      await both;
      console.log(JSON.stringify({{calls}}));
    }})();
    """
    assert _run_node(script) == {"calls": 2}


def test_gets_with_different_caller_policies_are_not_collapsed():
    """Codex P2: /api/model/auxiliary is requested with retries:0 and with the defaults."""
    script = f"""
    {_API_PRELUDE}
    let calls = 0;
    let release;
    const gate = new Promise(resolve => {{ release = resolve; }});
    global.fetch = () => {{
      calls += 1;
      return gate.then(() => ({{
        ok:true, headers:{{get:()=>'application/json'}},
        json:()=>Promise.resolve({{}}), text:()=>Promise.resolve(''),
      }}));
    }};
    (async () => {{
      const mixed = Promise.all([
        api('/api/model/auxiliary', {{retries:0, timeoutToast:false}}),
        api('/api/model/auxiliary'),
      ]);
      release();
      await mixed;
      const afterMixed = calls;
      let release2;
      const gate2 = new Promise(resolve => {{ release2 = resolve; }});
      global.fetch = () => {{
        calls += 1;
        return gate2.then(() => ({{
          ok:true, headers:{{get:()=>'application/json'}},
          json:()=>Promise.resolve({{}}), text:()=>Promise.resolve(''),
        }}));
      }};
      // Identical policies still share one request.
      const same = Promise.all([
        api('/api/model/auxiliary', {{retries:0, timeoutToast:false}}),
        api('/api/model/auxiliary', {{retries:0, timeoutToast:false}}),
      ]);
      release2();
      await same;
      console.log(JSON.stringify({{afterMixed, afterSame: calls}}));
    }})();
    """
    assert _run_node(script) == {"afterMixed": 2, "afterSame": 3}


def test_a_get_issued_after_a_write_does_not_join_one_issued_before_it():
    """A GET's response depends on more than its URL (cookies, server state)."""
    script = f"""
    {_API_PRELUDE}
    const calls = [];
    let releaseGet;
    const gate = new Promise(resolve => {{ releaseGet = resolve; }});
    global.fetch = (url, opts) => {{
      const method = (opts && opts.method) || 'GET';
      calls.push(method + ' ' + url);
      if (method !== 'GET') return Promise.resolve({{
        ok:true, headers:{{get:()=>'application/json'}},
        json:()=>Promise.resolve({{}}), text:()=>Promise.resolve(''),
      }});
      return gate.then(() => ({{
        ok:true, headers:{{get:()=>'application/json'}},
        json:()=>Promise.resolve({{}}), text:()=>Promise.resolve(''),
      }}));
    }};
    (async () => {{
      const first = api('/api/sessions');
      await api('/api/profile/switch', {{method:'POST', body:'{{}}'}});
      const second = api('/api/sessions');
      releaseGet();
      await Promise.all([first, second]);
      console.log(JSON.stringify({{calls}}));
    }})();
    """
    result = _run_node(script)
    assert result["calls"] == [
        "GET http://example.test/api/sessions",
        "POST http://example.test/api/profile/switch",
        "GET http://example.test/api/sessions",
    ], result


def test_post_is_not_retried_on_a_network_typeerror():
    script = f"""
    {_API_PRELUDE}
    const attempts = {{}};
    global.fetch = (url, opts) => {{
      const method = (opts && opts.method) || 'GET';
      attempts[method] = (attempts[method] || 0) + 1;
      return Promise.reject(new TypeError('Failed to fetch'));
    }};
    (async () => {{
      const errors = [];
      try {{ await api('/api/chat/start', {{method:'POST', body:'{{}}', retryDelayMs:0}}); }}
      catch (e) {{ errors.push('post:' + e.name); }}
      try {{ await api('/api/sessions', {{retryDelayMs:0}}); }}
      catch (e) {{ errors.push('get:' + e.name); }}
      console.log(JSON.stringify({{attempts, errors}}));
    }})();
    """
    result = _run_node(script)
    assert result["attempts"]["POST"] == 1, "a POST may already have been applied server-side"
    assert result["attempts"]["GET"] == 3, "idempotent GETs keep the network-error retry"
    assert result["errors"] == ["post:TypeError", "get:TypeError"], result


def test_mutating_requests_stamp_the_shared_mutation_clock():
    script = f"""
    {_API_PRELUDE}
    global.fetch = () => Promise.resolve({{
      ok:true, headers:{{get:()=>'application/json'}},
      json:()=>Promise.resolve({{}}), text:()=>Promise.resolve(''),
    }});
    (async () => {{
      await api('/api/sessions');
      const afterGet = globalThis.__apiLastMutationAt || 0;
      await api('/api/session/rename', {{method:'POST', body:'{{}}'}});
      const afterPost = globalThis.__apiLastMutationAt || 0;
      console.log(JSON.stringify({{afterGet, stamped: afterPost > 0}}));
    }})();
    """
    result = _run_node(script)
    assert result["afterGet"] == 0, "a read must not look like a write"
    assert result["stamped"] is True
