"""HWEB-5 — chat markdown reads as conversation, not as documentation.

Assistant answers used a document typography scale: 1.75 line height, 10px
paragraph gaps, and heading margins up to 24px. HWEB-5 replaced that with a
single conversation rhythm anchored on the existing size token:

  - `--message-body-line-height` defaults to 1.55
  - every prose block (paragraph, list, blockquote, heading) shares a 0.65em gap
  - the first and last block carry no outer margin
  - built-in skins may retune color and weight but not the size/spacing scale

`--message-body-font-size` stays the single size authority, so the Small /
Large / Extra Large preferences keep scaling the whole system with one step.
"""

from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")

SKINS_WITH_PROSE_RULES = ("graphite", "codex", "terracotta", "github")


def test_default_body_size_and_line_height_tokens():
    """Body prose stays ~14px; the tall document line height drops to 1.55."""
    assert "--message-body-font-size:14px;--message-body-line-height:1.55;" in CSS


def test_message_body_consumes_both_tokens():
    """`.msg-body` must read size and line height from the tokens, not literals."""
    assert (
        ".msg-body{font-family:var(--font-conversation);"
        "font-size:var(--message-body-font-size);"
        "line-height:var(--message-body-line-height);" in CSS
    )


def test_prose_blocks_share_one_rhythm():
    """Paragraphs, lists, blockquotes and headings all use the same 0.65em gap."""
    m = re.search(
        r"^\s*(\.msg-body p,[^{]*)\{margin-block:\.65em;\}", CSS, re.M
    )
    assert m, "no shared .65em block-rhythm rule found for .msg-body prose blocks"
    selector = m.group(1)
    for element in ("p", "ul", "ol", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"):
        assert f".msg-body {element}," in selector or selector.rstrip(",\n ").endswith(
            f".msg-body {element}"
        ), f"{element} is missing from the shared block rhythm: {selector!r}"


def test_blockquote_and_list_margins_are_not_respecified():
    """A second spacing scale would defeat the shared rhythm."""
    blockquote = re.search(r"^\s*\.msg-body blockquote\{([^}]*)\}", CSS, re.M)
    assert blockquote, ".msg-body blockquote rule missing"
    assert "margin" not in blockquote.group(1), (
        "blockquote must inherit the shared rhythm, not set its own margin"
    )
    # Lists keep only their indent; vertical spacing comes from the shared rule.
    assert ".msg-body ul,.msg-body ol{margin-left:20px;}" in CSS


def test_first_and_last_block_have_no_outer_margin():
    """A message must not carry empty space above or below its content."""
    assert ".msg-body > :first-child{margin-top:0;}" in CSS
    # A loose list wraps its items in <p>; without this the last item keeps a
    # trailing gap inside its own bullet.
    assert ".msg-body > :last-child,.msg-body li > :last-child{margin-bottom:0;}" in CSS


def test_block_rhythm_is_relative_so_font_size_preferences_scale():
    """No px gap may be reintroduced onto the shared prose blocks.

    Fixed px gaps would stay put while the body text grows or shrinks with the
    Small / Large / Extra Large preference.
    """
    for element in ("p", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6"):
        rule = re.search(rf"^\s*\.msg-body {element}\{{([^}}]*)\}}", CSS, re.M)
        if not rule:
            continue
        assert not re.search(r"margin[^:]*:\s*[^;]*\d+px", rule.group(1)), (
            f".msg-body {element} must not set a px margin: {rule.group(1)!r}"
        )


def test_builtin_skins_do_not_carry_their_own_prose_scale():
    """Skins may repaint prose, but size and spacing stay on the tokens."""
    for skin in SKINS_WITH_PROSE_RULES:
        rule = re.search(
            rf':root\[data-skin="{skin}"\] \.msg-body\{{([^}}]*)\}}', CSS
        )
        assert rule, f"{skin} .msg-body rule missing"
        body = rule.group(1)
        assert "font-size" not in body, f"{skin} must not override the prose size"
        assert "line-height" not in body, f"{skin} must not override the prose rhythm"
        assert "font-family:var(--font-conversation)" in body, (
            f"{skin} prose must stay on the conversation font token"
        )
        assert not re.search(
            rf':root\[data-skin="{skin}"\]\[data-font-size="[^"]+"\] \.msg-body', CSS
        ), f"{skin} must not keep a per-preference prose scale"
