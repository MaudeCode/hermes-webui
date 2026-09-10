"""HWEB-60: per-delegation cost on subagent delegation cards.

``delegate_task`` returns ``{"results": [{..., "cost_usd": <float>}, ...]}``.
``cost_usd`` sits at the END of each entry, behind the summary and the tool
trace, so the 4000-char ``snippet`` the tool card is built from truncates it
away on any real delegation. The cost is therefore read server-side, while the
whole result is still in hand, and carried to the card as its own field.

Read-only slice only: no control plane, no write path, no ownership change.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from api.streaming import (
    _TOOL_RESULT_SNIPPET_MAX,
    _delegation_cost_usd,
    _extract_tool_calls_from_messages,
    _tool_result_snippet,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
UI_JS_PATH = REPO_ROOT / "static" / "ui.js"
AGENT_DELEGATE_TOOL = Path.home() / ".hermes" / "hermes-agent" / "tools" / "delegate_tool.py"

NODE = shutil.which("node")


def _delegate_result(*costs, trace_entries: int = 0) -> str:
    """A delegate_task result shaped like the agent's, one entry per cost.

    ``trace_entries`` pads ``tool_trace`` the way a long-running child does —
    that padding is what pushes ``cost_usd`` past the snippet cap.
    """
    results = []
    for index, cost in enumerate(costs):
        entry = {
            "task_index": index,
            "status": "completed",
            "summary": f"child {index} finished",
            "api_calls": 12,
            "duration_seconds": 41.5,
            "model": "claude-opus-5",
            "exit_reason": "completed",
            "truncated": False,
            "tokens": {"input": 90000, "output": 4200},
            "tool_trace": [
                {
                    "tool": "read_file",
                    "args_bytes": 120,
                    "input_summary": f"path=/repo/src/module_{n}.py",
                    "result_bytes": 4096,
                    "status": "ok",
                }
                for n in range(trace_entries)
            ],
        }
        if cost is not None:
            entry["cost_usd"] = cost
            entry["cost_status"] = "estimated"
        results.append(entry)
    return json.dumps({"results": results, "total_duration_seconds": 41.5}, ensure_ascii=False)


# ── The field name, verified against the agent that emits it ─────────────────


@pytest.mark.skipif(
    not AGENT_DELEGATE_TOOL.exists(), reason="hermes-agent checkout not available"
)
def test_agent_emits_cost_usd_per_delegation_entry():
    """The name WebUI reads is the one delegate_task actually writes."""
    source = AGENT_DELEGATE_TOOL.read_text(encoding="utf-8")
    assert 'entry["cost_usd"]' in source, (
        "hermes-agent no longer stamps cost_usd on a delegation entry — "
        "_delegation_cost_usd() is reading a field that does not exist."
    )
    assert '"results": results' in source


def test_reads_cost_from_the_agents_field_name():
    assert _delegation_cost_usd("delegate_task", _delegate_result(0.4213)) == 0.4213


def test_sums_every_child_in_a_fan_out():
    assert _delegation_cost_usd("delegate_task", _delegate_result(0.25, 0.5, 1.25)) == 2.0


def test_ignores_non_delegation_tools():
    """A shell result that happens to carry a cost_usd key is not a delegation."""
    payload = json.dumps({"results": [{"cost_usd": 9.99}]})
    assert _delegation_cost_usd("run_shell", payload) is None


@pytest.mark.parametrize(
    "raw",
    [
        _delegate_result(None),  # entry with no cost field at all
        _delegate_result(0.0),  # a real reported zero
        '{"results": []}',
        '{"results": "not-a-list"}',
        "plain text, not json",
        "",
        None,
    ],
)
def test_absent_or_zero_cost_yields_none(raw):
    """None — never 0.0 — so the card can tell "no data" from a real zero."""
    assert _delegation_cost_usd("delegate_task", raw) is None


@pytest.mark.parametrize("hostile", [float("nan"), float("inf"), -1.5, True, "0.50", None])
def test_hostile_cost_values_are_dropped(hostile):
    """A non-finite or non-numeric cost must never reach the wire as JSON."""
    payload = json.dumps({"results": [{"cost_usd": 1.0}, {"cost_usd": None}]})
    payload = json.loads(payload)
    payload["results"][1]["cost_usd"] = hostile
    total = _delegation_cost_usd("delegate_task", payload)
    assert total == 1.0
    json.dumps({"cost_usd": total}, allow_nan=False)


# ── Why it cannot be parsed back out of the card's snippet ───────────────────


def test_cost_survives_a_result_whose_snippet_truncates_it_away():
    raw = _delegate_result(0.8721, trace_entries=200)
    snippet = _tool_result_snippet(raw)

    assert len(snippet) == _TOOL_RESULT_SNIPPET_MAX
    assert "cost_usd" not in snippet, "fixture no longer exercises truncation"

    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "delegate_task", "arguments": '{"goal": "audit"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": raw},
    ]
    (call,) = _extract_tool_calls_from_messages(messages)
    assert call["name"] == "delegate_task"
    assert call["cost_usd"] == 0.8721


def test_persisted_summary_omits_the_key_when_there_is_no_cost():
    messages = [
        {
            "role": "assistant",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "delegate_task", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": _delegate_result(0.0)},
    ]
    (call,) = _extract_tool_calls_from_messages(messages)
    assert "cost_usd" not in call


# ── Ownership: still read-only, still no write path ─────────────────────────


def test_subagent_child_session_mutation_still_refused(monkeypatch):
    """The delegate runner still owns the child; WebUI cannot materialize it."""
    from api import routes

    monkeypatch.setattr(routes, "_state_db_session_source", lambda sid: "subagent")
    monkeypatch.setattr(routes, "get_session", lambda sid: (_ for _ in ()).throw(KeyError(sid)))
    monkeypatch.setattr(routes, "_lookup_cli_session_metadata", lambda sid: {})

    with pytest.raises(PermissionError, match="read-only subagent child session"):
        routes._get_or_materialize_session("child-session-1")


def test_no_delegation_control_plane_shipped():
    """No steer / stop-early / running-children surface came along for the ride."""
    routes_src = (REPO_ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    ui_src = UI_JS_PATH.read_text(encoding="utf-8")
    for forbidden in ("steer_subagent", "stop_subagent", "list_running_children"):
        assert forbidden not in routes_src
        assert forbidden not in ui_src
    assert not re.search(r"""["']/api/[^"']*(?:delegation|subagent)[^"']*["']""", ui_src)


# ── The card ────────────────────────────────────────────────────────────────

_DRIVER_SRC = r"""
const fs = require('fs');
const src = fs.readFileSync(process.argv[2], 'utf8');

function extractFunc(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start);
  let depth = 1; i++;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++;
    else if (src[i] === '}') depth--;
    i++;
  }
  return src.slice(start, i);
}

function makeEl() {
  return {
    _class: '', innerHTML: '', _attrs: {},
    get className(){return this._class;}, set className(v){this._class=v;},
    dataset: {},
    setAttribute(k,v){this._attrs[k]=String(v);},
    getAttribute(k){return this._attrs[k];},
    removeAttribute(k){delete this._attrs[k];},
    appendChild(){}, querySelector(){return null;}, closest(){return null;},
  };
}
global.document = { createElement: () => makeEl() };
global.window = {};
global.esc = (s)=>String(s==null?'':s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
global.li = (n)=>'<i data-lucide="'+n+'"></i>';
global.t = (k)=>'Estimated cost';
global.toolIcon = ()=>'<i></i>';
global._toolActionLabelText = (tc)=>String(tc&&tc.name||'tool');
global._toolDisplayName = (tc)=>String(tc&&tc.name||'tool');
global._toolDisclosureIdentity = ()=>'';
global._toolCardPreviewText = ()=>'';
global._formatToolArgPreview = ()=>'';
global._toolDetailLeadText = ()=>'';
global._toolDetailLeadLabel = ()=>'Input';
global._isMemorySave = ()=>false;
global._isSkillUpdate = ()=>false;

for (const fn of ['_snippetLooksLikeDiff','_colorDiffLines','_toolActionKind',
                  '_toolCardAllowsDetail','_fmtCostUsd','_delegationCostUsd',
                  'buildToolCard','_anchorSceneToolCallFromRow']) {
  eval(extractFunc(fn));
}

const spec = JSON.parse(process.argv[3]);
const tc = spec.mode === 'restored'
  ? _anchorSceneToolCallFromRow(spec.payload, {settled: true})
  : spec.payload;
process.stdout.write(JSON.stringify({
  html: buildToolCard(tc).innerHTML,
  formatted: (spec.formatSamples || []).map(v => _fmtCostUsd(v)),
  cost: _delegationCostUsd(tc),
}));
"""


@pytest.fixture(scope="module")
def driver_path(tmp_path_factory):
    path = tmp_path_factory.mktemp("hweb60_card_driver") / "driver.js"
    path.write_text(_DRIVER_SRC, encoding="utf-8")
    return str(path)


def _run(driver_path, payload, *, mode="direct", format_samples=()):
    if NODE is None:
        pytest.skip("node not on PATH")
    spec = {"mode": mode, "payload": payload, "formatSamples": list(format_samples)}
    result = subprocess.run(
        [NODE, driver_path, str(UI_JS_PATH), json.dumps(spec)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _stable(html: str) -> str:
    """Drop the per-build random disclosure id so two cards can be compared."""
    return re.sub(r"tool-detail-[0-9a-z]+-\d+", "tool-detail-X", html)


def _cost_chip(html: str) -> str | None:
    match = re.search(r'<span class="tool-card-cost"[^>]*>(.*?)</span>', html, re.S)
    return match.group(1).strip() if match else None


def test_delegation_card_renders_the_cost(driver_path):
    out = _run(
        driver_path,
        {"name": "delegate_task", "args": {"goal": "audit"}, "snippet": "done", "done": True,
         "cost_usd": 0.4213},
    )
    assert _cost_chip(out["html"]) == "~$0.42"
    assert "Estimated cost" in out["html"]


def test_sub_cent_delegation_keeps_four_decimals(driver_path):
    out = _run(
        driver_path,
        {"name": "delegate_task", "snippet": "done", "done": True, "cost_usd": 0.0032},
    )
    assert _cost_chip(out["html"]) == "~$0.0032"


@pytest.mark.parametrize("cost", [None, 0, 0.0, "not-a-number", float("-1")])
def test_delegation_without_a_cost_renders_the_card_unchanged(driver_path, cost):
    base = {"name": "delegate_task", "args": {"goal": "audit"}, "snippet": "done", "done": True}
    baseline = _run(driver_path, dict(base))["html"]
    out = _run(driver_path, {**base, "cost_usd": cost})

    assert out["cost"] is None
    assert _cost_chip(out["html"]) is None
    assert "tool-card-cost" not in out["html"]
    assert "undefined" not in out["html"]
    assert "$0.00" not in out["html"]
    assert _stable(out["html"]) == _stable(baseline)


def test_non_delegation_tool_never_gets_a_cost_chip(driver_path):
    out = _run(
        driver_path,
        {"name": "run_shell", "snippet": "ok", "done": True, "cost_usd": 3.5},
    )
    assert "tool-card-cost" not in out["html"]


def test_restored_card_keeps_the_cost_after_reload(driver_path):
    row = {
        "role": "tool",
        "status": "completed",
        "tool_call_id": "call_1",
        "tool": {
            "id": "call_1",
            "name": "delegate_task",
            "args": {"goal": "audit"},
            "snippet": "done",
            "done": True,
            "cost_usd": 1.5,
        },
        "payload": {"tid": "call_1", "name": "delegate_task", "snippet": "done", "cost_usd": 1.5},
    }
    out = _run(driver_path, row, mode="restored")
    assert _cost_chip(out["html"]) == "~$1.50"


def test_card_cost_uses_the_shared_session_cost_format(driver_path):
    """One formatter, not a second one: sub-cent keeps 4 decimals, else 2."""
    samples = [0.0032, 0.009999, 0.01, 0.4213, 12.5]
    out = _run(
        driver_path,
        {"name": "delegate_task", "snippet": "done", "done": True, "cost_usd": samples[0]},
        format_samples=samples,
    )
    assert out["formatted"] == ["$0.0032", "$0.0100", "$0.01", "$0.42", "$12.50"]

    ui_src = UI_JS_PATH.read_text(encoding="utf-8")
    assert "toFixed(4):cost.toFixed(2)" not in ui_src, (
        "a second inline cost format survived — every USD display must go "
        "through _fmtCostUsd()"
    )
