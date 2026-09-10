"""Static regressions for frontend passive polling hardening."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = ROOT / "static" / "sessions.js"
MESSAGES_JS = ROOT / "static" / "messages.js"


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_session_list_refreshes_are_coalesced_while_in_flight():
    src = _source(SESSIONS_JS)
    assert "let _renderSessionListInFlight = null" in src
    assert "let _renderSessionListQueuedRequest = null" in src
    assert "async function _runRenderSessionListRefresh" in src
    assert "async function _drainRenderSessionListQueue" in src
    assert "const request={opts:opts||{},gen:++_renderSessionListGen}" in src
    assert "if(_renderSessionListInFlight)" in src
    assert "_renderSessionListQueuedRequest={" in src
    assert "opts:_mergeRenderSessionListOptions" in src
    assert "if (_gen !== _renderSessionListGen) return" in src
    assert "const sessionRequestOpts={" in src
    # HWEB-55 spreads sessionRequestOpts into a conditional-GET request object.
    assert "const requestOpts={...(sessionRequestOpts||{}),cache:'no-store'};" in src
    assert "api('/api/sessions' + sessionListQS,requestOpts)" in src
    assert "api('/api/projects' + projectQS,{timeoutToast:false})" in src


def test_approval_and_clarify_fallback_polls_do_not_overlap():
    """Both fallback polls still refuse to overlap.

    HWEB-38 moved the guards from module-scoped flags to closure-local `let
    inFlight` declared inside each _start*FallbackPoll. A module flag was reset
    by stopApprovalPolling()/stopClarifyPolling(), so a request that outlived a
    stop/restart released the REPLACEMENT poll's guard and let the next tick
    overlap it. A closure-local flag is per poller generation, so a stale
    completion writes to a closure nothing reads.
    """
    src = _source(MESSAGES_JS)
    approval = src[src.index("function _startApprovalFallbackPoll("):]
    approval = approval[: approval.index("\nfunction ")]
    assert "let inFlight = false;" in approval
    assert "if (inFlight) return;" in approval
    assert "inFlight = true;" in approval
    assert "finally { inFlight = false; }" in approval

    clarify = src[src.index("function _startClarifyFallbackPoll("):]
    clarify = clarify[: clarify.index("\nfunction ")]
    assert "let inFlight = false;" in clarify
    assert "if (inFlight) return;" in clarify
    assert "inFlight = true;" in clarify
    assert "finally {\n      inFlight = false;\n    }" in clarify

    # The old module-scoped flags must not come back.
    assert "_approvalFallbackPollInFlight" not in src
    assert "_clarifyFallbackPollInFlight" not in src


def test_idle_sidebar_hover_or_focus_cannot_defer_fresh_payloads_forever():
    src = _source(SESSIONS_JS)
    start = src.index("function _isSessionListUserInteracting(){")
    end = src.index("\n}\n", start) + 2
    body = src[start:end]

    assert ":hover" not in body
    assert ":focus-within" not in body
    assert "_sessionListLastPointerMoveAt" in body
    assert "_sessionListLastKeyboardAt" in body
    assert "now-_sessionListLastPointerMoveAt<SESSION_LIST_INTERACTION_IDLE_MS" in body
    assert "now-_sessionListLastKeyboardAt<SESSION_LIST_INTERACTION_IDLE_MS" in body
    assert "list.addEventListener('pointermove', _markSessionListPointerMove" in src
    assert "list.addEventListener('keydown', _markSessionListKeyboardInteraction)" in src
