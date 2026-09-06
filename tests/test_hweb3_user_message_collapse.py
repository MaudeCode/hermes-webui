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
    && typeof window._getCachedRender === 'function';
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
  };
  btn.click();
  const collapsed = {
    scrollTop: msgs.scrollTop,
    rowTop: row.getBoundingClientRect().top,
    clipHeight: clip.getBoundingClientRect().height,
    aria: btn.getAttribute('aria-expanded'),
    label: btn.textContent.trim(),
    rowExpanded: row.dataset.msgExpanded || '',
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
