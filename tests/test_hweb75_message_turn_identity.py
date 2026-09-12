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
    "_encodeIdentityComponent",
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
    "_userMessageRawText",
    "_syncUserMessageIdentityRow",
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
# Trimmed, like the `data-raw-text` renderMessages keys a user row on.
DISPLAY = ("/moa " + "explain this deploy log line by line\n" * 40).strip()
TRANSFORMED = ("explain this deploy log line by line\n" * 40).strip()  # /moa strips the prefix


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
function _stripWorkspaceDisplayPrefix(t){{ return t; }}
function _stripAttachedFilesMarkerForDisplay(t){{ return t; }}
function _userMessageDomId(i){{ return 'msg-user-'+i; }}
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
  // Imported twins: distinct ids, one FRACTIONAL timestamp and the same content
  // prefix. The id conflict is final — the turn alias must not cross them.
  const twinA={role:'user',content:'same opening',timestamp:1757400000.5,id:41,_statusCard:{kind:'A'}};
  const twinB={role:'user',content:'same opening',timestamp:1757400000.5,id:42};
  const twinB2={role:'user',content:'same opening',timestamp:1757400000.5,id:42};
  _carryForwardEphemeralTurnFields([twinA],[twinB2]);
  return {
    settled: settledUser._statusCard||null,
    later: laterTurn._statusCard||null,
    legacy: legacyNext._statusCard||null,
    intA: a2._statusCard.kind, intB: b2._statusCard.kind,
    intTurn: _messageTurnIdentity(a),
    twinShare: _messagesShareIdentity(twinA, twinB),
    twinCarried: twinB2._statusCard||null,
    sameTurnAlias: _messageTurnIdentity(twinA)===_messageTurnIdentity(twinB),
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
    # Precondition: the twins really do share a turn alias; the id still wins.
    assert r["sameTurnAlias"] is True, r
    assert r["twinShare"] is False, r
    assert r["twinCarried"] is None, r


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


def test_background_stamp_migrates_disclosure_state_under_the_owning_session():
    """The reader can switch sessions while /api/chat/start is pending. The
    accepted turn is still stamped, and state opened before the switch moves
    onto the turn key under the owning session, not the one now on screen."""
    r = _run(
        """
  const optimistic={role:'user',content:IN.display,_ts:1757599990.5,_pending:true};
  S.messages=[optimistic];
  _setUserMessageExpanded(_userMessageExpandKeys(optimistic, IN.display, 0), true);
  // Navigate away: another session owns the pane and S.messages.
  S.session={session_id:'elsewhere'};
  S.messages=[{role:'user',content:'unrelated'}];
  _adoptServerTurnIdentity(optimistic, IN.streamId, {pending_started_at:IN.startedAt}, 'hweb75-session');
  const turnKey='u|turn:'+IN.startedAt;
  const underOwner=_userMessageIsExpanded(turnKey, 'hweb75-session');
  const underScreen=_userMessageIsExpanded(turnKey);
  // Back on the owning session, the settled server row reads it.
  S.session={session_id:'hweb75-session'};
  const settled={role:'user',content:IN.transformed,timestamp:IN.startedAt,id:7};
  return {
    token: optimistic._active_turn_token,
    underOwner, underScreen,
    afterReturn: _userMessageIsExpanded(_userMessageExpandKeys(settled, IN.transformed, 0)),
  };
""",
        display=DISPLAY, transformed=TRANSFORMED, streamId=STREAM_ID, startedAt=STARTED_AT,
    )
    assert r["token"] == f"{STREAM_ID}:{STARTED_AT:.17g}", r
    assert r["underOwner"] is True, r
    assert r["underScreen"] is False, r
    assert r["afterReturn"] is True, r


def test_persisted_ids_are_encoded_so_a_comma_or_bar_cannot_split_or_collide():
    r = _run(
        """
  const withComma={role:'user',content:IN.display,id:'part,one',timestamp:1757500000};
  const prefixOnly={role:'user',content:IN.display,id:'part',timestamp:1757500000};
  const keysComma=_userMessageExpandKeys(withComma, IN.display, 0);
  _setUserMessageExpanded(keysComma, true);
  // A lone surrogate (valid JSON, preserved by /api/session/import) must not
  // throw, and distinct malformed ids must stay distinct.
  const lone={role:'user',content:'x',id:'\\ud800x'};
  let loneThrew=false, loneStable=null;
  try{ loneStable=_messageStableIdentities(lone); }catch(_){ loneThrew=true; }
  const enc=(id)=>_messageStableIdentities({role:'user',content:'x',id})[0];
  return {
    stable: _messageStableIdentities(withComma),
    barId: _messageStableIdentities({role:'user',content:'x',id:'a|b'}),
    loneThrew, loneStable,
    loneDeterministic: JSON.stringify(_messageStableIdentities(lone))===JSON.stringify(loneStable),
    loneDistinct: enc('\\ud800x')!==enc('\\ud801x'),
    pairKept: enc('\\ud83d\\ude00'),
    escapeInjective: enc('\\\\ud800x')!==enc('\\ud800x'),
    numericAliasesAgree: _messagePersistedId({id:1, message_id:'1'}),
    conflictingAliases: _messagePersistedId({id:1, message_id:2}),
    boolAliasPoisons: _messagePersistedId({id:1, message_id:true}),
    unsafeIntRejected: _messagePersistedId({id:9007199254740993}),
    fractionRejected: _messagePersistedId({id:1.5}),
    messageIdOnly: _messagePersistedId({message_id:12}),
    keyCount: _userMessageExpandKeyList(keysComma).length,
    prefixInherits: _userMessageIsExpanded(_userMessageExpandKeys(prefixOnly, IN.display, 0)),
    rejected: [_messagePersistedId({id:true}), _messagePersistedId({id:{}}), _messagePersistedId({id:''}), _messagePersistedId({message_id:'m1'})],
  };
""",
        display=DISPLAY,
    )
    assert r["stable"] == ["id:part%2Cone"], r
    assert r["barId"] == ["id:a%7Cb"], r
    assert r["loneThrew"] is False, r
    assert r["loneStable"] == ["id:%5Cud800x"], r
    assert r["loneDeterministic"] is True, r
    assert r["loneDistinct"] is True, r
    assert r["pairKept"] == "id:%F0%9F%98%80", r  # a valid pair encodes as UTF-8, untouched
    assert r["escapeInjective"] is True, r  # a literal backslash cannot impersonate an escape
    # Aliases mirror the backend: normalized agreement is one id, conflict fails closed.
    assert r["numericAliasesAgree"] == "1", r
    assert r["conflictingAliases"] is None, r
    assert r["boolAliasPoisons"] is None, r
    # Numeric ids count only as safe integers; anything JSON may have rounded is no id.
    assert r["unsafeIntRejected"] is None, r
    assert r["fractionRejected"] is None, r
    assert r["messageIdOnly"] == "12", r
    assert r["keyCount"] == 2, r  # id + content, no stray split
    # Both rows share the content key, so the prefix-id row does read the shared
    # content entry — the existing "identical prompts open together" semantics —
    # but only after the comma id is proven intact above, never by truncation.
    assert r["rejected"] == [None, None, None, "m1"], r


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
  // Imported twins: distinct ids, one fractional timestamp. The id decides.
  const twinA={role:'user',content:'a',id:41,timestamp:1757400000.5};
  const twinB={role:'user',content:'b',id:42,timestamp:1757400000.5};
  const twinIdx=_compressionAnchorIndex([{m:twinA},{m:twinB}], _compressionMessageAnchorKey(twinA), -1);
  const reloaded={role:'user',content:IN.transformed,timestamp:IN.startedAt,id:7};
  return {
    idx, legacyIdx, twinIdx,
    optimisticKey: _messageViewportAnchorKeyForMessage(optimistic),
    settledKey: _messageViewportAnchorKeyForMessage(settledUser),
    reloadedKey: _messageViewportAnchorKeyForMessage(reloaded),
    laterKey: _messageViewportAnchorKeyForMessage(laterTurn),
    twinKeysDistinct: _messageViewportAnchorKeyForMessage(twinA)!==_messageViewportAnchorKeyForMessage(twinB),
    legacyKey: _messageViewportAnchorKeyForMessage(legacyVis[0].m),
  };
""",
        display=DISPLAY, transformed=TRANSFORMED, streamId=STREAM_ID, startedAt=STARTED_AT,
    )
    # The optimistic anchor (turn only) finds its settled row (id + turn) by the turn.
    assert r["idx"] == 0, r
    assert r["legacyIdx"] == 0, r
    # Two persisted rows sharing a timestamp are told apart by id (never the twin).
    assert r["twinIdx"] == 0, r
    assert r["twinKeysDistinct"] is True, r
    assert r["optimisticKey"] == f"user|turn%3A{STARTED_AT!r}", r
    # Persisted rows key on their id, stable across reloads and never shared.
    assert r["settledKey"] == "user|id%3A7" == r["reloadedKey"], r
    assert r["settledKey"] != r["laterKey"], r
    assert r["legacyKey"].count("|") == 3, r  # legacy rows keep role|ts|attachments|text


def test_recovered_terminal_rows_keep_the_exact_start_time_and_get_an_id():
    """Cancel / provider-error / stale-pending recovery used to store the user
    row with an int-second timestamp and no id, so a transformed turn that ended
    on an error had no identity the optimistic row could share."""
    from types import SimpleNamespace

    from api import models
    from api.streaming import _materialize_pending_user_turn_before_error

    def session():
        # An imported numeric-string id ("9") normalizes like the integer 9 on
        # both sides, so the minted id must skip past it, not collide with it.
        return SimpleNamespace(
            session_id="hweb75-recover",
            messages=[
                {"role": "assistant", "content": "previous answer", "id": 3},
                {"role": "user", "content": "imported row", "id": "9", "timestamp": 1757500000},
                # message_id-only alias reserves its number too; a digit string
                # past the safe-integer range and an unsafe int are ignored, not
                # raised on and not allowed to push minted ids out of range.
                {"role": "assistant", "content": "alias only", "message_id": "12"},
                {"role": "user", "content": "absurd", "id": "9" * 5000},
                {"role": "user", "content": "unsafe", "id": 2**60},
            ],
            context_messages=[],
            pending_user_message=TRANSFORMED,
            pending_started_at=STARTED_AT,
            pending_user_source=None,
            pending_attachments=[],
            active_stream_id=STREAM_ID,
            truncation_watermark=None,
        )

    s = session()
    assert _materialize_pending_user_turn_before_error(s) is True
    row = s.messages[-1]
    assert row["role"] == "user" and row["content"] == TRANSFORMED
    assert row["timestamp"] == STARTED_AT and isinstance(row["timestamp"], float)
    assert row["id"] == 13
    # The exact-checkpoint guard still recognises the float row: no duplicate.
    assert _materialize_pending_user_turn_before_error(s) is False
    assert [m["role"] for m in s.messages].count("user") == 4

    s2 = session()
    recovered = models._append_recovered_pending_turn(s2, timestamp=STARTED_AT)
    assert recovered["timestamp"] == STARTED_AT and recovered["id"] == 13

    # Every JS-safe integer has at most 16 digits; a 16-digit string id is
    # reserved like its integer twin, while anything past the safe range is not.
    from api.streaming import _assign_stable_message_ids

    fresh = {"role": "user", "content": "new"}
    _assign_stable_message_ids(
        [fresh],
        [{"id": 1000000000000000}, {"id": "1000000000000001"}, {"id": "9007199254740993"}],
    )
    assert fresh["id"] == 1000000000000002
    assert s2.messages[-1] is recovered

    # And the client matches the optimistic row to that recovered row.
    r = _run(
        """
  const optimistic={role:'user',content:IN.display,_ts:1757599990.5,_pending:true,_statusCard:{kind:'x'}};
  _adoptServerTurnIdentity(optimistic, IN.streamId, {pending_started_at:IN.startedAt});
  const recovered=IN.recovered;
  _carryForwardEphemeralTurnFields([optimistic],[recovered]);
  return {shared:_messagesShareIdentity(optimistic, recovered), carried: recovered._statusCard||null};
""",
        display=DISPLAY, streamId=STREAM_ID, startedAt=STARTED_AT,
        recovered={k: v for k, v in row.items() if not k.startswith("_")},
    )
    assert r["shared"] is True, r
    assert r["carried"] == {"kind": "x"}, r


def test_send_stamps_the_optimistic_row_once_chat_start_is_accepted():
    start = MESSAGES_JS.index("async function send(")
    body = MESSAGES_JS[start:MESSAGES_JS.index("async function startRegeneration(", start)]
    start = body.index("const startData=await api('/api/chat/start'")
    stamp = body.index("_adoptServerTurnIdentity(userMsg, startData&&startData.stream_id, startData, activeSid);", start)
    # Before the ownership branch: a turn preserved for a background session is stamped too.
    background = body.index("if(!_sendPreprocessStillOwnsSession(activeSid)){", start)
    assert start < stamp < background


if __name__ == "__main__":  # pragma: no cover
    pytest.main([__file__, "-q"])
