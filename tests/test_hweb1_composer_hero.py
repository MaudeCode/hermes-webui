"""HWEB-1 — the composer is the new-conversation hero.

An empty conversation used to show a branded welcome panel (logo, generic
subtitle, three suggestion cards) with the composer docked separately at the
bottom. Now the one real ``#composerWrap`` composer floats into the middle of
the chat column under a short workspace-aware headline, and docks back down as
soon as the first optimistic user row renders.

The layout assertions are real-browser measurements against the running app, so
the shipped ``#mainChat.composer-hero`` spacer in ``static/style.css`` is what
gets measured rather than a re-derivation of it.
"""

from __future__ import annotations

import contextlib
import pathlib

import pytest

from tests._pytest_port import BASE

REPO = pathlib.Path(__file__).resolve().parents[1]
DESKTOP = (1440, 900)
NARROW = (900, 800)
PHONE = (390, 844)


def read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ── The retired welcome surface is gone from source ──────────────────────────

def test_welcome_panel_dom_is_replaced_by_one_hero_headline():
    html = read("static/index.html")
    assert 'id="emptyHeroTitle"' in html
    assert 'class="empty-logo"' not in html
    assert 'data-i18n="empty_title"' not in html
    assert 'data-i18n="empty_subtitle"' not in html
    assert 'class="suggestion"' not in html
    assert 'class="suggestion-grid"' not in html
    # The hero is the page's first paint for a fresh new chat, so the class ships
    # in the markup instead of waiting for JS (otherwise the composer paints
    # docked and then jumps).
    assert '<div id="mainChat" class="main-view composer-hero">' in html


def test_hide_welcome_and_hide_suggestions_settings_are_removed():
    for rel in ("static/index.html", "static/boot.js", "static/panels.js", "api/config.py"):
        src = read(rel)
        assert "hide_empty_state_panel" not in src, rel
        assert "hide_empty_state_suggestions" not in src, rel
    i18n = read("static/i18n.js")
    for key in (
        "settings_label_hide_suggestions",
        "settings_desc_hide_suggestions",
        "settings_label_hide_empty_state_panel",
        "settings_desc_hide_empty_state_panel",
        "\n    empty_title:",
        "\n    empty_subtitle:",
        "\n    suggest_files:",
    ):
        assert key not in i18n, key


def test_every_locale_carries_both_hero_headlines():
    i18n = read("static/i18n.js")
    assert i18n.count("    empty_hero_title: ") == 15
    assert i18n.count("    empty_hero_title_workspace: ") == 15
    # The workspace variant has to keep its interpolation slot in every locale.
    for line in i18n.splitlines():
        if line.startswith("    empty_hero_title_workspace: "):
            assert "{0}" in line, line


def test_taking_the_empty_state_down_releases_the_hero_layout():
    """Every transcript-painting caller goes through one chokepoint.

    A stray ``$('emptyState').style.display='none'`` would hide the headline but
    leave the composer stranded in the middle of the column.
    """
    for rel in ("static/ui.js", "static/messages.js"):
        assert "emptyState').style.display='none'" not in read(rel), rel
    ui = read("static/ui.js")
    assert "function hideConversationEmptyState()" in ui
    assert "classList.toggle('composer-hero'" in ui


# ── Real-browser layout ──────────────────────────────────────────────────────

@contextlib.contextmanager
def _page(size=DESKTOP, reduced_motion: str | None = None, init_script: str | None = None):
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
            viewport={"width": size[0], "height": size[1]},
            reduced_motion=reduced_motion,
        )
        page = context.new_page()
        if init_script:
            page.add_init_script(init_script)
        page.goto(BASE, wait_until="domcontentloaded")
        page.wait_for_selector("#composerBox", timeout=15000)
        # The onboarding wizard is a pointer-event-blocking modal whose visibility
        # depends on the shared test server's settings; hide it in this page only.
        page.evaluate(
            "() => { const o = document.getElementById('onboardingOverlay');"
            " if (o) o.style.display = 'none'; }"
        )
        _settle(page)
        yield page
    finally:
        browser.close()
        playwright.stop()


def _settle(page):
    page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")


_GEOMETRY_JS = """
() => {
  const chat = document.getElementById('mainChat');
  const box = document.getElementById('composerBox');
  const title = document.getElementById('emptyHeroTitle');
  const msg = document.getElementById('msg');
  const r = (el) => { const b = el.getBoundingClientRect();
    return { top: b.top, bottom: b.bottom, height: b.height, width: b.width }; };
  return {
    hero: chat.classList.contains('composer-hero'),
    chat: r(chat),
    box: r(box),
    title: r(title),
    titleText: title.textContent,
    titleOpacity: getComputedStyle(title).opacity,
    spacerTransition: getComputedStyle(chat, '::after').transitionDuration,
    msgValue: msg.value,
    msgFocused: document.activeElement === msg,
    msgNode: window.__hweb1Msg === msg,
  };
}
"""


def _geometry(page):
    page.evaluate("() => { window.__hweb1Msg = document.getElementById('msg'); }")
    return page.evaluate(_GEOMETRY_JS)


def _render_first_user_row(page):
    """Replay send()'s first optimistic pass without starting a real agent run."""
    page.evaluate(
        "() => { S.messages.push({role:'user',content:'hello there',"
        "_ts:Date.now()/1000,_pending:true}); renderMessages(); }"
    )
    _settle(page)


@pytest.mark.parametrize("size", [DESKTOP, NARROW, PHONE], ids=["desktop", "narrow", "phone"])
def test_empty_conversation_lifts_the_real_composer_off_the_bottom(size):
    with _page(size) as page:
        before = _geometry(page)
        assert before["hero"], before
        # The composer is materially off the bottom edge: at least a fifth of the
        # chat column's height sits below it.
        gap_below = before["chat"]["bottom"] - before["box"]["bottom"]
        assert gap_below > before["chat"]["height"] * 0.2, before
        # …and the headline sits directly on top of it, not floating mid-column.
        assert 0 <= before["box"]["top"] - before["title"]["bottom"] < 140, before

        _render_first_user_row(page)
        after = _geometry(page)
        assert not after["hero"], after
        # Same composer node, same draft, still focusable — the transition never
        # re-parents the textarea.
        assert after["msgNode"], "the composer textarea node was replaced"


def test_first_message_docks_the_composer_without_losing_draft_or_focus():
    with _page() as page:
        page.evaluate(
            "() => { const m = document.getElementById('msg');"
            " m.value = 'half-typed draft'; m.focus(); }"
        )
        _settle(page)
        before = _geometry(page)
        assert before["hero"] and before["msgFocused"] and before["msgValue"] == "half-typed draft"

        _render_first_user_row(page)
        # The dock is a CSS transition on the spacer, so wait it out before
        # measuring the settled position.
        page.wait_for_timeout(600)
        after = _geometry(page)
        assert not after["hero"], after
        assert after["msgValue"] == "half-typed draft", after
        assert after["msgFocused"], "docking stole focus from the composer"
        assert after["box"]["bottom"] > before["box"]["bottom"], (before, after)
        assert after["chat"]["bottom"] - after["box"]["bottom"] < 60, after


def test_headline_names_the_active_workspace():
    with _page() as page:
        # Let boot settle first, or its own syncWorkspaceDisplays() overwrites the
        # workspace under test a frame later.
        page.wait_for_function("() => typeof S !== 'undefined' && S._bootReady === true", timeout=20000)
        page.wait_for_timeout(400)  # the headline fades in over .2s

        named = page.evaluate(
            "() => { S.session = null;"
            " S._profileDefaultWorkspace = '/tmp/hweb1-hero-space';"
            " syncWorkspaceDisplays();"
            " const el = document.getElementById('emptyHeroTitle');"
            " return { text: el.textContent, opacity: getComputedStyle(el).opacity }; }"
        )
        assert "hweb1-hero-space" in named["text"], named
        assert named["opacity"] == "1", named

        plain = page.evaluate(
            "() => { S.session = null; S._profileDefaultWorkspace = '';"
            " syncWorkspaceDisplays();"
            " return document.getElementById('emptyHeroTitle').textContent; }"
        )
        assert plain == "What are we working on?", plain


# A returning session sets data-session-boot before any app script runs. Capture
# the composer's very first painted frame so a hero flash cannot hide behind the
# dock transition.
_FIRST_FRAME_INIT = """
window.__hweb1FirstFrame = null;
const capture = () => {
  if (document.documentElement) document.documentElement.dataset.sessionBoot = '1';
  const chat = document.getElementById('mainChat');
  const box = document.getElementById('composerBox');
  const empty = document.getElementById('emptyState');
  if (!chat || !box) { requestAnimationFrame(capture); return; }
  delete window.__hweb1Pending;
  const cr = chat.getBoundingClientRect();
  const br = box.getBoundingClientRect();
  window.__hweb1FirstFrame = {
    gapBelow: cr.bottom - br.bottom,
    classed: chat.classList.contains('composer-hero'),
    bootFlag: document.documentElement.dataset.sessionBoot || '',
    emptyVisible: !!(empty && empty.getClientRects().length > 0),
  };
};
requestAnimationFrame(capture);
"""


def test_returning_session_does_not_flash_the_hero_while_history_loads():
    """The boot flag a returning session sets must suppress the hero layout too.

    ``#mainChat`` ships the ``composer-hero`` class in the markup so a fresh new
    chat never paints a docked composer first; the flag is what keeps a session
    reload from paying for that with the inverse flash.
    """
    with _page(init_script=_FIRST_FRAME_INIT) as page:
        first = page.evaluate("() => window.__hweb1FirstFrame")
        assert first, "never captured a painted frame"
        assert first["bootFlag"] == "1", first
        # The class is on the element from the markup — the flag is what holds it back.
        assert first["classed"], first
        assert not first["emptyVisible"], first
        assert first["gapBelow"] < 60, first


def test_starting_a_session_load_releases_the_hero_before_the_fetch_resolves():
    """Selecting a saved session must drop the hero at once, not at the first row.

    ``renderMessages()`` early-returns while a session fetch is in flight, so a
    hero released only by a rendered transcript row stays centered over the
    "Loading conversation..." placeholder for the whole load — and forever if the
    fetch fails.
    """
    with _page() as page:
        page.wait_for_function(
            "() => typeof S !== 'undefined' && S._bootReady === true", timeout=20000
        )
        assert _geometry(page)["hero"], "precondition: a fresh new chat is in hero mode"

        state = page.evaluate(
            """() => {
              // Hang every request so the load can never reach renderMessages().
              window.api = () => new Promise(() => {});
              S.session = null;
              loadSession('hweb1-never-resolves');
              const chat = document.getElementById('mainChat');
              const empty = document.getElementById('emptyState');
              const box = document.getElementById('composerBox');
              const cr = chat.getBoundingClientRect();
              const br = box.getBoundingClientRect();
              return {
                hero: chat.classList.contains('composer-hero'),
                emptyVisible: empty.getClientRects().length > 0,
                gapBelow: cr.bottom - br.bottom,
              };
            }"""
        )
        assert not state["hero"], state
        assert not state["emptyVisible"], state

        page.wait_for_timeout(600)  # let the dock transition settle
        settled = _geometry(page)
        assert settled["chat"]["bottom"] - settled["box"]["bottom"] < 60, settled


def test_reduced_motion_removes_the_dock_transition():
    with _page(reduced_motion="reduce") as page:
        before = _geometry(page)
        assert before["hero"], before
        assert before["spacerTransition"] in ("0s", "0"), before

        _render_first_user_row(page)
        after = _geometry(page)
        assert not after["hero"], after
        # No transition to wait out: the composer is docked on the next frame.
        assert after["chat"]["bottom"] - after["box"]["bottom"] < 60, after
