"""HWEB-7 — the composer footer keeps only the controls the current message needs.

Attach, dictation, the selected model, the active reasoning mode, context usage
and Send/Stop stay on the footer. Profile, workspace, toolsets, quota, saved
prompts and voice mode moved into ``#composerMobileConfigPanel``, which is now
the single overflow surface at *every* width rather than a phone-only panel.

These are static contract tests over the shipped sources plus two real-browser
measurements (desktop and phone), so the CSS cascade that actually decides
visibility is what gets checked, not a re-derivation of it.
"""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from tests._pytest_port import BASE

REPO = Path(__file__).resolve().parents[1]
HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
BOOT_JS = (REPO / "static" / "boot.js").read_text(encoding="utf-8")
UI_JS = (REPO / "static" / "ui.js").read_text(encoding="utf-8")

# Controls the ticket moves out of the footer row, with the panel row that
# replaces each one.
OVERFLOW_ROWS = {
    "#profileChipWrap": "composerMobileProfileAction",
    ".composer-ws-wrap": "composerMobileWorkspaceAction",
    ".composer-toolsets-wrap": "composerMobileToolsetsAction",
    "#providerQuotaChip": "composerMobileQuotaAction",
}
# Controls that physically live inside the panel now.
RELOCATED_BUTTONS = ["btnSavedPrompts", "btnVoiceMode"]


def _panel_markup() -> str:
    start = HTML.index('id="composerMobileConfigPanel"')
    start = HTML.rfind("<div", 0, start)
    depth = 0
    for m in re.finditer(r"<(/?)div\b[^>]*>", HTML[start:]):
        depth += -1 if m.group(1) else 1
        if depth == 0:
            return HTML[start : start + m.end()]
    raise AssertionError("#composerMobileConfigPanel is not balanced")


def _base_rule(selector: str) -> str:
    """Body of a top-level rule for `selector`.

    Base rules are indented two spaces in this stylesheet; anything deeper sits
    inside an @media/@container block and would only answer for one width.
    """
    for m in re.finditer(re.escape(selector) + r"\s*\{", CSS):
        line_start = CSS.rfind("\n", 0, m.start()) + 1
        if CSS[line_start : m.start()] != "  ":
            continue
        brace = CSS.index("{", m.start())
        return CSS[brace + 1 : CSS.index("}", brace)]
    raise AssertionError(f"no base rule found for {selector}")


# ── The footer keeps only the per-message controls ──────────────────────────

def test_secondary_controls_leave_the_footer_at_every_width():
    """The hide rule must be a base rule — a media-query-scoped one would leave
    the old crowded footer in place on desktop, which is the bug."""
    m = re.search(
        r"\n  ((?:\.composer-left > [^,{]+,\n  )*\.composer-left > [^,{]+)\{display:none!important;\}",
        CSS,
    )
    assert m, "expected a base `.composer-left > …{display:none!important}` group"
    hidden = {sel.strip().removeprefix(".composer-left >").strip() for sel in m.group(1).split(",")}
    assert set(OVERFLOW_ROWS) <= hidden, (
        f"these controls must leave the footer row: {sorted(set(OVERFLOW_ROWS) - hidden)}"
    )


def test_overflow_button_is_no_longer_hidden_by_default():
    body = _base_rule(".icon-btn.composer-mobile-config-btn")
    assert "display:none" not in body, (
        "the overflow button is the only way into the panel — it cannot be "
        f"desktop-hidden any more: {body!r}"
    )


def test_essential_controls_stay_in_the_footer_row():
    left = HTML[HTML.index('<div class="composer-left">') : HTML.index('id="savedPromptsPopup"')]
    for keep in ("btnAttach", "btnMic", "composer-model-wrap", "composerReasoningWrap", "yoloPill"):
        assert keep in left, f"{keep} must stay directly visible in the footer row"


# ── Everything secondary is reachable from the one panel ────────────────────

def test_panel_exposes_every_secondary_control():
    panel = _panel_markup()
    for row in list(OVERFLOW_ROWS.values()) + RELOCATED_BUTTONS:
        assert f'id="{row}"' in panel, f"{row} must live inside the overflow panel"
    # The footer's own controls keep a panel fallback for the narrow stages.
    for row in ("composerMobileModelAction", "composerMobileReasoningAction", "composerMobileContextAction"):
        assert f'id="{row}"' in panel, f"{row} missing from the overflow panel"


def test_attachments_and_action_required_surfaces_stay_out_of_the_panel():
    panel = _panel_markup()
    for never in ("attachTray", "approvalCard", "clarifyCard", "queueCard", "btnSend", "composerStatus"):
        assert f'id="{never}"' not in panel, f"{never} must never be hidden inside overflow"


def test_relocated_buttons_keep_their_wiring():
    panel = _panel_markup()
    assert "toggleSavedPromptsPopup()" in panel
    assert 'data-i18n-title="voice_mode_toggle"' in panel
    for row in RELOCATED_BUTTONS:
        assert f'id="{row}"' not in HTML[: HTML.index('id="savedPromptsPopup"')], (
            f"{row} must no longer sit in the footer row"
        )


# ── Existing visibility / order preferences survive the move ────────────────

def test_visibility_settings_reach_both_surfaces():
    """Hiding a control in Preferences must hide it on the surface it now lives
    on, not just the footer chip nobody can see any more."""
    defs = re.findall(r"\{key:'(hide_composer_\w+)',[^}]*selectors:\[([^\]]*)\][^}]*?\}", BOOT_JS)
    by_key = {k: v for k, v in defs}
    assert "'#composerMobileProfileAction'" in by_key["hide_composer_profile"]
    assert "'#composerMobileWorkspaceAction'" in by_key["hide_composer_workspace"]
    assert "'#composerMobileToolsetsAction'" in by_key["hide_composer_toolsets"]
    assert "'#composerMobileQuotaAction'" in by_key["hide_composer_quota_chip"]


def test_control_order_applies_to_the_overflow_row_too():
    """A def with both surfaces must order both, or a reordered footer chip and
    its menu row drift apart."""
    assert "overflowSelector" in BOOT_JS
    body = BOOT_JS[BOOT_JS.index("function _applyComposerControlOrder("):]
    body = body[: body.index("\nwindow._applyComposerControlOrder")]
    assert "def.overflowSelector" in body, (
        "_applyComposerControlOrder must place the overflow row as well as the footer node"
    )


def test_widening_the_window_no_longer_closes_the_panel():
    assert "matchMedia('(max-width: 640px)').matches" not in UI_JS, (
        "the panel is no longer phone-only; a resize past 640px must not close it"
    )


def test_surfaces_opened_from_the_panel_do_not_close_it():
    body = UI_JS[UI_JS.index("e.target.closest('#composerMobileConfigBtn')") :][:900]
    for opened_from_panel in ("#composerToolsetsDropdown", "#profileDropdown", "#savedPromptsPopup"):
        assert opened_from_panel in body, (
            f"clicking inside {opened_from_panel} must not close the panel underneath it"
        )


# ── Real-browser measurement ────────────────────────────────────────────────

_STATE_JS = """
() => {
  const vis = (sel) => {
    const el = document.querySelector(sel);
    return !!(el && el.getClientRects().length > 0);
  };
  const panel = document.getElementById('composerMobileConfigPanel');
  return {
    footer: {
      attach: vis('#btnAttach'),
      overflowBtn: vis('#composerMobileConfigBtn'),
      send: vis('#btnSend'),
      profile: vis('.composer-left > #profileChipWrap'),
      workspace: vis('.composer-left > .composer-ws-wrap'),
      toolsets: vis('.composer-left > .composer-toolsets-wrap'),
      quota: vis('.composer-left > #providerQuotaChip'),
      savedPrompts: vis('.composer-left > #btnSavedPrompts'),
    },
    panelOpen: panel.classList.contains('open'),
    panelRows: Array.from(panel.children)
      .filter((el) => el.getClientRects().length > 0)
      .map((el) => el.id),
    leftOverflowPx: (() => {
      const l = document.querySelector('.composer-left');
      return l.scrollWidth - l.clientWidth;
    })(),
  };
}
"""


@contextlib.contextmanager
def _page(width: int):
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        pytest.skip("playwright is unavailable; run `playwright install chromium`")
    pw = sync_playwright().start()
    try:
        browser = pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"])
    except Exception as exc:
        pw.stop()
        pytest.skip(f"chromium unavailable for browser measurement: {exc}")
    try:
        ctx = browser.new_context(viewport={"width": width, "height": 900}, has_touch=width <= 640)
        page = ctx.new_page()
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#composerBox", timeout=20000)
        page.add_style_tag(content="#onboardingOverlay{display:none!important}")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        yield page
    finally:
        browser.close()
        pw.stop()


def test_desktop_footer_hides_the_secondary_controls_and_the_panel_holds_them():
    with _page(1440) as page:
        state = page.evaluate(_STATE_JS)
        assert state["footer"]["attach"] and state["footer"]["send"], state
        assert state["footer"]["overflowBtn"], "the overflow button must be visible at desktop"
        for gone in ("profile", "workspace", "toolsets", "quota", "savedPrompts"):
            assert not state["footer"][gone], (gone, state["footer"])
        assert state["leftOverflowPx"] <= 1, ("the footer must not scroll horizontally", state)

        page.evaluate("() => openMobileComposerConfig()")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        opened = page.evaluate(_STATE_JS)
        assert opened["panelOpen"], opened
        for row in ("composerMobileProfileAction", "composerMobileWorkspaceAction", "btnSavedPrompts"):
            assert row in opened["panelRows"], (row, opened["panelRows"])


def test_phone_keeps_its_panel_and_the_context_ring_on_the_button():
    with _page(390) as page:
        ring = page.evaluate(
            "() => getComputedStyle(document.getElementById('composerMobileCtxRing')).display"
        )
        glyph = page.evaluate(
            "() => getComputedStyle(document.querySelector('.composer-config-glyph')).display"
        )
        assert ring == "block", "the phone button keeps doubling as the context readout"
        assert glyph == "none", "…and must not also show the overflow glyph"
        page.evaluate("() => openMobileComposerConfig()")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        state = page.evaluate(_STATE_JS)
        assert state["panelOpen"], state
        assert "composerMobileModelAction" in state["panelRows"], state["panelRows"]
