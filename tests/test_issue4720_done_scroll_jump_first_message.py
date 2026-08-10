"""Terminal windows keep absolute scroll coordinates aligned after completion."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MESSAGES_JS = (REPO / "static" / "messages.js").read_text(encoding="utf-8")


def _compact(text: str) -> str:
    return "".join(text.split())


def test_terminal_metadata_helper_updates_truncation_and_offset():
    compact = _compact(MESSAGES_JS)
    assert "function_applyTerminalMessageWindowMetadata(sessionPayload)" in compact
    assert "_messagesTruncated=!!sessionPayload._messages_truncated" in compact
    assert "constoffset=Number(sessionPayload._messages_offset)" in compact
    assert "_oldestIdx=Number.isFinite(offset)&&offset>0?offset:0" in compact


def test_done_handler_applies_terminal_window_metadata_before_filter():
    compact = _compact(MESSAGES_JS)
    done_start = compact.index("source.addEventListener('done'")
    apply_idx = compact.index("_applyTerminalMessageWindowMetadata(d.session)", done_start)
    filter_idx = compact.index("S.messages=_filterRecoveryControlMessages")
    assert apply_idx < filter_idx


def test_terminal_recovery_uses_bounded_session_tail():
    restore_start = MESSAGES_JS.index("async function _restoreSettledSession")
    restore_end = MESSAGES_JS.index("function _handleStreamError", restore_start)
    restore = MESSAGES_JS[restore_start:restore_end]

    assert "api(_terminalSessionPath(activeSid))" in restore
    assert "_applyTerminalMessageWindowMetadata(session);" in restore
    assert "_messageRenderableMessageCount()" not in restore
