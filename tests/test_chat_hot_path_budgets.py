"""Regression coverage for long-running chat render budgets."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
COMMANDS_JS = (ROOT / "static" / "commands.js").read_text(encoding="utf-8")


def _function_source(src: str, name: str) -> str:
    marker = f"function {name}"
    start = src.index(marker)
    brace = src.index("{", src.index(")", start))
    depth = 0
    for idx in range(brace, len(src)):
        if src[idx] == "{":
            depth += 1
        elif src[idx] == "}":
            depth -= 1
            if depth == 0:
                return src[start : idx + 1]
    raise AssertionError(f"{name} did not close")


def test_pinned_snapshots_skip_semantic_row_layout_scan():
    capture = _function_source(UI_JS, "_captureMessageScrollSnapshot")
    assert "anchor:readerAwayFromBottom&&typeof _captureMessageViewportAnchor==='function'" in capture
    assert "const scrollHeight=el.scrollHeight;" in capture
    assert "const top=el.scrollTop;" in capture
    assert "const clientHeight=el.clientHeight;" in capture


def test_live_tail_follow_reuses_one_observer_and_one_raf():
    queue = _function_source(UI_JS, "_queueLiveTailFollowFrame")
    pinned = _function_source(UI_JS, "scrollIfPinned")
    assert "if(!_liveTailFollowRaf)" in queue
    assert "if(!_liveTailFollowRO&&typeof ResizeObserver==='function')" in queue
    assert "_queueLiveTailFollowFrame();" in pinned
    assert "_messageBottomDistance()>500" not in pinned


def test_live_dom_outer_html_is_not_serialized_on_paint_or_tool_boundaries():
    render_start = MESSAGES_JS.index("const _doRender=()=>{")
    render_end = MESSAGES_JS.index("const frameIntervalMs=", render_start)
    assert "snapshotLiveTurn" not in MESSAGES_JS[render_start:render_end]
    for event, next_event in (("'tool'", "'tool_complete'"), ("'tool_complete'", "'todo_state'")):
        start = MESSAGES_JS.index(f"source.addEventListener({event}")
        end = MESSAGES_JS.index(f"source.addEventListener({next_event}", start)
        assert "snapshotLiveTurn();" not in MESSAGES_JS[start:end]
    # Real session switches still have a synchronous recovery snapshot.
    assert "snapshotLiveTurnHtmlForSession(currentSid)" in (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")


def test_existing_live_prose_and_reasoning_patch_in_place():
    prose = _function_source(MESSAGES_JS, "_upsertAnchorProcessProse")
    reasoning = _function_source(MESSAGES_JS, "_upsertAnchorReasoning")
    assert "window._updateLiveAnchorProseRowForPatch" in prose
    assert "window._updateLiveAnchorReasoningRowForFallback" in reasoning
    assert prose.index("window._updateLiveAnchorProseRowForPatch") < prose.index("_renderAnchorLiveScene();")


def test_terminal_anchor_registry_releases_heavy_payloads_promptly():
    cleanup = _function_source(MESSAGES_JS, "_scheduleAnchorRegistryCleanup")
    assert "delayMs=30000" in cleanup
    assert "_anchorRegistryMap.get(streamId)===_anchorRegistry" in cleanup
    # Creation keeps a longer backstop for transports that never reach a
    # terminal handler; terminal call sites use the short default above.
    assert "_scheduleAnchorRegistryCleanup(600000);" in MESSAGES_JS


def test_settled_anchor_scene_is_bounded_below_transport_limit():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    row_budget = int(MESSAGES_JS.split("_SETTLED_ANCHOR_SCENE_ROW_BUDGET=")[1].split(";")[0])
    byte_budget = int(MESSAGES_JS.split("_SETTLED_ANCHOR_SCENE_BYTE_BUDGET=")[1].split(";")[0])
    identity_fn = _function_source(MESSAGES_JS, "_settledAnchorToolRowIdentity")
    fold_fn = _function_source(MESSAGES_JS, "_foldSettledAnchorToolRows")
    byte_length_fn = _function_source(MESSAGES_JS, "_settledAnchorSceneByteLength")
    bound_fn = _function_source(MESSAGES_JS, "_boundedSettledAnchorScene")
    script = f"""
const _SETTLED_ANCHOR_SCENE_ROW_BUDGET={row_budget};
const _SETTLED_ANCHOR_SCENE_BYTE_BUDGET={byte_budget};
{identity_fn}
{fold_fn}
{byte_length_fn}
{bound_fn}
const rows=[];
for(let i=0;i<500;i++){{
  const id='tool-'+i;
  rows.push({{
    role:'tool', row_id:'start-'+i, source_event_type:'tool', status:'running',
    text:'running '+id, payload:{{id,args:'x'.repeat(900)}},
    tool:{{id,name:'terminal',args:{{value:'x'.repeat(900)}},done:false}}
  }});
  rows.push({{
    role:'tool', row_id:'done-'+i, source_event_type:'tool_complete', status:'completed',
    text:'completed '+id, payload:{{id,result:'y'.repeat(900)}},
    tool:{{id,name:'terminal',snippet:'y'.repeat(900),done:true}}
  }});
}}
const out=_boundedSettledAnchorScene({{
  version:'activity_scene_v1', activity_rows:rows,
  final_answer:'z'.repeat(300000),
  artifacts:[{{source_event_type:'workspace_file',payload:{{kind:'file',path:'p'.repeat(300000)}}}}],
}});
const bytes=new TextEncoder().encode(JSON.stringify(out)).length;
console.log(JSON.stringify({{
  rows:out.activity_rows.length,
  total:out.activity_rows_total,
  toolTotal:out.tool_rows_total,
  omitted:out.activity_rows_omitted,
  bytes,
  allDone:out.activity_rows.every(row=>row.tool&&row.tool.done===true),
  hasPayload:out.activity_rows.some(row=>Object.prototype.hasOwnProperty.call(row,'payload')),
  scene:out,
}}));
"""
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["rows"] <= row_budget
    assert out["total"] == 1000
    assert out["toolTotal"] == 500
    assert out["omitted"] == 1000 - out["rows"]
    assert out["bytes"] <= byte_budget
    assert out["allDone"] is True
    assert out["hasPayload"] is False

    from api import routes

    assert routes._sanitize_anchor_activity_scene(out["scene"]) == out["scene"]


def test_manual_compression_keeps_display_transcript_client_side():
    apply_result = _function_source(COMMANDS_JS, "_applyManualCompressionResult")
    run = _function_source(COMMANDS_JS, "_runManualCompression")
    assert "&messages=0&resolve_model=0" in run
    assert "S.messages=data.session.messages" not in apply_result
    assert "S.toolCalls=data.session.tool_calls" not in apply_result
    assert "S.session={...(S.session||{}),...data.session};" in apply_result
