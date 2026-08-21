"""Reasoning-title contract shared by local and Gateway-backed chat."""

from pathlib import Path

from api.reasoning_titles import normalize_reasoning_titles, reasoning_event_payload
from api.gateway_chat import _gateway_tool_progress_event
from api.routes import _anchor_scene_thinking_row


ROOT = Path(__file__).resolve().parents[1]


def test_explicit_titles_override_derived_titles():
    assert normalize_reasoning_titles(
        "**Derived title**\nMore detail",
        explicit_titles=["Gateway title", "Gateway title", "Second title"],
        stable=True,
    ) == ["Gateway title", "Second title"]


def test_complete_bold_titles_are_ordered_and_deduplicated_while_streaming():
    text = "**Planning implementation**\nnotes\n**Running tests**\n**Planning implementation**"
    assert normalize_reasoning_titles(text) == ["Planning implementation", "Running tests"]


def test_plain_title_waits_for_a_stable_boundary():
    partial = "Planning temporary script implementation"
    assert normalize_reasoning_titles(partial) == []
    assert normalize_reasoning_titles(partial, stable=True) == [partial]
    assert normalize_reasoning_titles(partial + "\nmore detail") == [partial]


def test_unsafe_or_unusable_lines_do_not_become_titles():
    rejected = [
        "```python\nprint('no')\n```",
        "$ rm -rf ./tmp",
        '{"cmd":"git status"}',
        "<tool_call><arg>secret</arg></tool_call>",
        "x" * 500,
        "   ",
    ]
    for text in rejected:
        assert normalize_reasoning_titles(text, stable=True) == []


def test_reasoning_event_payload_keeps_legacy_text_and_adds_optional_snapshot():
    assert reasoning_event_payload("delta", "unfinished") == {"text": "delta"}
    assert reasoning_event_payload(
        "\n",
        "Planning temporary script implementation\n",
    ) == {
        "text": "\n",
        "titles": ["Planning temporary script implementation"],
    }


def test_gateway_bridge_accepts_future_explicit_titles():
    assert _gateway_tool_progress_event({
        "event": "reasoning.available",
        "text": "**Derived title**",
        "titles": ["Gateway title"],
    }) == ("reasoning", {"text": "**Derived title**", "titles": ["Gateway title"]})


def test_persisted_thinking_row_carries_titles_additively():
    row = _anchor_scene_thinking_row(
        "**Planning implementation**\nDetails",
        0,
        1,
        "stream-1",
    )
    assert row["thinking"]["titles"] == ["Planning implementation"]
    assert row["payload"]["titles"] == ["Planning implementation"]


def test_browser_consumes_titles_without_reimplementing_the_parser():
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    anchors = (ROOT / "static" / "assistant_turn_anchors.js").read_text(encoding="utf-8")
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "Array.isArray(d.titles)" in messages
    assert "titles:Object.freeze(titles)" in anchors
    assert "function _applyReasoningTitles" in ui
    assert "1500" in ui
    assert "normalize_reasoning_titles" not in messages + anchors + ui
