"""HWEB-75 — one message identity across the optimistic -> settled swap.

For ``/moa``, bundle-command and ``/use`` turns the optimistic user row shows
the user-facing text while the server stores and returns the TRANSFORMED
invocation, so every content-derived identity changes at settlement. The fix
stamps the optimistic row with the server-owned turn identity returned by
``/api/chat/start`` and matches rows on the first identity both sides share:
persisted ``id``, then the turn start, then the legacy role/ts/content key.

The three consumers named by the ticket are exercised through the shipped
functions, extracted from ``static/messages.js`` / ``static/ui.js`` and run in
node, with a transformed prompt — not only the identical-content case.
"""

from __future__ import annotations

import json
import pathlib
import subprocess

import pytest

from tests.js_source_extract import extract_function

REPO = pathlib.Path(__file__).resolve().parents[1]
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")

_MESSAGES_HELPERS = [
    "_messageIdentityKey",
    "_messagePersistedId",
    "_messageTurnStartedAt",
    "_messageTurnIdentity",
    "_messageStableIdentities",
    "_messageIdentityCandidates",
    "_messagesShareIdentity",
    "_formatTurnStartedAt",
    "_adoptServerTurnIdentity",
    "_isHistoricalAnchorActivityScene",
    "_carryForwardEphemeralTurnFields",
]
_UI_HELPERS = [
    "_worklogDetailHashKey",
    "_userMessageExpandIdentity",
    "_userMessageExpandKeys",
    "_userMessageExpandKey",
    "_userMessageExpandKeyList",
    "_clearUserMessageExpandState",
    "_userMessageIsExpanded",
    "_setUserMessageExpanded",
    "_safeEncodeURIComponent",
    "_messageViewportAnchorKeyForMessage",
    "_compressionMessageAnchorKey",
    "_compressionAnchorIndex",
]

STREAM_ID = "stream-hweb75"
STARTED_AT = 1757600000.123456  # what /api/chat/start returns as pending_started_at
DISPLAY = "/moa " + "explain this deploy log line by line\n" * 40
TRANSFORMED = "explain this deploy log line by line\n" * 40  # /moa strips the prefix


def _run(body: str, **inputs) -> dict:
    helpers = "\n".join(
        [extract_function(MESSAGES_JS, n) for n in _MESSAGES_HELPERS]
        + [extract_function(UI_JS, n) for n in _UI_HELPERS]
    )
    script = f"""
const _EPHEMERAL_TURN_FIELDS=['_turnUsage','_turnDuration','_turnTps','_gatewayRouting','_statusCard','_anchor_stream_id','_anchor_activity_scene'];
const USER_MSG_EXPANDED_MAX=200;
const _userMsgExpandedByKey=Object.create(null);
function msgContent(m){{ return typeof m.content==='string'?m.content:''; }}
const S={{session:{{session_id:'hweb75-session'}},messages:[]}};
{helpers}
const IN={json.dumps(inputs)};
const out=(function(){{
{body}
}})();
process.stdout.write(JSON.stringify(out));
"""
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


def _rows_js() -> str:
    """Shared scene: the optimistic row is stamped exactly as send() stamps it."""
    return """
  const optimistic={role:'user',content:IN.display,_ts:1757599990.5,_pending:true,_statusCard:{kind:'x'}};
  const stamped=_adoptServerTurnIdentity(optimistic, IN.streamId, {pending_started_at:IN.startedAt});
  // What the settled transcript hands back: minted id, server timestamp, transformed text.
  const settledUser={role:'user',content:IN.transformed,timestamp:IN.startedAt,id:7};
  const settledAssistant={role:'assistant',content:'done',timestamp:IN.startedAt+0.000001,id:8};
  // A later turn that repeats the same displayed prompt must stay a different message.
  const laterTurn={role:'user',content:IN.transformed,timestamp:IN.startedAt+30,id:9};
"""


def test_optimistic_row_adopts_the_server_turn_identity():
    r = _run(
        _rows_js()
        + """
  return {
    stamped,
    token: optimistic._active_turn_token,
    ts: optimistic._ts,
    candidates: _messageIdentityCandidates(optimistic),
    settledCandidates: _messageIdentityCandidates(settledUser),
    shared: _messagesShareIdentity(optimistic, settledUser),
    notLater: _messagesShareIdentity(optimistic, laterTurn),
    formatted: IN.samples.map(v => _formatTurnStartedAt(v)),
  };
""",
        display=DISPLAY, transformed=TRANSFORMED, streamId=STREAM_ID, startedAt=STARTED_AT,
        samples=[1757600000.123456, 1757600000.0, 1757600000.5, 1757600000.1, 1757600000.1234567],
    )
    assert r["stamped"] is True
    assert r["ts"] == STARTED_AT
    # The exact server format: build_active_turn_token(stream_id, started_at) = f"{sid}:{started:.17g}".
    assert r["token"] == f"{STREAM_ID}:{STARTED_AT:.17g}"
    assert r["formatted"] == [f"{v:.17g}" for v in [1757600000.123456, 1757600000.0, 1757600000.5, 1757600000.1, 1757600000.1234567]]
    # The one identity both sides share is the turn start; content and ts differ.
    assert f"user|turn:{STARTED_AT!r}" in r["candidates"]
    assert f"user|turn:{STARTED_AT!r}" in r["settledCandidates"]
    assert r["settledCandidates"][0] == "user|id:7"
    assert r["shared"] is True
    assert r["notLater"] is False


def test_carry_forward_matches_a_transformed_prompt_across_the_swap():
    r = _run(
        _rows_js()
        + """
  const next=[settledUser, settledAssistant, laterTurn];
  _carryForwardEphemeralTurnFields([optimistic], next);
  // Legacy rows (no id, no token) still carry on the old role/ts/content key.
  const legacyPrev={role:'user',content:'plain prompt',_ts:1757500000.25,_statusCard:{kind:'legacy'}};
  const legacyNext={role:'user',content:'plain prompt',timestamp:1757500000.25};
  _carryForwardEphemeralTurnFields([legacyPrev],[legacyNext]);
  // Two legacy rows sharing an integer-second timestamp never collapse into one identity.
  const a={role:'user',content:'alpha',timestamp:1757500000,id:1,_statusCard:{kind:'a'}};
  const b={role:'user',content:'beta',timestamp:1757500000,id:2,_statusCard:{kind:'b'}};
  const a2={role:'user',content:'alpha',timestamp:1757500000,id:1};
  const b2={role:'user',content:'beta',timestamp:1757500000,id:2};
  _carryForwardEphemeralTurnFields([a,b],[a2,b2]);
  return {
    settled: settledUser._statusCard||null,
    later: laterTurn._statusCard||null,
    legacy: legacyNext._statusCard||null,
    intA: a2._statusCard.kind, intB: b2._statusCard.kind,
    intTurn: _messageTurnIdentity(a),
  };
""",
        display=DISPLAY, transformed=TRANSFORMED, streamId=STREAM_ID, startedAt=STARTED_AT,
    )
    assert r["settled"] == {"kind": "x"}, r
    assert r["later"] is None, r
    assert r["legacy"] == {"kind": "legacy"}, r
    assert (r["intA"], r["intB"]) == ("a", "b"), r
    # An integer-second timestamp is not a WebUI turn start: fail closed to ids/legacy.
    assert r["intTurn"] == "", r


def test_disclosure_state_follows_the_turn_from_optimistic_to_settled_to_reload():
    r = _run(
        """
  const optimistic={role:'user',content:IN.display,_ts:1757599990.5,_pending:true};
  const keysBeforeStart=_userMessageExpandKeys(optimistic, IN.display, 0);
  // The reader opens the long prompt before /api/chat/start replies.
  _setUserMessageExpanded(keysBeforeStart, true);
  _adoptServerTurnIdentity(optimistic, IN.streamId, {pending_started_at:IN.startedAt});
  const keysAfterStart=_userMessageExpandKeys(optimistic, IN.display, 0);
  const afterStart=_userMessageIsExpanded(keysAfterStart);
  // Settlement: id, server timestamp, transformed text.
  const settled={role:'user',content:IN.transformed,timestamp:IN.startedAt,id:7};
  const keysSettled=_userMessageExpandKeys(settled, IN.transformed, 0);
  const afterSettle=_userMessageIsExpanded(keysSettled);
  // A fresh load (session switch and back): the server row alone, no token anywhere.
  const reloaded={role:'user',content:IN.transformed,timestamp:IN.startedAt,id:7};
  const keysReloaded=_userMessageExpandKeys(reloaded, IN.transformed, 0);
  const afterReload=_userMessageIsExpanded(keysReloaded);
  // A later turn repeating the same displayed prompt must not inherit it.
  const later={role:'user',content:IN.display,_ts:1757600100.75,_pending:true};
  _adoptServerTurnIdentity(later, 'stream-later', {pending_started_at:1757600100.75});
  const laterInherits=_userMessageIsExpanded(_userMessageExpandKeys(later, IN.display, 0));
  const legacyInherits=_userMessageIsExpanded(_userMessageExpandKeys({role:'user',content:IN.display}, IN.display, 0));
  // Another session never reads this one's entry.
  S.session={session_id:'other'};
  const otherSession=_userMessageIsExpanded(keysReloaded);
  S.session={session_id:'hweb75-session'};
  // Collapsing releases every identity of the row.
  _setUserMessageExpanded(keysReloaded, false);
  return {
    keysBeforeStart, keysAfterStart, keysSettled, keysReloaded,
    afterStart, afterSettle, afterReload, laterInherits, legacyInherits, otherSession,
    afterCollapse: [keysBeforeStart, keysAfterStart, keysSettled, keysReloaded].map(k => _userMessageIsExpanded(k)),
    storeSize: Object.keys(_userMsgExpandedByKey).length,
  };
""",
        display=DISPLAY, transformed=TRANSFORMED, streamId=STREAM_ID, startedAt=STARTED_AT,
    )
    turn = f"u|turn:{STARTED_AT!r}"
    assert "," not in r["keysBeforeStart"], r  # content only, before the reply
    assert r["keysAfterStart"].split(",")[0] == turn, r
    assert r["keysSettled"].split(",")[:2] == ["u|id:7", turn], r
    assert r["keysReloaded"].split(",")[:2] == ["u|id:7", turn], r
    assert r["afterStart"] is True, r
    assert r["afterSettle"] is True, r
    assert r["afterReload"] is True, r
    assert r["laterInherits"] is False, r
    assert r["legacyInherits"] is False, r
    assert r["otherSession"] is False, r
    assert r["afterCollapse"] == [False, False, False, False], r
    assert r["storeSize"] == 0, r


def test_viewport_anchor_matches_the_settled_row_and_keeps_fuzzy_legacy_matching():
    r = _run(
        _rows_js()
        + """
  const anchor=_compressionMessageAnchorKey(optimistic);
  const vis=[{m:settledUser},{m:settledAssistant},{m:laterTurn}];
  const idx=_compressionAnchorIndex(vis, anchor, -1);
  // Legacy anchors keep the deliberately fuzzy comparison: a missing ts still matches on text.
  const legacyAnchor=_compressionMessageAnchorKey({role:'user',content:'plain prompt'});
  const legacyVis=[{m:{role:'user',content:'plain prompt',timestamp:1757500000.25}}];
  const legacyIdx=_compressionAnchorIndex(legacyVis, legacyAnchor, -1);
  return {
    idx, legacyIdx,
    optimisticKey: _messageViewportAnchorKeyForMessage(optimistic),
    settledKey: _messageViewportAnchorKeyForMessage(settledUser),
    laterKey: _messageViewportAnchorKeyForMessage(laterTurn),
    legacyKey: _messageViewportAnchorKeyForMessage(legacyVis[0].m),
  };
""",
        display=DISPLAY, transformed=TRANSFORMED, streamId=STREAM_ID, startedAt=STARTED_AT,
    )
    assert r["idx"] == 0, r
    assert r["legacyIdx"] == 0, r
    assert r["optimisticKey"] == r["settledKey"], r
    assert r["settledKey"] != r["laterKey"], r
    assert r["legacyKey"].count("|") == 3, r  # legacy rows keep role|ts|attachments|text


def test_send_stamps_the_optimistic_row_once_chat_start_is_accepted():
    start = MESSAGES_JS.index("async function send(")
    body = MESSAGES_JS[start:MESSAGES_JS.index("async function startRegeneration(", start)]
    assign = body.index("S.activeStreamId = streamId;")
    stamp = body.index("_adoptServerTurnIdentity(userMsg, streamId, startData);", assign)
    attach = body.index("attachLiveStream(activeSid, streamId, uploadedNames);", stamp)
    assert assign < stamp < attach


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
