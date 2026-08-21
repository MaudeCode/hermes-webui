"""Reasoning-title contract shared by local and Gateway-backed chat."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

import api.reasoning_titles as reasoning_titles

from api.reasoning_titles import normalize_reasoning_titles, reasoning_event_payload
from api.gateway_chat import _gateway_tool_progress_event
from api.routes import _anchor_scene_thinking_row
from api.streaming import _build_partial_message


ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")


def _js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start:index + 1]
    raise AssertionError(f"function {name} body not found")


def test_explicit_titles_override_derived_titles():
    assert normalize_reasoning_titles(
        "**Derived title**\nMore detail",
        explicit_titles=["Gateway title", "Gateway title", "Second title"],
        stable=True,
    ) == ["Gateway title", "Second title"]


def test_explicit_empty_titles_clear_the_snapshot_without_deriving_fallback():
    assert normalize_reasoning_titles(
        "Derived fallback",
        explicit_titles=[],
        stable=True,
    ) == []
    assert reasoning_event_payload(
        "later delta",
        "Derived fallback\nlater delta",
        explicit_titles=[],
    ) == {"text": "later delta", "titles": []}
    partial = _build_partial_message("", "Derived fallback", [], [])
    assert partial["reasoning_titles"] == []


def test_complete_bold_titles_are_ordered_and_deduplicated_while_streaming():
    text = "**Planning implementation**\nnotes\n**Running tests**\n**Planning implementation**"
    assert normalize_reasoning_titles(text) == ["Planning implementation", "Running tests"]


def test_plain_title_waits_for_a_stable_boundary():
    partial = "Planning temporary script implementation"
    assert normalize_reasoning_titles(partial) == []
    assert normalize_reasoning_titles(partial, stable=True) == [partial]
    assert normalize_reasoning_titles(partial + "\nmore detail") == [partial]


def test_title_length_is_bounded_for_unbroken_text():
    title = normalize_reasoning_titles("x" * 81, stable=True)
    assert title == ["x" * 80]


@pytest.mark.parametrize(
    "unsafe",
    [
        "My password is swordfish.",
        "SSN 123-45-6789 belongs to the user.",
        "Authorization: Bearer abcdefghijklmnopqrstuvwxyz",
        "Private tool output: customer email user@example.com",
    ],
)
def test_sensitive_reasoning_never_becomes_a_derived_or_explicit_title(unsafe):
    assert normalize_reasoning_titles(unsafe, stable=True) == []
    assert normalize_reasoning_titles("", explicit_titles=[unsafe]) == []


def test_unsafe_or_unusable_lines_do_not_become_titles():
    rejected = [
        "```python\nprint('no')\n```",
        "$ rm -rf ./tmp",
        '{"cmd":"git status"}',
        "<tool_call><arg>secret</arg></tool_call>",
        "x" * 500,
        "   ",
        "npm test -- --runInBand",
        "./scripts/test.sh tests/test_reasoning_titles.py",
        "API_KEY=secret pytest -q",
    ]
    for text in rejected:
        assert normalize_reasoning_titles(text, stable=True) == []


def test_bold_commands_inside_code_fences_are_not_titles():
    text = """```bash
**npm test -- --runInBand**
```
"""
    assert normalize_reasoning_titles(text, stable=True) == []


def test_reasoning_event_payload_keeps_legacy_text_and_adds_optional_snapshot():
    assert reasoning_event_payload("delta", "unfinished") == {"text": "delta"}
    assert reasoning_event_payload(
        "\n",
        "Planning temporary script implementation\n",
        stable=True,
    ) == {
        "text": "\n",
        "titles": ["Planning temporary script implementation"],
    }


def test_unstable_reasoning_payload_derivation_is_incremental(monkeypatch):
    observed_lengths = []

    def fake_normalize(text, **_kwargs):
        observed_lengths.append(len(text))
        return []

    monkeypatch.setattr(reasoning_titles, "normalize_reasoning_titles", fake_normalize)
    cumulative = "x" * 50_000
    for _ in range(50_000):
        reasoning_titles.reasoning_event_payload("x", cumulative)

    assert sum(observed_lengths) == 50_000


def test_gateway_bridge_accepts_future_explicit_titles():
    assert _gateway_tool_progress_event({
        "event": "reasoning.available",
        "text": "**Derived title**",
        "titles": ["Gateway title"],
    }) == ("reasoning", {"text": "**Derived title**", "titles": ["Gateway title"]})
    assert _gateway_tool_progress_event({
        "event": "reasoning.available",
        "titles": [],
    }) == ("reasoning", {"text": "", "titles": []})


def test_persisted_thinking_row_carries_titles_additively():
    row = _anchor_scene_thinking_row(
        "**Planning implementation**\nDetails",
        0,
        1,
        "stream-1",
    )
    assert row["thinking"]["titles"] == ["Planning implementation"]
    assert row["payload"]["titles"] == ["Planning implementation"]

    title_only = _anchor_scene_thinking_row(
        "",
        0,
        1,
        "stream-1",
        ["Gateway title"],
    )
    assert title_only["text"] == ""
    assert title_only["thinking"]["titles"] == ["Gateway title"]

    explicit_clear = _anchor_scene_thinking_row(
        "Fallback title",
        0,
        1,
        "stream-1",
        [],
        titles_present=True,
    )
    assert explicit_clear["thinking"]["titles"] == []
    assert explicit_clear["payload"]["titles"] == []


@pytest.mark.skipif(NODE is None, reason="node required")
def test_reasoning_body_removes_all_promoted_headings_without_becoming_empty():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    function = _js_function(ui, "_reasoningBodyTextForDisplay")
    long_heading = "Long heading " + "detail " * 20
    long_title = normalize_reasoning_titles(long_heading, stable=True)[0]
    script = f"""
const _sanitizeThinkingDisplayText = value => String(value || '').trim();
const _reasoningTitleList = value => Array.isArray(value) ? value : [];
{function}
const out = {{
  multiple: _reasoningBodyTextForDisplay(
    '**Planning**\\nplan detail\\n**Executing**\\nexecution detail',
    ['Planning', 'Executing']
  ),
      long: _reasoningBodyTextForDisplay(
        '**' + 'Long heading ' + 'detail '.repeat(20) + '**\\nbody detail',
        [{json.dumps(long_title)}]
  ),
  single: _reasoningBodyTextForDisplay('Only title', ['Only title']),
}};
console.log(JSON.stringify(out));
"""
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    out = json.loads(result.stdout)
    assert out["multiple"] == "plan detail\nexecution detail"
    assert out["long"] == "body detail"
    assert out["single"] == "Only title"


def test_browser_consumes_titles_without_reimplementing_the_parser():
    messages = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
    anchors = (ROOT / "static" / "assistant_turn_anchors.js").read_text(encoding="utf-8")
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "Array.isArray(d.titles)" in messages
    assert "const hasTitles=Array.isArray(d.titles);" in messages
    assert "if(hasTitles)" in messages
    assert "titles:Object.freeze(titles)" in anchors
    assert "function _applyReasoningTitles" in ui
    assert "1500" in ui
    assert "normalize_reasoning_titles" not in messages + anchors + ui


def test_tool_disclosures_are_semantic_and_count_free_in_every_locale():
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    i18n = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")
    build_tool = ui.split("function buildToolCard(tc)", 1)[1].split("function _colorDiffLines", 1)[0]
    summary = ui.split("function _toolWorklogSummary(toolCalls, opts)", 1)[1].split("function _toolWorklogListEl", 1)[0]

    assert "<button" in build_tool and "aria-expanded" in build_tool
    assert "_toggleToolCardDisclosure(this)" in build_tool
    assert "1 failed" not in summary
    assert "_I18N_TOOL_SUMMARY_TEXT_RU_COUNT_FREE" in i18n
    assert ".tool-card-header" in css and "min-height:44px" in css
