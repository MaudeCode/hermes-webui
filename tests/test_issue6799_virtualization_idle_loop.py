"""Regression coverage for the settled-transcript virtualization loop (#6799)."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _scroll_listener_body() -> str:
    start = UI_JS.index("el.addEventListener('scroll',()=>{")
    end = UI_JS.index("  },{passive:true});", start)
    return UI_JS[start:end]


def test_programmatic_scroll_cannot_schedule_virtualized_rerender():
    listener = _scroll_listener_body()

    guard_pos = listener.index("if(_freshProgrammaticScrollActive()) return;")
    schedule_pos = listener.index("_scheduleMessageVirtualizedRender();")

    assert guard_pos < schedule_pos


def test_settled_render_restores_window_without_idle_rerender_loop():
    assert "function _restoreMessageRenderWindowAfterSettledRender()" in UI_JS

    first_block = MESSAGES_JS.index("// Arm one-shot keep-open")
    first_render = MESSAGES_JS.index("renderMessages({preserveScroll:true});", first_block)
    first_restore = MESSAGES_JS.index(
        "_restoreMessageRenderWindowAfterSettledRender();",
        first_render,
    )
    first_block_end = MESSAGES_JS.index(
        "loadDir('.', { preservePreview: true });",
        first_render,
    )
    assert first_render < first_restore < first_block_end

    second_block = MESSAGES_JS.index(
        "if(isSessionViewed) _markSessionViewed(completedSid, session.message_count"
    )
    second_render = MESSAGES_JS.index(
        "renderMessages({preserveScroll:true});",
        second_block,
    )
    second_restore = MESSAGES_JS.index(
        "_restoreMessageRenderWindowAfterSettledRender();",
        second_render,
    )
    assert second_render < second_restore
