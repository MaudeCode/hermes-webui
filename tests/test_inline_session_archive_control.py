"""HWEB-16: archive/restore is an inline sidebar control, not a ⋯ menu entry."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _function_block(src: str, name: str) -> str:
    marker = f"function {name}("
    start = src.find(marker)
    assert start >= 0, f"{name} not found"
    brace = src.find("{", start)
    assert brace > start, f"{name} body not found"
    depth = 1
    i = brace + 1
    while depth and i < len(src):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"{name} body did not close"
    return src[start:i]


def _build_toggles(sessions):
    """Run _buildSessionArchiveToggle against a stub DOM and click each result."""
    if NODE is None:
        pytest.skip("node not on PATH")
    driver = "\n".join(
        [
            "class El{",
            "  constructor(tag){this.tagName=tag;this.attrs={};this.children=[];this.className='';this.title='';this.innerHTML='';}",
            "  setAttribute(k,v){this.attrs[k]=v;}",
            "  appendChild(c){this.children.push(c);return c;}",
            "}",
            "const document={createElement:(tag)=>new El(tag)};",
            "const ICONS={archive:'ICON_ARCHIVE',unarchive:'ICON_UNARCHIVE'};",
            "const STRINGS={session_archive:'Archive conversation',session_restore:'Restore conversation',",
            "  session_archive_desc:'Hide it',session_archive_worktree_desc:'Hide it, keep the worktree',",
            "  session_restore_desc:'Bring it back'};",
            "function t(k){return STRINGS[k];}",
            "const archiveCalls=[];",
            "const callOrder=[];",
            "function closeSessionActionMenu(){callOrder.push('close');}",
            "async function _archiveSession(session,archived){callOrder.push('archive');archiveCalls.push([session.session_id,archived]);return true;}",
            _function_block(SESSIONS_JS, "_sessionArchiveDescription"),
            _function_block(SESSIONS_JS, "_buildSessionArchiveToggle"),
            "const out=JSON.parse(process.argv[1]).map(session=>{",
            "  const btn=_buildSessionArchiveToggle(session);",
            "  const ev={stopped:0,prevented:0,stopPropagation(){this.stopped++;},preventDefault(){this.prevented++;}};",
            "  btn.onpointerdown(ev);",
            "  btn.onpointerup(ev);",
            "  btn.onclick(ev);",
            "  return {tag:btn.tagName,type:btn.type,className:btn.className,title:btn.title,",
            "    ariaLabel:btn.attrs['aria-label'],icon:btn.innerHTML,",
            "    stopped:ev.stopped,prevented:ev.prevented};",
            "});",
            "process.stdout.write(JSON.stringify({buttons:out,archiveCalls,callOrder}));",
        ]
    )
    result = subprocess.run(
        [NODE, "-e", driver, json.dumps(sessions)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_inline_control_archives_and_restores_through_the_shared_flow():
    out = _build_toggles(
        [
            {"session_id": "s-open", "archived": False},
            {"session_id": "s-archived", "archived": True},
        ]
    )
    archive_btn, restore_btn = out["buttons"]

    assert archive_btn["ariaLabel"] == "Archive conversation"
    assert archive_btn["title"] == "Archive conversation — Hide it"
    assert archive_btn["icon"] == "ICON_ARCHIVE"
    assert restore_btn["ariaLabel"] == "Restore conversation"
    assert restore_btn["title"] == "Restore conversation — Bring it back"
    assert restore_btn["icon"] == "ICON_UNARCHIVE"
    # Both route through _archiveSession, toggling the current state.
    assert out["archiveCalls"] == [["s-open", True], ["s-archived", False]]


def test_inline_control_keeps_the_worktree_retention_wording():
    """The menu entry warned that archiving keeps the worktree; the button must too."""
    out = _build_toggles([{"session_id": "s-wt", "archived": False, "worktree_path": "/tmp/wt"}])

    assert out["buttons"][0]["title"] == "Archive conversation — Hide it, keep the worktree"


def test_inline_control_closes_an_open_menu_before_archiving():
    """stopPropagation blocks the document closer, and a live menu defers the repaint."""
    out = _build_toggles([{"session_id": "s-open", "archived": False}])

    assert out["callOrder"] == ["close", "archive"]


def test_inline_control_never_lets_the_click_reach_the_row():
    """The row itself opens the session, so the button must swallow the event."""
    out = _build_toggles([{"session_id": "s-open", "archived": False}])
    btn = out["buttons"][0]

    # pointerdown + pointerup + click all stop propagation; click also prevents default.
    assert btn["stopped"] == 3
    assert btn["prevented"] == 1
    assert btn["tag"] == "BUTTON" or btn["tag"] == "button"


def test_archive_menu_entry_only_survives_where_the_inline_control_is_hidden():
    """Coarse-pointer devices hide .session-actions, so the menu keeps archive there."""
    menu_body = _function_block(SESSIONS_JS, "_openSessionActionMenu")

    guard = menu_body.find("if(_sessionArchiveNeedsMenuEntry(anchorEl)){")
    assert guard >= 0, "archive menu entry is not gated on the inline control being hidden"
    # The one remaining _archiveSession call in the menu is inside that guard.
    assert menu_body.count("_archiveSession(session,!session.archived)") == 1
    assert menu_body.index("_archiveSession(session,!session.archived)") > guard
    # The separate external-session hide entry is untouched.
    assert "t('session_hide_external')" in menu_body


def _inline_actions_hidden(media_matches):
    """Run _sessionInlineActionsHidden with a stubbed matchMedia."""
    if NODE is None:
        pytest.skip("node not on PATH")
    driver = "\n".join(
        [
            "const cases=JSON.parse(process.argv[1]);",
            _function_block(SESSIONS_JS, "_sessionInlineActionsHidden"),
            "const out=cases.map(c=>{",
            "  globalThis.window = c === null ? {} : {matchMedia:(q)=>({matches: q === '(hover:none) and (pointer:coarse)' && c})};",
            "  return _sessionInlineActionsHidden();",
            "});",
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    result = subprocess.run(
        [NODE, "-e", driver, json.dumps(media_matches)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _needs_menu_entry(cases):
    """Run _sessionArchiveNeedsMenuEntry with a stubbed matchMedia and anchor."""
    if NODE is None:
        pytest.skip("node not on PATH")
    driver = "\n".join(
        [
            "const cases=JSON.parse(process.argv[1]);",
            _function_block(SESSIONS_JS, "_sessionInlineActionsHidden"),
            _function_block(SESSIONS_JS, "_sessionArchiveNeedsMenuEntry"),
            "const out=cases.map(c=>{",
            "  globalThis.window={matchMedia:(q)=>({matches: q==='(hover:none) and (pointer:coarse)' && c.coarse})};",
            "  const anchor = c.anchor===null ? null : {closest:(sel)=>{",
            "    return sel.split(',').some(s=>c.anchor.includes(s.trim())) ? {} : null;",
            "  }};",
            "  return _sessionArchiveNeedsMenuEntry(anchor);",
            "});",
            "process.stdout.write(JSON.stringify(out));",
        ]
    )
    result = subprocess.run(
        [NODE, "-e", driver, json.dumps(cases)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_menu_keeps_archive_for_every_caller_without_an_inline_control():
    """panels.js long-presses the titlebar on any touch device, fine pointer included."""
    sidebar_row = [".session-item"]
    fork_child = [".session-child-session-fork"]
    titlebar = []  # #chatTitle is in no sidebar row

    fine_sidebar, fine_fork, fine_titlebar, coarse_sidebar, no_anchor = _needs_menu_entry(
        [
            {"coarse": False, "anchor": sidebar_row},
            {"coarse": False, "anchor": fork_child},
            {"coarse": False, "anchor": titlebar},
            {"coarse": True, "anchor": sidebar_row},
            {"coarse": False, "anchor": None},
        ]
    )

    # Sidebar rows on a fine pointer have the inline control right there.
    assert fine_sidebar is False
    assert fine_fork is False
    # A hybrid laptop (fine pointer + touchscreen) still long-presses the titlebar.
    assert fine_titlebar is True
    # Coarse pointer hides the cluster, so even a sidebar row needs the entry.
    assert coarse_sidebar is True
    # Fail closed on an unrecognised caller.
    assert no_anchor is True


def test_inline_actions_are_reported_hidden_only_on_coarse_pointer_devices():
    coarse, fine, no_match_media = _inline_actions_hidden([True, False, None])

    assert coarse is True
    assert fine is False
    # Fail closed: an unknown environment keeps the menu entry rather than
    # leaving swipe as the only way to archive.
    assert no_match_media is True


def test_both_editable_row_builders_mount_the_inline_control():
    assert SESSIONS_JS.count("actions.appendChild(_buildSessionArchiveToggle(") == 2
    # Always immediately before the ⋯ trigger, inside the same .session-actions cluster.
    for marker in ("_buildSessionArchiveToggle(child)", "_buildSessionArchiveToggle(s)"):
        idx = SESSIONS_JS.index(marker)
        assert "actions.appendChild(menuBtn);" in SESSIONS_JS[idx : idx + 200]


def test_row_reserves_room_for_two_controls_when_the_cluster_is_visible():
    """40px fit one 26px button; two need ~58px, or the title runs under them."""
    assert ".session-item:focus-within,.session-item.menu-open{padding-right:64px;}" in STYLE_CSS
    assert ".session-item:hover{padding-right:64px;}" in STYLE_CSS
    assert ".session-item{min-height:44px;padding:10px 64px 10px 12px;}" in STYLE_CSS
    # The inline control shares the ⋯ trigger's chrome instead of redefining it.
    assert ".session-actions-trigger,.session-archive-toggle{width:26px;height:26px;" in STYLE_CSS


def test_every_skin_active_row_reserves_the_same_cluster_width():
    """Skin overrides are higher-specificity, so a stale 40px there wins on active rows."""
    for skin in ("graphite", "codex", "terracotta", "github"):
        cluster = (
            f'  :root[data-skin="{skin}"] .session-item.active:focus-within,\n'
            f'  :root[data-skin="{skin}"] .session-item.active.menu-open{{padding-right:64px;}}'
        )
        assert cluster in STYLE_CSS, f"{skin} focus/menu-open reservation not widened"
        hover = (
            f'@media (hover:hover){{:root[data-skin="{skin}"] '
            f'.session-item.active:hover{{padding-right:64px;}}}}'
        )
        assert hover in STYLE_CSS, f"{skin} hover reservation not widened"
        # The attention-indicator-only states legitimately stay at 40px.
        attention = (
            f'  :root[data-skin="{skin}"] .session-item.active.needs-attention'
            f'{{padding-right:40px;}}'
        )
        assert attention in STYLE_CSS, f"{skin} attention reservation changed unexpectedly"


def _measured_padding_right(skin, width, classes, coarse=False):
    """Measure the real cascade: load style.css in Chromium and read the row."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright not installed")
    # The viewport meta matters: without it a mobile context lays out at 980px
    # and the max-width:640px block never matches.
    html = f"""<!doctype html><html data-skin="{skin}" class="dark"><head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>{STYLE_CSS}</style></head>
    <body><div class="sidebar"><div class="session-list">
    <div class="session-item {classes}" id="row"><div class="session-text">
    <div class="session-title-row"><span class="session-title">A conversation title</span>
    <span class="session-time">11h</span></div></div>
    <div class="session-actions"><button class="session-archive-toggle"></button>
    <button class="session-actions-trigger"></button></div></div>
    </div></div></body></html>"""
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch()
        except Exception:
            pytest.skip("chromium not available")
        # has_touch + is_mobile is what actually flips (hover:none) and
        # (pointer:coarse) in Chromium; setEmulatedMedia does not carry them.
        context = browser.new_context(
            viewport={"width": width, "height": 600},
            has_touch=coarse,
            is_mobile=coarse,
        )
        page = context.new_page()
        page.set_content(html)
        page.wait_for_timeout(150)
        value = page.evaluate(
            "getComputedStyle(document.getElementById('row')).paddingRight"
        )
        browser.close()
    return value


@pytest.mark.parametrize("skin", ["graphite", "codex", "terracotta", "github"])
@pytest.mark.parametrize("classes", ["active", "active streaming", "active unread"])
def test_narrow_skinned_active_row_reserves_the_cluster_width(skin, classes):
    """Skin rules outrank the ≤640px reservation, and the cluster is always visible there."""
    assert _measured_padding_right(skin, 480, classes) == "64px"


@pytest.mark.parametrize("skin", ["graphite", "codex", "terracotta", "github"])
@pytest.mark.parametrize(
    "classes,expected", [("active", "12px"), ("active streaming", "40px")]
)
def test_coarse_pointer_skinned_active_row_keeps_its_own_gutter(skin, classes, expected):
    """The cluster is display:none on touch, so it must reserve nothing there."""
    assert _measured_padding_right(skin, 480, classes, coarse=True) == expected
