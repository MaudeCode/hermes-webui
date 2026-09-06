"""HWEB-38 — a backgrounded tab must issue zero polling requests.

Four pollers ran without a `document.hidden` gate, so a hidden tab kept hitting
the server for the whole turn (~600 requests over 10 minutes):

    static/messages.js  approval fallback   /api/approval/pending   1500ms
    static/messages.js  clarify fallback    /api/clarify/pending    3000ms
    static/panels.js    cron run watch      /api/crons/status       3000ms
    static/panels.js    logs auto-refresh   /api/logs               5000ms
    static/messages.js  background task     /api/background/status  3000ms

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
    assert "_approvalPollStop = startVisiblePoll(_tick, 1500);" in MESSAGES_JS
    assert "_clarifyFallbackStop = startVisiblePoll(_tick, 3000);" in MESSAGES_JS
    # The start tick is gated too, and runs only after the stop function is
    # stored — a first tick that stops the poller must not be overwritten.
    assert MESSAGES_JS.count("if (tabIsVisibleForPolling()) _tick();") == 2
    assert "_cronWatchStop = startVisiblePoll(" in PANELS_JS
    assert "_logsAutoRefreshStop = startVisiblePoll(" in PANELS_JS
    # The /background task poller was a self-rescheduling setTimeout chain
    # with no gate at all; it now runs on the same driver.
    assert "_bgPollTimers[taskId]=startVisiblePoll(_poll,3000)" in MESSAGES_JS
    assert "if(tabIsVisibleForPolling()) _poll();" in MESSAGES_JS


def test_slow_endpoints_cannot_stack_in_flight_requests():
    assert "if (_cronWatchInFlight) return;" in PANELS_JS
    assert "finally { _cronWatchInFlight = false; }" in PANELS_JS
    assert "if (_logsAutoRefreshInFlight) return;" in PANELS_JS
    assert "if (_sessionStreamHiddenPollInFlight) return;" in MESSAGES_JS
    assert "finally(() => { _sessionStreamHiddenPollInFlight = false; })" in MESSAGES_JS
    # The chain could not overlap; the interval-based driver can.
    assert "if(inFlight) return;" in MESSAGES_JS


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
    function record(url) {
      const key = String(url).split('?')[0];
      fetches[key] = (fetches[key] || 0) + 1;
      if (blockRequests) return new Promise((resolve) => pendingResolvers.push(resolve));
      return Promise.resolve({});
    }

    // ── shared stubs ───────────────────────────────────────────────────────
    global.encodeURIComponent = encodeURIComponent;
    global.S = { busy: true, session: { session_id: 'sid-1' }, activeStreamId: null };
    global.$ = () => null;
    global.api = (url) => record(url).then(() => ({ pending: null, running: true, elapsed: 1, lines: [] }));

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

    // background task
    global._bgPollTimers = {};
    global.hideBackgroundBadge = () => {};
    global.renderMessages = () => {};
    global.showToast = () => {};
    global.t = () => '';

    // logs
    global._logsAutoRefreshStop = null;
    global._logsAutoRefreshInFlight = false;
    global._currentPanel = 'logs';
    global.loadLogs = () => record('/api/logs');

    eval(extractFn(UI, 'tabIsVisibleForPolling'));
    eval(extractFn(UI, 'startVisiblePoll'));
    eval(extractFn(MSG, '_startApprovalFallbackPoll'));
    eval(extractFn(MSG, '_startClarifyFallbackPoll'));
    eval(extractFn(PAN, '_startCronWatch'));
    eval(extractFn(PAN, '_stopCronWatch'));
    eval(extractFn(PAN, '_startLogsAutoRefresh'));
    eval(extractFn(PAN, '_stopLogsAutoRefresh'));
    eval(extractFn(MSG, '_stopBackgroundPolling'));
    eval(extractFn(MSG, 'startBackgroundPolling'));

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
def test_stopped_pollers_do_not_tick_on_visibilitychange(driver):
    assert CRONS not in driver["afterStop"], driver["afterStop"]
    assert LOGS not in driver["afterStop"], driver["afterStop"]
