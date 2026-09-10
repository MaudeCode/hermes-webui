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


# ── Both live completion paths report the cost ───────────────────────────────


def _run_delegation_turn(monkeypatch, tmp_path, *, structured: bool):
    """Drive a real _run_agent_streaming turn whose agent runs one delegation.

    ``_run_agent_streaming`` branches on the agent constructor's real signature,
    so the fake agent declares exactly the callbacks the build under test has:
    a modern build takes ``tool_complete_callback``, an older one only reaches
    the ``on_tool`` ``tool.completed`` fallback. Both must carry the cost.
    """
    import queue
    import sys
    import types

    from api import models, streaming
    from api.models import Session

    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    monkeypatch.setattr(models, "SESSION_DIR", session_dir)
    monkeypatch.setattr(models, "SESSION_INDEX_FILE", session_dir / "_index.json")
    monkeypatch.setattr(streaming, "SESSION_DIR", session_dir)
    for registry in (models.SESSIONS, streaming.SESSIONS, streaming.STREAMS,
                     streaming.AGENT_INSTANCES, streaming.SESSION_AGENT_LOCKS):
        registry.clear()
    # A cached agent from an earlier test leaks its constructor signature into
    # the callback-capability probe, which is exactly what this test varies.
    from api.config import SESSION_AGENT_CACHE

    SESSION_AGENT_CACHE.clear()

    session_id, stream_id = "hweb60_session", "hweb60_stream"
    session = Session(
        session_id=session_id,
        title="delegation cost",
        workspace=str(tmp_path),
        model="gpt-4o",
        messages=[],
        context_messages=[],
    )
    session.active_stream_id = stream_id
    session.pending_user_message = "Delegate the audit."
    session.pending_started_at = 1.0
    session.save()
    models.SESSIONS[session_id] = session
    streaming.SESSIONS[session_id] = session
    event_queue: "queue.Queue" = queue.Queue()
    streaming.STREAMS[stream_id] = event_queue

    raw_result = _delegate_result(0.6125, trace_entries=200)
    assert "cost_usd" not in _tool_result_snippet(raw_result)

    agent_messages = [
        {"role": "user", "content": "Delegate the audit."},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "delegate_task", "arguments": '{"goal": "audit"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": raw_result},
        {"role": "assistant", "content": "Delegation finished."},
    ]

    class _BaseFakeAgent:
        def __init__(self, **kwargs):
            self._kwargs = kwargs
            self.session_id = kwargs.get("session_id")
            self.stream_delta_callback = kwargs.get("stream_delta_callback")
            self.context_compressor = None
            self.session_prompt_tokens = 0
            self.session_completion_tokens = 0
            self.session_estimated_cost_usd = None
            self.session_cache_read_tokens = 0
            self.session_cache_write_tokens = 0
            self.reasoning_config = None
            self.ephemeral_system_prompt = None
            self._last_error = None
            self._persist_user_message_idx = None
            self._current_turn_id = ""

        def interrupt(self, _message):
            return None

    class ModernFakeAgent(_BaseFakeAgent):
        def __init__(self, tool_progress_callback=None, tool_start_callback=None,
                     tool_complete_callback=None, **kwargs):
            super().__init__(
                tool_progress_callback=tool_progress_callback,
                tool_start_callback=tool_start_callback,
                tool_complete_callback=tool_complete_callback,
                **kwargs,
            )

        def run_conversation(self, **_kwargs):
            self._kwargs["tool_start_callback"]("call_1", "delegate_task", {"goal": "audit"})
            self._kwargs["tool_complete_callback"](
                "call_1", "delegate_task", {"goal": "audit"}, raw_result
            )
            return {"messages": list(agent_messages)}

    class LegacyFakeAgent(_BaseFakeAgent):
        def __init__(self, tool_progress_callback=None, **kwargs):
            super().__init__(tool_progress_callback=tool_progress_callback, **kwargs)

        def run_conversation(self, **_kwargs):
            progress = self._kwargs["tool_progress_callback"]
            progress("tool.started", "delegate_task", None, {"goal": "audit"})
            progress(
                "tool.completed",
                "delegate_task",
                _tool_result_snippet(raw_result),
                {"goal": "audit"},
                result=raw_result,
                duration=1.0,
                is_error=False,
            )
            return {"messages": list(agent_messages)}

    fake_hermes_state = types.ModuleType("hermes_state")
    fake_hermes_state.SessionDB = lambda *_a, **_k: object()

    with monkeypatch.context() as m:
        m.setattr(streaming, "get_session", lambda _sid: session)
        m.setattr(streaming, "_get_ai_agent",
                  lambda: ModernFakeAgent if structured else LegacyFakeAgent)
        m.setattr(streaming, "resolve_model_provider", lambda *_a, **_k: ("gpt-4o", "openai", None))
        m.setattr(streaming, "_streaming_requires_process_env_fallback", lambda **_k: False)
        m.setattr("api.config.get_config", lambda *_a, **_k: {})
        m.setattr("api.config._resolve_cli_toolsets", lambda *_a, **_k: [])
        m.setitem(sys.modules, "hermes_state", fake_hermes_state)
        streaming._run_agent_streaming(
            session_id=session_id,
            msg_text="Delegate the audit.",
            model="gpt-4o",
            workspace=str(tmp_path),
            stream_id=stream_id,
        )

    events = []
    while not event_queue.empty():
        events.append(event_queue.get_nowait())
    return events, json.loads((session_dir / f"{session_id}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "structured", [True, False], ids=["tool_complete_callback", "legacy_on_tool"]
)
def test_both_live_completion_paths_report_the_cost(monkeypatch, tmp_path, structured):
    events, payload = _run_delegation_turn(monkeypatch, tmp_path, structured=structured)

    completes = []
    for name, data in events:
        if name != "tool_complete":
            continue
        parsed = json.loads(data) if isinstance(data, str) else data
        if parsed.get("name") == "delegate_task":
            completes.append(parsed)
    assert completes, f"no delegate_task tool_complete event: {sorted({n for n, _ in events})}"
    assert completes[-1]["cost_usd"] == 0.6125

    persisted = [
        tc for tc in (payload.get("tool_calls") or []) if tc.get("name") == "delegate_task"
    ]
    assert persisted and persisted[-1]["cost_usd"] == 0.6125


# ── Reattach mid-turn: the journal snapshot rebuilds the card ───────────────


def _journal_snapshot(monkeypatch, events):
    from api import routes

    stream_id = "stream-hweb60"
    tail = [
        {"event": ev, "seq": i + 1, "event_id": f"{stream_id}:{i + 1}",
         "created_at": float(i + 1), "payload": payload}
        for i, (ev, payload) in enumerate(events)
    ]
    monkeypatch.setattr(routes, "find_run_summary", lambda sid: {
        "session_id": "session-hweb60",
        "last_seq": len(tail),
        "last_event_id": f"{stream_id}:{len(tail)}",
    })
    monkeypatch.setattr(routes, "read_run_event_tail",
                        lambda session_id, run_id: {"events": tail})
    return routes._run_journal_live_snapshot(stream_id)


def _delegation_row(snapshot):
    rows = snapshot["anchor_activity_scene"]["activity_rows"]
    return next(r for r in rows if (r.get("tool") or {}).get("name") == "delegate_task")


@pytest.mark.parametrize(
    "with_start", [True, False], ids=["started_then_completed", "completion_only"]
)
def test_reattach_mid_turn_keeps_the_delegation_cost(monkeypatch, with_start):
    """Refreshing while the parent turn still runs must not drop the chip.

    Only the completion payload can carry a final cost, so both journal
    reconstruction branches — updating a call the `tool` event already opened,
    and synthesising one from the completion alone — have to copy it.
    """
    events = []
    if with_start:
        events.append(("tool", {"name": "delegate_task", "tid": "call-1", "args": {"goal": "audit"}}))
    events.append(("tool_complete", {
        "name": "delegate_task", "tid": "call-1", "preview": "done", "cost_usd": 0.6125,
    }))

    snapshot = _journal_snapshot(monkeypatch, events)
    (call,) = [c for c in snapshot["tool_calls"] if c.get("name") == "delegate_task"]
    assert call["cost_usd"] == 0.6125

    row = _delegation_row(snapshot)
    assert row["tool"]["cost_usd"] == 0.6125
    assert row["payload"]["cost_usd"] == 0.6125


def test_reattach_without_a_cost_leaves_the_key_off(monkeypatch):
    snapshot = _journal_snapshot(monkeypatch, [
        ("tool", {"name": "delegate_task", "tid": "call-1", "args": {"goal": "audit"}}),
        ("tool_complete", {"name": "delegate_task", "tid": "call-1", "preview": "done"}),
    ])
    (call,) = [c for c in snapshot["tool_calls"] if c.get("name") == "delegate_task"]
    assert "cost_usd" not in call

    row = _delegation_row(snapshot)
    assert "cost_usd" not in row["tool"]
    assert "cost_usd" not in row["payload"]


# ── Cold reload: the hydrated anchor scene merges the persisted summary ──────


def test_cold_load_hydration_carries_the_cost_into_the_merged_row():
    """The provider-invocation row has no cost; the session summary is the source.

    On a cold /api/session load `_complete_hydrated_anchor_scene()` pushes the
    invocation row first and merges the session-summary row in behind it, so a
    merge that drops `cost_usd` silently loses the chip after reload — and an
    anchor-owned turn never reaches the legacy card fallback that would.
    """
    from api import routes

    messages = [
        {"role": "user", "content": "Delegate the audit"},
        {
            "role": "assistant",
            "content": "Done.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "delegate_task", "arguments": '{"goal": "audit"}'},
                }
            ],
        },
    ]
    scene = {
        "version": "activity_scene_v1",
        "final_answer": "Done.",
        "activity_rows": [
            {
                "row_id": "tool-1",
                "role": "tool",
                "tool_call_id": "call-1",
                "tool": {"id": "call-1", "name": "delegate_task"},
            }
        ],
    }
    tool_calls = [
        {"tid": "call-1", "name": "delegate_task", "snippet": "done", "done": True,
         "assistant_msg_idx": 1, "cost_usd": 0.6125},
    ]

    completed = routes._complete_hydrated_anchor_scene(
        messages, scene, 1, tool_calls=tool_calls, stream_id="stream-1"
    )
    row = next(
        r for r in completed["activity_rows"]
        if (r.get("tool") or {}).get("name") == "delegate_task"
    )
    assert row["tool"]["cost_usd"] == 0.6125
    assert row["payload"]["cost_usd"] == 0.6125


def test_cold_load_hydration_adds_no_cost_key_when_there_is_none():
    from api import routes

    messages = [
        {"role": "user", "content": "Delegate the audit"},
        {
            "role": "assistant",
            "content": "Done.",
            "tool_calls": [
                {
                    "id": "call-1",
                    "function": {"name": "delegate_task", "arguments": "{}"},
                }
            ],
        },
    ]
    scene = {
        "version": "activity_scene_v1",
        "final_answer": "Done.",
        "activity_rows": [
            {
                "row_id": "tool-1",
                "role": "tool",
                "tool_call_id": "call-1",
                "tool": {"id": "call-1", "name": "delegate_task"},
            }
        ],
    }
    completed = routes._complete_hydrated_anchor_scene(
        messages, scene, 1,
        tool_calls=[{"tid": "call-1", "name": "delegate_task", "snippet": "done",
                     "done": True, "assistant_msg_idx": 1}],
        stream_id="stream-1",
    )
    row = next(
        r for r in completed["activity_rows"]
        if (r.get("tool") or {}).get("name") == "delegate_task"
    )
    assert "cost_usd" not in row["tool"]
    assert "cost_usd" not in row["payload"]


_MESSAGES_FNS = r"""
const messagesFns = [
		  '_anchorSceneMessageText','_anchorSceneCleanText','_anchorSceneTextKey',
		  '_anchorSceneContentText','_anchorSceneContentVisibleText','_anchorSceneMessageHasContentToolUse',
          '_anchorSceneFinalAnswerText',
		  '_anchorSceneSafePayload','_anchorSceneToolId','_anchorSceneToolName',
	  '_anchorSceneToolArgs','_anchorSceneContentTool','_anchorSceneStringPayload','_anchorSceneRowBase',
	  '_anchorSceneProseRow','_anchorSceneThinkingRow','_anchorSceneToolRowFromCall',
	  '_anchorSceneToolRowName','_anchorSceneToolRowId',
	  '_anchorSceneToolRowsHaveNonConflictingIds','_anchorSceneToolRowsHaveDifferentExplicitIds',
	  '_anchorSceneToolRowStartedAt','_anchorSceneToolRowsHaveSameStartedAt',
	  '_anchorSceneToolRowBodyText','_anchorSceneToolRowsHaveCompatibleBody',
	  '_anchorSceneToolRowsHaveCompatibleNames',
	  '_anchorSceneToolRowArgs','_anchorSceneObjectContainsSubset',
	  '_anchorSceneToolRowsHaveCompatibleInvocation',
	  '_anchorSceneToolRowHasInvocationEvidence','_anchorSceneToolRowsCanNameMatch',
	  '_anchorSceneMatchingContentToolRow',
	  '_anchorSceneMessageReasoningText','_anchorSceneRowsFromContentParts',
	  '_enrichSettledToolRowBodyFromLive',
	  '_anchorSceneRowsByMessageIndex',
	];
"""


# ── Fresh settlement: the live card is replaced by a rebuilt settled row ─────

_SETTLED_DRIVER_GEN = r"""
'use strict';
const fs = require('fs');
const mSrc = fs.readFileSync(process.argv[2], 'utf8');
const uSrc = fs.readFileSync(process.argv[3], 'utf8');
function extractFunc(src, name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\(');
  const start = src.search(re);
  if (start < 0) throw new Error(name + ' not found');
  let i = src.indexOf('{', start), depth = 1; i++;
  while (depth > 0 && i < src.length) {
    if (src[i] === '{') depth++; else if (src[i] === '}') depth--; i++;
  }
  return src.slice(start, i);
}
__FNS__
const uiFns = ['_anchorSceneToolCallFromRow'];
let code = '(function(){\n';
code += 'var activeSid="test-session"; var streamId="test-stream"; var S;\n';
for (const n of messagesFns) code += extractFunc(mSrc, n) + '\n';
for (const n of uiFns) code += extractFunc(uSrc, n) + '\n';
code += `
var buf='';
process.stdin.on('data',c=>buf+=c);
process.stdin.on('end',()=>{
  var p=JSON.parse(buf||'{}');
  S=p.S||{toolCalls:[]};
  var messages=p.messages||[];
  var byIdx=_anchorSceneRowsByMessageIndex(messages,0,messages.length-1);
  var out=[];
  byIdx.forEach(function(bucket){
    bucket.forEach(function(row){
      if(row.role!=='tool') return;
      var tc=_anchorSceneToolCallFromRow(row,{settled:true});
      out.push({
        name:(row.tool&&row.tool.name)||'',
        toolHasCost:!!(row.tool&&Object.prototype.hasOwnProperty.call(row.tool,'cost_usd')),
        payloadHasCost:!!(row.payload&&Object.prototype.hasOwnProperty.call(row.payload,'cost_usd')),
        toolCost:row.tool?row.tool.cost_usd:undefined,
        payloadCost:row.payload?row.payload.cost_usd:undefined,
        cardCost:tc.cost_usd,
      });
    });
  });
  process.stdout.write(JSON.stringify(out));
});
})();
`;
process.stdout.write(code);
"""


@pytest.fixture(scope="module")
def settled_driver(tmp_path_factory):
    if NODE is None:
        pytest.skip("node not on PATH")
    gen = tmp_path_factory.mktemp("hweb60_gen") / "gen.js"
    gen.write_text(_SETTLED_DRIVER_GEN.replace("__FNS__", _MESSAGES_FNS), encoding="utf-8")
    built = subprocess.run(
        [NODE, str(gen), str(REPO_ROOT / "static" / "messages.js"), str(UI_JS_PATH)],
        capture_output=True, text=True, timeout=20,
    )
    assert built.returncode == 0, built.stderr
    driver = tmp_path_factory.mktemp("hweb60_driver") / "driver.js"
    driver.write_text(built.stdout, encoding="utf-8")
    return str(driver)


def _settle(settled_driver, live_call):
    """Settle a turn whose persisted tool_calls carry no cost, only the live one."""
    payload = {
        "messages": [
            {"role": "user", "content": "delegate the audit"},
            {
                "role": "assistant",
                "content": "Done.",
                "tool_calls": [
                    {"id": "call-1", "name": "delegate_task", "started_at": 100,
                     "args": {"goal": "audit"}, "snippet": "done"}
                ],
            },
            {"role": "assistant", "content": "final answer"},
        ],
        "S": {"toolCalls": [live_call]},
    }
    result = subprocess.run(
        [NODE, settled_driver], input=json.dumps(payload),
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    rows = json.loads(result.stdout)
    return next(r for r in rows if r["name"] == "delegate_task")


def test_fresh_settlement_keeps_the_delegation_cost(settled_driver):
    """messages[].tool_calls never carries a cost; the live entry is the source.

    Settlement rebuilds the row from the persisted tool_calls and dedupes the
    matching live S.toolCalls entry away, so a rebuild that drops cost_usd
    persists an anchor scene without it and the chip disappears.
    """
    row = _settle(settled_driver, {
        "id": "call-1", "name": "delegate_task", "assistant_msg_idx": 1,
        "started_at": 100, "args": {"goal": "audit"}, "snippet": "done",
        "cost_usd": 0.6125,
    })
    assert row["toolCost"] == 0.6125
    assert row["payloadCost"] == 0.6125
    assert row["cardCost"] == 0.6125


def test_fresh_settlement_adds_no_cost_key_when_there_is_none(settled_driver):
    row = _settle(settled_driver, {
        "id": "call-1", "name": "delegate_task", "assistant_msg_idx": 1,
        "started_at": 100, "args": {"goal": "audit"}, "snippet": "done",
    })
    assert row["toolHasCost"] is False
    assert row["payloadHasCost"] is False
    # undefined in JS, so JSON.stringify drops the key entirely
    assert "cardCost" not in row
