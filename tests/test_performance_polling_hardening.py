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
    assert "api('/api/sessions' + sessionListQS,sessionRequestOpts)" in src
    assert "api('/api/projects' + projectQS,{timeoutToast:false})" in src


def test_approval_and_clarify_fallback_polls_do_not_overlap():
    src = _source(MESSAGES_JS)
    assert "let _approvalFallbackPollInFlight = false" in src
    assert "if (_approvalFallbackPollInFlight) return" in src
    assert "_approvalFallbackPollInFlight = true" in src
    assert "finally { _approvalFallbackPollInFlight = false; }" in src
    assert "_approvalFallbackPollInFlight = false;\n  _approvalPollingSessionId = null;" in src

    assert "let _clarifyFallbackPollInFlight = false" in src
    assert "if (_clarifyFallbackPollInFlight) return" in src
    assert "_clarifyFallbackPollInFlight = true" in src
    assert "finally {\n      _clarifyFallbackPollInFlight = false;\n    }" in src
    assert "_clarifyFallbackPollInFlight = false;\n  _clarifyPollingSessionId = null;" in src


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
