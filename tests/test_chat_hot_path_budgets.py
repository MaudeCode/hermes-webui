"""Regression coverage for long-running chat render budgets."""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
COMMANDS_JS = (ROOT / "static" / "commands.js").read_text(encoding="utf-8")


def _function_source(src: str, name: str) -> str:
    marker = f"function {name}"
    start = src.index(marker)
    if src[max(0, start - 6) : start] == "async ":
        start -= 6
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


def test_settled_anchor_scene_preserves_full_rows_behind_bounded_preview():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    preview_rows = int(MESSAGES_JS.split("_SETTLED_ANCHOR_SCENE_PREVIEW_ROWS=")[1].split(";")[0])
    identity_fn = _function_source(MESSAGES_JS, "_settledAnchorToolRowIdentity")
    fold_fn = _function_source(MESSAGES_JS, "_foldSettledAnchorToolRows")
    prepare_fn = _function_source(MESSAGES_JS, "_prepareSettledAnchorScene")
    preview_fn = _function_source(MESSAGES_JS, "_settledAnchorScenePreview")
    script = f"""
const _SETTLED_ANCHOR_SCENE_PREVIEW_ROWS={preview_rows};
{identity_fn}
{fold_fn}
{prepare_fn}
{preview_fn}
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
const full=_prepareSettledAnchorScene({{version:'activity_scene_v1',activity_rows:rows,final_answer:'done'}});
const preview=_settledAnchorScenePreview(full);
console.log(JSON.stringify({{
  fullRows:full.activity_rows.length,
  previewRows:preview.activity_rows.length,
  total:preview.activity_rows_total,
  toolTotal:preview.tool_rows_total,
  offset:preview.activity_rows_offset,
  omitted:preview.activity_rows_omitted,
  allDone:full.activity_rows.every(row=>row.tool&&row.tool.done===true),
  hasPayload:full.activity_rows.some(row=>Object.prototype.hasOwnProperty.call(row,'payload')),
  localFullRows:preview._full_activity_rows.length,
  fullRowsSerialized:JSON.stringify(preview).includes('_full_activity_rows'),
  scene:full,
}}));
"""
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["fullRows"] == 500
    assert out["previewRows"] == preview_rows
    assert out["total"] == 500
    assert out["toolTotal"] == 500
    assert out["offset"] == 500 - preview_rows
    assert out["omitted"] == 500 - preview_rows
    assert out["allDone"] is True
    assert out["hasPayload"] is False
    assert out["localFullRows"] == 500
    assert out["fullRowsSerialized"] is False

    from api import routes

    assert routes._sanitize_anchor_activity_scene(out["scene"]) == out["scene"]
    server_preview = routes._anchor_activity_scene_transport_preview(
        out["scene"], scene_ref="scene-ref"
    )
    assert len(server_preview["activity_rows"]) == routes._ANCHOR_ACTIVITY_SCENE_PREVIEW_ROWS
    assert server_preview["activity_rows_total"] == 500
    assert server_preview["activity_rows_offset"] == 500 - routes._ANCHOR_ACTIVITY_SCENE_PREVIEW_ROWS
    assert server_preview["activity_scene_ref"] == "scene-ref"


def test_earlier_activity_is_loaded_from_durable_scene_in_chunks():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required")
    count_fn = _function_source(UI_JS, "_anchorSceneEarlierRowCount")
    loader = _function_source(UI_JS, "_loadEarlierAnchorActivityRows")
    compact = _function_source(UI_JS, "_syncCompactEarlierActivityAffordance")
    transparent = _function_source(UI_JS, "_revealTransparentEarlierSteps")
    assert "/api/session/anchor-scene?session_id=" in loader
    assert "scene.activity_rows=earlierRows.concat(currentRows);" in loader
    assert "scene.activity_rows_offset=start;" in loader
    assert "_loadEarlierAnchorActivityRows(owner.message,owner.rawIdx,80)" in compact
    assert "await _loadEarlierAnchorActivityRows(message,rawIdx,80);" in transparent
    script = f"""
const all=Array.from({{length:250}},(_,i)=>({{row_id:'row-'+i}}));
const scene={{activity_rows:all.slice(170),activity_rows_offset:170,activity_rows_total:250}};
Object.defineProperty(scene,'_full_activity_rows',{{value:all,enumerable:false}});
const message={{_anchor_activity_scene:scene}};
const S={{session:{{session_id:'sid'}},messages:[message]}};
const _oldestIdx=0;
async function api(){{ throw new Error('durable fetch should not run with local full rows'); }}
{count_fn}
{loader}
(async()=>{{
  const loaded=await _loadEarlierAnchorActivityRows(message,0,80);
  console.log(JSON.stringify({{loaded,offset:scene.activity_rows_offset,first:scene.activity_rows[0].row_id,count:scene.activity_rows.length}}));
}})().catch(err=>{{console.error(err);process.exit(1);}});
"""
    proc = subprocess.run([node, "-e", script], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout) == {
        "loaded": 80,
        "offset": 90,
        "first": "row-90",
        "count": 160,
    }


def test_anchor_scene_chunk_endpoint_returns_earlier_rows_without_mutating_storage(monkeypatch):
    from api import routes

    rows = [{"role": "thinking", "row_id": f"row-{idx}", "text": str(idx)} for idx in range(250)]
    scene = {"version": "activity_scene_v1", "activity_rows": rows}
    record = {"message_ref": "a" * 64, "message_index": 7, "scene": scene}
    session = SimpleNamespace(
        profile=None,
        anchor_activity_scenes={"a" * 64: record},
        _loaded_metadata_only=False,
    )
    monkeypatch.setattr(routes, "get_session", lambda _sid: session)
    monkeypatch.setattr(routes, "_session_visible_to_active_profile", lambda *_args: True)
    monkeypatch.setattr(routes, "j", lambda _handler, payload, **_kwargs: payload)
    parsed = SimpleNamespace(
        query=f"session_id=sid&message_ref={'a' * 64}&before=170&limit=80"
    )

    result = routes._handle_get_session_anchor_scene(object(), parsed)

    assert result["start"] == 90
    assert result["end"] == 170
    assert result["total"] == 250
    assert [row["row_id"] for row in result["rows"]] == [f"row-{idx}" for idx in range(90, 170)]
    assert len(scene["activity_rows"]) == 250


def test_session_hydration_sends_preview_while_durable_record_keeps_all_rows():
    from api import routes

    messages = [{"role": "assistant", "content": "done"}]
    ref = routes._assistant_anchor_scene_message_ref(messages[0])
    rows = [
        {"role": "thinking", "row_id": f"row-{idx}", "text": f"step {idx}"}
        for idx in range(250)
    ]
    records = {
        ref: {
            "message_index": 0,
            "message_ref": ref,
            "stream_id": "stream",
            "scene": {"version": "activity_scene_v1", "activity_rows": rows},
        }
    }

    hydrated = routes._hydrate_anchor_activity_scenes(messages, records)
    preview = hydrated[0]["_anchor_activity_scene"]

    assert len(preview["activity_rows"]) == routes._ANCHOR_ACTIVITY_SCENE_PREVIEW_ROWS
    assert preview["activity_rows"][0]["row_id"] == "row-170"
    assert preview["activity_rows_offset"] == 170
    assert preview["activity_rows_total"] == 250
    assert preview["activity_scene_ref"] == ref
    assert len(records[ref]["scene"]["activity_rows"]) == 250


def test_manual_compression_keeps_display_transcript_client_side():
    apply_result = _function_source(COMMANDS_JS, "_applyManualCompressionResult")
    run = _function_source(COMMANDS_JS, "_runManualCompression")
    assert "&messages=0&resolve_model=0" in run
    assert "S.messages=data.session.messages" not in apply_result
    assert "S.toolCalls=data.session.tool_calls" not in apply_result
    assert "S.session={...(S.session||{}),...data.session};" in apply_result
