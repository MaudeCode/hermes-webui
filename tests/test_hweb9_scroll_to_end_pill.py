"""HWEB-9 — one contextual scroll-to-end pill centred above the composer.

Before this ticket the transcript carried two right-edge controls: a circular
scroll-to-bottom arrow and an opt-in "Start" jump button. The primary recovery
action after leaving the live edge sat in the far corner, visually detached from
the composer and from the response the reader is following.

The geometry assertions are real-browser measurements against the running test
server, so the shipped cascade (including the mobile media block and the
per-skin overrides) is what gets measured. The behavioural assertions stay at
the source level, matching how the surrounding scroll-cue suites (#677, #3545,
session jump buttons) already pin the pin/unpin state machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._pytest_port import BASE

REPO = Path(__file__).resolve().parents[1]
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")

# Long enough to make the transcript genuinely scrollable at every viewport.
_LONG_TRANSCRIPT = "".join(
    f'<div class="msg-row" data-role="assistant"><div class="msg-body">'
    f"<p>Paragraph {i} of a long transcript.</p></div></div>"
    for i in range(120)
)

_MEASURE_JS = """
(opts) => {
  const doc = document;
  doc.getElementById('msgInner').innerHTML = opts.transcript;
  const empty = doc.getElementById('emptyState');
  if (empty) empty.style.display = 'none';

  const btn = doc.getElementById('scrollToBottomBtn');
  btn.classList.toggle('scroll-to-bottom-btn--new-message', !!opts.newMessage);
  btn.style.display = 'flex';

  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom,
             width: r.width, height: r.height };
  };

  const cs = getComputedStyle(btn);
  const label = btn.querySelector('.session-jump-btn__text');
  return {
    btn: box(btn),
    label: box(label),
    labelText: label ? label.textContent.trim() : '',
    column: box(doc.getElementById('msgInner')),
    composer: box(doc.getElementById('composerWrap')),
    outlineFab: box(doc.getElementById('outlineToggleBtn')),
    borderRadius: cs.borderRadius,
    animationName: cs.animationName,
    accessibleName: btn.getAttribute('aria-label') || '',
    tagName: btn.tagName,
    viewportWidth: window.innerWidth,
  };
}
"""


def _measure(viewport_width: int, new_message: bool = False):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run `playwright install chromium`")

    playwright = sync_playwright().start()
    try:
        browser = playwright.chromium.launch(
            headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
        )
    except Exception as exc:  # pragma: no cover - no browser binary in sandbox
        playwright.stop()
        pytest.skip(f"chromium unavailable for browser measurement: {exc}")

    try:
        page = browser.new_page(viewport={"width": viewport_width, "height": 900})
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#composerBox", timeout=15000)
        return page.evaluate(
            _MEASURE_JS,
            {"transcript": _LONG_TRANSCRIPT, "newMessage": new_message},
        )
    finally:
        browser.close()
        playwright.stop()


def _function_body(src: str, signature: str) -> str:
    start = src.index(signature)
    brace = src.index("{", start)
    depth = 0
    for i in range(brace, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"function body not found: {signature}")


@pytest.mark.parametrize("label,viewport", [("desktop", 1440), ("mobile", 390)])
def test_pill_is_centred_immediately_above_the_composer(label, viewport):
    m = _measure(viewport)
    btn, col, composer = m["btn"], m["column"], m["composer"]

    # Centred on the reading column, not parked on an edge.
    assert abs((btn["left"] + btn["right"]) / 2 - (col["left"] + col["right"]) / 2) <= 1, (label, m)

    # Immediately above the composer, and never over any composer control.
    assert btn["bottom"] <= composer["top"] + 1, (label, m)
    assert composer["top"] - btn["bottom"] <= 24, (label, m)

    # Inside the viewport at every width, including the narrow one.
    assert btn["left"] >= 0 and btn["right"] <= m["viewportWidth"], (label, m)


@pytest.mark.parametrize("label,viewport", [("desktop", 1440), ("mobile", 390)])
def test_pill_is_a_labelled_pill_in_both_states(label, viewport):
    for new_message in (False, True):
        m = _measure(viewport, new_message=new_message)
        assert m["tagName"] == "BUTTON", (label, m)
        assert m["accessibleName"], (label, m)
        assert m["labelText"], (label, m)
        # The label is rendered, so the control reads as a pill and not a bare arrow.
        assert m["label"]["width"] > 0, (label, new_message, m)
        assert m["btn"]["width"] > m["btn"]["height"], (label, new_message, m)
        # The new-output state is a static restyle, never a running animation.
        assert m["animationName"] == "none", (label, new_message, m)


def test_pill_does_not_collide_with_the_outline_fab():
    """Both float over the transcript; the pill is centred, the FAB edge-parked."""
    m = _measure(1440)
    btn, fab = m["btn"], m["outlineFab"]
    if fab is None or fab["width"] == 0:
        pytest.skip("outline FAB is hidden in this state")
    assert btn["right"] < fab["left"] or fab["bottom"] < btn["top"], m


def test_pill_is_hidden_while_following_the_live_edge():
    """Only an unpinned reader more than 80px off the tail sees it."""
    listener = UI_JS[
        UI_JS.index("el.addEventListener('scroll'") : UI_JS.index(
            "})();", UI_JS.index("el.addEventListener('scroll'")
        )
    ]
    assert "const showBottomButton=!_scrollPinned && el.scrollHeight-top-el.clientHeight>80" in listener
    assert "_syncScrollToBottomCue(showBottomButton,{newMessage:_newMessageCueVisible})" in listener

    maybe = _function_body(UI_JS, "function _maybeShowNewMessageScrollCue")
    assert "el.scrollHeight>previousHeight+24" in maybe and "distance>80" in maybe

    # Session switching and stream resets both drop the cue with the old scroll state.
    assert "_clearNewMessageScrollCue();" in _function_body(
        UI_JS, "function _resetScrollDirectionTracker"
    )
    assert "_clearNewMessageScrollCue();" in _function_body(
        UI_JS, "function _resetStreamScrollFollow"
    )


def test_activating_the_pill_settles_on_the_true_transcript_end():
    """Late layout (markdown, images, approval flyout, mobile toolbar) is re-anchored."""
    scroll = _function_body(UI_JS, "function scrollToBottom")
    assert "_setMessageScrollToBottom();" in scroll
    # explicit=true so the settle runs even with auto-follow off.
    assert "_settleMessageScrollToBottom(false, true, true);" in scroll
    assert "_syncScrollToBottomCue(false,{newMessage:false});" in scroll

    settle = _function_body(UI_JS, "function _settleMessageScrollToBottom")
    assert "new ResizeObserver" in settle
    assert "_settleFinalTimer=setTimeout" in settle
    # #4702: the explicit path also observes the scroller for late viewport reflow.
    assert "if(explicit&&observed!==el){ try{ ro.observe(el); }catch(_){ } }" in settle


def test_pill_is_keyboard_activatable_with_a_clear_label():
    btn = re.search(r'<button id="scrollToBottomBtn".*?</button>', INDEX_HTML, re.S)
    assert btn, "scrollToBottomBtn markup not found"
    markup = btn.group(0)
    # A native <button> handles Enter/Space and focus without extra wiring.
    assert "onclick=\"scrollToBottom()\"" in markup
    assert 'data-i18n-aria-label="session_jump_end_label"' in markup
    assert 'data-i18n-title="session_jump_end_label"' in markup
    assert 'class="session-jump-btn__text" data-i18n="session_jump_end"' in markup
    # The arrow glyph stays decorative so the accessible name is the label alone.
    assert '<span aria-hidden="true">↓</span>' in markup


def test_optional_start_navigation_stays_behind_its_setting():
    update = _function_body(UI_JS, "function _updateSessionStartJumpButton")
    assert "if(!_isSessionJumpButtonsEnabled()){" in update
    assert "btn.style.display='none'" in update
    assert 'id="jumpToSessionStartBtn"' in INDEX_HTML
    # The pill itself is unconditional chrome — no opt-in class gates it.
    assert "session-nav-enabled" not in STYLE_CSS
    assert "session-nav-enabled" not in UI_JS
