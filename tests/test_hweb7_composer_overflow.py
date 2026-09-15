from __future__ import annotations

import contextlib
import re
from pathlib import Path

import pytest

from tests._pytest_port import BASE

REPO = Path(__file__).resolve().parents[1]
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


def _function_body(src: str, signature: str) -> str:
    start = src.index(signature)
    depth = 0
    for i in range(src.index("{", start), len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start : i + 1]
    raise AssertionError(f"function body not found: {signature}")


# ── The footer keeps only the per-message controls ──────────────────────────

# ── Everything secondary is reachable from the one panel ────────────────────

# ── Existing visibility / order preferences survive the move ────────────────

# Controls that own both a footer chip and an overflow row. Anchoring on the
# panel's `.open` alone is wrong for these: at the widths where the footer keeps
# its chip, the row is display:none and has no box.
DUAL_SURFACE_ANCHORS = [
    ("_positionModelDropdown", "composerMobileModelAction"),
    ("_positionReasoningDropdown", "composerMobileReasoningAction"),
    ("_toolsetsDropdownAnchor", "composerMobileToolsetsAction"),
]


# Every panel row that opens a dropdown, and the click-away handler that must
# exempt it. A row whose trigger is missing here has its own opening click
# bubble into the close handler, so the control is unusable.
PANEL_TRIGGERS = [
    ("#composerMobileProfileAction", "closeProfileDropdown"),
    ("#composerMobileWorkspaceAction", "closeWsDropdown"),
    ("#composerMobileModelAction", "closeModelDropdown"),
    ("#composerMobileReasoningAction", "closeReasoningDropdown"),
    ("#composerMobileToolsetsAction", "closeToolsetsDropdown"),
]


def _click_handlers(src: str) -> list[str]:
    """Every `document.addEventListener('click', …)` body, brace-matched."""
    out = []
    for m in re.finditer(r"document\.addEventListener\('click'", src):
        brace = src.index("{", m.end())
        depth = 0
        for i in range(brace, len(src)):
            if src[i] == "{":
                depth += 1
            elif src[i] == "}":
                depth -= 1
                if depth == 0:
                    out.append(src[m.start() : i + 1])
                    break
    return out


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


def test_clicking_a_panel_row_leaves_its_dropdown_open():
    """A real click, not a direct call: the opening click also bubbles to the
    document click-away handlers, which is where the trigger has to be exempt."""
    with _page(1440) as page:
        page.click("#composerMobileConfigBtn")
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        for trigger, dropdown in (
            ("#composerMobileToolsetsAction", "#composerToolsetsDropdown"),
            ("#composerMobileProfileAction", "#profileDropdown"),
        ):
            page.click(trigger)
            page.wait_for_timeout(300)
            state = page.evaluate(
                "(sel) => {"
                " const dd = document.querySelector(sel);"
                " const panel = document.getElementById('composerMobileConfigPanel');"
                " return {open: dd.classList.contains('open'), panel: panel.classList.contains('open')};"
                "}",
                dropdown,
            )
            assert state["open"], f"{trigger} opened {dropdown} and something closed it again"
            assert state["panel"], f"{trigger} closed the panel it was clicked in"
            page.keyboard.press("Escape")
            page.wait_for_timeout(200)
            page.click("#composerMobileConfigBtn")
            page.wait_for_timeout(200)


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
