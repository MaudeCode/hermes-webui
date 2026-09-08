"""HWEB-4: assistant turns stop repeating avatar/name chrome.

The header markup and the footer action markup are both built as template
strings inside renderMessages, so these tests evaluate the real slices in node
and assert on the produced HTML rather than on source text.
"""

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.resolve()
UI_JS_PATH = REPO_ROOT / "static" / "ui.js"
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")
I18N_JS = (REPO_ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
ICONS_JS = (REPO_ROOT / "static" / "icons.js").read_text(encoding="utf-8")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not on PATH")


def _run_node(source: str) -> dict:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".cjs", encoding="utf-8", dir=REPO_ROOT, delete=False
    ) as script:
        script.write(source)
        script_path = Path(script.name)
    try:
        result = subprocess.run(
            [NODE, str(script_path)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
        )
    finally:
        script_path.unlink(missing_ok=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr)
    return json.loads(result.stdout.strip())


def _prefix() -> str:
    return f"""
const fs = require('fs');
const src = fs.readFileSync({json.dumps(str(UI_JS_PATH))}, 'utf8');
function extractBetween(startMarker, endMarker, startFrom = 0) {{
  const start = src.indexOf(startMarker, startFrom);
  if (start < 0) throw new Error(startMarker + ' not found');
  const end = src.indexOf(endMarker, start);
  if (end < 0) throw new Error(endMarker + ' not found');
  return src.slice(start, end);
}}
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const li = (name, size) => `<svg data-icon="${{name}}" width="${{size}}"></svg>`;
"""


def _role_html(tps_enabled: bool, tps_text: str) -> str:
    script = _prefix() + f"""
const roleFn = extractBetween("function _assistantRoleHtml(", "function _closeMessageActionMenus(");
const assistantDisplayName = () => 'Hermes';
const isTpsDisplayEnabled = () => {json.dumps(tps_enabled)};
eval(roleFn.slice(0, roleFn.lastIndexOf('}}') + 1));
console.log(JSON.stringify({{html: _assistantRoleHtml('Mon 3:04 PM', {json.dumps(tps_text)})}}));
"""
    return _run_node(script)["html"]


def test_assistant_header_has_no_avatar_and_only_a_screen_reader_name():
    html = _role_html(False, "")
    assert "role-icon" not in html, "the repeated assistant avatar must be gone"
    # The name is still emitted so every assistant turn keeps an explicit role
    # announcement; CSS is what hides it from sighted readers.
    assert '<span class="msg-role-name">Hermes</span>' in html
    assert "msg-tps-inline" not in html


def test_live_turn_keeps_its_tps_chip_in_the_header():
    # Only the streaming turn still stamps a header chip — a settled turn's TPS
    # is rendered in the metadata footer instead.
    html = _role_html(True, "42.0 t/s")
    assert 'class="msg-tps-inline"' in html
    assert "42.0 t/s" in html
    assert _role_html(False, "42.0 t/s").count("msg-tps-inline") == 0


def _foot_html(is_user: bool, is_last_assistant: bool) -> str:
    script = _prefix() + f"""
const footChunk = extractBetween("    // HWEB-4: assistant rows keep Copy", "\\n\\n    if(_isContextCompactionMessage(m)){{");
const t = key => key;
const isUser = {json.dumps(is_user)};
const rawIdx = 3;
const isEditableUser = isUser;
const isLastAssistant = {json.dumps(is_last_assistant)};
const timeHtml = '<span class="msg-time">3:04 PM</span>';
const questionJumpBtn = '';
const editBtn = isEditableUser ? '<button class="msg-action-btn" data-a="edit"></button>' : '';
const undoBtn = isLastAssistant ? '<button class="msg-action-btn msg-more-item" data-a="undo"></button>' : '';
const retryBtn = isLastAssistant ? '<button class="msg-action-btn msg-more-item" data-a="retry"></button>' : '';
const copyBtn = '<button class="msg-copy-btn msg-action-btn" data-a="copy"></button>';
const forkBtn = `<button class="msg-action-btn${{isUser?'':' msg-more-item'}}" data-a="fork"></button>`;
const ttsBtn = !isUser ? '<button class="msg-action-btn msg-tts-btn msg-more-item" data-a="tts"></button>' : '';
eval(footChunk.replace(/\\bconst /g, 'var '));
console.log(JSON.stringify({{html: footHtml}}));
"""
    return _run_node(script)["html"]


def _inline_actions(html: str) -> list:
    """Actions rendered directly in the footer, i.e. outside the overflow menu."""
    without_menu = re.sub(r'<div class="msg-more-menu">.*?</div>', "", html, flags=re.S)
    return re.findall(r'data-a="([a-z]+)"', without_menu)


def _menu_actions(html: str) -> list:
    menu = re.search(r'<div class="msg-more-menu">(.*?)</div>', html, flags=re.S)
    return re.findall(r'data-a="([a-z]+)"', menu.group(1)) if menu else []


def test_terminal_assistant_response_keeps_copy_inline_and_folds_the_rest():
    html = _foot_html(is_user=False, is_last_assistant=True)
    assert _inline_actions(html) == ["copy"], "only Copy stays on the response itself"
    assert _menu_actions(html) == ["tts", "fork", "retry", "undo"]
    assert '<details class="msg-more">' in html
    assert 'aria-label="more_actions"' in html


def test_non_terminal_assistant_response_still_offers_listen_and_fork():
    html = _foot_html(is_user=False, is_last_assistant=False)
    assert _inline_actions(html) == ["copy"]
    # Retry/undo only apply to the last exchange; the overflow shrinks with them.
    assert _menu_actions(html) == ["tts", "fork"]


def test_user_rows_keep_their_inline_controls_and_never_grow_an_overflow():
    html = _foot_html(is_user=True, is_last_assistant=False)
    assert _inline_actions(html) == ["edit", "fork", "copy"]
    assert "msg-more" not in html


def test_settled_tps_renders_in_the_metadata_footer():
    ui_js = UI_JS_PATH.read_text(encoding="utf-8")
    # The settled metadata pass must build (and de-duplicate on) a TPS chip.
    assert "const tpsText=isTpsDisplayEnabled()?_formatTurnTps(msg._turnTps):'';" in ui_js
    assert "tps.className='msg-tps-inline';" in ui_js
    assert ".msg-used-model-inline,.msg-tps-inline')) continue;" in ui_js
    # ...and the settled header must no longer be handed a chip to render.
    assert "_createAssistantTurn(tsTitle, '')" in ui_js
    assert "_assistantRoleHtml(tsTitle, '')" in ui_js


def test_css_hides_the_assistant_name_but_keeps_the_transparent_collapse_tag():
    assert ".msg-role.assistant .msg-role-name {" in STYLE_CSS
    assert "role-icon" not in STYLE_CSS, "avatar styling should be deleted, not merely unused"
    reveal = '.assistant-turn[data-transparent-turn-toggle-bound="1"] .msg-role.assistant .msg-role-name {'
    assert reveal in STYLE_CSS, "transparent stream needs its clickable name tag back"
    # An open menu must survive the footer's hover-only opacity.
    assert ".msg-foot:has(.msg-more[open]) { opacity: 1; }" in STYLE_CSS
    assert ".msg-more-menu .msg-more-item{min-height:44px;" in STYLE_CSS


def test_overflow_label_is_translated_in_every_locale():
    listen = I18N_JS.count("tts_listen:")
    assert I18N_JS.count("more_actions:") == listen, (
        "the overflow label must ship in every locale that ships tts_listen"
    )
    assert "'more-horizontal'" in ICONS_JS


def test_overflow_menu_never_unconditionally_shows_the_listen_row():
    """Listen stays gated on body.tts-enabled inside the overflow.

    A blanket `display:flex` on the menu row outranks `.msg-tts-btn{display:none}`
    and leaks the control with TTS switched off, and so does the mobile
    `.msg-action-btn` rule, which is later in the sheet and equally specific to
    the bare gate — hence the menu-scoped restatement of both halves.
    """
    assert ".msg-more-menu .msg-more-item{display:" not in STYLE_CSS
    assert ".msg-more-menu .msg-tts-btn{display:none;}" in STYLE_CSS
    assert "body.tts-enabled .msg-more-menu .msg-tts-btn{display:flex;}" in STYLE_CSS
