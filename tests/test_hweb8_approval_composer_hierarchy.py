"""HWEB-8: the approval strip gives primary weight only to the choices that are
valid for the request in front of the user.

`Allow once` and `Deny` answer THIS request and stay in the button row. The
policy changes that outlive it — `Allow session`, `Always allow` and YOLO —
move behind one overflow menu, so the strip reads as the next composer action
instead of five equally weighted buttons.
"""

import json
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
NODE = shutil.which("node")


def _element(html: str, marker: str, closing: str) -> str:
    start = html.index(marker)
    return html[start : html.index(closing, start)]


def _primary_row() -> str:
    return _element(INDEX_HTML, '<div class="approval-btns" id="approvalBtns">', "</div>")


def _overflow_menu() -> str:
    return _element(INDEX_HTML, '<div class="approval-more-menu" id="approvalMoreMenu"', "\n          </div>")


# ── Hierarchy ────────────────────────────────────────────────────────────────

def test_primary_row_offers_only_the_two_choices_valid_for_this_request():
    row = _primary_row()
    assert "respondApproval('once')" in row
    assert "respondApproval('deny')" in row
    for policy in ("respondApproval('session')", "respondApproval('always')", "toggleYoloFromApproval()"):
        assert policy not in row, f"{policy} must not sit in the primary approval row"


def test_policy_choices_live_in_the_overflow_menu():
    menu = _overflow_menu()
    assert 'role="menu"' in INDEX_HTML[INDEX_HTML.index('id="approvalMoreMenu"') - 120 : INDEX_HTML.index('id="approvalMoreMenu"') + 120]
    for btn_id in ("approvalBtnSession", "approvalBtnAlways", "approvalSkipAll"):
        assert f'id="{btn_id}"' in menu, f"{btn_id} missing from the approval overflow menu"
    assert menu.count('role="menuitem"') == 3


def test_overflow_trigger_is_wired_to_its_menu():
    trigger = _element(INDEX_HTML, '<button class="approval-btn more" id="approvalMoreBtn"', "</button>")
    assert 'aria-haspopup="menu"' in trigger
    assert 'aria-expanded="false"' in trigger
    assert 'aria-controls="approvalMoreMenu"' in trigger
    assert 'onclick="toggleApprovalMoreMenu()"' in trigger


def test_skin_no_longer_promotes_allow_session_to_a_primary_button():
    # geist-contrast paints its primary actions with the accent fill; "Allow
    # session" is a policy choice now, so it must not be on that list.
    assert '.approval-btn.session,' not in STYLE_CSS
    assert ':root[data-skin="geist-contrast"] .approval-btn.once,' in STYLE_CSS


def test_collapsed_strip_hides_the_overflow_menu_with_the_buttons():
    collapsed = [line for line in STYLE_CSS.splitlines() if ".approval-card.collapsed .approval-btns" in line]
    assert collapsed and ".approval-card.collapsed .approval-more-menu" in collapsed[0]


def test_overflow_menu_is_closed_by_default_in_css():
    assert ".approval-more-menu[hidden]{display:none;}" in STYLE_CSS


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_enter_on_a_card_control_activates_it_instead_of_approving():
    # The Allow-once accelerator must not fire for Deny, More options, collapse
    # or dismiss — activating any of those would silently approve the command.
    start = BOOT_JS.index("// Enter is the Allow-once accelerator")
    handler = BOOT_JS[start : BOOT_JS.index("if((e.metaKey||e.ctrlKey)&&e.key==='k')", start)]
    script = "\n".join([
        "const out=[]; let active=null;",
        "const card={classList:{contains:v=>v==='visible'},contains:node=>node&&node.inCard};",
        "const document={get activeElement(){return active;}};",
        "const $=id=>(id==='approvalCard'?card:null);",
        "const respondApproval=choice=>{out.push('respondApproval:'+choice);};",
        "function press(label,el){active=el;let prevented=false;",
        " const e={key:'Enter',preventDefault(){prevented=true;}};",
        " (function(e){" + handler + "})(e);",
        " out.push([label,prevented]);}",
        "press('deny-button',{tagName:'BUTTON',inCard:true});",
        "press('more-button',{tagName:'BUTTON',inCard:true});",
        "press('composer-textarea',{tagName:'TEXTAREA',inCard:false});",
        "press('nothing-focused',null);",
        "press('body',{tagName:'BODY',inCard:false});",
        "process.stdout.write(JSON.stringify(out));",
    ])
    assert _run_node(script) == [
        ["deny-button", False],
        ["more-button", False],
        ["composer-textarea", False],
        # focus outside the card's controls still gets the accelerator
        "respondApproval:once",
        ["nothing-focused", True],
        "respondApproval:once",
        ["body", True],
    ]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_escape_only_claims_an_on_screen_menu():
    # switchPanel() hides #mainChat with display:none and leaves the menu's own
    # hidden flag alone, so an off-screen menu must not swallow the Escape that
    # closes Settings.
    start = BOOT_JS.index("// Close the approval overflow menu first")
    guard = BOOT_JS[start : BOOT_JS.index("// Close onboarding overlay if open", start)]
    script = "\n".join([
        "const out=[]; let menu={hidden:false,offsetParent:{}};",
        "const $=id=>(id==='approvalMoreMenu'?menu:null);",
        "const closeApprovalMoreMenu=()=>{out.push('closed');menu.hidden=true;return true;};",
        "function escape(label){let claimed=true;",
        " (function(){" + guard + " claimed=false;})();",
        " out.push([label,claimed]);}",
        "escape('on-screen');",
        "menu={hidden:false,offsetParent:null};",   # chat replaced by a panel
        "escape('off-screen');",
        "menu={hidden:true,offsetParent:{}};",
        "escape('menu-closed');",
        "process.stdout.write(JSON.stringify(out));",
    ])
    assert _run_node(script) == [
        "closed",
        ["on-screen", True],
        ["off-screen", False],   # Escape falls through to the settings panel
        ["menu-closed", False],
    ]


def test_collapsed_clarify_strip_hides_the_counter_with_the_rest_of_the_body():
    collapsed = [line for line in STYLE_CSS.splitlines() if ".clarify-card.collapsed .clarify-hint" in line]
    assert collapsed and ".clarify-card.collapsed .clarify-counter" in collapsed[0]


def test_escape_closes_the_menu_before_any_surface_behind_it():
    onboarding = BOOT_JS.index("// Close onboarding overlay if open")
    start = BOOT_JS.rindex("if(e.key==='Escape'){", 0, onboarding)
    escape_block = BOOT_JS[start:][:2000]
    assert "closeApprovalMoreMenu()" in escape_block
    assert escape_block.index("closeApprovalMoreMenu()") < escape_block.index("onboardingOverlay")


# ── Behavior ─────────────────────────────────────────────────────────────────

def _run_node(script: str) -> dict:
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as handle:
        handle.write(script)
        path = handle.name
    result = subprocess.run([NODE, path], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _menu_source() -> str:
    start = MESSAGES_JS.index("// ── Approval policy overflow ──")
    return MESSAGES_JS[start : MESSAGES_JS.index("\nasync function respondApproval(", start)]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_toggling_the_menu_tracks_aria_focus_and_transcript_space():
    script = "\n".join([
        "const out={syncs:0};",
        "function El(){this.hidden=true;this.attrs={};this.disabled=false;this.focused=0;this.classes=[];}",
        "El.prototype.setAttribute=function(k,v){this.attrs[k]=v;};",
        "El.prototype.contains=function(node){return node===this||node===this.child;};",
        "El.prototype.querySelector=function(){return this.child;};",
        "El.prototype.focus=function(){this.focused++;active=this;};",
        "const menu=new El(); const item=new El(); menu.child=item;",
        "const btn=new El(); btn.hidden=false;",
        "const card={classList:{contains:()=>true}};",
        "let active=null;",
        "const els={approvalMoreMenu:menu,approvalMoreBtn:btn,approvalCard:card};",
        "const $=id=>els[id];",
        "const document={get activeElement(){return active;},addEventListener(){}};",
        "const _syncApprovalTranscriptSpace=()=>{out.syncs++;};",
        _menu_source(),
        "out.openReturn=toggleApprovalMoreMenu();",
        "out.openHidden=menu.hidden; out.openAria=btn.attrs['aria-expanded']; out.itemFocused=item.focused;",
        "out.closeReturn=closeApprovalMoreMenu();",
        "out.closedHidden=menu.hidden; out.closedAria=btn.attrs['aria-expanded']; out.triggerFocused=btn.focused;",
        "out.secondClose=closeApprovalMoreMenu();",
        "out.forceOpenIdempotent=(toggleApprovalMoreMenu(true),toggleApprovalMoreMenu(true),out.syncs);",
        "process.stdout.write(JSON.stringify(out));",
    ])
    out = _run_node(script)
    assert out["openReturn"] is True
    assert out["openHidden"] is False and out["openAria"] == "true"
    assert out["itemFocused"] == 1, "opening the menu must land focus on its first choice"
    assert out["closeReturn"] is True
    assert out["closedHidden"] is True and out["closedAria"] == "false"
    assert out["triggerFocused"] == 1, "closing must return focus to the trigger"
    assert out["secondClose"] is False, "a closed menu must not swallow Escape"
    # open, close, open — a redundant force-open must not re-measure the card
    assert out["forceOpenIdempotent"] == 3


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_arrow_keys_cycle_the_menu_choices():
    script = "\n".join([
        "function El(id){this.id=id;this.disabled=false;this.focused=0;}",
        "El.prototype.focus=function(){this.focused++;active=this;};",
        "const items=[new El('a'),new El('b'),new El('c')];",
        "items[1].disabled=true;",
        "const menu={hidden:false,contains:node=>items.includes(node),",
        " querySelectorAll:sel=>items.filter(i=>!i.disabled)};",
        "let active=items[0];",  # opening the menu already focused its first choice
        "const $=id=>(id==='approvalMoreMenu'?menu:undefined);",
        "const document={get activeElement(){return active;},addEventListener(){}};",
        "const _syncApprovalTranscriptSpace=()=>{};",
        _menu_source(),
        "const seen=[];",
        "for(const key of ['ArrowDown','ArrowDown','ArrowDown','ArrowUp','Enter']){",
        " const handled=_approvalMoreMenuArrowKey({key});",
        " seen.push([key,handled,active?active.id:null]);",
        "}",
        "menu.hidden=true;",
        "seen.push(['closed',_approvalMoreMenuArrowKey({key:'ArrowDown'}),null]);",
        "process.stdout.write(JSON.stringify(seen));",
    ])
    assert _run_node(script) == [
        ["ArrowDown", True, "c"],   # the disabled choice is skipped
        ["ArrowDown", True, "a"],   # and the list wraps
        ["ArrowDown", True, "c"],
        ["ArrowUp", True, "a"],
        ["Enter", False, "a"],      # other keys fall through untouched
        ["closed", False, None],
    ]


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_menu_helpers_no_op_without_the_card_in_the_dom():
    script = "\n".join([
        "const document={activeElement:null,addEventListener(){}};",
        "const $=()=>undefined;",
        "const _syncApprovalTranscriptSpace=()=>{throw new Error('measured a card that is not there');};",
        _menu_source(),
        "process.stdout.write(JSON.stringify({open:toggleApprovalMoreMenu(),close:closeApprovalMoreMenu()}));",
    ])
    assert _run_node(script) == {"open": False, "close": False}


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_repeat_render_of_the_same_approval_leaves_focus_alone():
    # /api/approval/pending polls every 1.5s and re-renders the same card. That
    # tick must not pull focus back to Allow once — with the overflow open the
    # next Enter would approve instead of activating the navigated choice.
    start = MESSAGES_JS.index("function showApprovalCard(pending, pendingCount) {")
    show_approval = MESSAGES_JS[start : MESSAGES_JS.index("\nfunction dismissApprovalCard(", start)]
    script = "\n".join([
        "const out={focusCalls:[]};",
        "function El(id){this.id=id;this.textContent='';this.style={};this.disabled=false;",
        " this.classes=new Set();this.attrs={};",
        " this.classList={contains:c=>this.classes.has(c),add:c=>this.classes.add(c),",
        "  remove:c=>this.classes.delete(c),toggle:(c,on)=>(on?this.classes.add(c):this.classes.delete(c))};}",
        "El.prototype.focus=function(){out.focusCalls.push(this.id);};",
        "El.prototype.setAttribute=function(k,v){this.attrs[k]=v;};",
        "El.prototype.querySelector=function(){return null;};",
        "const els={}; const $=id=>(els[id]||(els[id]=new El(id)));",
        "const document={activeElement:null};",
        "const setTimeout=fn=>fn();",   # run the deferred focus inline
        "const S={session:{session_id:'s1'}}; let _loadSessionGeneration=1;",
        "let _approvalSessionId=null,_approvalCurrentId=null,_approvalSignature='',",
        " _approvalVisibleSince=0,_approvalDisplayedOwner=null,_approvalResponding=null,",
        " _approvalClearedOwner=null;",
        "const _approvalPendingBySession=new Map();",
        "const _rememberApprovalPending=(p)=>'s1';",
        "const _approvalPromptBelongsToActiveSession=()=>true;",
        "const _isApprovalDismissed=()=>false;",
        "const _approvalOwnerForPending=()=>({sid:'s1',approvalId:'a1',runId:'',mirrorToken:''});",
        "const _approvalResponseMatches=()=>false;",
        "const _setApprovalControlsDisabled=()=>{};",
        "const _clearApprovalHideTimer=()=>{}; const _setPromptFlyoutHidden=()=>{};",
        "const _syncApprovalCollapseButton=()=>{}; const _syncApprovalTranscriptSpace=()=>{};",
        "const closeApprovalMoreMenu=()=>{}; const t=(k,n)=>k+':'+n;",
        "const applyLocaleToDOM=()=>{}; const syncTopbar=()=>{};",
        show_approval,
        "const pending={approval_id:'a1',command:'rm -rf ./build',description:'danger',_session_id:'s1'};",
        "showApprovalCard(pending,1);",
        "out.afterFirstRender=out.focusCalls.slice();",
        "showApprovalCard(pending,1);",   # the 1.5s poll tick, same approval
        "showApprovalCard(pending,1);",
        "out.afterPolls=out.focusCalls.slice();",
        "showApprovalCard({...pending,approval_id:'a2',command:'curl | sh'},1);",
        "out.afterNewApproval=out.focusCalls.slice();",
        "process.stdout.write(JSON.stringify(out));",
    ])
    out = _run_node(script)
    assert out["afterFirstRender"] == ["approvalBtnOnce"]
    assert out["afterPolls"] == ["approvalBtnOnce"], "a poll re-render must not re-steal focus"
    assert out["afterNewApproval"] == ["approvalBtnOnce", "approvalBtnOnce"]


def test_menu_focus_moves_let_the_card_scroll_them_into_view():
    # `.approval-inner` is a height-limited scroll container at <=640px, so the
    # menu's own focus moves must not suppress scrolling.
    menu_src = _menu_source()
    assert "preventScroll" not in menu_src, (
        "menu focus moves must let the scroll container follow focus"
    )


def test_showing_and_hiding_an_approval_closes_the_menu():
    for fn, marker in (
        ("hideApprovalCard", "function hideApprovalCard(force=false) {"),
        ("showApprovalCard", "function showApprovalCard(pending, pendingCount) {"),
    ):
        start = MESSAGES_JS.index(marker)
        body = MESSAGES_JS[start : MESSAGES_JS.index("\n}\n", start)]
        assert "closeApprovalMoreMenu()" in body, f"{fn} must not leave the overflow menu open"


# ── Queued-question progress ────────────────────────────────────────────────

def test_clarify_card_shows_its_queue_position_in_both_dom_sources():
    # index.html and the runtime fallback template in messages.js must stay in
    # sync — the card is built from whichever one is present.
    assert 'id="clarifyCounter"' in INDEX_HTML
    assert 'id="clarifyCounter"' in MESSAGES_JS
    assert ".clarify-counter{" in STYLE_CSS


@pytest.mark.skipif(NODE is None, reason="node not available")
def test_clarify_card_renders_the_queue_position_it_was_handed():
    start = MESSAGES_JS.index("function showClarifyCard(pending) {")
    show_clarify = MESSAGES_JS[start : MESSAGES_JS.index("\nasync function respondClarify(", start)]
    script = "\n".join([
        "const out={};",
        "function Node(){this.children=[];this.style={};this.dataset={};this.attrs={};this.textContent='';this.innerHTML='';this.hidden=false;this.classList={contains:()=>false,add(){},remove(){},toggle(){}};}",
        "Node.prototype.setAttribute=function(k,v){this.attrs[k]=v;};",
        "Node.prototype.removeAttribute=function(){};",
        "Node.prototype.appendChild=function(c){this.children.push(c);};",
        "Node.prototype.focus=function(){};",
        "Node.prototype.querySelector=function(){return null;};",
        "const els={}; const $=id=>(els[id]||(els[id]=new Node()));",
        "const document={createElement:()=>new Node()};",
        "const S={session:{session_id:'s1'}};",
        "const t=(k,n)=>k+':'+n;",
        "const _clarifyPromptBelongsToActiveSession=()=>true;",
        "const _rememberClarifyPending=p=>'s1';",
        "const _ensureClarifyCardDom=()=>$('clarifyCard');",
        "const _startClarifyCountdown=()=>{}; const _clearClarifyCountdownTimer=()=>{};",
        "const _renderClarifyBatch=()=>{}; const _clarifySetControlsDisabled=()=>{};",
        "const _ensureClarifyResizeListener=()=>{}; const _setPromptFlyoutHidden=()=>{};",
        "const _syncClarifyCollapseButton=()=>{}; const _syncClarifyTranscriptSpace=()=>{};",
        "const lockComposerForClarify=()=>{}; const applyLocaleToDOM=()=>{}; const syncTopbar=()=>{};",
        "const respondClarify=()=>{};",
        "let _clarifySessionId=null,_clarifyId=null,_clarifySignature='',_clarifyVisibleSince=0;",
        "const _clearClarifyHideTimer=()=>{};",
        show_clarify,
        "showClarifyCard({question:'Which branch?',clarify_id:'c1',pending_count:3});",
        "out.queued={text:$('clarifyCounter').textContent,display:$('clarifyCounter').style.display};",
        "showClarifyCard({question:'Only one?',clarify_id:'c2',pending_count:1});",
        "out.single={text:$('clarifyCounter').textContent,display:$('clarifyCounter').style.display};",
        "process.stdout.write(JSON.stringify(out));",
    ])
    out = _run_node(script)
    assert out["queued"] == {"text": "approval_pending_count:3", "display": ""}
    assert out["single"]["display"] == "none"


@pytest.mark.parametrize("advance", ["oldest", "by_id"])
def test_live_clarify_callback_carries_the_queue_depth(advance):
    # The live path (streaming.py::_clarify_notify_cb -> SSE "clarify") forwards
    # this payload verbatim, so the count has to be on it — the poll is slower
    # and a queued question can be answered before the first poll lands. Every
    # head emission counts, not just arrival: a queue advance that drops the
    # field blanks the counter until the next poll.
    import api.clarify as clarify_mod

    sid = "hweb8-live-clarify-" + advance
    seen = []
    clarify_mod.register_gateway_notify(sid, seen.append)
    try:
        first = clarify_mod.submit_pending(sid, {"question": "first?"})
        clarify_mod.submit_pending(sid, {"question": "second?"})
        clarify_mod.submit_pending(sid, {"question": "third?"})
        if advance == "oldest":
            clarify_mod.resolve_clarify(sid, "answer")
        else:
            clarify_mod.resolve_clarify_by_id(sid, first.clarify_id, "answer")
    finally:
        clarify_mod.unregister_gateway_notify(sid)
        clarify_mod.clear_pending(sid)

    # three arrivals (head unchanged, depth growing) then the advance to "second?"
    assert [payload["pending_count"] for payload in seen] == [1, 2, 3, 2]
    assert [payload["question"] for payload in seen] == [
        "first?", "first?", "first?", "second?",
    ]


def test_clarify_poll_plumbs_the_pending_count_onto_the_prompt():
    poll = re.search(r"if \(data\.pending\) \{.*?showClarifyForSession\(sid, data\.pending\);", MESSAGES_JS, re.S)
    assert poll and "data.pending_count" in poll.group(0)
    counter = MESSAGES_JS[MESSAGES_JS.index('const counter = $("clarifyCounter");') :][:400]
    assert "pending.pending_count" in counter and "pendingCount > 1" in counter
