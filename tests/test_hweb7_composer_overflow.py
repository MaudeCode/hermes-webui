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


def test_toolsets_resize_handler_uses_the_shared_anchor():
    """The footer chip is hidden at every width now, so a resize handler that
    only checks it closes a picker opened from the overflow row."""
    start = UI_JS.index("window.addEventListener('resize', () => {\n  const dd = $('composerToolsetsDropdown');")
    handler = UI_JS[start : UI_JS.index("});", start)]
    assert "_toolsetsDropdownAnchor()" in handler, handler
    assert "$('composerToolsetsChip')" not in handler, handler


# Controls that own both a footer chip and an overflow row. Anchoring on the
# panel's `.open` alone is wrong for these: at the widths where the footer keeps
# its chip, the row is display:none and has no box.
DUAL_SURFACE_ANCHORS = [
    ("_positionModelDropdown", "composerMobileModelAction"),
    ("_positionReasoningDropdown", "composerMobileReasoningAction"),
    ("_toolsetsDropdownAnchor", "composerMobileToolsetsAction"),
]


def test_dropdown_anchors_require_the_overflow_row_to_be_laid_out():
    """`panel.open && row` picks a zero-rect row at any width where the footer
    still shows the chip, dropping the popup at the footer's left edge."""
    helper = _function_body(UI_JS, "function _composerOverflowAnchor")
    assert "offsetParent !== null" in helper, helper
    assert "classList.contains('open')" in helper, helper

    for fn, row in DUAL_SURFACE_ANCHORS:
        body = _function_body(UI_JS, f"function {fn}")
        assert f"_composerOverflowAnchor('{row}'" in body, (
            f"{fn} must resolve its anchor through _composerOverflowAnchor"
        )
        assert "classList.contains('open')" not in body, (
            f"{fn} still picks the overflow row from the panel's open state alone"
        )

    ws = _function_body(
        (REPO / "static" / "panels.js").read_text(encoding="utf-8"),
        "function _positionComposerWsDropdown",
    )
    assert "_composerOverflowAnchor('composerMobileWorkspaceAction'" in ws, ws


def test_every_profile_label_write_resyncs_the_overflow_row():
    """The panel stays open across a profile switch, so a writer that updates
    #profileChipLabel without resyncing leaves the row showing the old name."""
    for name in ("ui.js", "panels.js", "boot.js"):
        src = (REPO / "static" / name).read_text(encoding="utf-8")
        for m in re.finditer(r"^(.*?)\.textContent\s*=\s*(?!.*titlebar).*$", src, re.M):
            line = m.group(0)
            if "profileChipLabel" not in line and not re.search(
                r"\b_?(_chipLabel|profileLabel|_profileLabel)\b", line
            ):
                continue
            tail = src[m.end() : m.end() + 400]
            assert "_syncComposerOverflowLabels()" in tail, (
                f"{name}: this #profileChipLabel write does not resync the "
                f"overflow row:\n{line.strip()}"
            )


def test_escape_closes_the_saved_prompts_popup_with_the_panel():
    """Saved prompts is positioned against the footer, not the panel, so closing
    the panel alone leaves it on screen with its trigger hidden."""
    start = UI_JS.index("document.addEventListener('keydown',function(e){\n  if(e.key!=='Escape') return;")
    handler = UI_JS[start : UI_JS.index("\n});", start)]
    assert "savedPromptsPopup" in handler, handler
    assert "aria-expanded" in handler, "the trigger's expanded state must reset too"


def test_widening_the_window_no_longer_closes_the_panel():
    assert "matchMedia('(max-width: 640px)').matches" not in UI_JS, (
        "the panel is no longer phone-only; a resize past 640px must not close it"
    )


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


def test_every_panel_trigger_is_exempt_from_its_own_click_away_handler():
    handlers = _click_handlers(UI_JS) + _click_handlers(
        (REPO / "static" / "panels.js").read_text(encoding="utf-8")
    )
    for trigger, closer in PANEL_TRIGGERS:
        closing = [h for h in handlers if f"{closer}()" in h and "closest(" in h]
        assert closing, f"no click-away handler found calling {closer}()"
        for handler in closing:
            assert f"closest('{trigger}')" in handler, (
                f"the click that opens this dropdown from {trigger} bubbles into "
                f"a {closer} click-away handler and closes it again"
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
