"""HWEB-6 — quiet code blocks and reading tables inside chat messages.

Two layers:

* the CSS/JS contract read straight from the shipped sources, so the intent
  survives even where a browser is unavailable,
* real-browser measurements at a desktop and a mobile viewport, because the
  claims that matter here ("no cell grid", "no zebra", "still scrolls
  horizontally", "sort chrome only in structured-data mode") are properties of
  the rendered cascade, not of a source string.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests._pytest_port import BASE

ROOT = Path(__file__).resolve().parents[1]
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _rule(selector: str, contains: str = "") -> str:
    """The declaration body of a rule. `contains` picks past media-query copies."""
    bodies = re.findall(r"(?:^|\n)\s*" + re.escape(selector) + r"\{([^}]*)\}", CSS)
    matches = [b for b in bodies if contains in b]
    assert matches, f"rule {selector} (containing {contains!r}) not found in style.css"
    return matches[0]


# ── source contract ─────────────────────────────────────────────────────────

def test_code_blocks_use_the_tightened_padding_and_radius():
    rule = _rule(".msg-body pre", "padding")
    assert "padding:10px 12px" in rule
    assert "border-radius:8px" in rule
    # Retained behavior: horizontal overflow and the containment fix from #5760.
    assert "overflow-x:auto" in rule
    assert "contain:content" in rule
    assert "border:1px solid var(--border)" in rule


def test_code_block_text_still_hangs_off_the_existing_size_token():
    assert "font-size:var(--message-pre-code-font-size)" in _rule(".msg-body pre code", "font-size")
    assert "font-size:var(--message-code-font-size)" in _rule(".msg-body code", "font-size")


def test_code_block_header_matches_the_block_radius():
    assert "border-radius:8px 8px 0 0" in _rule(".pre-header")
    assert "border-radius:0 0 8px 8px" in _rule(".msg-body .pre-header+pre")


def test_diff_lines_keep_one_inset_matching_the_code_padding():
    assert "padding:0 12px" in _rule(".diff-block .diff-line", "padding")
    assert "padding-left:0" in _rule(".diff-block")


def test_markdown_tables_are_row_separated_not_grid_ruled():
    table = _rule(".msg-body table")
    assert "width:max-content" in table, "columns must take their natural width"
    assert "max-width:100%" in table and "overflow-x:auto" in table

    th = _rule(".msg-body th")
    assert "border:0" in th and "border-bottom:1px solid" in th
    assert "background:" not in th, "the header fill is what HWEB-6 removed"

    td = _rule(".msg-body td")
    assert "border:0" in td and "border-bottom:1px solid" in td
    assert "min-width:10ch" in _rule(".msg-body th,.msg-body td", "min-width")

    assert ".msg-body tr:nth-child(even)" not in CSS, "no zebra fill in chat tables"


def test_light_theme_keeps_a_readable_header_separator():
    assert ":root:not(.dark) .msg-body th{border-bottom-color:" in CSS
    assert ":root:not(.dark) .msg-body td{border-color:" in CSS


def test_sort_and_filter_chrome_is_scoped_to_structured_data_tables():
    helper = MESSAGES_JS[
        MESSAGES_JS.index("function enhanceMarkdownTables(root)"):
        MESSAGES_JS.index("function _markdownTableText")
    ]
    assert ".msg-body .csv-table-wrap table:not([data-markdown-table-enhanced])" in helper
    # The old "enhance everything, then opt CSV out" guard must be gone.
    assert "if(table.closest('.csv-table-wrap')) return;" not in helper


# ── rendered behavior ───────────────────────────────────────────────────────

_TRANSCRIPT_HTML = """
<div class="msg-row" data-role="assistant">
  <div class="msg-body" id="probeBody">
    <p>Answer with <code id="probeInlineCode">inline_code()</code> in it.</p>
    <div class="pre-header" id="probePreHeader">python</div>
    <pre id="probePre"><code>def f():
    return "a very long single line of code that will not wrap under any circumstances at all 0123456789 0123456789 0123456789 0123456789 0123456789"</code></pre>
    <pre class="diff-block" id="probeDiff"><code><span class="diff-line diff-hunk">@@ -1,2 +1,2 @@</span>
<span class="diff-line diff-minus" id="probeDiffMinus">-old line</span>
<span class="diff-line diff-plus">+new line</span></code></pre>
    <ul id="probeList"><li>outer item
      <ul id="probeNestedList"><li id="probeNestedItem">nested item</li></ul>
    </li></ul>
    <table id="probeMdTable">
      <thead><tr><th id="probeMdTh">alpha column</th><th>beta column</th><th>gamma column</th><th>delta column</th><th>epsilon column</th><th>zeta column</th><th>eta column</th><th>theta column</th></tr></thead>
      <tbody>
        <tr><td id="probeMdTd">1111111111</td><td>2222222222</td><td>3333333333</td><td>4444444444</td><td>5555555555</td><td>6666666666</td><td>7777777777</td><td>8888888888</td></tr>
        <tr id="probeMdEvenRow"><td>aaaaaaaaaa</td><td>bbbbbbbbbb</td><td>cccccccccc</td><td>dddddddddd</td><td>eeeeeeeeee</td><td>ffffffffff</td><td>gggggggggg</td><td>hhhhhhhhhh</td></tr>
        <tr><td>a</td><td>b</td><td>c</td><td>d</td><td>e</td><td>f</td><td>g</td><td>h</td></tr>
        <tr><td>i</td><td>j</td><td>k</td><td>l</td><td>m</td><td>n</td><td>o</td><td>p</td></tr>
        <tr><td>q</td><td>r</td><td>s</td><td>t</td><td>u</td><td>v</td><td>w</td><td>x</td></tr>
      </tbody>
    </table>
    <div class="csv-table-wrap" id="probeCsvWrap">
      <table class="csv-table" id="probeCsvTable">
        <thead><tr><th>alpha</th><th>beta</th></tr></thead>
        <tbody>
          <tr><td>1</td><td>2</td></tr>
          <tr><td>3</td><td>4</td></tr>
          <tr><td>5</td><td>6</td></tr>
          <tr><td>7</td><td>8</td></tr>
        </tbody>
      </table>
    </div>
  </div>
</div>
"""

_MEASURE_JS = """
() => {
  const doc = document;
  const inner = doc.getElementById('msgInner');
  inner.innerHTML = window.__hweb6Transcript;
  const empty = doc.getElementById('emptyState');
  if (empty) empty.style.display = 'none';
  window.enhanceMarkdownTables(inner);

  const cs = (id) => getComputedStyle(doc.getElementById(id));
  const box = (id) => {
    const el = doc.getElementById(id);
    const r = el.getBoundingClientRect();
    return {left: r.left, right: r.right, width: r.width,
            scrollWidth: el.scrollWidth, clientWidth: el.clientWidth};
  };
  const themed = (fn) => {
    const root = doc.documentElement;
    const had = root.classList.contains('dark');
    const out = {};
    for (const dark of [false, true]) {
      root.classList.toggle('dark', dark);
      out[dark ? 'dark' : 'light'] = fn();
    }
    root.classList.toggle('dark', had);
    return out;
  };

  const pre = cs('probePre');
  const th = () => {
    const s = cs('probeMdTh');
    return {top: s.borderTopWidth, left: s.borderLeftWidth, right: s.borderRightWidth,
            bottom: s.borderBottomWidth, background: s.backgroundColor,
            color: s.color, borderBottomColor: s.borderBottomColor};
  };
  const td = () => {
    const s = cs('probeMdTd');
    return {top: s.borderTopWidth, left: s.borderLeftWidth, right: s.borderRightWidth,
            bottom: s.borderBottomWidth, background: s.backgroundColor};
  };

  return {
    pre: {paddingTop: pre.paddingTop, paddingLeft: pre.paddingLeft,
          radiusTop: pre.borderTopLeftRadius, radiusBottom: pre.borderBottomLeftRadius,
          whiteSpace: pre.whiteSpace,
          overflowX: pre.overflowX, fontFamily: pre.fontFamily,
          ...box('probePre')},
    headerRadiusTop: cs('probePreHeader').borderTopLeftRadius,
    headerPaddingLeft: cs('probePreHeader').paddingLeft,
    diffLinePaddingLeft: cs('probeDiffMinus').paddingLeft,
    diffLineBackground: cs('probeDiffMinus').backgroundColor,
    inlineCodeBackground: cs('probeInlineCode').backgroundColor,
    inlineCodeFont: cs('probeInlineCode').fontFamily,
    nestedListIndent: doc.getElementById('probeNestedItem').getBoundingClientRect().left
                      - doc.getElementById('probeList').getBoundingClientRect().left,
    th: themed(th),
    td: themed(td),
    evenRowBackground: cs('probeMdEvenRow').backgroundColor,
    mdTable: box('probeMdTable'),
    mdNarrowestColumn: Math.min(...Array.from(
      doc.querySelectorAll('#probeMdTable tbody tr:first-child td'),
      (td) => td.getBoundingClientRect().width)),
    mdSortControls: doc.querySelectorAll('#probeMdTable .markdown-table-sort').length,
    mdEnhanced: doc.getElementById('probeMdTable').hasAttribute('data-markdown-table-enhanced'),
    csvSortControls: doc.querySelectorAll('#probeCsvTable .markdown-table-sort').length,
    csvFilterControls: doc.querySelectorAll('#probeBody .markdown-table-filter').length,
    csvFilterOutsideWrap: !!(doc.querySelector('#probeBody > .markdown-table-filter')),
    bodyBox: box('probeBody'),
    messagesScrollWidth: doc.getElementById('messages').scrollWidth,
    messagesClientWidth: doc.getElementById('messages').clientWidth,
  };
}
"""


def _measure(viewport_width: int):
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
        page.wait_for_function("() => typeof window.enhanceMarkdownTables === 'function'",
                               timeout=15000)
        page.evaluate("(html) => { window.__hweb6Transcript = html; }", _TRANSCRIPT_HTML)
        return page.evaluate(_MEASURE_JS)
    finally:
        browser.close()
        playwright.stop()


_TRANSPARENT = ("rgba(0, 0, 0, 0)", "transparent")


@pytest.mark.parametrize("label,viewport", [("desktop", 1440), ("mobile", 390)])
def test_chat_code_and_tables_render_quietly(label, viewport):
    m = _measure(viewport)

    # Code blocks: tightened box, mono face, inline code still tinted.
    assert m["pre"]["paddingTop"] == "10px", m["pre"]
    assert m["pre"]["paddingLeft"] == "12px", m["pre"]
    # The block sits under its language/Copy header: rounded pair, one seam.
    assert m["headerRadiusTop"] == "8px", m
    assert m["headerPaddingLeft"] == "12px", m
    assert m["pre"]["radiusTop"] == "0px", m["pre"]
    assert m["pre"]["radiusBottom"] == "8px", m["pre"]
    assert "mono" in m["pre"]["fontFamily"].lower(), m["pre"]
    assert m["inlineCodeBackground"] not in _TRANSPARENT
    assert "mono" in m["inlineCodeFont"].lower()

    # Diffs keep their per-line tint and a single inset.
    assert m["diffLinePaddingLeft"] == "12px", m
    assert m["diffLineBackground"] not in _TRANSPARENT

    # Nested lists still indent.
    assert m["nestedListIndent"] > 10, m["nestedListIndent"]

    # Tables: row separators only, no header fill, no zebra — in both themes.
    for theme in ("light", "dark"):
        th, td = m["th"][theme], m["td"][theme]
        assert th["background"] in _TRANSPARENT, (theme, th)
        assert (th["top"], th["left"], th["right"]) == ("0px", "0px", "0px"), (theme, th)
        assert float(th["bottom"].rstrip("px")) > 0, (theme, th)
        assert (td["top"], td["left"], td["right"]) == ("0px", "0px", "0px"), (theme, td)
        assert float(td["bottom"].rstrip("px")) > 0, (theme, td)
        assert td["background"] in _TRANSPARENT, (theme, td)
    assert m["evenRowBackground"] in _TRANSPARENT, m["evenRowBackground"]

    # No sort/filter chrome on an ordinary markdown table…
    assert m["mdSortControls"] == 0, m
    assert m["mdEnhanced"] is False, m
    # …but the explicit structured-data table keeps both.
    assert m["csvSortControls"] == 2, m
    assert m["csvFilterControls"] == 1, m
    assert m["csvFilterOutsideWrap"] is True, m

    # A wide table keeps readable columns and scrolls inside the reading column
    # instead of squeezing them; nothing escapes the column or the page.
    assert m["mdNarrowestColumn"] >= 70, m
    if m["mdTable"]["scrollWidth"] > m["mdTable"]["clientWidth"] + 1:
        assert m["mdTable"]["clientWidth"] <= m["bodyBox"]["width"] + 1, m
    assert m["mdTable"]["right"] <= m["bodyBox"]["right"] + 1, m
    assert m["mdTable"]["left"] >= m["bodyBox"]["left"] - 1, m
    assert m["messagesScrollWidth"] <= m["messagesClientWidth"] + 1, m

    if viewport <= 640:
        # Mobile containment: long code wraps instead of scrolling the page.
        assert m["pre"]["whiteSpace"] == "pre-wrap", m["pre"]
        assert m["pre"]["scrollWidth"] <= m["pre"]["clientWidth"] + 1, m["pre"]
    else:
        assert m["pre"]["overflowX"] == "auto", m["pre"]
        assert m["pre"]["scrollWidth"] > m["pre"]["clientWidth"], m["pre"]
