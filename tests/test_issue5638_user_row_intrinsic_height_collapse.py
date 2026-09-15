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


# ── HWEB-66: the reserve follows the disclosure state ──


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
    thumbnails stack one per row (3 images = 300px strip); twelve short badges
    pack four to a row at 390px (3 rows, 99px strip).

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
                short12: Array.from({length: 12}, (_, i) => 'f' + (i + 1) + '.txt'),
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
        slack += 36 * 2 if variant in ("file3", "short12") else 0  # badge width / 80%-column rounding: two rows
        slack += 170 + 72 if variant == "media" else 0             # video ceiling vs landscape/unloaded + player chrome font variance
        assert r["reserve"] <= r["real"] + slack + 40, (
            f"{key}: reserve {r['reserve']}px is loose against the rendered {r['real']}px "
            f"(allowed slack {slack + 40}px) — tighten USER_MSG_FILES_PX"
        )


def test_reserve_follows_the_transcript_column_width():
    """HWEB-66 (Codex round-4): the thumbnails-per-row figure is read from the
    transcript column at render time, but rotation and panel changes resize the
    column without a re-render. A ResizeObserver on #msgInner re-applies every
    user row's reserve, so an unseen three-image row rendered in a wide column
    (one thumbnail per row at 700px, where the sidebar narrows the column) drops
    to the two-per-row figure when the column widens to a phone layout, and
    grows back when it narrows again — the direction that would otherwise
    under-reserve and shift scrollHeight on first paint."""
    from tests.test_hweb3_user_message_collapse import _page

    render = """
    () => {
      S.messages = [];
      renderMessages();
      window._clearUserMessageExpandState();
      window._clearMessageVirtualHeightCache();
      S.messages = [{ role: 'user', content: 'resize' + 'y'.repeat(10000), attachments: ['a.png', 'b.png', 'c.png'] },
                    { role: 'assistant', content: 'ok' }];
      renderMessages();
      return document.querySelector('#msgInner .msg-row[data-role="user"]').style.containIntrinsicSize;
    }
    """
    read = "() => document.querySelector('#msgInner .msg-row[data-role=\"user\"]').style.containIntrinsicSize"
    px = lambda v: int("".join(ch for ch in str(v) if ch.isdigit()))

    playwright, browser, page = _page(700)
    try:
        wide = px(page.evaluate(render))
        page.set_viewport_size({"width": 390, "height": 700})
        page.wait_for_function(f"() => {read.split('=> ')[1]} !== 'auto {wide}px'", timeout=5000)
        phone = px(page.evaluate(read))
        page.set_viewport_size({"width": 700, "height": 700})
        page.wait_for_function(f"() => {read.split('=> ')[1]} !== 'auto {phone}px'", timeout=5000)
        back = px(page.evaluate(read))
    finally:
        browser.close()
        playwright.stop()
    assert wide - phone == 102, (
        f"widening the column to two thumbnails per row must drop one image row; got {wide} -> {phone}"
    )
    assert back == wide, f"narrowing again must restore the one-per-row reserve; got {back} (was {wide})"
