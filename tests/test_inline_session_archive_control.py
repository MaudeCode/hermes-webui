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
            "async function _archiveSession(session,archived){archiveCalls.push([session.session_id,archived]);return true;}",
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
            "process.stdout.write(JSON.stringify({buttons:out,archiveCalls}));",
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


def test_inline_control_never_lets_the_click_reach_the_row():
    """The row itself opens the session, so the button must swallow the event."""
    out = _build_toggles([{"session_id": "s-open", "archived": False}])
    btn = out["buttons"][0]

    # pointerdown + pointerup + click all stop propagation; click also prevents default.
    assert btn["stopped"] == 3
    assert btn["prevented"] == 1
    assert btn["tag"] == "BUTTON" or btn["tag"] == "button"


def test_archive_is_no_longer_an_action_menu_entry():
    menu_body = _function_block(SESSIONS_JS, "_openSessionActionMenu")

    assert "_archiveSession" not in menu_body
    assert "t('session_archive')" not in menu_body
    assert "t('session_restore')" not in menu_body
    # The separate external-session hide entry is untouched.
    assert "t('session_hide_external')" in menu_body


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
