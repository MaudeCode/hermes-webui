"""HWEB-38 — a backgrounded tab must issue zero polling requests.

Four pollers ran without a `document.hidden` gate, so a hidden tab kept hitting
the server for the whole turn (~600 requests over 10 minutes):

    static/messages.js  approval fallback   /api/approval/pending   1500ms
    static/messages.js  clarify fallback    /api/clarify/pending    3000ms
    static/panels.js    cron run watch      /api/crons/status       3000ms
    static/panels.js    logs auto-refresh   /api/logs               5000ms
    static/messages.js  background task     /api/background/status  3000ms
    static/panels.js    kanban SSE fallback /api/kanban/events     30000ms

They now run through the shared `startVisiblePoll` driver in `static/ui.js`,
which skips the tick while hidden and fires exactly one catch-up tick when the
tab is shown again. The cron, logs and hidden session-status pollers also gained
an in-flight flag so a slow endpoint cannot stack overlapping requests.

The node driver below executes the real, unmodified poller sources against a
fake `document` / `setInterval`, so it fails if the gate is removed from the
product code — not just if a string disappears.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import tempfile
import textwrap

import pytest

REPO = pathlib.Path(__file__).parent.parent
STATIC = REPO / "static"
UI_JS = (STATIC / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (STATIC / "messages.js").read_text(encoding="utf-8")
PANELS_JS = (STATIC / "panels.js").read_text(encoding="utf-8")

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node not on PATH")


# ── Static wiring ────────────────────────────────────────────────────────────


def test_shared_driver_gates_and_releases():
    assert "function startVisiblePoll(tick,ms){" in UI_JS
    assert "const run=()=>{if(!tabIsVisibleForPolling())return;tick();};" in UI_JS
    assert "document.addEventListener('visibilitychange',run)" in UI_JS
    # The stop function must drop the listener too, or a stopped poller keeps
    # ticking on every visibilitychange.
    assert "document.removeEventListener('visibilitychange',run)" in UI_JS


def test_every_previously_ungated_poller_uses_the_driver():
    # HWEB-69 wraps these two in the chat-stream health gate; that wrapper
    # still runs every tick through startVisiblePoll.
    assert "_approvalPollStop = startStreamGatedPoll(sid, _tick, 1500);" in MESSAGES_JS
    assert "_clarifyFallbackStop = startStreamGatedPoll(sid, _tick, 3000);" in MESSAGES_JS
    assert "stop=startVisiblePoll(run,healthy?STREAM_HEALTHY_SAFETY_POLL_MS:ms);" in MESSAGES_JS
    # The start tick is gated too, and runs only after the stop function is
    # stored — a first tick that stops the poller must not be overwritten.
    assert MESSAGES_JS.count("if (tabIsVisibleForPolling()) _tick();") == 2
    assert "_cronWatchStop = startVisiblePoll(" in PANELS_JS
    assert "_logsAutoRefreshStop = startVisiblePoll(" in PANELS_JS
    # The /background task poller was a self-rescheduling setTimeout chain
    # with no gate at all; it now runs on the same driver.
    assert "_bgPollTimers.set(parentSid,startVisiblePoll(_poll,3000))" in MESSAGES_JS
    assert "if(tabIsVisibleForPolling()) _poll();" in MESSAGES_JS
    # Session ids are [0-9a-zA-Z_-], so a session named `constructor` or
    # `__proto__` would hit an inherited Object property on a plain-object map.
    assert "let _bgPollTimers=new Map();" in MESSAGES_JS
    assert "let _bgPendingTasksByParent=new Map();" in MESSAGES_JS
    # Kanban had three separate fallback setInterval sites; they now share one
    # start helper so the gate cannot be reintroduced at just one of them.
    assert "_kanbanPollStop = startVisiblePoll(refreshKanbanEvents, 30000)" in PANELS_JS
    assert PANELS_JS.count("_kanbanStartFallbackPoll();") == 3  # 3 call sites
    assert "setInterval(refreshKanbanEvents" not in PANELS_JS


def test_slow_endpoints_cannot_stack_in_flight_requests():
    # Every guard is closure-local (`let inFlight`), never module-scoped: a
    # module flag let a request outliving its poller's stop release the guard of
    # the poller that replaced it, and the next tick then overlapped.
    assert "if (inFlight) return;" in PANELS_JS
    assert "finally { inFlight = false; }" in PANELS_JS
    assert "finally(() => { inFlight = false; });" in MESSAGES_JS
    assert "if(inFlight) return;" in MESSAGES_JS
    for stale in (
        "_cronWatchInFlight",
        "_logsAutoRefreshInFlight",
        "_sessionStreamHiddenPollInFlight",
        "_approvalFallbackPollInFlight",
        "_clarifyFallbackPollInFlight",
    ):
        assert stale not in PANELS_JS and stale not in MESSAGES_JS, stale


def test_dashboard_status_interval_is_releasable():
    assert "function _stopDashboardStatusPoll(){" in UI_JS
    assert "_dashboardStatusTimer=setInterval(refreshDashboardStatus,DASHBOARD_STATUS_TTL_MS)" in UI_JS
    assert "window.addEventListener('pagehide',_stopDashboardStatusPoll)" in UI_JS


def test_bfcache_restore_restarts_the_poll_without_reloading_the_settings_form():
    # A bfcache restore must not run the full probe: loadDashboardSettings()
    # overwrites the dashboard mode/URL inputs from the server, discarding the
    # unsaved draft bfcache just restored for the user navigating back.
    start = UI_JS.index("window.addEventListener('pageshow'")
    handler = UI_JS[start:UI_JS.index("});", start)]
    assert "_startDashboardStatusPoll()" in handler
    assert "refreshDashboardStatus(true)" in handler
    assert "_initDashboardLinkProbe" not in handler
    assert "loadDashboardSettings" not in handler


# ── Behavioural driver ───────────────────────────────────────────────────────

_HARNESS = textwrap.dedent(
    """\
    const fs = require('fs');

    // Brace-matching extractor that skips strings, template literals and
    // comments (the poller bodies contain both `${...}` and `//` braces).
    function extractFn(src, name) {
      let start = -1;
      for (const m of [`async function ${name}(`, `function ${name}(`]) {
        start = src.indexOf(m); if (start >= 0) break;
      }
      if (start < 0) throw new Error(`${name}() not found`);
      let i = src.indexOf('{', start), depth = 0, s = null, esc = false, lc = false, bc = false;
      for (; i < src.length; i++) {
        const ch = src[i], nx = src[i + 1] || '';
        if (lc) { if (ch === '\\n') lc = false; continue; }
        if (bc) { if (ch === '*' && nx === '/') bc = false; continue; }
        if (s) { if (esc) esc = false; else if (ch === '\\\\') esc = true; else if (ch === s) s = null; continue; }
        if (ch === '/' && nx === '/') { lc = true; continue; }
        if (ch === '/' && nx === '*') { bc = true; continue; }
        if (ch === '\\'' || ch === '"' || ch === '`') { s = ch; continue; }
        if (ch === '{') depth += 1;
        if (ch === '}') { depth -= 1; if (depth === 0) return src.slice(start, i + 1); }
      }
      throw new Error(`could not extract ${name}`);
    }

    const STATIC = process.argv[2];
    const UI = fs.readFileSync(STATIC + '/ui.js', 'utf8');
    const MSG = fs.readFileSync(STATIC + '/messages.js', 'utf8');
    const PAN = fs.readFileSync(STATIC + '/panels.js', 'utf8');

    // ── fake document ──────────────────────────────────────────────────────
    const listeners = new Map();
    global.document = {
      hidden: true,
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (listeners.has(type)) listeners.get(type).delete(fn);
      },
    };
    function setHidden(v) {
      document.hidden = v;
      for (const fn of listeners.get('visibilitychange') || []) fn();
    }

    // ── fake timers ────────────────────────────────────────────────────────
    const timers = new Map();
    let nextTimerId = 1;
    global.setInterval = (fn, ms) => { const id = nextTimerId++; timers.set(id, fn); return id; };
    global.clearInterval = (id) => { timers.delete(id); };
    function tickAll() { for (const fn of Array.from(timers.values())) fn(); }

    // ── request counters ───────────────────────────────────────────────────
    const fetches = {};
    let pendingResolvers = [];
    let blockRequests = false;
    let concurrent = 0, maxConcurrent = 0;
    function record(url) {
      const key = String(url).split('?')[0];
      fetches[key] = (fetches[key] || 0) + 1;
      if (blockRequests) {
        concurrent += 1;
        maxConcurrent = Math.max(maxConcurrent, concurrent);
        return new Promise((resolve) => pendingResolvers.push(() => { concurrent -= 1; resolve({}); }));
      }
      return Promise.resolve({});
    }

    // ── shared stubs ───────────────────────────────────────────────────────
    global.encodeURIComponent = encodeURIComponent;
    global.S = { busy: true, session: { session_id: 'sid-1' }, activeStreamId: null, messages: [] };
    let bgResults = null;
    global.$ = () => null;
    global.api = (url) => record(url).then(() => {
      // /api/background/status is DESTRUCTIVE: get_results(parentSid) returns
      // every completed result for the parent and removes it from tracking, so
      // a second reader gets nothing. Model that — it is the whole bug.
      let results = null;
      if (String(url).startsWith('/api/background/status')) { results = bgResults; bgResults = null; }
      return { pending: null, running: true, elapsed: 1, lines: [], results };
    });

    // approval
    global._approvalFallbackPollInFlight = false;
    global._approvalPollStop = null;
    global._approvalPollingSessionMissingOrMismatched = () => false;
    global._approvalPendingBySession = new Map();
    global._clearApprovalPendingForSession = () => {};
    global._unmarkApprovalDismissed = () => {};
    global._hideApprovalCardIfOwner = () => {};
    global.showApprovalForSession = () => {};
    // Mirrors the real stopApprovalPolling's release of the poll handle.
    global.stopApprovalPolling = () => {
      if (_approvalPollStop) { _approvalPollStop(); _approvalPollStop = null; }
    };
    global.stopApprovalPollingForSession = () => {};

    // clarify
    global._clarifyFallbackPollInFlight = false;
    global._clarifyFallbackStop = null;
    global._clarifyPollingSessionId = null;
    global._clarifyMissingEndpointWarned = false;
    global.showClarifyForSession = () => {};
    global._clearClarifyPendingForSession = () => {};
    global._hideClarifyCardIfOwner = () => {};
    global.stopClarifyPolling = () => {};
    global.setComposerStatus = () => {};

    // cron
    global._cronWatchStop = null;
    global._cronWatchTimerStop = null;
    global._cronWatchInFlight = false;
    global._cronWatchStart = null;
    global._cronDetailMatches = () => false;
    global._loadCronDetailRuns = () => {};
    global._formatElapsed = () => '0s';
    global._injectRunningIndicator = () => {};

    // kanban
    global._kanbanPollStop = null;
    global._kanbanLatestEventId = 1;
    global._kanbanEventSourceFailures = 3;
    global._kanbanBoardQuery = () => '';
    global.loadKanban = async () => {};
    global.loadKanbanTask = async () => {};
    global._kanbanCurrentTaskId = null;
    global._kanbanEventSource = null;

    // background task
    global._bgPollTimers = new Map();
    global._bgPendingTasksByParent = new Map();
    global.hideBackgroundBadge = () => {};
    global.renderMessages = () => {};
    global.showToast = () => {};
    global.t = () => '';

    // logs
    global._logsAutoRefreshStop = null;
    global._logsAutoRefreshInFlight = false;
    global._currentPanel = 'logs';
    global.loadLogs = () => record('/api/logs');

    // HWEB-69 stream gate: no live chat stream in this harness, so the
    // approval/clarify pollers stay on their fast cadence.
    global.LIVE_STREAMS = {};
    global._chatStreamHealthListeners = new Set();
    global.STREAM_HEALTHY_SAFETY_POLL_MS = 15000;

    eval(extractFn(UI, 'tabIsVisibleForPolling'));
    eval(extractFn(UI, 'startVisiblePoll'));
    eval(extractFn(MSG, 'chatStreamIsHealthy'));
    eval(extractFn(MSG, 'startStreamGatedPoll'));
    eval(extractFn(MSG, '_startApprovalFallbackPoll'));
    eval(extractFn(MSG, '_startClarifyFallbackPoll'));
    eval(extractFn(PAN, '_startCronWatch'));
    eval(extractFn(PAN, '_stopCronWatch'));
    eval(extractFn(PAN, '_startLogsAutoRefresh'));
    eval(extractFn(PAN, '_stopLogsAutoRefresh'));
    eval(extractFn(MSG, '_stopBackgroundPolling'));
    eval(extractFn(MSG, 'startBackgroundPolling'));
    eval(extractFn(PAN, 'refreshKanbanEvents'));
    eval(extractFn(PAN, '_kanbanStartFallbackPoll'));
    eval(extractFn(PAN, '_kanbanStartPolling'));
    eval(extractFn(PAN, '_kanbanStopPolling'));

    const flush = () => new Promise((r) => setTimeout(r, 0));

    (async () => {
      const out = {};

      // ── Hidden tab: start every poller and run several intervals ─────────
      _startApprovalFallbackPoll('sid-1');
      _startClarifyFallbackPoll('sid-1');
      _startCronWatch('job-1', 'key-1');
      _startLogsAutoRefresh();
      startBackgroundPolling('sid-1', 'task-1', 'do a thing');
      for (let i = 0; i < 5; i++) { tickAll(); await flush(); }
      out.hidden = JSON.parse(JSON.stringify(fetches));

      // ── Becoming visible: exactly one catch-up fetch each ────────────────
      setHidden(false);
      await flush();
      out.onVisible = JSON.parse(JSON.stringify(fetches));

      // ── Overlap: a slow /api/crons/status must not stack requests ────────
      for (const k of Object.keys(fetches)) delete fetches[k];
      blockRequests = true;
      _startCronWatch('job-2', 'key-2');
      for (let i = 0; i < 4; i++) { tickAll(); await flush(); }
      out.slowCron = fetches['/api/crons/status'] || 0;
      pendingResolvers.forEach((r) => r({}));
      pendingResolvers = [];
      blockRequests = false;
      await flush();

      // ── Stopping a poller must also drop its visibilitychange listener ───
      _stopCronWatch();
      _stopLogsAutoRefresh();
      _stopBackgroundPolling('task-1');
      for (const k of Object.keys(fetches)) delete fetches[k];
      setHidden(true); setHidden(false);
      await flush();
      out.afterStop = JSON.parse(JSON.stringify(fetches));

      // ── Ordering: a start tick that stops its own poller must not leak ───
      // _tick can decide the session is gone and call stopApprovalPolling()
      // synchronously. If the start tick ran before the stop handle was
      // stored, that stop is a no-op and the interval survives forever.
      timers.clear();
      (listeners.get('visibilitychange') || new Set()).clear();
      _approvalPollStop = null;
      _approvalPollingSessionMissingOrMismatched = () => true;
      _startApprovalFallbackPoll('sid-gone');
      out.timersAfterSelfStop = timers.size;
      out.listenersAfterSelfStop = (listeners.get('visibilitychange') || new Set()).size;

      // ── /background: one poller per parent, every result delivered ───────
      // /api/background/status is destructive (get_results drains the parent),
      // so a second poller on the same parent would drain and then discard its
      // sibling's result, stranding that task forever.
      timers.clear();
      (listeners.get('visibilitychange') || new Set()).clear();
      _bgPollTimers = new Map();
      _bgPendingTasksByParent = new Map();
      S.messages = [];
      const hiddenBadges = [];
      hideBackgroundBadge = (id) => { hiddenBadges.push(id); };
      startBackgroundPolling('parent-1', 'task-A', 'first');
      startBackgroundPolling('parent-1', 'task-B', 'second');
      await flush();
      out.bgPollersForOneParent = timers.size;

      for (const k of Object.keys(fetches)) delete fetches[k];
      bgResults = [{ task_id: 'task-A', answer: 'A!' }, { task_id: 'task-B', answer: 'B!' }];
      tickAll();
      await flush();
      out.bgRequestsPerTick = fetches['/api/background/status'] || 0;
      out.bgDelivered = hiddenBadges.slice().sort();
      out.bgMessages = S.messages.length;
      out.bgTimersAfterAllDone = timers.size;
      bgResults = null;

      // ── Kanban SSE fallback: hidden = silent, visible = one catch-up ─────
      // Its own phase because refreshKanbanEvents() and the logs poller both
      // read _currentPanel and want different values.
      timers.clear();
      (listeners.get('visibilitychange') || new Set()).clear();
      _kanbanPollStop = null;
      _currentPanel = 'kanban';
      setHidden(true);
      for (const k of Object.keys(fetches)) delete fetches[k];
      _kanbanStartPolling();  // EventSource is undefined in node -> fallback path
      for (let i = 0; i < 5; i++) { tickAll(); await flush(); }
      out.kanbanHidden = fetches['/api/kanban/events'] || 0;
      setHidden(false);
      await flush();
      out.kanbanOnVisible = fetches['/api/kanban/events'] || 0;
      _kanbanStopPolling();
      for (const k of Object.keys(fetches)) delete fetches[k];
      setHidden(true); setHidden(false);
      await flush();
      out.kanbanAfterStop = fetches['/api/kanban/events'] || 0;

      // ── A session named `constructor` must behave like any other ────────
      // is_safe_session_id() allows all-letter ids, so this is a reachable sid.
      // On plain objects _bgPollTimers[sid] is truthy (Object.prototype
      // .constructor), so the poller never starts, and the Map init is skipped
      // so pending.set() throws.
      timers.clear();
      (listeners.get('visibilitychange') || new Set()).clear();
      _bgPollTimers = new Map();
      _bgPendingTasksByParent = new Map();
      S.messages = [];
      const protoBadges = [];
      hideBackgroundBadge = (id) => { protoBadges.push(id); };
      for (const k of Object.keys(fetches)) delete fetches[k];
      out.protoSidThrew = false;
      try {
        startBackgroundPolling('constructor', 'task-P', 'proto task');
        await flush();
      } catch (e) { out.protoSidThrew = true; }
      out.protoSidPollers = timers.size;
      bgResults = [{ task_id: 'task-P', answer: 'P!' }];
      tickAll();
      await flush();
      out.protoSidDelivered = protoBadges.slice();
      out.protoSidMessages = S.messages.length;
      bgResults = null;

      // ── A stop/restart must not release the replacement poller's guard ──
      // _stopCronWatch() cannot know whether the outgoing watch has a request
      // still pending. With a module-scoped flag that stale request's finally
      // released the REPLACEMENT watch's guard and the next tick overlapped it.
      timers.clear();
      (listeners.get('visibilitychange') || new Set()).clear();
      _cronWatchStop = null; _cronWatchTimerStop = null; _cronWatchStart = null;
      for (const k of Object.keys(fetches)) delete fetches[k];
      pendingResolvers = [];
      concurrent = 0; maxConcurrent = 0;
      blockRequests = true;
      _startCronWatch('job-1', 'k1');
      tickAll(); await flush();          // request A in flight (old watch)
      _startCronWatch('job-2', 'k2');    // stop + restart while A is pending
      tickAll(); await flush();          // request B in flight (new watch)
      pendingResolvers.shift()();        // A completes -> its stale finally fires
      await flush();
      // A is gone; only B is pending. Re-baseline so we measure the REPLACEMENT
      // watch overlapping ITSELF, not the old/new overlap a restart always has.
      maxConcurrent = concurrent;
      tickAll(); await flush();          // B2 must NOT start while B is pending
      out.cronMaxConcurrentAfterStaleCompletion = maxConcurrent;
      pendingResolvers.forEach((r) => r());
      pendingResolvers = [];
      blockRequests = false;
      await flush();
      _stopCronWatch();

      console.log(JSON.stringify(out));
    })();
    """
)

APPROVAL = "/api/approval/pending"
CLARIFY = "/api/clarify/pending"
CRONS = "/api/crons/status"
LOGS = "/api/logs"
BACKGROUND = "/api/background/status"


def _run_driver():
    with tempfile.TemporaryDirectory() as tmp:
        script = pathlib.Path(tmp) / "driver.js"
        script.write_text(_HARNESS, encoding="utf-8")
        proc = subprocess.run(
            [NODE, str(script), str(STATIC)],
            capture_output=True,
            text=True,
            timeout=60,
        )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def driver():
    if NODE is None:
        pytest.skip("node not on PATH")
    return _run_driver()


@requires_node
def test_hidden_tab_issues_zero_polling_requests(driver):
    assert driver["hidden"] == {}, driver["hidden"]


@requires_node
def test_becoming_visible_fires_exactly_one_catch_up_per_poller(driver):
    # Approval and clarify state is current again within one catch-up tick of
    # the tab regaining focus — not a full 1500ms/3000ms interval later.
    assert driver["onVisible"] == {
        APPROVAL: 1,
        CLARIFY: 1,
        CRONS: 1,
        LOGS: 1,
        BACKGROUND: 1,
    }, driver["onVisible"]


@requires_node
def test_slow_cron_status_cannot_produce_concurrent_requests(driver):
    assert driver["slowCron"] == 1, driver["slowCron"]


@requires_node
def test_start_tick_that_stops_its_own_poller_leaks_nothing(driver):
    assert driver["timersAfterSelfStop"] == 0, driver["timersAfterSelfStop"]
    assert driver["listenersAfterSelfStop"] == 0, driver["listenersAfterSelfStop"]


@requires_node
def test_sibling_background_tasks_share_one_poller_and_all_results_land(driver):
    # Two /background tasks on one parent session must be served by a single
    # poller. Two pollers race for a destructive endpoint: whichever response
    # lands first drains both results and discards the one whose task_id does
    # not match, stranding that task's badge and poller forever.
    assert driver["bgPollersForOneParent"] == 1, driver["bgPollersForOneParent"]
    assert driver["bgRequestsPerTick"] == 1, driver["bgRequestsPerTick"]
    # The endpoint drains on read, so both results must be delivered from the
    # single response — an unclaimed one is gone for good.
    assert driver["bgDelivered"] == ["task-A", "task-B"], driver["bgDelivered"]
    assert driver["bgMessages"] == 2, driver["bgMessages"]
    # Last task done → poller released.
    assert driver["bgTimersAfterAllDone"] == 0, driver["bgTimersAfterAllDone"]


@requires_node
def test_kanban_sse_fallback_is_gated_and_releasable(driver):
    # refreshKanbanEvents() gates on _currentPanel but not document.hidden, so
    # the raw 30s fallback intervals polled from a hidden tab parked on Kanban.
    assert driver["kanbanHidden"] == 0, driver["kanbanHidden"]
    assert driver["kanbanOnVisible"] == 1, driver["kanbanOnVisible"]
    assert driver["kanbanAfterStop"] == 0, driver["kanbanAfterStop"]


@requires_node
def test_session_id_matching_an_object_property_still_polls(driver):
    # `constructor` passes is_safe_session_id(), so it is a reachable session id.
    # On a plain object it silently broke the poller: the truthy inherited
    # lookup skipped both the poller creation and the Map init.
    assert driver["protoSidThrew"] is False
    assert driver["protoSidPollers"] == 1, driver["protoSidPollers"]
    assert driver["protoSidDelivered"] == ["task-P"], driver["protoSidDelivered"]
    assert driver["protoSidMessages"] == 1, driver["protoSidMessages"]


@requires_node
def test_stop_restart_cannot_release_the_replacement_pollers_guard(driver):
    # Measures the replacement watch overlapping ITSELF after the outgoing
    # watch's request completes — not the old/new overlap inherent to any
    # restart. Verified against the pre-fix module-scoped flag: this reached 2.
    assert driver["cronMaxConcurrentAfterStaleCompletion"] == 1, driver[
        "cronMaxConcurrentAfterStaleCompletion"
    ]


@requires_node
def test_stopped_pollers_do_not_tick_on_visibilitychange(driver):
    assert CRONS not in driver["afterStop"], driver["afterStop"]
    assert LOGS not in driver["afterStop"], driver["afterStop"]
