"""HWEB-3 — user bubbles widen to 80% of the column; long ones fold away.

Two shipped contracts, both measured in a real browser against the running app
so the actual CSS cascade and the actual ``_userMessageBodyHtml`` markup are
what get exercised:

  * width — a user row may use up to ~80% of the shared reading column
    (`--msg-max`), not the old 60%, and never escapes it;
  * disclosure — a message over 600 characters *or* over 8 lines renders
    clipped behind a keyboard-reachable toggle that flips ``aria-expanded``;
    a shorter one gets no control at all; and toggling never moves the
    transcript's scroll offset.

The 600-char / 8-line boundary is asserted on both sides (599 vs 601, 8 lines
vs 9 lines) because an off-by-one there is the whole contract.
"""

from __future__ import annotations

import pytest

from tests._pytest_port import BASE


CHAT_COLUMN_PX = 768  # --msg-max: 48rem at the CSS-default 16px root

# Filler so #messages is genuinely scrollable and a scroll offset can be held
# across the toggle. Assistant rows are untouched by this ticket.
_FILLER_ROW = (
    '<div class="msg-row" data-role="assistant">'
    '<div class="msg-body"><p>filler paragraph line</p></div></div>'
)

_SETUP_JS = """
() => {
  // Build user rows through the shipped markup builder, not a hand copy of it.
  window.__hweb3Row = (text, rawIdx) => {
    const row = document.createElement('div');
    row.className = 'msg-row';
    row.dataset.role = 'user';
    row.dataset.msgIdx = String(rawIdx);
    row.dataset.sessionMsgIdx = String(rawIdx);
    row.dataset.rawText = text;
    // Render through the shipped user-message renderer so line breaks, markdown
    // and escaping match production exactly.
    row.innerHTML = window._userMessageBodyHtml(
      window._getCachedRender(text, true), text, rawIdx, false);
    return row;
  };
  const empty = document.getElementById('emptyState');
  if (empty) empty.style.display = 'none';
  return typeof window._userMessageBodyHtml === 'function'
    && typeof window.toggleMessageExpand === 'function'
    && typeof window._getCachedRender === 'function'
    && typeof window._userMessageIsExpanded === 'function'
    && typeof window._setUserMessageExpanded === 'function'
    && typeof window._clearUserMessageExpandState === 'function';
}
"""

_MEASURE_JS = """
(cases) => {
  const inner = document.getElementById('msgInner');
  inner.innerHTML = '';
  const out = {};
  cases.forEach(([name, text], i) => {
    const row = window.__hweb3Row(text, 9000 + i);
    row.id = 'probe_' + name;
    inner.appendChild(row);
    const btn = row.querySelector('.msg-expand-btn');
    const clip = row.querySelector('.msg-clip');
    out[name] = {
      hasToggle: !!btn,
      label: btn ? btn.textContent.trim() : null,
      ariaExpanded: btn ? btn.getAttribute('aria-expanded') : null,
      // aria-controls must actually resolve, or the toggle announces nothing.
      controlsResolves: !!(btn && document.getElementById(btn.getAttribute('aria-controls'))),
      clipHeight: clip ? clip.getBoundingClientRect().height : null,
      scrollHeight: clip ? clip.scrollHeight : null,
      rowWidth: row.getBoundingClientRect().width,
      rowRight: row.getBoundingClientRect().right,
    };
  });
  const col = inner.getBoundingClientRect();
  out.__column = { width: col.width, left: col.left, right: col.right };
  return out;
}
"""

_TOGGLE_JS = """
(text) => {
  const msgs = document.getElementById('messages');
  const inner = document.getElementById('msgInner');
  inner.innerHTML = window.__hweb3Filler + '';
  const row = window.__hweb3Row(text, 9100);
  row.id = 'probeToggle';
  inner.appendChild(row);
  inner.insertAdjacentHTML('beforeend', window.__hweb3Filler);
  const btn = row.querySelector('.msg-expand-btn');
  const clip = row.querySelector('.msg-clip');

  // Park the reader mid-transcript so a jump would be measurable in both
  // directions, then let layout settle before sampling.
  msgs.scrollTop = Math.round(msgs.scrollHeight / 2);
  const before = {
    scrollTop: msgs.scrollTop,
    rowTop: row.getBoundingClientRect().top,
    clipHeight: clip.getBoundingClientRect().height,
    aria: btn.getAttribute('aria-expanded'),
  };
  btn.click();
  const expanded = {
    scrollTop: msgs.scrollTop,
    rowTop: row.getBoundingClientRect().top,
    clipHeight: clip.getBoundingClientRect().height,
    aria: btn.getAttribute('aria-expanded'),
    label: btn.textContent.trim(),
    rowExpanded: row.dataset.msgExpanded || '',
    stored: window._userMessageIsExpanded(9100),
    i18nKey: btn.getAttribute('data-i18n'),
  };
  btn.click();
  const collapsed = {
    scrollTop: msgs.scrollTop,
    rowTop: row.getBoundingClientRect().top,
    clipHeight: clip.getBoundingClientRect().height,
    aria: btn.getAttribute('aria-expanded'),
    label: btn.textContent.trim(),
    rowExpanded: row.dataset.msgExpanded || '',
    stored: window._userMessageIsExpanded(9100),
    i18nKey: btn.getAttribute('data-i18n'),
  };
  return { before, expanded, collapsed };
}
"""


def _page(viewport_width: int):
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

    page = browser.new_page(viewport={"width": viewport_width, "height": 700})
    page.goto(BASE, wait_until="domcontentloaded")
    page.wait_for_selector("#msgInner", timeout=15000)
    page.wait_for_function("() => typeof window._userMessageBodyHtml === 'function'", timeout=15000)
    ready = page.evaluate(_SETUP_JS)
    assert ready, "the shipped collapse helpers must be reachable as globals"
    page.evaluate("(html) => { window.__hweb3Filler = html.repeat(40); }", _FILLER_ROW)
    return playwright, browser, page


def _measure(cases, viewport_width: int = 1440):
    playwright, browser, page = _page(viewport_width)
    try:
        return page.evaluate(_MEASURE_JS, cases)
    finally:
        browser.close()
        playwright.stop()


# A long single line (no newlines) isolates the character threshold; a stack of
# short lines isolates the line threshold.
_599 = "a" * 599
_601 = "b" * 601
_8_LINES = "\n".join(f"line {i}" for i in range(8))
_9_LINES = "\n".join(f"line {i}" for i in range(9))


def test_short_messages_get_no_disclosure_control():
    m = _measure([("c599", _599), ("l8", _8_LINES), ("tiny", "hi")])
    for name in ("c599", "l8", "tiny"):
        assert m[name]["hasToggle"] is False, (name, m[name])
        # No clip wrapper either — a short bubble is plain markup, as before.
        assert m[name]["clipHeight"] is None, (name, m[name])


def test_long_messages_collapse_with_an_aria_expanded_control():
    m = _measure([("c601", _601), ("l9", _9_LINES)])
    for name in ("c601", "l9"):
        probe = m[name]
        assert probe["hasToggle"] is True, (name, probe)
        assert probe["ariaExpanded"] == "false", (name, probe)
        assert probe["controlsResolves"] is True, (name, probe)
        assert probe["label"], (name, probe)
        # Clipped: the rendered box is shorter than the content it holds.
        assert probe["clipHeight"] < probe["scrollHeight"] - 1, (name, probe)


def test_toggle_flips_aria_expanded_and_holds_the_scroll_offset():
    playwright, browser, page = _page(1440)
    try:
        r = page.evaluate(_TOGGLE_JS, _601)
    finally:
        browser.close()
        playwright.stop()

    assert r["before"]["aria"] == "false", r
    assert r["expanded"]["aria"] == "true", r
    assert r["collapsed"]["aria"] == "false", r
    assert r["expanded"]["rowExpanded"] == "1", r
    assert r["collapsed"]["rowExpanded"] == "", r
    # The owning store, not just the DOM row, tracks the state.
    assert r["expanded"]["stored"] is True, r
    assert r["collapsed"]["stored"] is False, r
    assert r["expanded"]["label"] != r["collapsed"]["label"], r

    # The content really opened and closed again.
    assert r["expanded"]["clipHeight"] > r["before"]["clipHeight"] + 1, r
    assert abs(r["collapsed"]["clipHeight"] - r["before"]["clipHeight"]) <= 1, r

    # No viewport jump: the scroll offset and the toggled row's on-screen
    # position are both unchanged across expand and collapse.
    for phase in ("expanded", "collapsed"):
        assert r[phase]["scrollTop"] == r["before"]["scrollTop"], (phase, r)
        assert abs(r[phase]["rowTop"] - r["before"]["rowTop"]) <= 1, (phase, r)


@pytest.mark.parametrize(
    "label,viewport,expected_column",
    [
        ("desktop", 1440, CHAT_COLUMN_PX),
        ("wide desktop", 1920, CHAT_COLUMN_PX),
    ],
)
def test_user_bubble_uses_about_80_percent_of_the_column(label, viewport, expected_column):
    m = _measure([("probe", "A normal length question that wraps across the bubble. " * 12)], viewport)
    col = m["__column"]
    assert round(col["width"]) == expected_column, (label, m)
    ratio = m["probe"]["rowWidth"] / col["width"]
    assert 0.75 <= ratio <= 0.81, (label, ratio, m)
    assert m["probe"]["rowRight"] <= col["right"] + 1, (label, m)


def test_narrow_and_mobile_widths_stay_inside_the_column():
    for viewport, lo in ((820, 0.75), (390, 0.85)):
        m = _measure([("probe", "A question long enough to fill the bubble. " * 12)], viewport)
        col = m["__column"]
        ratio = m["probe"]["rowWidth"] / col["width"]
        assert lo <= ratio <= 0.91, (viewport, ratio, m)
        assert m["probe"]["rowRight"] <= col["right"] + 1, (viewport, m)


def test_mobile_disclosure_meets_the_44px_touch_target():
    playwright, browser, page = _page(390)
    try:
        h = page.evaluate(
            """
            (text) => {
              const inner = document.getElementById('msgInner');
              inner.innerHTML = '';
              inner.appendChild(window.__hweb3Row(text, 9200));
              const btn = inner.querySelector('.msg-expand-btn');
              return btn.getBoundingClientRect().height;
            }
            """,
            _601,
        )
    finally:
        browser.close()
        playwright.stop()
    assert h >= 44, h


_RERENDER_JS = """
(text) => {
  const inner = document.getElementById('msgInner');
  inner.innerHTML = '';
  // Ordinary renders build FRESH nodes (row recycling is only on inside the
  // virtual-scroll path), so a rebuilt row can only learn the disclosure state
  // from the store. Simulate exactly that: open the message, throw the DOM away,
  // rebuild from scratch, and read the new button.
  const first = window.__hweb3Row(text, 7001);
  inner.appendChild(first);
  first.querySelector('.msg-expand-btn').click();
  const afterToggle = first.querySelector('.msg-expand-btn').getAttribute('aria-expanded');

  inner.innerHTML = '';
  const rebuilt = document.createElement('div');
  rebuilt.className = 'msg-row';
  rebuilt.dataset.role = 'user';
  rebuilt.dataset.sessionMsgIdx = '7001';
  const expanded = window._userMessageIsExpanded(7001);
  rebuilt.innerHTML = window._userMessageBodyHtml(
    window._getCachedRender(text, true), text, 7001, expanded);
  if (expanded) rebuilt.dataset.msgExpanded = '1';
  inner.appendChild(rebuilt);
  const btn = rebuilt.querySelector('.msg-expand-btn');
  const clip = rebuilt.querySelector('.msg-clip');
  const rebuiltState = {
    aria: btn.getAttribute('aria-expanded'),
    i18nKey: btn.getAttribute('data-i18n'),
    clipHeight: clip.getBoundingClientRect().height,
    contentHeight: clip.scrollHeight,
  };

  // A session switch releases the state, so the next session starts collapsed.
  window._clearUserMessageExpandState();
  return { afterToggle, rebuiltState, afterClear: window._userMessageIsExpanded(7001) };
}
"""


def test_expansion_survives_a_rebuild_and_is_released_on_session_switch():
    """The state must outlive a fresh-node rerender, which is every ordinary
    renderMessages() call — row recycling only happens in the virtual-scroll path."""
    playwright, browser, page = _page(1440)
    try:
        r = page.evaluate(_RERENDER_JS, _601)
    finally:
        browser.close()
        playwright.stop()

    assert r["afterToggle"] == "true", r
    # Rebuilt from scratch, it comes back open — not silently re-collapsed.
    assert r["rebuiltState"]["aria"] == "true", r
    assert r["rebuiltState"]["i18nKey"] == "show_less_message", r
    assert r["rebuiltState"]["clipHeight"] >= r["rebuiltState"]["contentHeight"] - 1, r
    # And the store is released on session switch, so state can't leak sessions.
    assert r["afterClear"] is False, r


_FOCUS_JS = r"""
() => {
  const inner = document.getElementById('msgInner');
  inner.innerHTML = '';
  const text = Array.from({length: 12}, (_, i) => 'line ' + i).join('\n');
  const row = window.__hweb3Row(text, 7100);
  inner.appendChild(row);
  const clip = row.querySelector('.msg-clip');
  // A focusable descendant below the eighth line -- a link in a rendered user
  // message, say. Clipping is visual only, so it stays in the tab order and a
  // keyboard reader can land on a control inside the hidden overflow.
  const link = document.createElement('a');
  link.href = 'https://example.com/buried';
  link.id = 'buriedLink';
  link.textContent = 'buried link';
  clip.appendChild(link);
  const btn = row.querySelector('.msg-expand-btn');
  const before = {
    aria: btn.getAttribute('aria-expanded'),
    linkTop: link.getBoundingClientRect().top,
    clipBottom: clip.getBoundingClientRect().bottom,
  };
  link.focus();
  return {
    before,
    after: {
      aria: btn.getAttribute('aria-expanded'),
      focused: document.activeElement === link,
      linkVisible: link.getBoundingClientRect().bottom
        <= clip.getBoundingClientRect().bottom + 1,
    },
  };
}
"""


def test_focusing_a_clipped_control_opens_the_message():
    playwright, browser, page = _page(1440)
    try:
        r = page.evaluate(_FOCUS_JS)
    finally:
        browser.close()
        playwright.stop()

    # Precondition: the link really did start below the visible preview.
    assert r["before"]["linkTop"] > r["before"]["clipBottom"] + 1, r
    assert r["before"]["aria"] == "false", r
    assert r["after"]["aria"] == "true", r
    assert r["after"]["focused"] is True, r
    assert r["after"]["linkVisible"] is True, r


_LOCALE_JS = """
() => {
  const inner = document.getElementById('msgInner');
  inner.innerHTML = '';
  const row = window.__hweb3Row('x'.repeat(601), 7200);
  inner.appendChild(row);
  const btn = row.querySelector('.msg-expand-btn');
  const before = btn.textContent.trim();
  // Swap the active locale the way Settings -> Language does, then re-apply.
  const original = window.t('show_full_message');
  window.LOCALES.__hweb3test = Object.assign({}, window.LOCALES.en, {
    _lang: 'en', show_full_message: 'HWEB3_TRANSLATED',
  });
  window.setLocale('__hweb3test');
  window.applyLocaleToDOM();
  const after = btn.textContent.trim();
  window.setLocale('en');
  window.applyLocaleToDOM();
  return { before, after, restored: btn.textContent.trim(), original };
}
"""


def test_locale_change_retranslates_an_already_rendered_control():
    playwright, browser, page = _page(1440)
    try:
        r = page.evaluate(_LOCALE_JS)
    finally:
        browser.close()
        playwright.stop()
    assert r["before"] == r["original"], r
    assert r["after"] == "HWEB3_TRANSLATED", r
    assert r["restored"] == r["original"], r
