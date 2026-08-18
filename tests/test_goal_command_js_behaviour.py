"""Behavioural tests that drive the ACTUAL cmdGoal() from static/commands.js via node.

The source-inspection regressions in test_goal_command_webui.py assert that
certain expressions/order exist inside cmdGoal's source text. They can stay
green when a refactor preserves those strings but sends the wrong
explicit_model_pick or consumes the pending session-model marker at the wrong
time (#6705, greptile-apps P2). This file closes that gap by spawning node on
the real static/commands.js, extracting cmdGoal, and driving it against a
mocked browser environment (sessionStorage, S, window, api) — asserting the
OBSERVABLE effects: the explicit_model_pick field on the /api/goal payload and
the pending marker's survival/consumption in sessionStorage.

Mirrors the approach of test_renderer_js_behaviour.py.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
COMMANDS_JS_PATH = REPO_ROOT / "static" / "commands.js"

NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


_DRIVER_SRC = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');
const scenario = process.argv[3] || '';

// ---- mocked browser environment ----
const _store = new Map();
global.sessionStorage = {
  getItem: k => (_store.has(k) ? _store.get(k) : null),
  setItem: (k, v) => { _store.set(k, String(v)); },
  removeItem: k => { _store.delete(k); },
};
global.window = {};

// Pending session-model marker helpers mirror static/ui.js. The marker identity
// makes a same-value rewrite distinguishable from the request's captured marker.
const PENDING_PREFIX = 'hermes-webui-pending-session-model:';
const MAX_AGE_MS = 10 * 60 * 1000;
const _key = sid => PENDING_PREFIX + String(sid || '');
let _markerSeq = 0;
function rememberPending(sid, model, provider) {
  const s = String(sid || '').trim();
  const value = String(model || '').trim();
  if (!s || !value) return;
  const savedAt = Date.now();
  sessionStorage.setItem(_key(s), JSON.stringify({
    model: value,
    model_provider: provider ? String(provider).trim() : null,
    saved_at: savedAt,
    marker_id: `${savedAt}:${++_markerSeq}`,
  }));
}
function readPending(sid) {
  const s = String(sid || '').trim();
  if (!s) return null;
  try {
    const raw = sessionStorage.getItem(_key(s));
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    const model = String(parsed && parsed.model || '').trim();
    if (!model) { sessionStorage.removeItem(_key(s)); return null; }
    const savedAt = Number(parsed.saved_at || 0);
    if (savedAt && Date.now() - savedAt > MAX_AGE_MS) { sessionStorage.removeItem(_key(s)); return null; }
    return {
      model,
      model_provider: parsed && parsed.model_provider ? String(parsed.model_provider) : null,
      saved_at: savedAt,
      marker_id: parsed && parsed.marker_id ? String(parsed.marker_id) : '',
    };
  } catch (_) { return null; }
}
function clearPending(sid) {
  const s = String(sid || '').trim();
  if (!s) return;
  try { sessionStorage.removeItem(_key(s)); } catch (_) {}
}
const _readPendingSessionModel = readPending;
const _clearPendingSessionModel = clearPending;

// ---- command helpers the extracted cmdGoal references ----
const t = k => k;
const showToast = () => {};
const renderMessages = () => {};
const clearLiveToolCards = () => {};
const appendThinking = () => {};
const setBusy = () => {};
const setComposerStatus = () => {};
const markInflight = () => {};
const saveInflightState = () => {};
const startApprovalPolling = () => {};
const startClarifyPolling = () => {};
const _fetchYoloState = () => {};
const attachLiveStream = () => {};
const renderSessionList = () => {};
const newSession = async () => {};
const $ = () => null;
const INFLIGHT = {};
function _preserveAcceptedChatStartForBackgroundSession(sid, streamId, _startData, messages){
  INFLIGHT[sid]={streamId,reattach:true,journalReplayFromStart:true,messages:[...(messages||[])],uploaded:[],toolCalls:[]};
}
let _ownerCurrent = true;
function createCommandOwnerContext(){
  return {sid:(S.session&&S.session.session_id)||'',session:S.session};
}
function commandOwnerCurrent(ctx){
  return !!(_ownerCurrent&&ctx&&S.session&&S.session.session_id===ctx.sid);
}
function commandSessionChanged(){ return 'session-changed'; }

// ---- api mock: record every /api/goal payload, respond per scenario ----
const _apiCalls = [];
let _nextResponse = () => ({});
async function api(url, opts) {
  _apiCalls.push({ url, body: JSON.parse(opts.body) });
  return _nextResponse();
}

// ---- extract cmdGoal from the real file and evaluate it ----
function extractFunc(name) {
  // Preserve a leading `async` keyword — dropping it would make the
  // extracted `await` statements a SyntaxError.
  const re = new RegExp('(?:async\\s+)?function\\s+' + name + '\\s*\\(');
  const m = re.exec(src);
  if (!m) throw new Error(name + ' not found');
  const start = m.index;
  let i = src.indexOf('{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(start, i);
}
eval(extractFunc('cmdGoal'));

// ---- scenario state ----
const SID = 'sid-6705-behaviour';
const S = {
  session: {
    session_id: SID,
    workspace: '/tmp/ws',
    model: 'openai/gpt-5.4',
    model_provider: 'openai',
    profile: 'default',
    active_stream_id: null,
  },
  activeProfile: 'default',
  messages: [],
  toolCalls: [],
  activeStreamId: null,
};

(async () => {
  const out = {};
  if (scenario === 'kickoff_consumes') {
    // Pending pick matches the session model; server returns a real kickoff.
    rememberPending(SID, 'openai/gpt-5.4', 'openai');
    window._defaultModel = 'gpt-4o'; window._activeProvider = 'openai';
    _nextResponse = () => ({ stream_id: 's1', pending_started_at: 1,
      effective_model: 'openai/gpt-5.4', effective_model_provider: 'openai' });
    await cmdGoal('ship it');
    out.payload = _apiCalls[0].body;
    out.markerAfter = readPending(SID);
  } else if (scenario === 'control_then_kickoff') {
    // Control-only /goal status: server responds WITHOUT stream_id.
    rememberPending(SID, 'openai/gpt-5.4', 'openai');
    window._defaultModel = 'gpt-4o'; window._activeProvider = 'openai';
    _nextResponse = () => ({ message: 'no active goal', message_key: 'goal_no_active' });
    await cmdGoal('status');
    out.controlPayload = _apiCalls[0].body;
    out.markerAfterControl = readPending(SID);
    // Next real send must still carry the marker and consume it on kickoff.
    _nextResponse = () => ({ stream_id: 's2', pending_started_at: 1 });
    await cmdGoal('ship it');
    out.kickoffPayload = _apiCalls[1].body;
    out.markerAfterKickoff = readPending(SID);
  } else if (scenario === 'midflight_newer_marker_kept') {
    // A newer dropdown selection is recorded WHILE the request is in flight.
    rememberPending(SID, 'openai/gpt-5.4', 'openai');
    window._defaultModel = 'gpt-4o'; window._activeProvider = 'openai';
    _nextResponse = () => {
      rememberPending(SID, 'openai/gpt-6', 'openai');
      return { stream_id: 's3', pending_started_at: 1 };
    };
    await cmdGoal('ship it');
    out.payload = _apiCalls[0].body;
    out.markerAfter = readPending(SID);
  } else if (scenario === 'midflight_same_value_marker_kept') {
    // A same-value rewrite is still a newer user action and must survive.
    rememberPending(SID, 'openai/gpt-5.4', 'openai');
    const originalMarker = readPending(SID);
    window._defaultModel = 'gpt-4o'; window._activeProvider = 'openai';
    _nextResponse = () => {
      rememberPending(SID, 'openai/gpt-5.4', 'openai');
      return { stream_id: 's3b', pending_started_at: 1 };
    };
    await cmdGoal('ship it');
    out.originalMarker = originalMarker;
    out.markerAfter = readPending(SID);
  } else if (scenario === 'owner_switch_after_accept') {
    // A successful kickoff still consumes its marker even if navigation moves
    // the visible pane before the response is handled. UI state stays untouched.
    rememberPending(SID, 'openai/gpt-5.4', 'openai');
    window._defaultModel = 'gpt-4o'; window._activeProvider = 'openai';
    _nextResponse = () => {
      _ownerCurrent = false;
      S.session = {
        session_id: 'other-session', workspace: '/tmp/other', model: 'gpt-4o',
        model_provider: 'openai', profile: 'other', active_stream_id: null,
      };
      return { stream_id: 's3c', pending_started_at: 1 };
    };
    await cmdGoal('ship it');
    out.payload = _apiCalls[0].body;
    out.markerAfter = readPending(SID);
    out.activeStreamId = S.activeStreamId;
    out.inflightCreated = !!INFLIGHT[SID];
    out.inflightStreamId = INFLIGHT[SID] && INFLIGHT[SID].streamId;
    out.journalReplayFromStart = !!(INFLIGHT[SID] && INFLIGHT[SID].journalReplayFromStart);
    out.visibleSession = S.session.session_id;
  } else if (scenario === 'no_marker_no_pick') {
    // Untouched default session: no pending marker, no cross-provider pick.
    S.session.model = 'gpt-4o'; S.session.model_provider = 'openai';
    window._defaultModel = 'gpt-4o'; window._activeProvider = 'openai';
    _nextResponse = () => ({ stream_id: 's4', pending_started_at: 1 });
    await cmdGoal('ship it');
    out.payload = _apiCalls[0].body;
    out.markerAfter = readPending(SID);
  } else {
    throw new Error('unknown scenario: ' + scenario);
  }
  process.stdout.write(JSON.stringify(out));
})().catch(e => {
  process.stderr.write(String((e && e.stack) || e));
  process.exit(1);
});
"""


@pytest.fixture(scope="module")
def driver_path(tmp_path_factory):
    """Write the node driver to a tmp file (works around `node -e` arg quirks)."""
    p = tmp_path_factory.mktemp("goal_driver") / "driver.js"
    p.write_text(_DRIVER_SRC, encoding="utf-8")
    return str(p)


def _run_scenario(driver_path, scenario):
    """Run cmdGoal against the real commands.js with mocked browser state."""
    result = subprocess.run(
        [NODE, driver_path, str(COMMANDS_JS_PATH), scenario],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(f"node driver failed for {scenario}: {result.stderr}")
    return json.loads(result.stdout)


def test_goal_kickoff_consumes_pending_marker_after_success(driver_path):
    """#6703/#6705: a real kickoff carries explicit_model_pick and consumes the
    one-shot pending marker AFTER the successful response (r.stream_id)."""
    out = _run_scenario(driver_path, "kickoff_consumes")
    assert out["payload"].get("explicit_model_pick") is True
    assert out["markerAfter"] is None


def test_goal_control_command_keeps_marker_and_next_send_still_picks(driver_path):
    """#6705: a control-only /goal (e.g. `/goal status`, no stream_id) must NOT
    consume the pending explicit-pick marker; the next real send still carries
    explicit_model_pick and only then consumes the marker."""
    out = _run_scenario(driver_path, "control_then_kickoff")
    # Control round-trip: marker read for the payload but left intact.
    assert out["controlPayload"].get("explicit_model_pick") is True
    assert out["markerAfterControl"] is not None
    assert out["markerAfterControl"]["model"] == "openai/gpt-5.4"
    # Next real send: still carries the pick, then consumes the marker.
    assert out["kickoffPayload"].get("explicit_model_pick") is True
    assert out["markerAfterKickoff"] is None


def test_goal_kickoff_keeps_newer_midflight_marker(driver_path):
    """#6705: a marker re-recorded while the request is in flight (newer
    dropdown selection) must not be clobbered by the stale consume-clear."""
    out = _run_scenario(driver_path, "midflight_newer_marker_kept")
    assert out["payload"].get("explicit_model_pick") is True
    assert out["markerAfter"] is not None
    assert out["markerAfter"]["model"] == "openai/gpt-6"


def test_goal_kickoff_keeps_same_value_midflight_marker(driver_path):
    """Marker identity, not model value alone, protects a newer same-value write."""
    out = _run_scenario(driver_path, "midflight_same_value_marker_kept")
    assert out["markerAfter"] is not None
    assert out["markerAfter"]["model"] == "openai/gpt-5.4"
    assert out["markerAfter"]["marker_id"] != out["originalMarker"]["marker_id"]


def test_goal_kickoff_consumes_marker_after_owner_switch(driver_path):
    """Accepted background kickoff owns marker cleanup, but not the new pane UI."""
    out = _run_scenario(driver_path, "owner_switch_after_accept")
    assert out["payload"]["session_id"] == "sid-6705-behaviour"
    assert out["payload"]["workspace"] == "/tmp/ws"
    assert out["payload"]["profile"] == "default"
    assert out["markerAfter"] is None
    assert out["visibleSession"] == "other-session"
    assert out["activeStreamId"] is None
    assert out["inflightCreated"] is True
    assert out["inflightStreamId"] == "s3c"
    assert out["journalReplayFromStart"] is True


def test_goal_kickoff_without_marker_sends_no_explicit_pick(driver_path):
    """#6703: untouched default sessions (no pending marker, no cross-provider
    pick) must not send the marker at all."""
    out = _run_scenario(driver_path, "no_marker_no_pick")
    assert "explicit_model_pick" not in out["payload"]
    assert out["markerAfter"] is None
