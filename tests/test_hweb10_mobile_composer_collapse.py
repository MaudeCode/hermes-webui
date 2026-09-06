"""HWEB-10 — the unfocused phone composer collapses to one prompt-preview row.

Before this ticket the phone composer always rendered a full-height textarea
plus a row of 44px configuration controls, permanently spending transcript
height on chrome the reader was not using.

These are real-browser measurements against the running app: the page is loaded
from the test server, so the shipped `cf-collapsed` stage (toggled by
`_fitComposerFooter()` in ``static/ui.js``, styled inside the
``@media(max-width:640px)`` block of ``static/style.css``) is what gets
measured, not a re-derivation of it.
"""

from __future__ import annotations

import contextlib

import pytest

from tests._pytest_port import BASE

PHONE = 390          # iPhone 14 class
LEGACY_PHONE = 320   # narrow legacy phone
DESKTOP = 1440

# Every interactive control the composer exposes, in either state, must keep a
# 44px hit box. #fileInput is the visually-hidden native picker behind the
# paperclip button and is never tapped directly.
_HITBOX_JS = """
() => {
  const wrap = document.getElementById('composerWrap');
  const out = [];
  wrap.querySelectorAll('button, a[href], select, textarea, input:not([type=file])').forEach((el) => {
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height) return;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.opacity === '0') return;
    out.push({
      sel: el.id ? '#' + el.id : (el.className && el.className.baseVal !== undefined
        ? String(el.className.baseVal) : String(el.className || el.tagName)),
      w: r.width, h: r.height, left: r.left, right: r.right,
    });
  });
  const col = document.getElementById('composerBox').getBoundingClientRect();
  return { controls: out, colLeft: col.left, colRight: col.right };
}
"""

_STATE_JS = """
() => {
  const footer = document.querySelector('.composer-footer');
  const box = document.getElementById('composerBox');
  const left = document.querySelector('.composer-left');
  const msg = document.getElementById('msg');
  const send = document.getElementById('btnSend');
  const boxRect = box.getBoundingClientRect();
  return {
    collapsed: footer.classList.contains('cf-collapsed'),
    boxHeight: boxRect.height,
    boxBottom: boxRect.bottom,
    leftVisible: left.getClientRects().length > 0,
    sendVisible: send.getClientRects().length > 0,
    sendAction: send.dataset.action || '',
    value: msg.value,
    selectionStart: msg.selectionStart,
    focused: document.activeElement === msg,
    msgTransition: getComputedStyle(msg).transitionProperty,
    boxTransition: getComputedStyle(box).transitionProperty,
    msgAnimation: getComputedStyle(msg).animationName,
    innerHeight: window.innerHeight,
  };
}
"""


@contextlib.contextmanager
def _phone_page(width: int = PHONE, height: int = 844, reduced_motion: str | None = None):
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
        context = browser.new_context(
            viewport={"width": width, "height": height},
            has_touch=True,
            is_mobile=False,  # keeps the desktop-sized visual viewport predictable
            reduced_motion=reduced_motion,
        )
        page = context.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#composerBox", timeout=15000)
        _settle(page)
        yield page
    finally:
        browser.close()
        playwright.stop()


def _settle(page):
    """Let the rAF-coalesced fit pass run before measuring."""
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


def _state(page):
    return page.evaluate(_STATE_JS)


def _blur(page):
    page.evaluate("() => { const m = document.getElementById('msg'); if (m) m.blur(); }")
    _settle(page)


def _assert_composer_controls_are_tappable(page, label):
    """Every visible composer control keeps a 44px hit box inside the column."""
    measured = page.evaluate(_HITBOX_JS)
    assert measured["controls"], (label, "no composer controls were measured")
    for hit in measured["controls"]:
        assert round(hit["w"]) >= 44 and round(hit["h"]) >= 44, (label, hit)
        assert hit["left"] >= measured["colLeft"] - 1, (label, hit, measured["colLeft"])
        assert hit["right"] <= measured["colRight"] + 1, (label, hit, measured["colRight"])


# ── Collapsed idle state ─────────────────────────────────────────────────────

def test_unfocused_phone_composer_collapses_to_one_row():
    with _phone_page() as page:
        collapsed = _state(page)
        assert collapsed["collapsed"], collapsed
        assert not collapsed["leftVisible"], "config controls must be hidden while collapsed"
        assert collapsed["sendVisible"], "the primary action stays on the collapsed row"

        page.evaluate("() => document.getElementById('msg').focus()")
        _settle(page)
        expanded = _state(page)
        assert not expanded["collapsed"], expanded
        assert expanded["leftVisible"], "focusing restores the full composer"

        # The whole point of the ticket: the collapsed composer is materially
        # shorter, so the transcript gets that height back.
        assert collapsed["boxHeight"] < expanded["boxHeight"] - 20, (collapsed, expanded)
        assert collapsed["boxHeight"] <= 48, collapsed


def test_touch_targets_stay_44px_in_both_states():
    for width in (PHONE, LEGACY_PHONE):
        with _phone_page(width=width) as page:
            assert _state(page)["collapsed"]
            _assert_composer_controls_are_tappable(page, f"collapsed@{width}")
            page.evaluate("() => document.getElementById('msg').focus()")
            _settle(page)
            assert not _state(page)["collapsed"]
            _assert_composer_controls_are_tappable(page, f"expanded@{width}")


# ── Draft and caret survive the round trip ───────────────────────────────────

def test_draft_and_caret_survive_a_collapse_expand_round_trip():
    with _phone_page() as page:
        page.click("#msg")
        page.keyboard.type("review the deploy plan")
        page.evaluate("() => { const m = document.getElementById('msg'); m.setSelectionRange(7, 7); }")
        _settle(page)
        typed = _state(page)
        assert not typed["collapsed"]
        assert typed["value"] == "review the deploy plan"

        _blur(page)
        after_blur = _state(page)
        assert after_blur["collapsed"], "an ordinary single-line draft still collapses"
        assert after_blur["value"] == "review the deploy plan", after_blur
        assert after_blur["sendAction"] == "send", after_blur

        # The round trip keeps the same textarea, so both the draft and the
        # caret come back exactly as they were.
        page.evaluate("() => document.getElementById('msg').focus()")
        _settle(page)
        reopened = _state(page)
        assert not reopened["collapsed"], reopened
        assert reopened["value"] == "review the deploy plan", reopened
        assert reopened["selectionStart"] == 7, reopened

        # Tapping the collapsed row expands it and hands focus to the draft.
        _blur(page)
        assert _state(page)["collapsed"]
        page.click("#msg")
        _settle(page)
        tapped = _state(page)
        assert not tapped["collapsed"], tapped
        assert tapped["focused"], tapped
        assert tapped["value"] == "review the deploy plan", tapped


def test_empty_composer_returns_to_the_compact_state_on_blur():
    with _phone_page() as page:
        page.evaluate("() => document.getElementById('msg').focus()")
        _settle(page)
        assert not _state(page)["collapsed"]
        _blur(page)
        assert _state(page)["collapsed"]


# ── States that must keep the composer open ──────────────────────────────────

def test_multiline_draft_stays_expanded_while_unfocused():
    with _phone_page() as page:
        page.evaluate(
            "() => { const m = document.getElementById('msg');"
            " m.value = 'first line\\nsecond line'; m.dispatchEvent(new Event('input', {bubbles: true})); }"
        )
        _blur(page)
        state = _state(page)
        assert not state["collapsed"], "a multi-line draft must not hide behind one row"
        assert state["value"] == "first line\nsecond line"


@pytest.mark.parametrize(
    "label,script",
    [
        ("attachments", "() => document.getElementById('attachTray').classList.add('has-files')"),
        ("approval", "() => { const c = document.getElementById('approvalCard');"
                     " c.removeAttribute('hidden'); c.removeAttribute('inert'); c.classList.add('visible'); }"),
        ("clarify", "() => { const c = document.getElementById('clarifyCard');"
                    " c.removeAttribute('hidden'); c.removeAttribute('inert'); c.classList.add('visible'); }"),
        ("queued messages", "() => document.getElementById('queueCard').classList.add('visible')"),
        ("reconnect banner", "() => showReconnectBanner('reload?')"),
        ("offline banner", "() => showOfflineBanner('browser')"),
        ("agent health banner", "() => _showAgentHealthAlert({})"),
    ],
)
def test_action_required_surfaces_block_auto_collapse(label, script):
    with _phone_page() as page:
        assert _state(page)["collapsed"], f"{label}: precondition — starts collapsed"
        page.evaluate(script)
        _settle(page)
        assert not _state(page)["collapsed"], f"{label} must keep the composer expanded"


def test_programmatic_draft_restore_recomputes_the_collapse_state():
    """Session-switch draft restore assigns `#msg.value` directly and never fires
    an `input` event, so the collapse stage has to be recomputed from the shared
    `autoResize()` chokepoint every programmatic composer write already calls."""
    with _phone_page() as page:
        assert _state(page)["collapsed"], "precondition: starts collapsed"

        # The real session-switch restore path (static/sessions.js).
        page.evaluate(
            "() => _restoreComposerDraft({text: 'line one\\nline two', files: []}, null, {})"
        )
        _settle(page)
        restored = _state(page)
        assert restored["value"] == "line one\nline two", restored
        assert not restored["collapsed"], (
            "a restored multi-line draft must expand without waiting for a focus, "
            "resize or observed-surface mutation"
        )

        # Restoring an empty draft over it returns to compact the same way.
        page.evaluate("() => _restoreComposerDraft({text: '', files: []}, null, {})")
        _settle(page)
        cleared = _state(page)
        assert cleared["value"] == "", cleared
        assert cleared["collapsed"], cleared


def test_an_open_composer_popup_survives_a_focus_loss():
    """iOS does not focus a tapped button, so focusout alone must not collapse."""
    with _phone_page() as page:
        page.evaluate("() => document.getElementById('msg').focus()")
        _settle(page)
        page.evaluate("() => toggleMobileComposerConfig()")
        _blur(page)
        state = _state(page)
        assert not state["collapsed"], state
        panel_open = page.evaluate(
            "() => document.getElementById('composerMobileConfigPanel')"
            ".classList.contains('open')"
        )
        assert panel_open, "precondition: the config panel is open"
        assert state["leftVisible"], "the button that closes the panel must stay reachable"


def test_stop_stays_reachable_on_the_collapsed_row_while_a_turn_runs():
    with _phone_page() as page:
        page.evaluate("() => { S.busy = true; S.activeStreamId = 'hweb10-probe'; updateSendBtn(); }")
        _settle(page)
        state = _state(page)
        assert state["collapsed"], state
        assert state["sendVisible"] and state["sendAction"] == "stop", state
        _assert_composer_controls_are_tappable(page, "busy-collapsed")


# ── Viewport shape: desktop, orientation, keyboard inset ─────────────────────

def test_desktop_composer_never_collapses():
    with _phone_page(width=DESKTOP, height=900) as page:
        state = _state(page)
        assert not state["collapsed"], state
        assert state["leftVisible"], state


def test_orientation_change_recomputes_the_state():
    with _phone_page() as page:
        assert _state(page)["collapsed"]
        page.set_viewport_size({"width": 844, "height": 390})  # landscape
        _settle(page)
        assert not _state(page)["collapsed"], "landscape is past the phone breakpoint"
        page.set_viewport_size({"width": PHONE, "height": 844})  # back to portrait
        _settle(page)
        assert _state(page)["collapsed"]


def test_collapsed_row_stays_on_screen_under_a_keyboard_safe_area_inset():
    """Installed-PWA / on-screen-keyboard geometry: the row must not be pushed off."""
    with _phone_page() as page:
        page.evaluate(
            "() => document.documentElement.style.setProperty('--keyboard-bottom-inset', '260px')"
        )
        _settle(page)
        state = _state(page)
        assert state["collapsed"], state
        assert state["boxBottom"] <= state["innerHeight"] + 1, state
        _assert_composer_controls_are_tappable(page, "keyboard-inset")


def test_reduced_motion_round_trip_is_instant():
    with _phone_page(reduced_motion="reduce") as page:
        collapsed = _state(page)
        assert collapsed["collapsed"]
        # Nothing animates the collapse: the transition list must not cover the
        # geometry that changes, and no keyframe animation runs on the row.
        for state in (collapsed,):
            for prop in ("msgTransition", "boxTransition"):
                assert "height" not in state[prop] and "all" not in state[prop], (prop, state)
            assert state["msgAnimation"] == "none", state
        page.evaluate("() => document.getElementById('msg').focus()")
        _settle(page)
        assert not _state(page)["collapsed"]
        _blur(page)
        assert _state(page)["collapsed"]
