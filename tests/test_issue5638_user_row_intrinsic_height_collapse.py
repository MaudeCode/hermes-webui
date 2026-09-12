"""Regression tests for the mobile scroll jump-back caused by user-row
content-visibility scrollHeight collapse (#5637 / #5638 follow-up).

Root cause (proven on-device + in mobile-emulated Playwright): under
`@media (pointer: coarse)`, `.msg-row[data-role="user"]` carries
`content-visibility: auto; contain-intrinsic-size: auto 96px`. A virtualization
wipe-and-rebuild (`renderMessages`) recreates the user row as a FRESH element,
which discards content-visibility:auto's last-remembered size. An off-screen tall
user row (e.g. a long paste measuring thousands of px) therefore falls back to the
flat 96px estimate the instant it is rebuilt, collapsing scrollHeight by
(realHeight - 96). The browser then either force-clamps scrollTop (the dTop≈dH
"layer-1" jump) or re-anchors to a far row (the dTop≫dH browser re-anchor jump) --
both mobile jump-back classes trace to this one collapse. Desktop rests at
content-visibility:visible so intrinsic-size is inert there (why desktop never
reproduces).

Fix: remember each user row's height keyed by its stable session-relative index,
and apply it as an inline `contain-intrinsic-size` both when the row is (re)built
and when it is measured. A content-length estimate reserves the bulk before the
row has ever been measured so even the first fresh-element frame does not collapse.

Every behavioral test below is designed to FAIL on the known-buggy version (no
inline intrinsic-size written -> the row keeps the flat 96px stylesheet estimate)
and PASS only on the fixed version.
"""
import json
import pathlib
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).parent.parent
UI_JS_PATH = ROOT / "static" / "ui.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(source: str) -> str:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".cjs", encoding="utf-8", dir=ROOT, delete=False
    ) as script:
        script.write(source)
        script_path = pathlib.Path(script.name)
    try:
        result = subprocess.run(
            [NODE, str(script_path)],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        script_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


def _extract_func_script(js: str) -> str:
    # Brace-matches a function body while skipping braces inside string / template
    # / regex literals and comments (same hardened extractor as the sibling vscroll
    # suites). Built with a plain string so JS braces need no doubling.
    prelude = "const src = " + json.dumps(js) + ";\n"
    body = r"""
function extractFunc(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start);
  let depth = 1; i++;
  let str = null;
  let inLine = false;
  let inBlock = false;
  let inRegex = false;
  let prev = '';
  while (depth > 0 && i < src.length) {
    const c = src[i];
    const n = src[i + 1];
    if (inLine) { if (c === '\n') inLine = false; i++; continue; }
    if (inBlock) { if (c === '*' && n === '/') { inBlock = false; i++; } i++; continue; }
    if (str) {
      if (c === '\\') { i += 2; continue; }
      if (c === str) str = null;
      i++; continue;
    }
    if (inRegex) {
      if (c === '\\') { i += 2; continue; }
      if (c === '/') inRegex = false;
      i++; continue;
    }
    if (c === '/' && n === '/') { inLine = true; i += 2; continue; }
    if (c === '/' && n === '*') { inBlock = true; i += 2; continue; }
    if (c === '"' || c === "'" || c === '`') { str = c; i++; continue; }
    if (c === '/' && !'})]0123456789'.includes(prev) && !/[A-Za-z_$]/.test(prev)) {
      inRegex = true; i++; continue;
    }
    if (c === '{') depth++;
    else if (c === '}') depth--;
    if (c.trim()) prev = c;
    i++;
  }
  return src.slice(start, i);
}
// HWEB-66: the estimator caps a collapsed row, so it needs the shipped collapse
// threshold beside it. Read the constants out of the source (a direct eval of a
// `const` would not leak into this scope) so a drift in ui.js is exercised here.
var USER_MSG_COLLAPSE_CHARS = Number(src.match(/const USER_MSG_COLLAPSE_CHARS=(\d+);/)[1]);
var USER_MSG_COLLAPSE_LINES = Number(src.match(/const USER_MSG_COLLAPSE_LINES=(\d+);/)[1]);
var USER_MSG_COLLAPSED_ROW_PX = Number(src.match(/const USER_MSG_COLLAPSED_ROW_PX=(\d+);/)[1]);
eval(src.match(/const USER_MSG_FILES_PX=\{[^}]*\};/)[0].replace('const', 'var'));
eval(extractFunc('_estimateUserRowFilesHeight'));
eval(extractFunc('_userRowFilesReserve'));
eval(extractFunc('_userRowIntrinsicHeightKey'));
eval(extractFunc('_userMessageNeedsCollapse'));"""
    return prelude + body


def _fake_row_prelude() -> str:
    """A minimal fake DOM row/element supporting .style.containIntrinsicSize,
    .dataset, and getBoundingClientRect, shared by the tests. Also declares the
    module-level backing store the extracted helpers close over."""
    return r"""
var _userRowIntrinsicHeightBySessionIdx = Object.create(null);
function makeRow(role, sessionMsgIdx, measuredHeight){
  return {
    style: { containIntrinsicSize: '' },
    dataset: { role: role, sessionMsgIdx: String(sessionMsgIdx) },
    classList: { contains(){ return false; } },
    getBoundingClientRect(){ return { height: measuredHeight }; },
  };
}
"""


def test_estimate_reserves_more_than_96px_for_a_tall_user_message():
    """A long user message the reader has OPENED must estimate an intrinsic height
    well above the flat 96px stylesheet fallback, so a rebuilt off-screen row
    reserves close to its real height and scrollHeight does not collapse.

    HWEB-66: a 2000-char message renders collapsed by default (HWEB-3 clips
    anything past 600 chars / 8 lines), so its real height is the flat collapsed
    row size — ~266px at the default font on a phone — not the ~948px the full
    text would wrap to. The full-text estimate is only right for an expanded row;
    a collapsed row must estimate the collapsed size instead, or it over-reserves
    by the difference and that space collapses on paint (the mobile jump-back's
    other half)."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + r"""
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
// ~2000 chars across the mobile bubble width => tens of lines => >>96px.
const longText = 'x'.repeat(2000);
const shortText = 'hi';
console.log(JSON.stringify({
  tall: _estimateUserRowIntrinsicHeight(longText, true),
  collapsed: _estimateUserRowIntrinsicHeight(longText, false),
  collapsedDefault: _estimateUserRowIntrinsicHeight(longText),
  short: _estimateUserRowIntrinsicHeight(shortText),
  cap: USER_MSG_COLLAPSED_ROW_PX,
  // The attachment strip sits above the text in both states.
  collapsedFiles: _estimateUserRowIntrinsicHeight(longText, false, 200),
  tallFiles: _estimateUserRowIntrinsicHeight(longText, true, '200'),
}));
"""
    m = json.loads(_run_node(source))
    # An EXPANDED 2000-char row wraps to ~42 lines -> ~948px, far above 96.
    assert m["tall"] > 800, (
        "an opened long user message must reserve far more than the flat 96px "
        f"estimate; got {m['tall']}"
    )
    # The same text collapsed reserves the flat collapsed-row size (HWEB-66) — and
    # collapsed is the default, matching how renderMessages builds a row the
    # reader has not opened.
    assert m["collapsed"] == m["cap"] == m["collapsedDefault"], (
        "a collapsed long user message must reserve the collapsed-row height, "
        f"not its full-text estimate; got {m['collapsed']} / {m['collapsedDefault']} "
        f"(cap {m['cap']})"
    )
    assert m["collapsed"] < m["tall"], "the collapsed reserve must be below the full estimate"
    # A short row must never reserve LESS than today's 96px floor (no regression).
    assert m["short"] == 96, f"short row must floor at 96px, got {m['short']}"
    # The attachment strip is not in rawText, so it is added per attachment on top
    # of either state's text estimate (a string count, as read from a dataset).
    assert m["collapsedFiles"] == m["cap"] + 200, m
    assert m["tallFiles"] == m["tall"] + 200, m


def test_files_strip_estimate_by_kind_and_column_width():
    """HWEB-66: the strip reserve is sized per attachment kind and by how many
    thumbnails share a row at the current transcript column width — a flat
    per-attachment figure over-reserved three file badges by ~270px (Codex
    round-2 finding). Rows from the browser fixture, normal font: 3 images =
    198px strip at 390px (2 per row) and 300px at 700px (1 per row, the sidebar
    narrows the column); 3 wrapped long badges = 100px; an audio player 150px;
    a video player 266px; every strip ends in a 10px margin."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
const px = USER_MSG_FILES_PX;
const row = makeRow('user', 1, 0);
row.dataset.attachmentKinds = 'image,badge';
console.log(JSON.stringify({
  none: _estimateUserRowFilesHeight('', 370),
  img3phone: _estimateUserRowFilesHeight('image,image,image', 370),   // 2 per row -> 2 rows
  img3narrow: _estimateUserRowFilesHeight('image,image,image', 312),  // 1 per row -> 3 rows
  img3unknown: _estimateUserRowFilesHeight('image,image,image', NaN), // fail closed: 1 per row
  badges3: _estimateUserRowFilesHeight('badge,badge,badge', 370),
  media: _estimateUserRowFilesHeight('audio,video', 370),
  unknownKind: _estimateUserRowFilesHeight('zip', 370),
  // The row helper reads the stamped kinds; with no $() it fails closed to 1 per row.
  viaRow: _userRowFilesReserve(row),
  px,
}));
"""
    m = json.loads(_run_node(source))
    px = m["px"]
    assert m["none"] == 0
    assert m["img3phone"] == px["strip"] + 2 * px["image"], m
    assert m["img3narrow"] == px["strip"] + 3 * px["image"], m
    assert m["img3unknown"] == m["img3narrow"], "unknown width must fail closed to one thumbnail per row"
    assert m["badges3"] == px["strip"] + 3 * px["badge"], m
    assert m["media"] == px["strip"] + px["audio"] + px["video"], m
    assert m["unknownKind"] == px["strip"] + px["badge"], "an unknown kind counts as a badge"
    assert m["viaRow"] == px["strip"] + px["image"] + px["badge"], m
    # Each figure covers its measured row (96 / 150 / 29 + the 6px gap); the video
    # figure covers the stylesheet ceiling (320px max-height + border + 114px chrome).
    assert px["image"] >= 102 and px["audio"] >= 156 and px["video"] >= 442 and px["badge"] >= 35, px


def test_apply_uses_remembered_measured_height_over_estimate():
    """When a row's real measured height has been remembered (from a prior measure
    pass), _applyUserRowIntrinsicHeight must write THAT exact height onto the
    rebuilt row's inline contain-intrinsic-size -- not the 96px stylesheet default
    and not the coarser content estimate."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
// Remember that the user row at sessionIdx 7 really measured 4901px earlier.
_rememberUserRowIntrinsicHeight(7, 4901);
// Now a wipe rebuilds it as a fresh element (short rawText in hand at build time).
const rebuilt = makeRow('user', 7, /*unused*/0);
_applyUserRowIntrinsicHeight(rebuilt, 'short text at build time');
console.log(JSON.stringify({ intrinsic: rebuilt.style.containIntrinsicSize }));
"""
    m = json.loads(_run_node(source))
    assert m["intrinsic"] == "auto 4901px", (
        "rebuilt user row must reserve its remembered measured height (4901px), "
        f"not the 96px default; got {m['intrinsic']!r}. On the buggy version no "
        "inline intrinsic-size is written and the row keeps the collapsing 96px "
        "stylesheet estimate."
    )


def test_apply_falls_back_to_estimate_before_first_measure():
    """A never-measured tall row (no remembered height) must still reserve a
    content-derived estimate >> 96px at build time, so even the very first
    fresh-element frame does not collapse scrollHeight."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
// sessionIdx 99 was never measured/remembered. The reader opened it (HWEB-3
// disclosure), so it renders at full height — the row attribute renderMessages
// stamps from the expand store is what the reserve reads.
const fresh = makeRow('user', 99, 0);
fresh.dataset.msgExpanded = '1';
const longText = 'y'.repeat(1500);
_applyUserRowIntrinsicHeight(fresh, longText);
const val = fresh.style.containIntrinsicSize; // 'auto <N>px'
const px = parseInt(String(val).replace(/[^0-9]/g,''), 10);
// The same never-measured text NOT opened renders collapsed (HWEB-66).
const collapsed = makeRow('user', 98, 0);
_applyUserRowIntrinsicHeight(collapsed, longText);
console.log(JSON.stringify({ intrinsic: val, px, collapsed: collapsed.style.containIntrinsicSize, cap: USER_MSG_COLLAPSED_ROW_PX }));
"""
    m = json.loads(_run_node(source))
    # 1500 chars / 48 per line -> ~32 lines -> ~728px for the opened row.
    assert m["px"] > 600, (
        "a never-measured opened tall row must reserve an estimate well above 96px "
        f"at build time; got {m['intrinsic']!r}"
    )
    assert m["collapsed"] == f"auto {m['cap']}px", (
        "a never-measured COLLAPSED long row must reserve the collapsed-row height "
        f"(HWEB-66), not the full-text estimate; got {m['collapsed']!r}"
    )


def test_measure_persists_user_row_height_and_writes_inline_intrinsic():
    """_measureMessageVirtualRow, after measuring a user row, must (a) write the
    measured height inline as contain-intrinsic-size on the measured element and
    (b) remember it so the NEXT rebuild of that sessionIdx reserves the real
    height. The buggy version never touched intrinsic-size, so a rebuilt row
    collapsed to 96px."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
eval(extractFunc('_measureMessageVirtualRow'));
// The measured user row lives in the fake inner keyed by data-msg-idx.
const measuredRow = makeRow('user', 7, 3200);
const inner = {
  querySelector(selector){
    if(selector.indexOf('[data-msg-idx="42"]') !== -1) return measuredRow;
    return null;
  }
};
// Measure it (rawIdx 42 maps to sessionIdx 7).
const h = _measureMessageVirtualRow(inner, { rawIdx: 42 });
// (a) the measured element got its real height reserved inline...
const inlineOnMeasured = measuredRow.style.containIntrinsicSize;
// (b) ...and a subsequent rebuild of sessionIdx 7 reserves that same height.
const rebuilt = makeRow('user', 7, 0);
_applyUserRowIntrinsicHeight(rebuilt, 'short');
console.log(JSON.stringify({
  measuredHeight: h,
  inlineOnMeasured: inlineOnMeasured,
  rebuiltIntrinsic: rebuilt.style.containIntrinsicSize,
}));
"""
    m = json.loads(_run_node(source))
    assert m["measuredHeight"] == 3200, f"expected measured height 3200, got {m['measuredHeight']}"
    assert m["inlineOnMeasured"] == "auto 3200px", (
        "measure pass must write the real height inline on the measured user row; "
        f"got {m['inlineOnMeasured']!r} (buggy version wrote nothing)"
    )
    assert m["rebuiltIntrinsic"] == "auto 3200px", (
        "a rebuild after measuring must reserve the remembered 3200px, not 96px; "
        f"got {m['rebuiltIntrinsic']!r}"
    )


def test_measure_ignores_assistant_rows_for_intrinsic_writeback():
    """The intrinsic-size writeback must apply ONLY to user rows (assistant rows
    are content-visibility:visible on mobile per #5638; writing intrinsic-size on
    them would be meaningless and could mask a real regression). Measuring an
    assistant row must NOT write an inline intrinsic-size."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
eval(extractFunc('_measureMessageVirtualRow'));
const assistantRow = makeRow('assistant', 5, 1200);
const inner = {
  querySelector(selector){
    if(selector.indexOf('[data-msg-idx="10"]') !== -1) return assistantRow;
    return null;
  }
};
_measureMessageVirtualRow(inner, { rawIdx: 10 });
console.log(JSON.stringify({ inline: assistantRow.style.containIntrinsicSize }));
"""
    m = json.loads(_run_node(source))
    assert m["inline"] == "", (
        "assistant rows must NOT get an inline intrinsic-size writeback; "
        f"got {m['inline']!r}"
    )


def test_cache_cleared_on_session_switch_prevents_stale_height_bleed():
    """Greptile #5672 review: the module-level height cache is keyed by
    session-relative index (_messageSessionIndexBase()+rawIdx), and the base is 0
    for the common non-offset session, so keys collide across sessions. Without a
    clear on session switch, a new session's off-screen user row at the same key
    inherits the previous session's remembered height and inflates scrollHeight.

    _clearUserRowIntrinsicHeightCache() (wired into _clearMessageVirtualHeightCache,
    which _resetMessageRenderWindow calls on session switch) must empty the cache so
    a rebuilt row at a colliding key falls back to the content estimate, NOT the
    stale remembered height.

    Mutation: make _clearUserRowIntrinsicHeightCache a no-op and this fails (the
    rebuilt row reserves the stale 5000px instead of the ~short estimate)."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
eval(extractFunc('_clearUserRowIntrinsicHeightCache'));
// Session A: a tall user row at session-relative index 3 measured 5000px.
_rememberUserRowIntrinsicHeight(3, 5000);
const beforeClear = makeRow('user', 3, 0);
_applyUserRowIntrinsicHeight(beforeClear, 'x');   // would reserve the remembered 5000
// Session switch clears the cache.
_clearUserRowIntrinsicHeightCache();
// Session B: a SHORT user row at the SAME colliding key 3, never measured here.
const afterClear = makeRow('user', 3, 0);
_applyUserRowIntrinsicHeight(afterClear, 'hi');   // must fall back to the estimate
const estimate = _estimateUserRowIntrinsicHeight('hi');
console.log(JSON.stringify({
  beforeClear: beforeClear.style.containIntrinsicSize,
  afterClear: afterClear.style.containIntrinsicSize,
  estimate: 'auto ' + estimate + 'px',
}));
"""
    m = json.loads(_run_node(source))
    assert m["beforeClear"] == "auto 5000px", (
        "sanity: before the clear, the remembered 5000px must be reserved; "
        f"got {m['beforeClear']!r}"
    )
    assert m["afterClear"] == m["estimate"], (
        "after a session switch clear, a rebuilt row at the colliding key must fall "
        f"back to the content estimate ({m['estimate']}), NOT the stale remembered "
        f"5000px; got {m['afterClear']!r} (buggy: cache not cleared → stale bleed)"
    )
    assert m["afterClear"] != "auto 5000px", "stale height must not survive the clear"



# ── HWEB-66: the reserve follows the disclosure state ──


def test_collapsed_row_keeps_a_taller_remembered_measurement():
    """The #5638 invariant survives the cap: a row must never reserve LESS than a
    real measurement. If a collapsed row measured taller than the flat cap (an
    attachment strip above the clipped text, say), the remembered value still wins
    over the collapsed estimate, so scrollHeight cannot collapse on a rebuild.

    Mutation: replace `Math.max(remembered, estimate)` with the collapsed cap for
    a collapsed row and this fails."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
_rememberUserRowIntrinsicHeight(7, 640);
const row = makeRow('user', 7, 0);              // collapsed: no msgExpanded flag
_applyUserRowIntrinsicHeight(row, 'z'.repeat(10000));
console.log(JSON.stringify({ reserved: row.style.containIntrinsicSize }));
"""
    m = json.loads(_run_node(source))
    assert m["reserved"] == "auto 640px", (
        "a collapsed row measured taller than the cap must keep its real height; "
        f"got {m['reserved']!r}"
    )


def _toggle_prelude() -> str:
    """Fake row + disclosure button for toggleMessageExpand, plus stubs for the
    collaborators it touches that this test does not exercise (the expand store,
    i18n, the session HTML cache)."""
    return r"""
function _setUserMessageExpanded(){}
function t(k){ return k; }
function makeToggleRow(sessionMsgIdx, rawText, expanded){
  const row = makeRow('user', sessionMsgIdx, 0);
  row.dataset.rawText = rawText;
  if(expanded) row.dataset.msgExpanded = '1';
  const btn = {
    attrs: {},
    textContent: '',
    setAttribute(k, v){ this.attrs[k] = v; },
    closest(sel){ return sel === '.msg-row' ? row : null; },
  };
  return { row, btn };
}
"""


def test_toggling_the_disclosure_refreshes_the_reserve():
    """Toggling the disclosure changes the row's real height at that moment, so the
    reserve must follow: collapsing drops to the collapsed cap even though the
    expanded measurement was remembered (max() would otherwise pin the row at the
    stale expanded height on the next rebuild), and expanding again reserves the
    expanded-state measurement. Remembered heights are keyed by disclosure state,
    so the expanded measurement is neither read by the folded row nor lost.

    Mutation: remove the re-apply from toggleMessageExpand and the collapsed row
    keeps the 5000px inline reserve; key the map by index alone and the collapsed
    row reserves the remembered 5000px."""
    js = UI_JS_PATH.read_text(encoding="utf-8")
    source = _extract_func_script(js) + _fake_row_prelude() + _toggle_prelude() + r"""
eval(extractFunc('_rememberUserRowIntrinsicHeight'));
eval(extractFunc('_estimateUserRowIntrinsicHeight'));
eval(extractFunc('_applyUserRowIntrinsicHeight'));
eval(extractFunc('toggleMessageExpand'));
const text = 'q'.repeat(10000);
// The row was measured while open (its real expanded height), then rebuilt open.
_rememberUserRowIntrinsicHeight(7, 5000, true);
const { row, btn } = makeToggleRow(7, text, true);
_applyUserRowIntrinsicHeight(row, text);
const open = row.style.containIntrinsicSize;
toggleMessageExpand(btn);                       // -> collapsed
const collapsed = row.style.containIntrinsicSize;
toggleMessageExpand(btn);                       // -> expanded again
const reopened = row.style.containIntrinsicSize;
// A duplicate prompt sharing the expand identity folds on its next render and
// reads its own folded-state entry: the twin's expanded measurement is not it.
_rememberUserRowIntrinsicHeight(9, 5000, true);
const twin = makeRow('user', 9, 0);
twin.dataset.rawText = text;                     // folded: no msgExpanded flag
_applyUserRowIntrinsicHeight(twin);
console.log(JSON.stringify({
  open, collapsed, reopened,
  twin: twin.style.containIntrinsicSize,
  keys: Object.keys(_userRowIntrinsicHeightBySessionIdx),
  expandedFlag: row.dataset.msgExpanded || '',
  cap: USER_MSG_COLLAPSED_ROW_PX,
}));
"""
    m = json.loads(_run_node(source))
    assert m["open"] == "auto 5000px", f"sanity: open row reserves the measurement; got {m['open']!r}"
    assert m["collapsed"] == f"auto {m['cap']}px", (
        "collapsing must drop the reserve to the collapsed-row height; "
        f"got {m['collapsed']!r} (stale expanded measurement kept?)"
    )
    assert m["reopened"] == "auto 5000px", (
        f"re-expanding must reserve the expanded-state measurement again; got {m['reopened']!r}"
    )
    assert m["twin"] == f"auto {m['cap']}px", (
        f"a folded duplicate must not read its twin's expanded measurement; got {m['twin']!r}"
    )
    assert sorted(m["keys"]) == ["7:x", "9:x"], f"expanded measurements are keyed by state; got {m['keys']}"
    assert m["expandedFlag"] == "1", "sanity: the row attribute flipped back to expanded"


@pytest.mark.parametrize("viewport_width", [320, 390, 700])
def test_collapsed_row_reserve_covers_the_rendered_production_row(viewport_width):
    """Re-justifies USER_MSG_COLLAPSED_ROW_PX and USER_MSG_ATTACHMENT_PX against the
    shipped stylesheet in a real browser, through renderMessages so the row carries
    everything production does: the attachment strip, the clipped body, the
    disclosure button and the action footer (opacity 0 on user rows, but still
    laid out — 40px touch targets under 640px). For a never-painted folded row
    the inline reserve is all content-visibility:auto has, so at the mobile width
    and every font-size setting the reserve must be at least the rendered height
    (under-reserving is the #5638 jump-back), and the plain-text cap must stay
    within 40px of the tallest real row so it does not drift loose. Measured at
    the time of writing at 390px (plain / 1 image / 3 images / 3 file badges):
    small 285/391/493/394, normal 310/416/518/419, large 334/440/542/444,
    xlarge 359/465/567/469; at 700px the sidebar narrows the column so
    thumbnails stack one per row (3 images = 300px strip).

    Both bounds matter (Codex round 2): every variant must also reserve no more
    than its rendered height plus the slack the constants deliberately carry —
    the collapsed cap's font-size headroom (60px at the default size) plus, for
    attachment rows, one thumbnail row for the fail-closed per-row count at
    320px and one badge/gap allowance per attachment for badges that share a
    row. Over-reserving by more than that recreates the paint-time shrink this
    change removes."""
    from tests.test_hweb3_user_message_collapse import _page

    playwright, browser, page = _page(viewport_width)
    try:
        m = page.evaluate(
            """
            (sizes) => {
              const long = 'y'.repeat(10000);
              const variants = {
                plain: [],
                img1: ['a.png'],
                img3: ['a.png', 'b.png', 'c.png'],
                file3: ['notes-long-name-1.txt', 'notes-long-name-2.txt', 'notes-long-name-3.txt'],
                media: ['voice.mp3', 'clip.mp4'],
              };
              const out = { cap: USER_MSG_COLLAPSED_ROW_PX, rows: {} };
              const px = (v) => parseInt(String(v).replace(/[^0-9]/g, ''), 10) || 0;
              sizes.forEach((sz) => {
                document.documentElement.setAttribute('data-font-size', sz);
                for (const [name, attachments] of Object.entries(variants)) {
                  // Start from an empty transcript and cleared caches so the row is
                  // built FRESH (no remembered measurement): the reserve read below
                  // is exactly what a never-painted off-screen row would carry.
                  S.messages = [];
                  renderMessages();
                  window._clearUserMessageExpandState();
                  window._clearMessageVirtualHeightCache();
                  S.messages = [{ role: 'user', content: name + sz + long, attachments },
                                { role: 'assistant', content: 'ok' }];
                  renderMessages();
                  const row = document.querySelector('#msgInner .msg-row[data-role="user"]');
                  out.rows[sz + '/' + name] = {
                    real: row.getBoundingClientRect().height,
                    reserve: px(row.style.containIntrinsicSize),
                    folded: !!row.querySelector('.msg-expand-btn') && row.dataset.msgExpanded !== '1',
                    hasFoot: !!row.querySelector('.msg-foot'),
                    files: row.querySelectorAll('.msg-files > *').length,
                    kinds: row.dataset.attachmentKinds || '',
                  };
                }
              });
              document.documentElement.removeAttribute('data-font-size');
              S.messages = [];
              renderMessages();
              return out;
            }
            """,
            ["small", "normal", "large", "xlarge"],
        )
    finally:
        browser.close()
        playwright.stop()
    tallest_plain = max(r["real"] for k, r in m["rows"].items() if k.endswith("/plain"))
    assert m["cap"] <= tallest_plain + 40, (
        f"USER_MSG_COLLAPSED_ROW_PX={m['cap']} is loose against the tallest real "
        f"folded row ({tallest_plain}px); re-measure and tighten it"
    )
    for key, r in m["rows"].items():
        variant = key.split("/")[1]
        assert r["folded"] and r["hasFoot"], f"{key}: sanity — production folded row with a footer; got {r}"
        assert r["files"] == len(r["kinds"].split(",")) if r["kinds"] else r["files"] == 0, (
            f"{key}: stamped kinds must mirror the rendered strip; got {r}"
        )
        assert r["reserve"] >= r["real"], (
            f"{key}: fresh folded row renders {r['real']}px but reserves only "
            f"{r['reserve']}px — raise USER_MSG_COLLAPSED_ROW_PX / USER_MSG_FILES_PX"
        )
        # The cap's font-size headroom at this size, then the per-kind allowances.
        slack = m["cap"] - m["rows"][key.split("/")[0] + "/plain"]["real"]
        slack += 102 if variant.startswith("img") else 0          # one fail-closed thumbnail row
        slack += 36 * r["files"] if variant in ("file3", "media") else 0  # badges sharing a row / gap
        slack += 170 if variant == "media" else 0                  # video ceiling vs landscape/unloaded
        assert r["reserve"] <= r["real"] + slack + 40, (
            f"{key}: reserve {r['reserve']}px is loose against the rendered {r['real']}px "
            f"(allowed slack {slack + 40}px) — tighten USER_MSG_FILES_PX"
        )
