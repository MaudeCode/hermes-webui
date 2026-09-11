"""HWEB-69 — suspend approval and clarify polling while the chat stream is healthy.

Every turn ran two pollers for its whole length (`/api/approval/pending` every
1500 ms, `/api/clarify/pending` every 3000 ms): 60 requests a minute per visible
tab that only ever confirmed what the per-turn `/api/chat/stream` EventSource
had already pushed as `approval` / `clarify` frames.

`startStreamGatedPoll` in `static/messages.js` now wraps both pollers. It reads
chat-stream health off the `LIVE_STREAMS` entry (`open` marks it healthy, the
stream's error handler clears it, every (re)attach republishes "unhealthy"):

    healthy   -> 15 s safety cadence (the subscriber queue can drop a frame)
    unhealthy -> one immediate catch-up tick, then the fast 1500/3000 ms cadence

The node driver below runs the real poller, gate and stream-install sources
against time-aware fake timers, so it fails if the gate is removed or its
transitions stop firing — not just if a string disappears.
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
MESSAGES_JS = (STATIC / "messages.js").read_text(encoding="utf-8")

NODE = shutil.which("node")
requires_node = pytest.mark.skipif(NODE is None, reason="node not on PATH")

APPROVAL = "/api/approval/pending"
CLARIFY = "/api/clarify/pending"


# ── Static wiring ────────────────────────────────────────────────────────────
# The `open` / `error` hooks live inside attachLiveStream's _wireSSE closure,
# which cannot be evaluated standalone; the driver below exercises the
# _setChatStreamHealth transitions they call.


def test_chat_stream_open_and_error_drive_the_health_signal():
    wire = MESSAGES_JS.index("function _wireSSE(source){")
    err = MESSAGES_JS.index("source.addEventListener('error',async e=>{", wire)
    assert "source.addEventListener('open',()=>_setChatStreamHealth(activeSid,source,true));" in MESSAGES_JS[wire:err]
    # First statement of the error handler, before any early return: a stream
    # error deferred for a hidden tab or a pending recovery must still resume
    # polling.
    handler_head = MESSAGES_JS[err : err + 200]
    assert "_setChatStreamHealth(activeSid,source,false);" in handler_head.split("if(")[0]
    # Only the installed transport may flip health; a stale probe's error must
    # not touch the live stream's state.
    assert "if(!live||live.source!==source||live.healthy===healthy) return;" in MESSAGES_JS


def test_both_pollers_share_the_one_gate():
    assert "_approvalPollStop = startStreamGatedPoll(sid, _tick, 1500);" in MESSAGES_JS
    assert "_clarifyFallbackStop = startStreamGatedPoll(sid, _tick, 3000);" in MESSAGES_JS
    assert "const STREAM_HEALTHY_SAFETY_POLL_MS=15000;" in MESSAGES_JS
    # The dedicated approval/clarify EventSources stay closed: the socket
    # budget HWEB-33 won must not go back to four.
    assert "new EventSource(new URL('api/approval/stream" not in MESSAGES_JS
    assert "new EventSource(new URL('api/clarify/stream" not in MESSAGES_JS


# ── Behavioural driver ───────────────────────────────────────────────────────

_HARNESS = textwrap.dedent(
    """\
    const fs = require('fs');

    // Brace-matching extractor that skips strings, template literals and
    // comments. `marker` is either a function name or a literal source prefix.
    function extractFrom(src, marker) {
      let start = -1;
      for (const m of [`async function ${marker}(`, `function ${marker}(`, marker]) {
        start = src.indexOf(m); if (start >= 0) break;
      }
      if (start < 0) throw new Error(`${marker} not found`);
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
      throw new Error(`could not extract ${marker}`);
    }

    const STATIC = process.argv[2];
    const UI = fs.readFileSync(STATIC + '/ui.js', 'utf8');
    const MSG = fs.readFileSync(STATIC + '/messages.js', 'utf8');

    // ── fake document (always visible) ─────────────────────────────────────
    const listeners = new Map();
    global.document = {
      hidden: false,
      addEventListener(type, fn) {
        if (!listeners.has(type)) listeners.set(type, new Set());
        listeners.get(type).add(fn);
      },
      removeEventListener(type, fn) {
        if (listeners.has(type)) listeners.get(type).delete(fn);
      },
    };

    // ── time-aware fake timers ─────────────────────────────────────────────
    let now = 0;
    const timers = new Map();
    let nextTimerId = 1;
    global.setInterval = (fn, ms) => {
      const id = nextTimerId++; timers.set(id, { fn, ms, next: now + ms }); return id;
    };
    global.clearInterval = (id) => { timers.delete(id); };
    const flush = () => new Promise((r) => setTimeout(r, 0));
    async function advance(ms) {
      const end = now + ms;
      while (now < end) {
        now += 100;
        for (const t of Array.from(timers.values())) {
          if (now >= t.next) { t.next += t.ms; t.fn(); }
        }
        await flush();
      }
    }

    // ── request counters ───────────────────────────────────────────────────
    const fetches = {};
    const reset = () => { for (const k of Object.keys(fetches)) delete fetches[k]; };
    const snap = () => ({ approval: fetches['/api/approval/pending'] || 0, clarify: fetches['/api/clarify/pending'] || 0 });
    global.encodeURIComponent = encodeURIComponent;
    global.api = (url) => { const k = String(url).split('?')[0]; fetches[k] = (fetches[k] || 0) + 1; return Promise.resolve({ pending: null }); };

    // ── shared stubs ───────────────────────────────────────────────────────
    global.S = { busy: true, session: { session_id: 'sid-1' }, activeStreamId: null };
    global._approvalPollStop = null;
    global._approvalPollingSessionMissingOrMismatched = () => false;
    global._approvalPendingBySession = new Map();
    global._clearApprovalPendingForSession = () => {};
    global._unmarkApprovalDismissed = () => {};
    global._hideApprovalCardIfOwner = () => {};
    const shown = [];
    global.showApprovalForSession = (sid, pending) => { shown.push({ sid, pending }); };
    global.stopApprovalPolling = () => { if (_approvalPollStop) { _approvalPollStop(); _approvalPollStop = null; } };
    global.stopApprovalPollingForSession = () => {};
    global._clarifyFallbackStop = null;
    global._clarifyPollingSessionId = null;
    global._clarifyMissingEndpointWarned = false;
    global.showClarifyForSession = () => {};
    global._clearClarifyPendingForSession = () => {};
    global._hideClarifyCardIfOwner = () => {};
    global.stopClarifyPolling = () => { if (_clarifyFallbackStop) { _clarifyFallbackStop(); _clarifyFallbackStop = null; } };
    global.setComposerStatus = () => {};

    // stream ownership + health state (module-level consts in messages.js)
    global.LIVE_STREAMS = {};
    global._LIVE_STREAM_OWNERS = { 'sid-1': { streamId: 'st-1', generation: 1 } };
    global._chatStreamHealthListeners = new Set();
    global.STREAM_HEALTHY_SAFETY_POLL_MS = 15000;

    eval(extractFrom(UI, 'tabIsVisibleForPolling'));
    eval(extractFrom(UI, 'startVisiblePoll'));
    eval(extractFrom(MSG, '_liveStreamOwnerMatches'));
    eval(extractFrom(MSG, '_installOwnedLiveStreamSource'));
    eval(extractFrom(MSG, 'chatStreamIsHealthy'));
    eval(extractFrom(MSG, '_notifyChatStreamHealth'));
    eval(extractFrom(MSG, '_setChatStreamHealth'));
    eval(extractFrom(MSG, 'startStreamGatedPoll'));
    eval(extractFrom(MSG, '_startApprovalFallbackPoll'));
    eval(extractFrom(MSG, '_startClarifyFallbackPoll'));

    const fakeSource = () => ({ readyState: 1, close() { this.readyState = 2; } });

    (async () => {
      const out = {};

      // ── Turn start: pollers start before the stream attaches ─────────────
      _startApprovalFallbackPoll('sid-1');
      _startClarifyFallbackPoll('sid-1');
      await flush();
      out.prestart = snap();

      // ── Attach: one catch-up per endpoint, then `open` suspends ──────────
      reset();
      const src1 = fakeSource();
      _installOwnedLiveStreamSource('sid-1', 'st-1', 1, src1);
      out.onAttach = snap();
      await flush();
      _setChatStreamHealth('sid-1', src1, true);   // the `open` hook
      await flush();
      out.afterOpen = snap();

      // ── Healthy 60 s turn: only the 15 s safety poll ─────────────────────
      reset();
      await advance(60000);
      out.healthy60s = snap();

      // ── Error: immediate catch-up, then 1500 / 3000 ms cadence ───────────
      reset();
      _setChatStreamHealth('sid-1', src1, false);  // the error hook
      out.onError = snap();
      await flush();
      reset();
      await advance(6000);
      out.after6sUnhealthy = snap();
      // A stale source (already replaced) must not flip health.
      const stale = fakeSource();
      _setChatStreamHealth('sid-1', stale, true);
      out.staleOpenIgnored = !chatStreamIsHealthy('sid-1');

      // ── Reattach (reconnect): catch-up first, suspension only after open ─
      reset();
      const src2 = fakeSource();
      _installOwnedLiveStreamSource('sid-1', 'st-1', 1, src2, src1);
      out.onReattach = snap();
      out.reattachClosedOld = src1.readyState === 2;
      await flush();
      _setChatStreamHealth('sid-1', src2, true);
      reset();
      await advance(10000);
      out.after10sHealthy = snap();
      await advance(5000);
      out.after15sHealthy = snap();

      // ── Torn down without an error frame: next tick re-arms fast ─────────
      // closeLiveStream() deletes the entry and publishes nothing; the safety
      // tick must notice and never leave a dead stream on the slow cadence.
      delete LIVE_STREAMS['sid-1'];
      reset();
      await advance(15000);
      out.tickAfterTeardown = snap();
      reset();
      await advance(3000);
      out.fastAfterTeardown = snap();

      // ── Stop: interval, safety timer and health listener all released ────
      stopApprovalPolling();
      stopClarifyPolling();
      out.timersAfterStop = timers.size;
      out.visibilityListenersAfterStop = (listeners.get('visibilitychange') || new Set()).size;
      out.healthListenersAfterStop = _chatStreamHealthListeners.size;
      reset();
      _notifyChatStreamHealth('sid-1', false);
      await advance(30000);
      out.requestsAfterStop = snap();

      // ── An `approval` frame on the chat stream shows the card, no poll ──
      // Exercise the real listener body from _wireSSE against a fake source.
      const wire = MSG.slice(MSG.indexOf('function _wireSSE(source){'));
      const approvalBlock = extractFrom(wire, "source.addEventListener('approval',e=>") + ')';
      const activeSid = 'sid-1';
      const handlers = {};
      const source = { addEventListener(type, fn) { handlers[type] = fn; } };
      global._applyToAnchor = () => {};
      global.playAttentionSound = () => {};
      global._attentionSoundKey = () => 'k';
      global.sendBrowserNotification = () => {};
      eval(approvalBlock);
      reset();
      handlers.approval({ data: JSON.stringify({ approval_id: 'ap-1', description: 'run rm' }) });
      out.frameShown = shown.map((s) => [s.sid, s.pending.approval_id]);
      out.frameRequests = snap();

      console.log(JSON.stringify(out));
    })();
    """
)


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
def test_healthy_stream_leaves_only_the_safety_poll(driver):
    # Pre-fix: 40 approval + 20 clarify requests over the same 60 s.
    assert driver["healthy60s"] == {"approval": 4, "clarify": 4}, driver["healthy60s"]


@requires_node
def test_attach_runs_one_catch_up_per_endpoint_before_suspending(driver):
    assert driver["prestart"] == {"approval": 1, "clarify": 1}, driver["prestart"]
    assert driver["onAttach"] == {"approval": 1, "clarify": 1}, driver["onAttach"]
    # `open` itself issues nothing; the stream now carries the frames.
    assert driver["afterOpen"] == driver["onAttach"], driver["afterOpen"]
    assert driver["onReattach"] == {"approval": 1, "clarify": 1}, driver["onReattach"]
    assert driver["reattachClosedOld"] is True
    assert driver["after10sHealthy"] == {"approval": 0, "clarify": 0}, driver["after10sHealthy"]
    assert driver["after15sHealthy"] == {"approval": 1, "clarify": 1}, driver["after15sHealthy"]


@requires_node
def test_stream_error_catches_up_then_resumes_fast_polling(driver):
    # The catch-up is synchronous with the error: the request leaves before
    # any await, so a prompt raised during the outage surfaces at once.
    assert driver["onError"] == {"approval": 1, "clarify": 1}, driver["onError"]
    # 6 s at 1500 ms = 4, at 3000 ms = 2.
    assert driver["after6sUnhealthy"] == {"approval": 4, "clarify": 2}, driver["after6sUnhealthy"]
    assert driver["staleOpenIgnored"] is True


@requires_node
def test_stream_torn_down_without_error_falls_back_to_fast_polling(driver):
    assert driver["tickAfterTeardown"] == {"approval": 1, "clarify": 1}, driver["tickAfterTeardown"]
    assert driver["fastAfterTeardown"] == {"approval": 2, "clarify": 1}, driver["fastAfterTeardown"]


@requires_node
def test_stop_releases_interval_safety_timer_and_health_listener(driver):
    assert driver["timersAfterStop"] == 0, driver["timersAfterStop"]
    assert driver["visibilityListenersAfterStop"] == 0, driver["visibilityListenersAfterStop"]
    assert driver["healthListenersAfterStop"] == 0, driver["healthListenersAfterStop"]
    assert driver["requestsAfterStop"] == {"approval": 0, "clarify": 0}, driver["requestsAfterStop"]


@requires_node
def test_approval_frame_on_chat_stream_shows_card_without_a_poll(driver):
    assert driver["frameShown"] == [["sid-1", "ap-1"]], driver["frameShown"]
    assert driver["frameRequests"] == {"approval": 0, "clarify": 0}, driver["frameRequests"]
