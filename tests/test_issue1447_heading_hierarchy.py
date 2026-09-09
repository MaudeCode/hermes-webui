"""Markdown heading hierarchy in chat (#1447) — rescaled for chat by HWEB-5.

Issue #1447 (Cygnus, Discord, May 1 2026, relayed by @AvidFuturist):
    "Headings seem to be missing across the board in Hermes. They're there,
     but all plaintext. They get lost so easily in all the plaintext."

The #1447 fix gave headings a document scale (h1 24px, h2 20px, ... ) plus
h1/h2 divider borders and h5/h6 uppercase label styling. HWEB-5 kept the
hierarchy but retuned it for conversation: the scale is now relative to
`--message-body-font-size` (so it tracks the small/large/xlarge preference
without px overrides), and the borders and uppercase transforms are gone
because they read as documentation chrome inside a chat answer.

These tests pin what survived and what deliberately changed:
  - Every heading level is still distinctly larger than the next-deeper level
  - h1..h4 are still visibly above body size; h5/h6 sit at body size
  - h1/h2 no longer carry a divider border; h5/h6 no longer uppercase
  - `.preview-md` (the file/notes preview pane) keeps the document scale — it
    renders documents, not conversation, so it did not follow HWEB-5
"""

from __future__ import annotations

import re
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
CSS = (REPO / "static" / "style.css").read_text(encoding="utf-8")


def _rule(scope: str, level: str) -> str:
    """Return the body of the BARE `<scope> <level>` rule.

    Anchors at the start of a line (after whitespace) so the data-font-size
    overrides like `[data-font-size="small"] .msg-body h1` are not matched.
    """
    pat = re.compile(rf"^\s*{re.escape(scope)}\s+{level}\s*\{{([^}}]*)\}}", re.M)
    m = pat.search(CSS)
    assert m, f"no bare rule found for `{scope} {level}`"
    return m.group(1)


def _em_size(level: str) -> float:
    """Extract the em font-size for the bare `.msg-body <level>` selector."""
    m = re.search(r"font-size:\s*([\d.]+)em", _rule(".msg-body", level))
    assert m, f".msg-body {level} must size in em so it tracks the body token"
    return float(m.group(1))


def _px_size(scope: str, level: str) -> int:
    m = re.search(r"font-size:\s*(\d+)px", _rule(scope, level))
    assert m, f"font-size not found for `{scope} {level}`"
    return int(m.group(1))


# ── Hierarchy: each level larger than the next ───────────────────────────────


def test_msg_body_heading_sizes_form_clear_hierarchy():
    """h1 > h2 > h3 > h4 > h5 >= h6, all relative to the body size token.

    Cygnus's complaint was that headings read as plaintext. h3 in particular
    must stay visibly above body size.
    """
    sizes = {level: _em_size(level) for level in ("h1", "h2", "h3", "h4", "h5", "h6")}

    assert sizes["h1"] > sizes["h2"] > sizes["h3"] > sizes["h4"] > sizes["h5"], (
        f"h1..h5 must strictly decrease, got {sizes}"
    )
    assert sizes["h5"] > sizes["h6"], f"h5 must exceed h6, got {sizes}"
    # h1..h4 stay above body (1em); h3 is Cygnus's specific case.
    for level in ("h1", "h2", "h3", "h4"):
        assert sizes[level] > 1.0, f"{level} ({sizes[level]}em) must be above body size"
    # h5/h6 lean on weight + color rather than size, but must not shrink away.
    assert sizes["h6"] >= 0.9, f"h6 ({sizes['h6']}em) must stay close to body size"


def test_msg_body_h1_and_h2_have_no_divider_border():
    """HWEB-5: automatic horizontal rules under h1/h2 read as documentation."""
    for level in ("h1", "h2"):
        assert "border" not in _rule(".msg-body", level), (
            f".msg-body {level} must not add a divider border in chat"
        )


def test_msg_body_h5_and_h6_are_not_uppercased():
    """HWEB-5: uppercase label styling is documentation chrome, not chat prose."""
    for level in ("h5", "h6"):
        assert "text-transform" not in _rule(".msg-body", level), (
            f".msg-body {level} must not uppercase heading text"
        )


def test_msg_body_headings_use_strong_color_and_bold_weight():
    """All headings must use bold weight (700) and strong color, not light grey."""
    base_match = re.search(
        r"\.msg-body\s+h1,\s*\.msg-body\s+h2,\s*\.msg-body\s+h3,\s*\.msg-body\s+h4,"
        r"\s*\.msg-body\s+h5,\s*\.msg-body\s+h6\s*\{[^}]*font-weight:\s*700",
        CSS,
    )
    assert base_match, "Combined .msg-body h1..h6 selector must set font-weight:700"
    # Color must reference --strong (with --text fallback).
    color_match = re.search(
        r"\.msg-body\s+h1,\s*\.msg-body\s+h2,\s*\.msg-body\s+h3,\s*\.msg-body\s+h4,"
        r"\s*\.msg-body\s+h5,\s*\.msg-body\s+h6\s*\{[^}]*color:\s*var\(--strong",
        CSS,
    )
    assert color_match, "Combined heading selector must use color:var(--strong, ...)"


# ── preview-md: still a document surface ─────────────────────────────────────


def test_preview_md_keeps_the_document_heading_scale():
    """The file/notes preview pane renders documents, so it keeps #1447's scale.

    HWEB-5 deliberately did not tighten `.preview-md`; only conversation prose
    was rescaled. This pins the divergence so it stays a decision, not drift.
    """
    sizes = {
        level: _px_size(".preview-md", level)
        for level in ("h1", "h2", "h3", "h4", "h5", "h6")
    }
    assert sizes == {"h1": 24, "h2": 20, "h3": 17, "h4": 15, "h5": 14, "h6": 13}, sizes


# ── data-font-size: the em scale replaces the px overrides ───────────────────


def test_no_px_heading_overrides_remain_for_the_font_size_preference():
    """Per-preference px heading rules would defeat the relative scale.

    `.msg-body` headings are em-based, so small/large/xlarge scale from the
    single `--message-body-font-size` step; any leftover px override would pin
    a heading to one size across all preferences.
    """
    stale = re.findall(
        r':root\[data-font-size="[^"]+"\]\s*\.msg-body\s+h[1-6]\s*\{[^}]*\}', CSS
    )
    assert not stale, f"stale per-preference heading overrides: {stale}"


def test_specific_heading_sizes_match_the_hweb5_scale():
    """Pin the exact spec'd ratios so unrelated CSS edits don't drift them."""
    assert _em_size("h1") == 1.35
    assert _em_size("h2") == 1.22
    assert _em_size("h3") == 1.12
    assert _em_size("h4") == 1.05
    assert _em_size("h5") == 1
    assert _em_size("h6") == 0.95
