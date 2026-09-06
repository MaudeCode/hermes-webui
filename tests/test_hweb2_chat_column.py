"""HWEB-2 — every chat surface renders in one shared reading column.

Before this ticket the transcript (`--msg-max`), the composer
(`clamp(780px,60vw,1100px)`, then `1600px` at >=1600px viewports) and the
selection chips each carried their own width rule, so at a 1600px viewport the
composer was ~2x the width of the messages it controlled.

These are real-browser measurements against the running app: the page is loaded
from the test server, representative transcript markup is injected into the real
`#msgInner`, and the flyout surfaces are un-hidden — so the shipped CSS cascade
(including the mobile media block and the `data-chat-width="full"` override) is
what gets measured, not a re-derivation of it.
"""

from __future__ import annotations

import pytest

from tests._pytest_port import BASE


CHAT_COLUMN_PX = 768  # --msg-max: 48rem at the CSS-default 16px root

# Deliberately hostile transcript content: an unbreakable token, a wide table
# and a long code line are the three things that historically escaped the
# column and gave the whole page a horizontal scrollbar.
_TRANSCRIPT_HTML = """
<div class="msg-row" data-role="user" id="probeUserRow">
  <div class="msg-body" id="probeUserBody">Short question</div>
</div>
<div class="msg-row" data-role="user">
  <div class="msg-body">Averyveryverylongunbreakabletokenthatcannotwrapanywhere_0123456789_0123456789_0123456789</div>
</div>
<div class="msg-row" data-role="assistant">
  <div class="msg-body" id="probeAssistantBody">
    <p>Answer paragraph.</p>
    <pre id="probePre"><code>def f(): return "a very long single line of code that will not wrap under any circumstances at all 0123456789"</code></pre>
    <table id="probeTable"><tr><th>alpha</th><th>beta</th><th>gamma</th><th>delta</th><th>epsilon</th><th>zeta</th><th>eta</th><th>theta</th></tr>
    <tr><td>1111111111</td><td>2222222222</td><td>3333333333</td><td>4444444444</td><td>5555555555</td><td>6666666666</td><td>7777777777</td><td>8888888888</td></tr></table>
  </div>
</div>
<div class="tool-card" id="probeToolCard">tool output</div>
"""

_MEASURE_JS = """
(paneWidth) => {
  const doc = document;
  const chat = doc.getElementById('mainChat');
  if (paneWidth) {
    chat.style.setProperty('flex', '0 0 ' + paneWidth + 'px', 'important');
    chat.style.setProperty('max-width', paneWidth + 'px', 'important');
  }
  doc.getElementById('msgInner').innerHTML = window.__hweb2Transcript;

  // Reveal the surfaces the app hides until they are needed.
  const approval = doc.getElementById('approvalCard');
  approval.removeAttribute('hidden');
  approval.removeAttribute('inert');
  approval.classList.add('visible');
  doc.querySelector('.queue-pill-outer').classList.add('show');
  doc.getElementById('composerSelectionChips').removeAttribute('hidden');
  const scrollBtn = doc.getElementById('scrollToBottomBtn');
  scrollBtn.style.display = 'flex';
  const empty = doc.getElementById('emptyState');
  if (empty) empty.style.display = 'none';

  const box = (sel) => {
    const el = typeof sel === 'string' ? doc.querySelector(sel) : sel;
    if (!el) return null;
    const r = el.getBoundingClientRect();
    const cs = getComputedStyle(el);
    return {
      left: r.left, right: r.right, width: r.width,
      contentLeft: r.left + parseFloat(cs.paddingLeft) + parseFloat(cs.borderLeftWidth),
      contentRight: r.right - parseFloat(cs.paddingRight) - parseFloat(cs.borderRightWidth),
    };
  };

  return {
    pane: box('#mainChat'),
    messagesInner: box('#msgInner'),
    composerBox: box('#composerBox'),
    selectionChips: box('#composerSelectionChips'),
    approvalInner: box('#approvalCard .approval-inner'),
    queuePill: box('#queuePill'),
    assistantBody: box('#probeAssistantBody'),
    pre: box('#probePre'),
    table: box('#probeTable'),
    userRow: box('#probeUserRow'),
    toolCard: box('#probeToolCard'),
    scrollBtn: box(scrollBtn),
    // Widest thing that escapes the chat pane. Scoped to #mainChat: the
    // off-canvas workspace panel legitimately sits past the viewport edge, so
    // documentElement.scrollWidth can't answer this question. Content inside a
    // scroll container (a wide table, a long code line) is that container's
    // problem, and the container itself is measured on its own.
    widestOverflow: (() => {
      const edge = chat.getBoundingClientRect().right;
      const contained = (el) => {
        for (let a = el.parentElement; a && a !== chat; a = a.parentElement) {
          if (getComputedStyle(a).overflowX !== 'visible') return true;
        }
        return false;
      };
      let worst = null;
      chat.querySelectorAll('*').forEach((el) => {
        const r = el.getBoundingClientRect();
        if (r.width && r.right > edge + 1 && !contained(el)
            && (!worst || r.right > worst.right)) {
          worst = { sel: el.id || String(el.className), right: r.right, edge };
        }
      });
      return worst;
    })(),
    paneScrollWidth: chat.scrollWidth,
    paneClientWidth: chat.clientWidth,
    messagesScrollWidth: doc.getElementById('messages').scrollWidth,
    messagesClientWidth: doc.getElementById('messages').clientWidth,
  };
}
"""


def _measure(viewport_width: int, pane_width: int | None = None, full_width: bool = False):
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
        page.evaluate("(html) => { window.__hweb2Transcript = html; }", _TRANSCRIPT_HTML)
        if full_width:
            page.evaluate("() => { document.documentElement.dataset.chatWidth = 'full'; }")
        return page.evaluate(_MEASURE_JS, pane_width)
    finally:
        browser.close()
        playwright.stop()


def _assert_no_horizontal_overflow(m):
    assert m["widestOverflow"] is None, m["widestOverflow"]
    assert m["paneScrollWidth"] <= m["paneClientWidth"] + 1, m
    assert m["messagesScrollWidth"] <= m["messagesClientWidth"] + 1, m


def _assert_surfaces_share_the_column(m):
    """Transcript, composer and the flyout surfaces occupy one column."""
    col = m["messagesInner"]
    for name in ("composerBox", "selectionChips", "approvalInner", "queuePill",
                 "assistantBody", "toolCard", "pre", "table"):
        assert abs(m[name]["left"] - col["left"]) <= 1, (name, m[name], col)
        assert m[name]["right"] <= col["right"] + 1, (name, m[name], col)
    assert round(m["composerBox"]["width"]) == round(col["width"]), m

    # User bubbles size against the shared column, never the whole pane.
    assert m["userRow"]["width"] <= col["width"] + 1, m
    assert m["userRow"]["right"] <= col["right"] + 1, m


@pytest.mark.parametrize(
    "label,viewport,pane",
    [
        ("desktop", 1440, None),
        ("wide desktop", 1920, None),
        # Split pane: the workspace/files panel takes the rest of a wide window,
        # leaving the chat pane far narrower than the viewport.
        ("split pane", 1600, 860),
    ],
)
def test_desktop_widths_share_one_48rem_column(label, viewport, pane):
    m = _measure(viewport, pane_width=pane)
    assert round(m["messagesInner"]["width"]) == CHAT_COLUMN_PX, (label, m)
    _assert_surfaces_share_the_column(m)
    _assert_no_horizontal_overflow(m)


def test_scroll_affordance_rides_the_column_edge():
    """The scroll-to-bottom button hugs the reading column, not the pane edge."""
    m = _measure(1920)
    col = m["messagesInner"]
    assert m["scrollBtn"]["right"] <= col["right"] + 1, m
    # It is pulled well in from the pane edge on a wide window.
    assert m["scrollBtn"]["right"] < m["pane"]["right"] - 100, m


def test_mobile_uses_the_available_width_without_overflow():
    m = _measure(390)
    col = m["messagesInner"]
    # The column uses the pane (minus the 10px mobile gutter) instead of being
    # capped at 48rem.
    assert round(col["width"]) == round(m["pane"]["width"]) - 20, m
    _assert_surfaces_share_the_column(m)
    _assert_no_horizontal_overflow(m)


def test_full_width_preference_remains_an_explicit_override():
    m = _measure(1920, full_width=True)
    col = m["messagesInner"]
    assert round(col["width"]) == round(m["pane"]["width"]) - 40, m
    assert round(m["composerBox"]["width"]) > CHAT_COLUMN_PX, m
    _assert_surfaces_share_the_column(m)
    _assert_no_horizontal_overflow(m)
