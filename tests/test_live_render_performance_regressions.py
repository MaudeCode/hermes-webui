"""Browser-side regression gates for long live-turn rendering performance."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
UI_JS = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _run_node(script: str) -> dict:
    assert NODE, "node is required for DOM-executed live-render tests"
    result = subprocess.run(
        [NODE, "-e", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


_EXTRACT_FUNC_JS = r"""
function extractFunc(name){
  const start=src.indexOf('function '+name+'(');
  if(start<0) throw new Error(name+' not found');
  const params=src.indexOf('(',start);
  let depth=0,close=-1;
  for(let i=params;i<src.length;i++){
    if(src[i]==='(') depth++;
    else if(src[i]===')'&&--depth===0){close=i;break;}
  }
  const brace=src.indexOf('{',close);
  depth=0;
  for(let i=brace;i<src.length;i++){
    if(src[i]==='{') depth++;
    else if(src[i]==='}'&&--depth===0) return src.slice(start,i+1);
  }
  throw new Error(name+' body did not close');
}
"""


def _function_body_ui(name: str) -> str:
    start = UI_JS.index(f"function {name}(")
    depth = 0
    brace = UI_JS.index("{", UI_JS.index(")", start))
    for i in range(brace, len(UI_JS)):
        if UI_JS[i] == "{":
            depth += 1
        elif UI_JS[i] == "}":
            depth -= 1
            if depth == 0:
                return UI_JS[start:i + 1]
    raise AssertionError(f"{name} body did not close")


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_compact_worklog_reconcile_preserves_unchanged_rows_without_clearing_list():
    """A new live event must not tear down every earlier Compact Worklog row."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
class FakeNode{{
  constructor(tag='div'){{this.tagName=tag.toUpperCase();this.children=[];this.parentNode=null;this.attributes={{}};this.className='';this.clearCount=0;this._text='';}}
  get firstChild(){{return this.children[0]||null;}}
  get nextSibling(){{if(!this.parentNode)return null;const i=this.parentNode.children.indexOf(this);return this.parentNode.children[i+1]||null;}}
  get firstElementChild(){{return this.firstChild;}}
  get lastElementChild(){{return this.children[this.children.length-1]||null;}}
  get innerHTML(){{return this.children.map(x=>x._text).join('');}}
  set innerHTML(value){{if(value===''){{this.clearCount++;for(const child of this.children)child.parentNode=null;this.children=[];}}}}
  setAttribute(name,value){{this.attributes[name]=String(value);}}
  getAttribute(name){{return Object.prototype.hasOwnProperty.call(this.attributes,name)?this.attributes[name]:null;}}
  removeAttribute(name){{delete this.attributes[name];}}
  toggleAttribute(name,force){{if(force)this.setAttribute(name,'');else delete this.attributes[name];}}
  querySelectorAll(selector){{
    const found=[];
    const visit=node=>{{
      for(const child of node.children){{
        if(selector==='[data-anchor-scene-row="1"]'&&child.getAttribute('data-anchor-scene-row')==='1') found.push(child);
        visit(child);
      }}
    }};
    visit(this);
    return found;
  }}
  appendChild(child){{return this.insertBefore(child,null);}}
  insertBefore(child,ref){{
    if(child.parentNode){{const old=child.parentNode.children.indexOf(child);if(old>=0)child.parentNode.children.splice(old,1);}}
    child.parentNode=this;
    const idx=ref?this.children.indexOf(ref):-1;
    if(idx<0)this.children.push(child);else this.children.splice(idx,0,child);
    return child;
  }}
  remove(){{if(!this.parentNode)return;const i=this.parentNode.children.indexOf(this);if(i>=0)this.parentNode.children.splice(i,1);this.parentNode=null;}}
  replaceWith(node){{if(!this.parentNode)return;this.parentNode.insertBefore(node,this);this.remove();}}
}}
global.document={{createElement:tag=>new FakeNode(tag)}};
global._toolWorklogListEl=group=>group.list;
global._anchorSceneNodeForRow=row=>{{
  const node=new FakeNode('div');
  node._text=String(row.text||'');
  node.setAttribute('data-anchor-scene-row','1');
  node.setAttribute('data-anchor-row-id',row.row_id||'');
  node.setAttribute('data-anchor-row-role',row.role||'activity');
  node.setAttribute('data-anchor-source-event-type',row.source_event_type||'');
  node._anchorSceneRenderSignature=JSON.stringify(row);
  return node;
}};
global._syncToolCallGroupSummary=()=>{{}};
global._activitySequenceNodeKey=node=>node.getAttribute('data-anchor-row-id')||'sequence';
global._createActivitySequenceGroup=(key,live)=>{{
  const sequence=new FakeNode('div');
  sequence.setAttribute('data-activity-sequence-group','1');
  sequence.setAttribute('data-activity-sequence-key',key);
  sequence.list=new FakeNode('div');
  sequence.appendChild(sequence.list);
  return sequence;
}};
global._syncActivitySequenceSummary=()=>{{}};
for(const name of [
  '_anchorSceneCompactRowKey','_anchorSceneCompactTopLevelKey',
  '_anchorSceneCompactNodesEqual','_reconcileAnchorSceneCompactChildren',
  '_renderAnchorSceneRowsIntoWorklog'
]){{if(src.includes('function '+name))eval(extractFunc(name));}}
const list=new FakeNode('div');
const group={{list}};
_renderAnchorSceneRowsIntoWorklog(group,[
  {{row_id:'reason-1',role:'thinking',source_event_type:'reasoning',text:'unchanged'}},
  {{row_id:'status-1',role:'control',source_event_type:'status',text:'running'}},
],{{live:true}});
const firstBefore=list.children[0];
const thinkingBefore=firstBefore;
const secondBefore=list.children[1];
_renderAnchorSceneRowsIntoWorklog(group,[
  {{row_id:'reason-1',role:'thinking',source_event_type:'reasoning',text:'unchanged'}},
  {{row_id:'status-1',role:'control',source_event_type:'status',text:'complete'}},
],{{live:true}});
process.stdout.write(JSON.stringify({{
  firstPreserved:list.children[0]===firstBefore,
  thinkingPreserved:list.children[0]===thinkingBefore,
  changedReplaced:list.children[1]!==secondBefore,
  clearCount:list.clearCount,
  order:list.children.map(x=>x.getAttribute('data-activity-sequence-key')||x.getAttribute('data-anchor-row-id')),
}}));
"""
    result = _run_node(script)
    assert result == {
        "firstPreserved": True,
        "thinkingPreserved": True,
        "changedReplaced": True,
        "clearCount": 0,
        "order": ["reason-1", "status-1"],
    }


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_anchor_owned_helpers_do_not_render_the_same_scene_twice():
    """The source-event apply owns painting; compatibility appenders must no-op."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
global.S={{session:{{session_id:'sid-1'}},activeStreamId:'stream-1'}};
global.isFinalAnswerOnlyMode=()=>false;
global.isLiveAnchorActivitySceneOwner=()=>true;
let renders=0;
global._renderLiveAnchorActivitySceneForStream=()=>{{renders++;return true;}};
eval(extractFunc('appendLiveToolCard'));
eval(extractFunc('appendLiveCompressionCard'));
appendLiveToolCard({{tid:'tool-1',name:'read_file'}},{{sessionId:'sid-1',streamId:'stream-1'}});
appendLiveCompressionCard({{sessionId:'sid-1',streamId:'stream-1',phase:'running',automatic:true}});
process.stdout.write(JSON.stringify({{renders}}));
"""
    assert _run_node(script)["renders"] == 0


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_compact_scene_defers_scroll_restore_until_animation_frame():
    """DOM mutation and scroll geometry reads must not share the same JS stack."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
const turn={{
  dataset:{{sessionId:'sid-1'}},attributes:{{}},
  setAttribute(name,value){{this.attributes[name]=String(value);}},
}};
const blocks={{querySelectorAll:()=>[]}};
const emptyState={{style:{{}}}};
global.window={{}};
global.S={{session:{{session_id:'sid-1',pending_started_at:1}},activeStreamId:'stream-1'}};
global.chatActivityMode=()=> 'compact_worklog';
global.isSimplifiedToolCalling=()=>true;
global.$=id=>id==='liveAssistantTurn'?turn:id==='emptyState'?emptyState:null;
global._anchorSceneRowsForRendering=scene=>scene.activity_rows;
global._liveAnchorRowWindow=rows=>({{rows,hiddenCount:0,total:rows.length}});
global._buildLiveAnchorWindowEdges=()=>({{beforeNode:null,afterNode:null}});
global._assistantTurnBlocks=()=>blocks;
global._captureWorklogDetailDisclosureState=()=>null;
global._captureMessageScrollSnapshot=()=>({{pinned:true,userUnpinned:false}});
global._prepareLiveAnchorScrollRebuildGuard=()=>({{readerAwayFromBottom:false,release:null}});
global._anchorSceneWorklogGroup=()=>({{}});
global._renderAnchorSceneRowsIntoWorklog=()=>true;
global._restoreWorklogDetailDisclosureState=()=>{{}};
global._startActivityElapsedTimer=()=>{{}};
global._dedupeLiveProcessedWorklogAnchors=()=>{{}};
global._moveLiveRunStatusToTurnEnd=()=>{{}};
global._messageUserUnpinned=false;
let restores=0,scrolls=0;
global._restoreMessageScrollSnapshotSameFrame=()=>{{restores++;}};
global.scrollIfPinned=()=>{{scrolls++;}};
const frames=[];
global.requestAnimationFrame=fn=>{{frames.push(fn);return frames.length;}};
eval(extractFunc('_restoreLiveAnchorScrollSnapshotAfterRebuild'));
eval(extractFunc('renderLiveAnchorActivityScene'));
const rendered=renderLiveAnchorActivityScene('stream-1',{{activity_rows:[{{row_id:'x',role:'thinking',text:'work'}}]}},{{sessionId:'sid-1'}});
const beforeFrame={{restores,scrolls,frames:frames.length}};
while(frames.length)frames.shift()();
process.stdout.write(JSON.stringify({{rendered,beforeFrame,afterFrame:{{restores,scrolls}}}}));
"""
    result = _run_node(script)
    assert result["rendered"] is True
    assert result["beforeFrame"] == {"restores": 0, "scrolls": 0, "frames": 1}
    assert result["afterFrame"] == {"restores": 1, "scrolls": 0}


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_compact_scene_keeps_its_existing_owned_group_for_reconciliation():
    """The outer renderer must not remove the group the keyed reconciler owns."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
let groupRemoves=0,rowRemoves=0,disclosureApplies=0;
const row={{remove(){{rowRemoves++;}}}};
const group={{
  remove(){{groupRemoves++;}},
  contains(node){{return node===row;}},
  getAttribute(name){{return name==='data-activity-disclosure-key'?'live:stream-1':'';}},
}};
const blocks={{
  querySelectorAll(selector){{
    if(selector.includes('[data-anchor-scene-owner="1"]'))return [group,row];
    if(selector.includes('.live-worklog'))return [group];
    return [];
  }},
}};
const turn={{dataset:{{sessionId:'sid-1'}},setAttribute(){{}}}};
global.window={{}};
global.S={{session:{{session_id:'sid-1'}},activeStreamId:'stream-1'}};
global.chatActivityMode=()=> 'compact_worklog';
global.isSimplifiedToolCalling=()=>true;
global.$=id=>id==='liveAssistantTurn'?turn:id==='emptyState'?{{style:{{}}}}:null;
global._anchorSceneRowsForRendering=scene=>scene.activity_rows;
global._liveAnchorRowWindow=rows=>({{rows,hiddenCount:0,total:rows.length}});
global._buildLiveAnchorWindowEdges=()=>({{beforeNode:null,afterNode:null}});
global._assistantTurnBlocks=()=>blocks;
global._captureWorklogDetailDisclosureState=()=>null;
global._captureMessageScrollSnapshot=()=>({{pinned:true}});
global._prepareLiveAnchorScrollRebuildGuard=()=>({{readerAwayFromBottom:false,release:null}});
global._anchorSceneWorklogGroup=()=>group;
global._renderAnchorSceneRowsIntoWorklog=()=>true;
global._restoreWorklogDetailDisclosureState=()=>{{}};
global._startActivityElapsedTimer=()=>{{}};
global._dedupeLiveProcessedWorklogAnchors=()=>group;
global._readActivityDisclosureState=()=>null;
global._applyLiveActivityDisclosureIntent=(candidate,opts,saved)=>{{
  if(candidate===group&&opts.live===true&&opts.collapsed===false&&saved===null) disclosureApplies++;
}};
global._moveLiveRunStatusToTurnEnd=()=>{{}};
global._restoreLiveAnchorScrollSnapshotAfterRebuild=()=>{{}};
eval(extractFunc('renderLiveAnchorActivityScene'));
renderLiveAnchorActivityScene('stream-1',{{activity_rows:[{{row_id:'x',role:'thinking',text:'work'}}]}},{{sessionId:'sid-1'}});
process.stdout.write(JSON.stringify({{groupRemoves,rowRemoves,disclosureApplies}}));
"""
    result = _run_node(script)
    assert result == {"groupRemoves": 0, "rowRemoves": 0, "disclosureApplies": 1}


def test_closed_workspace_panel_skips_hidden_subtree_rendering():
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    closed = "html[data-workspace-panel=\"closed\"] .rightpanel"
    assert closed in css
    rule = css.split(closed, 1)[1].split("}", 1)[0]
    assert "content-visibility:hidden" in rule


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_live_activity_window_scrolls_bidirectionally_with_bounded_overlap():
    """Long active runs page both directions without growing mounted DOM."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
if(!src.includes('const _LIVE_ANCHOR_ROW_CAP=80;')||!src.includes('const _LIVE_ANCHOR_ROW_PAGE=40;')) throw new Error('live anchor row window constants missing');
global._LIVE_ANCHOR_ROW_CAP=80;
global._LIVE_ANCHOR_ROW_PAGE=40;
eval(extractFunc('_liveAnchorRowWindow'));
eval(extractFunc('_shiftLiveAnchorRowWindowState'));
const all=Array.from({{length:500}},(_,i)=>({{row_id:'row-'+i}}));
const turn={{dataset:{{}}}};
const first=_liveAnchorRowWindow(all,turn);
_shiftLiveAnchorRowWindowState(turn,'earlier',all.length);
const earlier=_liveAnchorRowWindow(all,turn);
_shiftLiveAnchorRowWindowState(turn,'newer',all.length);
const newer=_liveAnchorRowWindow(all,turn);
const overlap=first.rows.filter(row=>earlier.rows.includes(row)).length;
process.stdout.write(JSON.stringify({{
  firstCount:first.rows.length,
  firstId:first.rows[0].row_id,
  firstHidden:first.hiddenCount,
  earlierCount:earlier.rows.length,
  earlierFirst:earlier.rows[0].row_id,
  earlierHidden:earlier.hiddenCount,
  earlierNewer:earlier.newerCount,
  overlap,
  newerFirst:newer.rows[0].row_id,
  newerHidden:newer.hiddenCount,
  newerMode:turn.dataset.liveAnchorWindowMode,
}}));
"""
    assert _run_node(script) == {
        "firstCount": 80,
        "firstId": "row-420",
        "firstHidden": 420,
        "earlierCount": 80,
        "earlierFirst": "row-380",
        "earlierHidden": 380,
        "earlierNewer": 40,
        "overlap": 40,
        "newerFirst": "row-420",
        "newerHidden": 420,
        "newerMode": "tail",
    }


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_tool_show_more_reads_full_result_from_row_state_not_html_attribute():
    """Full tool output stays usable without duplicating it into the DOM."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
global._colorDiffLines=text=>text;
eval(extractFunc('_toolCardFullSnippet'));
eval(extractFunc('_toggleToolDiff'));
const pre={{textContent:'short',querySelector:()=>null,appendChild(){{}}}};
const row={{_tcData:{{snippet:'complete result that must remain available'}}}};
const result={{querySelector:selector=>selector==='pre'?pre:null}};
const btn={{
  dataset:{{short:'short',isDiff:'0',moreLabel:'Show more',lessLabel:'Show less'}},
  textContent:'Show more',
  closest:selector=>selector==='.tool-card-result'?result:selector==='.tool-card-row'?row:null,
}};
_toggleToolDiff(btn);
process.stdout.write(JSON.stringify({{text:pre.textContent,label:btn.textContent,htmlCarriesFull:'full' in btn.dataset}}));
"""
    assert _run_node(script) == {
        "text": "complete result that must remain available",
        "label": "Show less",
        "htmlCarriesFull": False,
    }


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_live_activity_window_prefetches_before_scroll_reaches_edge():
    """Approaching a live-window sentinel shifts automatically, without a click."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
let shifts=[];
global.S={{activeStreamId:'stream-1'}};
global._shiftLiveAnchorRowWindow=(_turn,direction)=>shifts.push(direction);
const top={{getBoundingClientRect:()=>({{top:150,bottom:170}})}};
const bottom={{getBoundingClientRect:()=>({{top:1800,bottom:1820}})}};
const turn={{
  isConnected:true,
  dataset:{{liveAnchorWindowMode:'tail'}},
  querySelector:selector=>selector.includes('="earlier"')?top:selector.includes('="newer"')?bottom:null,
}};
const messages={{
  clientHeight:800,
  getBoundingClientRect:()=>({{top:0,bottom:800}}),
}};
global.$=id=>id==='liveAssistantTurn'?turn:null;
eval(extractFunc('_maybeShiftLiveAnchorWindowOnScroll'));
_maybeShiftLiveAnchorWindowOnScroll(messages,{{movedUp:true,movedDown:false}});
_maybeShiftLiveAnchorWindowOnScroll(messages,{{movedUp:false,movedDown:true}});
process.stdout.write(JSON.stringify({{shifts,mode:turn.dataset.liveAnchorWindowMode}}));
"""
    assert _run_node(script) == {"shifts": ["earlier"], "mode": "history"}


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_live_activity_window_shift_preserves_visible_row_position():
    """Swapping overlapping slices compensates scrollTop around a stable row."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
global._LIVE_ANCHOR_ROW_CAP=80;
global._LIVE_ANCHOR_ROW_PAGE=40;
global._liveAnchorWindowShiftBusy=false;
global._programmaticScroll=false;
global._programmaticScrollSetAt=0;
global._lastScrollTop=0;
global.performance={{now:()=>123}};
global.requestAnimationFrame=fn=>{{fn();return 1;}};
global.S={{activeStreamId:'stream-1',session:{{session_id:'sid-1'}}}};
global.chatActivityMode=()=> 'compact_worklog';
let rowTop=100;
const row={{
  getAttribute:name=>name==='data-anchor-row-id'?'row-450':name==='data-anchor-row-role'?'tool':name==='data-anchor-source-event-type'?'tool_complete':'',
  getBoundingClientRect:()=>({{top:rowTop,bottom:rowTop+30}}),
}};
const turn={{
  isConnected:true,
  dataset:{{liveAnchorWindowStart:'420',liveAnchorWindowTotal:'500',liveAnchorWindowMode:'tail',sessionId:'sid-1'}},
  getAttribute:name=>name==='data-anchor-stream-id'?'stream-1':'',
  querySelectorAll:()=>[row],
}};
const messages={{
  scrollTop:500,
  getBoundingClientRect:()=>({{top:0,bottom:800}}),
}};
global.$=id=>id==='messages'?messages:id==='liveAssistantTurn'?turn:null;
global.window={{_renderLiveAnchorActivitySceneForStream:()=>{{rowTop=300;return true;}}}};
for(const name of ['_shiftLiveAnchorRowWindowState','_liveAnchorWindowVisibleAnchor','_findLiveAnchorWindowAnchor','_shiftLiveAnchorRowWindow']) global[name]=eval('('+extractFunc(name)+')');
const shifted=_shiftLiveAnchorRowWindow(turn,'earlier');
process.stdout.write(JSON.stringify({{shifted,start:turn.dataset.liveAnchorWindowStart,mode:turn.dataset.liveAnchorWindowMode,scrollTop:messages.scrollTop,programmatic:_programmaticScroll}}));
"""
    assert _run_node(script) == {
        "shifted": True,
        "start": "380",
        "mode": "history",
        "scrollTop": 700,
        "programmatic": True,
    }


def test_live_tool_dom_does_not_retain_full_serialized_rows():
    node_builder = UI_JS[UI_JS.index("function _anchorSceneNodeForRow"):UI_JS.index("function _anchorSceneTransparentNodeForRow")]
    tool_builder = UI_JS[UI_JS.index("function buildToolCard"):UI_JS.index("function _colorDiffLines")]
    assert "JSON.stringify(row)" not in node_builder
    assert "data-full=" not in tool_builder


def test_returning_session_boot_does_not_animate_transient_empty_logo():
    html = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
    ui = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
    assert "document.documentElement.dataset.sessionBoot='1'" in html
    assert "location.pathname.indexOf('/session/')===0" in html
    assert "localStorage.getItem('hermes-webui-session')" in html
    assert 'html:not([data-session-boot="1"]) .empty-logo svg' in css
    assert 'html[data-session-boot="1"] .empty-state{display:none}' in css
    assert "function showConversationEmptyState()" in ui
    assert "delete document.documentElement.dataset.sessionBoot" in ui


MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
_MESSAGES_SRC_JS = f"const src=require('fs').readFileSync({json.dumps(str(ROOT / 'static' / 'messages.js'))},'utf8');"




@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_compact_echo_suffix_strip_is_linear_on_a_long_interim_message():
    """A 30k-char interim message with a reasoning echo must not scan quadratically."""
    script = f"""
{_MESSAGES_SRC_JS}
{_EXTRACT_FUNC_JS}
eval(extractFunc('_compactVisibleEchoText'));
eval(extractFunc('_stripCompactEchoSuffix'));
const _ECHO_WHITESPACE_RE=/\\s/;
// The pre-HWEB-36 implementation, kept here as the behavioural oracle.
function naive(value, suffix){{
  const raw=String(value||'');
  const candidate=_compactVisibleEchoText(suffix);
  if(!raw||!candidate) return {{text:raw,removed:false}};
  const windowSize=Math.max(String(suffix||'').length*3,4096);
  const offset=Math.max(0,raw.length-windowSize);
  const tail=raw.slice(offset);
  for(let idx=0;idx<=tail.length;idx+=1){{
    if(_compactVisibleEchoText(tail.slice(idx))===candidate){{
      return {{text:raw.slice(0,offset+idx).trimEnd(),removed:true}};
    }}
  }}
  return {{text:raw,removed:false}};
}}
const shortEcho='the model restated this visible sentence';
const cases=[
  ['', shortEcho],
  ['nothing to strip here', shortEcho],
  ['prefix text\\n\\n'+shortEcho, shortEcho],
  ['prefix text\\n\\n'+shortEcho.replace(/ /g,'\\n'), shortEcho],
  ['prefix '+shortEcho+' trailing', shortEcho],
  [shortEcho, shortEcho],
  ['x'.repeat(5000)+'  '+shortEcho+'  ', shortEcho],
  ['x'.repeat(30000), shortEcho],
  ['x'.repeat(30000)+'\\n'+shortEcho, shortEcho],
];
const mismatches=[];
for(const [value,suffix] of cases){{
  const a=_stripCompactEchoSuffix(value,suffix);
  const b=naive(value,suffix);
  if(a.removed!==b.removed||a.text!==b.text) mismatches.push({{value:value.slice(0,40),a,b}});
}}
// `windowSize` is max(suffix.length*3, 4096), so only a long visible echo
// forces the 30k tail the quadratic scan choked on. A short echo pins the
// window at the 4096 floor and would let the old implementation pass.
const echo='visible echoed sentence '.repeat(500);
const windowSize=Math.max(echo.length*3,4096);
const filler='word '.repeat(8000);
const long=filler+'\\n\\n'+echo;
const started=Date.now();
let result=null;
// Four calls: what one interim_assistant event with a reasoning_echo makes.
for(let i=0;i<4;i+=1) result=_stripCompactEchoSuffix(long,echo);
const elapsedMs=Date.now()-started;
process.stdout.write(JSON.stringify({{
  mismatches,
  windowSize,
  tailLength:Math.min(long.length,windowSize),
  removed:result.removed,
  textOk:result.text===filler.trimEnd(),
  elapsedMs,
}}));
"""
    result = _run_node(script)
    assert result["mismatches"] == []
    # The window must actually reach 30k, or this is not a regression gate.
    assert result["windowSize"] >= 30000, result
    assert result["tailLength"] >= 30000, result
    assert result["removed"] is True
    assert result["textOk"] is True
    # The quadratic scan needs ~450M character copies per call at this window
    # size and takes seconds; it cannot pass this bound.
    assert result["elapsedMs"] < 250, result


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_post_tool_segments_use_the_incremental_xml_tool_call_stripper():
    """Segments after the first must not re-scan their whole text every frame."""
    assert "_stripXmlToolCalls(assistantText.slice(segmentStart))" not in MESSAGES_JS, (
        "live render paths must go through _stripXmlToolCallsForSegment()"
    )
    script = f"""
{_MESSAGES_SRC_JS}
{_EXTRACT_FUNC_JS}
eval(extractFunc('_createIncrementalXmlToolCallStripper'));
eval(extractFunc('_stripXmlToolCallsForSegment'));
global._segmentXmlStripper=null;
global._segmentXmlStripperStart=-1;
let batchCalls=0;
global._stripXmlToolCalls=s=>{{batchCalls++;return String(s).replace(/<function_calls>[\\s\\S]*?<\\/function_calls>/g,'').trim();}};
global.segmentStart=100;
global.assistantText='p'.repeat(100)+'answer';
const frames=[];
for(let i=0;i<200;i+=1){{
  assistantText+=' token'+i;
  frames.push(_stripXmlToolCallsForSegment());
}}
const cleanCalls=batchCalls;
const cleanTail=frames[frames.length-1];
// A tool-call marker arriving mid-segment still has to be stripped.
assistantText+='<function_calls>{{"name":"read"}}</function_calls>';
const stripped=_stripXmlToolCallsForSegment();
const markerCalls=batchCalls-cleanCalls;
// A new segment gets a fresh stripper, so the previous segment's latched
// marker state cannot force batch scans on it.
segmentStart=assistantText.length;
assistantText+='second segment prose';
const nextSegment=_stripXmlToolCallsForSegment();
process.stdout.write(JSON.stringify({{
  cleanCalls,
  markerCalls,
  nextSegmentCalls:batchCalls-cleanCalls-markerCalls,
  cleanTailOk:cleanTail.endsWith('token199'),
  strippedOk:stripped.indexOf('function_calls')===-1&&stripped.startsWith('answer'),
  nextSegment,
}}));
"""
    result = _run_node(script)
    assert result["cleanCalls"] == 0, result
    assert result["markerCalls"] == 1, result
    assert result["nextSegmentCalls"] == 0, result
    assert result["cleanTailOk"] is True
    assert result["strippedOk"] is True
    assert result["nextSegment"] == "second segment prose"


@pytest.mark.skipif(NODE is None, reason="node is required for DOM-executed live-render tests")
def test_live_anchor_scene_skips_cleanup_sweeps_until_the_turn_changes():
    """Unchanged frames skip the whole-turn sweeps; a new child re-runs them."""
    script = f"""
const fs=require('fs');
const src=fs.readFileSync({json.dumps(str(ROOT / 'static' / 'ui.js'))},'utf8');
{_EXTRACT_FUNC_JS}
let sweeps=0;
const removals=[];
const hidden=[];
const makeRow=(name,attrs)=>({{
  name,
  attributes:attrs||{{}},
  classList:{{add(){{}}}},
  getAttribute(k){{return this.attributes[k]||null;}},
  setAttribute(k,v){{this.attributes[k]=String(v);}},
  remove(){{removals.push(this.name);}},
  set hidden(v){{hidden.push(this.name);}},
}});
const group={{
  contains:()=>false,
  getAttribute:name=>name==='data-activity-disclosure-key'?'live:stream-1':'',
}};
let foreignRows=[makeRow('foreign-scene-row')];
let liveSegments=[makeRow('segment-1')];
const blocks={{
  _children:[{{}},{{}}],
  get childElementCount(){{return this._children.length;}},
  querySelectorAll(selector){{
    sweeps++;
    if(selector.includes('[data-anchor-scene-owner="1"]')) return foreignRows.slice();
    if(selector.includes('.live-worklog')) return [];
    if(selector.includes('[data-live-assistant="1"]')) return liveSegments.slice();
    return [];
  }},
}};
const turn={{dataset:{{sessionId:'sid-1'}},setAttribute(){{}}}};
global.window={{}};
global.S={{session:{{session_id:'sid-1'}},activeStreamId:'stream-1'}};
global.chatActivityMode=()=> 'compact_worklog';
global.isSimplifiedToolCalling=()=>true;
global.$=id=>id==='liveAssistantTurn'?turn:id==='emptyState'?{{style:{{}}}}:null;
global._anchorSceneRowsForRendering=scene=>scene.activity_rows;
global._liveAnchorRowWindow=rows=>({{rows,hiddenCount:0,total:rows.length}});
global._buildLiveAnchorWindowEdges=()=>({{beforeNode:null,afterNode:null}});
global._assistantTurnBlocks=()=>blocks;
global._captureWorklogDetailDisclosureState=()=>null;
global._captureMessageScrollSnapshot=()=>({{pinned:true}});
global._prepareLiveAnchorScrollRebuildGuard=()=>({{readerAwayFromBottom:false,release:null}});
global._anchorSceneWorklogGroup=()=>group;
global._renderAnchorSceneRowsIntoWorklog=()=>true;
global._restoreWorklogDetailDisclosureState=()=>{{}};
global._startActivityElapsedTimer=()=>{{}};
global._dedupeLiveProcessedWorklogAnchors=()=>group;
global._readActivityDisclosureState=()=>null;
global._applyLiveActivityDisclosureIntent=()=>{{}};
global._moveLiveRunStatusToTurnEnd=()=>{{}};
global._restoreLiveAnchorScrollSnapshotAfterRebuild=()=>{{}};
global._liveAnchorSceneSweepGenerations=new WeakMap();
eval(extractFunc('renderLiveAnchorActivityScene'));
const scene={{activity_rows:[{{row_id:'x',role:'thinking',text:'work'}}]}};
const render=()=>renderLiveAnchorActivityScene('stream-1',scene,{{sessionId:'sid-1'}});

render();
const first={{sweeps,removals:removals.slice(),hidden:hidden.slice()}};
// Nothing about the turn changed: the sweeps must not run again.
render();
render();
const cached={{sweeps}};
// A new live assistant segment lands as a new direct child of `blocks`, which
// bumps the generation and must re-run every sweep on the new nodes.
foreignRows=[makeRow('foreign-scene-row-2')];
liveSegments=[makeRow('segment-1'),makeRow('segment-2')];
blocks._children.push({{}});
render();
const afterChange={{sweeps,removals:removals.slice(),hidden:hidden.slice()}};
process.stdout.write(JSON.stringify({{first,cached,afterChange}}));
"""
    result = _run_node(script)
    # Frame 1 runs all three sweeps and cleans up.
    assert result["first"]["sweeps"] == 3, result
    assert result["first"]["removals"] == ["foreign-scene-row"], result
    assert result["first"]["hidden"] == ["segment-1"], result
    # Frames 2 and 3 change nothing, so no sweep runs at all.
    assert result["cached"]["sweeps"] == 3, result
    # A new direct child invalidates the generation and the sweeps run again.
    assert result["afterChange"]["sweeps"] == 6, result
    assert result["afterChange"]["removals"] == ["foreign-scene-row", "foreign-scene-row-2"], result
    assert result["afterChange"]["hidden"] == ["segment-1", "segment-1", "segment-2"], result
