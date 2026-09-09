"""HWEB-12 - a user-turn timeline minimap in the chat reading column's gutter.

The rail is an extension of the shipped conversation-outline mechanism
(``static/outline.js``): the same ``_buildEntries()`` turns, the same
``_jumpToMessage()`` loader/jump, the same ``_outlineAllowed()`` preference and
desktop gate. Only the presentation is new, so the assertions split the same way
the sibling suites do - real-browser measurements for the geometry, visibility
and interaction contract, source-level assertions for the state machine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._pytest_port import BASE

REPO = Path(__file__).resolve().parents[1]
OUTLINE_JS = (REPO / "static" / "outline.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")
I18N_JS = (REPO / "static" / "i18n.js").read_text(encoding="utf-8")

# Locale blocks in static/i18n.js (en, it, ja, ru, es, de, zh, zh-Hant, pt, ko,
# fr, cs, tr, pl, vi). Every user-visible string must exist in all of them.
LOCALE_COUNT = 15

# Enough turns to clear MINIMAP_MIN_MARKS with room to spare.
_TURNS = 8

_SETUP_JS = """
(opts) => {
  const doc = document;
  // A fresh state dir (the CI runner's, and any first run) shows the onboarding
  // wizard; its modal overlay intercepts every pointer event aimed at the rail.
  const onboarding = doc.getElementById('onboardingOverlay');
  if (onboarding) onboarding.remove();
  const msgs = [];
  let html = '';
  for (let i = 0; i < opts.turns; i++) {
    msgs.push({ role: 'user', content: 'Question number ' + (i + 1) });
    html += '<div class="msg-row" data-role="user" id="msg-user-' + (msgs.length - 1) +
            '"><div class="msg-body"><p>Question number ' + (i + 1) + '</p></div></div>';
    msgs.push({ role: 'assistant', content: 'Answer number ' + (i + 1) });
    html += '<div class="msg-row" data-role="assistant"><div class="msg-body"><p>' +
            ('Filler line for turn ' + (i + 1) + '. ').repeat(80) + '</p></div></div>';
  }
  doc.getElementById('msgInner').innerHTML = html;
  const empty = doc.getElementById('emptyState');
  if (empty) empty.style.display = 'none';

  if (opts.fullWidth) doc.documentElement.dataset.chatWidth = 'full';
  else delete doc.documentElement.dataset.chatWidth;
  // Pin the chat pane's width: an open workspace panel narrows it, and whether
  // it starts open depends on saved state the assertions should not ride on.
  doc.documentElement.dataset.workspacePanel = 'closed';
  if (typeof _oldestIdx !== 'undefined') _oldestIdx = 0;
  if (typeof _messagesTruncated !== 'undefined') _messagesTruncated = false;

  // boot.js re-applies the persisted preference when its settings request
  // lands, which would flip the flag mid-test. Pin it behind an accessor whose
  // setter ignores writes, so the value cannot change under a running assertion.
  const enabled = opts.enabled !== false;
  Object.defineProperty(window, '_showConversationOutline', {
    configurable: true,
    get: function() { return enabled; },
    set: function() {},
  });
  // A fresh session id per setup, so no minimap state leaks between tests
  // (the real equivalent is switching sessions, which tears the rail down).
  window.__hweb12Run = (window.__hweb12Run || 0) + 1;
  S.session = { session_id: 'hweb12-test-' + window.__hweb12Run };
  S.messages = msgs;
  applyConversationOutlinePreference();
  doc.getElementById('messages').scrollTop = 0;
  return msgs.length;
}
"""

_MEASURE_JS = """
() => {
  const box = (el) => {
    if (!el) return null;
    const r = el.getBoundingClientRect();
    return { left: r.left, right: r.right, top: r.top, bottom: r.bottom,
             width: r.width, height: r.height };
  };
  const map = document.getElementById('outlineMinimap');
  const marks = Array.from(map.querySelectorAll('.outline-mark'));
  return {
    hidden: map.hidden,
    map: box(map),
    column: box(document.getElementById('msgInner')),
    shell: box(map.parentElement),
    markCount: marks.length,
    order: marks.map(m => Number(m.dataset.rawIdx)),
    labels: marks.map(m => m.getAttribute('aria-label')),
    tabStops: marks.map(m => m.tabIndex),
    current: marks.filter(m => m.getAttribute('aria-current') === 'true')
                  .map(m => Number(m.dataset.rawIdx)),
    mapPointerEvents: getComputedStyle(map).pointerEvents,
    markPointerEvents: marks.length ? getComputedStyle(marks[0]).pointerEvents : null,
    mapUserSelect: getComputedStyle(map).userSelect,
    gutter: document.getElementById('msgInner').getBoundingClientRect().left -
            map.parentElement.getBoundingClientRect().left,
    markAnimation: marks.length ? getComputedStyle(marks[0]).animationName : null,
    markBoxes: marks.map(box),
  };
}
"""


# Wide enough that the rail still has a gutter with the sidebar AND the
# workspace panel open, which is the CI runner's first-run layout.
_WIDE = 1920


@pytest.fixture(scope="module")
def page():
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
        p = browser.new_page(viewport={"width": _WIDE, "height": 900})
        p.goto(BASE, wait_until="domcontentloaded")
        p.wait_for_selector("#composerBox", timeout=15000)
        yield p
    finally:
        browser.close()
        playwright.stop()


def _setup(page, *, turns=_TURNS, enabled=True, full_width=False, width=_WIDE):
    page.set_viewport_size({"width": width, "height": 900})
    page.evaluate(
        _SETUP_JS,
        {"turns": turns, "enabled": enabled, "fullWidth": full_width},
    )
    return page.evaluate(_MEASURE_JS)


def _settle(page):
    """Stop the app's load-time bottom settle, which keeps re-claiming the scroller.

    Injecting a transcript restarts it; left running, its ResizeObserver and
    timers yank scrollTop back to the tail mid-assertion. The product cancels it
    the same way before a jump (see _jumpToMessage).
    """
    page.evaluate(
        "() => { if (typeof _cancelBottomSettle === 'function') _cancelBottomSettle(); }"
    )
    page.wait_for_timeout(50)


def _setup_visible(page, **kwargs):
    """Setup for the interaction tests: fail on the measurement, not on a timeout."""
    m = _setup(page, **kwargs)
    _settle(page)
    assert m["hidden"] is False, m
    assert m["markCount"] == kwargs.get("turns", _TURNS), m
    return m


def test_one_mark_per_loaded_user_turn_in_chronological_order(page):
    m = _setup(page)
    assert m["hidden"] is False, m
    assert m["markCount"] == _TURNS, m
    # rawIdx order is the transcript order, and every mark carries a real label.
    assert m["order"] == sorted(m["order"]), m
    assert m["order"] == [i * 2 for i in range(_TURNS)], m
    assert all(label and "Question number" in label for label in m["labels"]), m
    # Marks divide the rail evenly, so mark k sits ~k/N through the conversation.
    tops = [b["top"] for b in m["markBoxes"]]
    assert tops == sorted(tops), m
    span = m["markBoxes"][-1]["bottom"] - m["markBoxes"][0]["top"]
    assert span >= m["map"]["height"] - 1, m
    heights = [b["height"] for b in m["markBoxes"]]
    assert max(heights) - min(heights) <= 1, m


def test_rail_lives_in_the_unused_gutter_and_never_takes_the_transcript(page):
    m = _setup(page)
    # Entirely left of the reading column: it can never sit over transcript text.
    assert m["map"]["right"] <= m["column"]["left"], m
    assert m["map"]["left"] >= m["shell"]["left"], m
    # The rail itself is inert; only the marks are hit targets.
    assert m["mapPointerEvents"] == "none", m
    assert m["markPointerEvents"] == "auto", m
    assert m["mapUserSelect"] == "none", m


def test_the_nearest_visible_turn_is_marked_without_animation(page):
    _setup(page)
    page.wait_for_timeout(200)
    m = page.evaluate(_MEASURE_JS)
    # Exactly one mark is current, and it is the first turn while parked at the top.
    assert m["current"] == [0], m
    # Distinguishable through a static style change, not a running animation.
    assert m["markAnimation"] in ("none", None), m
    # Tab lands on the reader's current turn, the rest are arrow-key reachable.
    assert m["tabStops"].count(0) == 1, m
    assert m["tabStops"][0] == 0, m

    # Scrolling down moves the marker to the turn the reader is now inside.
    page.evaluate("() => { const el = document.getElementById('messages');"
                  " el.scrollTop = el.scrollHeight; }")
    page.wait_for_timeout(300)
    later = page.evaluate(_MEASURE_JS)
    assert later["current"] and later["current"][0] > 0, later


def test_a_turn_is_current_even_when_no_user_row_is_on_screen(page):
    """A long answer can fill the viewport; the turn it belongs to stays marked."""
    _setup_visible(page)
    page.evaluate(
        """() => {
          const row = document.getElementById('msg-user-6');
          // An answer tall enough to fill the viewport on its own.
          row.nextElementSibling.style.minHeight = '2400px';
          const el = document.getElementById('messages');
          // Take the scroller the way the app itself does before moving a
          // reader; a fresh session is still pinned, and growing a row would
          // otherwise snap straight back to the tail.
          if (typeof _cancelBottomSettle === 'function') _cancelBottomSettle();
          if (typeof _beginMessageJumpScroll === 'function') _beginMessageJumpScroll(el);
          el.scrollTop += row.getBoundingClientRect().top - el.getBoundingClientRect().top
                          + row.offsetHeight + 600;
        }"""
    )
    page.wait_for_timeout(300)
    parked = page.evaluate(
        """() => {
          const el = document.getElementById('messages');
          const s = el.getBoundingClientRect();
          return Array.from(document.querySelectorAll('[id^="msg-user-"]')).filter(r => {
            const b = r.getBoundingClientRect();
            return b.top < s.bottom && b.bottom > s.top;
          }).map(r => r.id);
        }"""
    )
    assert parked == [], parked   # precondition: no user row is on screen
    m = page.evaluate(_MEASURE_JS)
    assert m["current"] == [6], m
    assert m["tabStops"].count(0) == 1, m
    assert m["tabStops"][3] == 0, m   # the 4th mark is rawIdx 6


def test_hover_and_focus_preview_show_the_question_and_its_final_answer(page):
    _setup_visible(page)
    page.hover(".outline-mark:nth-of-type(3)")
    page.wait_for_timeout(120)
    preview = page.evaluate(
        """() => {
          const el = document.querySelector('.outline-mark-preview');
          const r = el.getBoundingClientRect();
          return {
            hidden: el.hidden,
            user: (el.querySelector('.outline-preview-user') || {}).textContent || '',
            reply: (el.querySelector('.outline-preview-reply') || {}).textContent || '',
            pointerEvents: getComputedStyle(el).pointerEvents,
            top: r.top, bottom: r.bottom,
            shellTop: document.querySelector('.messages-shell').getBoundingClientRect().top,
            shellBottom: document.querySelector('.messages-shell').getBoundingClientRect().bottom,
          };
        }"""
    )
    assert preview["hidden"] is False, preview
    assert "Question number 3" in preview["user"], preview
    assert "Answer number 3" in preview["reply"], preview
    # The card floats over the transcript, so it must not swallow clicks there.
    assert preview["pointerEvents"] == "none", preview
    # Clamped inside the transcript pane rather than escaping over the header.
    assert preview["top"] >= preview["shellTop"] - 1, preview
    assert preview["bottom"] <= preview["shellBottom"] + 1, preview

    # Keyboard focus is an equal path to the same preview.
    page.evaluate("() => document.querySelector('.outline-mark-preview').dispatchEvent("
                  "new MouseEvent('pointerout', {bubbles: true}))")
    page.evaluate("() => document.querySelectorAll('.outline-mark')[5].focus()")
    page.wait_for_timeout(120)
    focused = page.evaluate(
        """() => {
          const el = document.querySelector('.outline-mark-preview');
          return { hidden: el.hidden,
                   user: (el.querySelector('.outline-preview-user') || {}).textContent || '' };
        }"""
    )
    assert focused["hidden"] is False, focused
    assert "Question number 6" in focused["user"], focused


def test_activating_a_mark_anchors_its_user_message_in_view(page):
    _setup_visible(page)
    before = page.evaluate(
        """() => {
          const row = document.getElementById('msg-user-10');
          const el = document.getElementById('messages');
          const r = row.getBoundingClientRect(), s = el.getBoundingClientRect();
          return { visible: r.top < s.bottom && r.bottom > s.top, scrollTop: el.scrollTop };
        }"""
    )
    assert before["visible"] is False, before

    page.click(".outline-mark:nth-of-type(6)")   # rawIdx 10 -> the 6th user turn
    page.wait_for_timeout(900)
    after = page.evaluate(
        """() => {
          const row = document.getElementById('msg-user-10');
          const el = document.getElementById('messages');
          const r = row.getBoundingClientRect(), s = el.getBoundingClientRect();
          return {
            visible: r.top < s.bottom && r.bottom > s.top,
            offCentre: Math.abs((r.top + r.bottom) / 2 - (s.top + s.bottom) / 2),
            height: s.height,
          };
        }"""
    )
    assert after["visible"] is True, after
    # block:'center' - anchored for reading, not flush against an edge.
    assert after["offCentre"] < after["height"] * 0.25, after


def test_keyboard_arrows_walk_the_rail_and_activate_a_turn(page):
    _setup_visible(page)
    page.evaluate("() => document.querySelectorAll('.outline-mark')[0].focus()")
    page.keyboard.press("ArrowDown")
    page.keyboard.press("ArrowDown")
    state = page.evaluate(
        """() => {
          const marks = Array.from(document.querySelectorAll('.outline-mark'));
          return { focused: marks.indexOf(document.activeElement),
                   tabStops: marks.map(m => m.tabIndex) };
        }"""
    )
    assert state["focused"] == 2, state
    # Roving tabindex: one tab stop, on the focused mark.
    assert state["tabStops"].count(0) == 1 and state["tabStops"][2] == 0, state

    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.wait_for_timeout(900)
    assert page.evaluate(
        """() => {
          const row = document.getElementById('msg-user-14');
          const el = document.getElementById('messages');
          const r = row.getBoundingClientRect(), s = el.getBoundingClientRect();
          return r.top < s.bottom && r.bottom > s.top;
        }"""
    ), "End + Enter should jump to the last turn"


def test_focus_survives_the_rebuild_a_jump_into_history_triggers(page):
    """Loading older history re-renders the marks; the keyboard user keeps their place."""
    _setup_visible(page)
    page.evaluate("() => { _oldestIdx = 2; applyConversationOutlinePreference(); }")
    page.evaluate("() => document.querySelectorAll('.outline-mark')[3].focus()")
    # A jump into unloaded history prepends turns and rebuilds every mark, and
    # drops the loaded window's offset to 0 the way the real load does.
    page.evaluate(
        """() => {
          S.messages = [{ role: 'user', content: 'Older question' },
                        { role: 'assistant', content: 'Older answer' }].concat(S.messages);
          _oldestIdx = 0;
          applyConversationOutlinePreference();
        }"""
    )
    state = page.evaluate(
        """() => {
          const marks = Array.from(document.querySelectorAll('.outline-mark'));
          const active = document.activeElement;
          return { count: marks.length,
                   focusedRawIdx: marks.includes(active) ? Number(active.dataset.rawIdx) : null,
                   tabStops: marks.map(m => m.tabIndex) };
        }"""
    )
    assert state["count"] == _TURNS + 1, state
    # Same TURN as before the rebuild: the prepend shifted its rawIdx 6 -> 8.
    assert state["focusedRawIdx"] == 8, state
    assert state["tabStops"].count(0) == 1, state


@pytest.mark.parametrize(
    "label,kwargs",
    [
        ("too few turns", {"turns": 3}),
        ("no gutter (full-width chat)", {"full_width": True}),
        ("narrow / mobile width", {"width": 820}),
        ("outline preference off", {"enabled": False}),
    ],
)
def test_the_rail_hides_when_it_cannot_help(page, label, kwargs):
    m = _setup(page, **kwargs)
    assert m["hidden"] is True, (label, m)
    _setup(page)  # restore the shared page for the next test


def test_a_mark_still_lands_on_its_own_turn_after_unloaded_history_arrives(page):
    """A truncated session numbers rows from the tail; the full load renumbers them."""
    _setup_visible(page)
    page.evaluate(
        """() => {
          // The session still has older messages the initial tail window skipped:
          // two of them, so the loaded window starts at session index 2. The
          // marks have to be re-stamped under that base, exactly as they would
          // have been had the session loaded truncated in the first place.
          _messagesTruncated = true;
          _oldestIdx = 2;
          applyConversationOutlinePreference();
          // Replaces S.messages and nothing else, exactly like the real one:
          // every row in the DOM still carries its pre-load index afterwards.
          window._ensureAllMessagesLoaded = function() {
            S.messages = [{ role: 'user', content: 'Older question' },
                          { role: 'assistant', content: 'Older answer' }].concat(S.messages);
            _messagesTruncated = false;
            _oldestIdx = 0;               // the whole transcript is loaded now
            return Promise.resolve();
          };
        }"""
    )
    page.click(".outline-mark:nth-of-type(6)")   # the 6th user turn
    page.wait_for_timeout(600)
    landed = page.evaluate(
        """() => {
          // _flashRow() marks the row the jump actually resolved to.
          const row = document.querySelector('.outline-jump-flash');
          const twelve = document.getElementById('msg-user-12');
          return {
            id: row ? row.id : null,
            text: row ? row.textContent.trim().slice(0, 40) : null,
            twelve: twelve ? twelve.textContent.trim().slice(0, 40) : null,
          };
        }"""
    )
    # Index 10 in the reloaded transcript is turn 5; the mark must still be turn 6,
    # which the prepend moved to index 12.
    assert landed["id"] == "msg-user-12", landed
    assert "Question number 6" in (landed["text"] or ""), landed
    # The transcript was rebuilt too, so the row id and the index agree.
    assert "Question number 6" in (landed["twelve"] or ""), landed
    _setup(page)  # restore the shared page for the next test


def test_a_turn_appended_during_the_load_does_not_move_the_jump_target(page):
    """Another writer can append while the full-history request is in flight."""
    _setup_visible(page)
    page.evaluate(
        """() => {
          _messagesTruncated = true;
          _oldestIdx = 2;
          applyConversationOutlinePreference();   // re-stamp under the real base
          window._ensureAllMessagesLoaded = function() {
            // Older history arrives AND a second writer appends a new turn.
            S.messages = [{ role: 'user', content: 'Older question' },
                          { role: 'assistant', content: 'Older answer' }]
                         .concat(S.messages)
                         .concat([{ role: 'user', content: 'Question number 99' },
                                  { role: 'assistant', content: 'Answer number 99' }]);
            _messagesTruncated = false;
            _oldestIdx = 0;
            return Promise.resolve();
          };
        }"""
    )
    page.click(".outline-mark:nth-of-type(6)")   # the 6th user turn
    page.wait_for_timeout(600)
    landed = page.evaluate(
        """() => {
          const row = document.querySelector('.outline-jump-flash');
          return { id: row ? row.id : null,
                   text: row ? row.textContent.trim().slice(0, 40) : null };
        }"""
    )
    # A position-from-the-end key would have shifted by the appended turn.
    assert landed["id"] == "msg-user-12", landed
    assert "Question number 6" in (landed["text"] or ""), landed
    _setup(page)  # restore the shared page for the next test


def test_a_language_change_rebuilds_the_marks(page):
    """Mark labels come from t(), not data-i18n-*, so applyLocaleToDOM() cannot
    reach them -- only a rebuild re-runs t() and relabels them."""
    _setup_visible(page)
    state = page.evaluate(
        """() => {
          const before = Array.from(document.querySelectorAll('.outline-mark'));
          before.forEach(m => { m.dataset.stale = '1'; });
          const label = before[0].getAttribute('aria-label');
          const lang = document.documentElement.lang;
          document.documentElement.lang = lang === 'de' ? 'fr' : 'de';
          applyConversationOutlinePreference();
          const after = Array.from(document.querySelectorAll('.outline-mark'));
          document.documentElement.lang = lang;
          return {
            label: label,
            count: after.length,
            survivors: after.filter(m => m.dataset.stale === '1').length,
          };
        }"""
    )
    assert state["label"].startswith("Question 1"), state
    assert state["count"] == _TURNS, state
    # Every mark is a new node, so t() ran again under the new locale.
    assert state["survivors"] == 0, state
    _setup(page)  # restore the shared page for the next test


def test_switching_to_full_width_chat_hides_the_rail(page):
    """boot.js applies the outline preference before the chat width, and the
    width switch does not resize the pane -- so without watching the root
    attribute nothing re-syncs and CSS leaves a focusable rail off-column."""
    m = _setup_visible(page)
    assert m["hidden"] is False, m
    # MutationObserver callbacks are microtasks, so one await is the whole
    # window: no render, no resize and no settings sync can intervene.
    hidden = page.evaluate(
        """async () => {
          document.documentElement.dataset.chatWidth = 'full';
          await Promise.resolve();
          const el = document.getElementById('outlineMinimap');
          const state = { hidden: el.hidden,
                          focusable: el.querySelectorAll('.outline-mark[tabindex="0"]').length };
          delete document.documentElement.dataset.chatWidth;
          return state;
        }"""
    )
    assert hidden["hidden"] is True, hidden
    _setup(page)  # restore the shared page for the next test


def test_preview_reads_responses_style_and_compacted_answers(page):
    """Assistant prose also arrives as input_text/output_text parts, or in the
    anchor scene of a compacted turn."""
    _setup_visible(page)
    page.evaluate(
        """() => {
          // Turn 2's answer uses Responses-style parts; turn 3's was compacted.
          S.messages[3] = { role: 'assistant',
                            content: [{ type: 'output_text', text: 'Responses-style answer' }] };
          S.messages[5] = { role: 'assistant', content: [],
                            _anchor_activity_scene: { final_answer: 'Compacted final answer' } };
          applyConversationOutlinePreference();
        }"""
    )
    previews = []
    for nth in (2, 3):
        page.hover(f".outline-mark:nth-of-type({nth})")
        page.wait_for_timeout(120)
        previews.append(
            page.evaluate(
                "() => (document.querySelector('.outline-preview-reply') || {}).textContent || ''"
            )
        )
    assert "Responses-style answer" in previews[0], previews
    assert "Compacted final answer" in previews[1], previews
    _setup(page)  # restore the shared page for the next test


def test_markup_and_locale_contract():
    """The rail ships as static markup with a translated accessible name."""
    tag = re.search(r'<div id="outlineMinimap".*?</div>', INDEX_HTML, re.S)
    assert tag, "outlineMinimap markup not found"
    markup = tag.group(0)
    assert "hidden" in markup
    # A toolbar is the ARIA pattern that sanctions roving tabindex + arrow keys.
    assert 'role="toolbar"' in markup and 'aria-orientation="vertical"' in markup
    assert 'data-i18n-aria-label="outline_minimap_label"' in markup
    for key in ("outline_minimap_label:", "outline_minimap_mark:"):
        assert I18N_JS.count(key) == LOCALE_COUNT, key
    # The mark label carries both the turn number and its excerpt.
    assert "outline_minimap_mark: 'Question {0}: {1}'" in I18N_JS


def test_reuses_the_outline_mechanism_rather_than_a_second_index():
    """Turn discovery, jumping and the desktop/preference gate are all shared."""
    body = OUTLINE_JS[OUTLINE_JS.index("function _syncMinimap()"):]
    assert "_buildEntries()" in body
    assert "function _minimapAllowed() {\n  return _outlineAllowed();\n}" in OUTLINE_JS
    assert "_jumpToMessage(rawIdx);" in OUTLINE_JS
    assert "_ensureOutlineMessagesLoaded(sid).then" in OUTLINE_JS
    # The jump takes scroller ownership the same way ui.js's question jump does,
    # or the load-time bottom settle snaps the reader back to the tail.
    assert "_cancelBottomSettle();" in OUTLINE_JS
    assert "_beginMessageJumpScroll(scroller);" in OUTLINE_JS
    # Mark identity across a reload is ui.js's session-absolute index, which
    # holds under a prepend AND a concurrent append -- not a list position.
    assert "_messageSessionIndexForRawIdx(rawIdx)" in OUTLINE_JS
    assert "_messageRawIdxForSessionIndex(sessionIdx)" in OUTLINE_JS
    # Stamped at render time: read later, it would resolve against the new base,
    # so the signature carries the base and a move re-stamps every mark.
    assert "data-session-idx=" in OUTLINE_JS
    assert "_minimapSessionIndex(0)" in OUTLINE_JS
    # Full-width chat changes the column without resizing the pane, so the rail
    # watches the root attribute rather than relying on a resize.
    assert "attributeFilter: ['data-workspace-panel', 'data-chat-width']" in OUTLINE_JS
    # Preview text matches ui.js's visible-assistant-content definition.
    assert "p.type !== 'input_text' && p.type !== 'output_text'" in OUTLINE_JS
    assert "_assistantAnchorSceneFinalAnswerText(m)" in OUTLINE_JS
    # Generated labels are not reachable by applyLocaleToDOM(), so the signature
    # carries the locale and a language change rebuilds them.
    assert "document.documentElement.lang + '|'" in OUTLINE_JS
    # One IntersectionObserver over the rendered user rows - no scroll-time scan.
    assert OUTLINE_JS.count("new IntersectionObserver") == 1
    assert "root: document.getElementById('messages')" in OUTLINE_JS
    assert "addEventListener('scroll'" not in OUTLINE_JS


def test_mark_identity_survives_streaming_paging_and_session_switches():
    """Marks are keyed by absolute rawIdx and rebuilt only when the turns change."""
    # Identity is the absolute message index, the same key the jump path uses.
    assert "data-raw-idx=\"' + e.rawIdx +" in OUTLINE_JS
    assert "'msg-user-' + rawIdx" in OUTLINE_JS
    # A session switch drops every observation before the new marks are built.
    assert "if (sid !== _minimapSid) {" in OUTLINE_JS
    assert "_teardownMinimap();" in OUTLINE_JS
    # Virtualized rows: re-observed on every render, not only on turn changes.
    assert "_reobserveMinimapRows();" in OUTLINE_JS
    assert "_scheduleMinimapSync();" in OUTLINE_JS
    # Streaming re-renders coalesce into one sync per frame.
    assert "window.requestAnimationFrame ||" in OUTLINE_JS
    # Unloaded history is only fetched through the existing explicit jump path.
    assert OUTLINE_JS.count("/api/session") == 1


def test_reduced_motion_and_static_active_state():
    rule = re.search(
        r"@media \(prefers-reduced-motion:reduce\)\{\s*\.outline-mark::before\{[^}]*\}[^}]*\}",
        STYLE_CSS,
    )
    assert rule, "minimap reduced-motion block not found"
    assert "transition:none" in rule.group(0)
    assert ".outline-jump-flash{animation:none;}" in rule.group(0)
    # The active mark is a width/colour swap, never a keyframe animation.
    current = re.search(r"\.outline-mark\[aria-current=\"true\"\]::before\{([^}]*)\}", STYLE_CSS)
    assert current and "animation" not in current.group(1)
