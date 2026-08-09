"""Behavioral regressions for the focused chat/frontend audit fixes."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
UI = (ROOT / "static" / "ui.js").read_text(encoding="utf-8")
COMMANDS = (ROOT / "static" / "commands.js").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    markers = (f"async function {name}", f"function {name}")
    start = next((source.index(marker) for marker in markers if marker in source), None)
    assert start is not None, name
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        if source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return source[start : index + 1]
    raise AssertionError(f"unterminated function {name}")


def _node(script: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    result = subprocess.run(
        [node, "-e", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_partial_upload_failure_rolls_back_successful_prefix_and_reports_both_sides():
    # These are the final upload declarations in ui.js; slice to EOF so braces
    # inside template literals do not confuse the generic test extractor.
    upload = UI[UI.index("async function _rollbackUploadedFiles") :].strip()
    script = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'A'}},pendingFiles:[{{name:'kept'}}]}};
        const MAX_UPLOAD_BYTES=1e9, _ARCHIVE_EXTS=/\\.zip$/i;
        const document={{baseURI:'http://local/'}}; const location={{href:'http://local/'}};
        class FormData{{append(){{}}}};
        let request=0, renders=0, rollbackBody=null;
        async function fetch(){{
          request++;
          if(request===1) return {{ok:true,json:async()=>({{filename:'ok.txt',path:'/ok.txt',rollback_token:'receipt-1'}})}};
          return {{ok:false,text:async()=> 'disk full'}};
        }}
        async function api(path,opts){{rollbackBody=JSON.parse(opts.body);return {{ok:true,rolled_back:1,failed:0}};}}
        function _redirectIfUnauth(){{return false;}}
        function _uploadTooLargeMessage(){{return 'too large';}}
        function _uploadPendingFilesUpdateProgress(){{}}
        function _uploadPendingFilesCurrentSession(sid){{return S.session.session_id===sid;}}
        function renderTray(){{renders++;}}
        function setStatus(){{}} function showToast(){{}}
        function t(key,n){{return key==='all_uploads_failed'?`all ${{n}} failed`:key;}}
        {upload}
        (async()=>{{
          const files=[{{name:'ok.txt',size:1}},{{name:'bad.txt',size:1}}];
          try{{await uploadPendingFiles({{files,sessionId:'A'}});}}
          catch(error){{
            console.log(JSON.stringify({{
              message:error.message,
              failed:error.failedFiles.map(f=>f.name),
              uploaded:error.uploaded.map(f=>f.name),
              pending:S.pendingFiles.map(f=>f.name),
              renders,
              rollbackBody,
            }}));
          }}
        }})().catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    result = _node(script)
    assert result["failed"] == ["bad.txt"]
    assert result["uploaded"] == ["ok.txt"]
    assert result["pending"] == ["kept"]
    assert result["rollbackBody"] == {"session_id": "A", "rollback_tokens": ["receipt-1"]}
    assert "1 of 2 uploads failed" in result["message"]


def test_partial_upload_rollback_failure_is_reported_without_losing_retry_intent():
    upload = UI[UI.index("async function _rollbackUploadedFiles") :].strip()
    script = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'A'}},pendingFiles:[{{name:'new-draft.txt'}}]}};
        const MAX_UPLOAD_BYTES=1e9, _ARCHIVE_EXTS=/\\.zip$/i;
        const document={{baseURI:'http://local/'}}; const location={{href:'http://local/'}};
        class FormData{{append(){{}}}};
        let request=0;
        async function fetch(){{
          request++;
          if(request===1)return {{ok:true,json:async()=>({{filename:'same.txt',path:'/same.txt',rollback_token:'receipt-1'}})}};
          return {{ok:false,text:async()=> 'disk full'}};
        }}
        async function api(){{return {{ok:false,rolled_back:0,failed:1}};}}
        function _redirectIfUnauth(){{return false;}} function _uploadTooLargeMessage(){{return 'too large';}}
        function _uploadPendingFilesUpdateProgress(){{}} function _uploadPendingFilesCurrentSession(){{return true;}}
        function renderTray(){{}} function setStatus(){{}} function showToast(){{}}
        function t(key,n){{return key==='all_uploads_failed'?`all ${{n}} failed`:key;}}
        {upload}
        (async()=>{{
          const files=[{{name:'same.txt',size:1}},{{name:'bad.txt',size:1}}];
          try{{await uploadPendingFiles({{files,sessionId:'A'}});}}
          catch(error){{console.log(JSON.stringify({{
            rollbackFailed:!!error.rollbackError,
            message:error.message,
            retryDraft:S.pendingFiles.map(file=>file.name),
          }}));}}
        }})().catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    result = _node(script)
    assert result["rollbackFailed"] is True
    assert "cleanup failed" in result["message"]
    assert result["retryDraft"] == ["new-draft.txt"]


def test_send_restores_whole_intent_and_returns_on_any_upload_error():
    body = _function(MESSAGES, "send")
    catch_start = body.index("catch(e){", body.index("await uploadPendingFiles"))
    catch_end = body.index("if(_abortSendAfterOwnerSwitch", catch_start)
    catch_body = body[catch_start:catch_end]
    assert "if(!text)" not in catch_body
    assert "_restoreComposerDraftAfterFailedSend(" in catch_body
    assert "_failedSendFilesSnapshot" in catch_body
    assert "return;" in catch_body


def test_compact_render_cache_verifies_full_content_on_collision():
    start = UI.index("const _renderCache = new Map()")
    end = UI.index("function _currentMessageRenderWindowSize", start)
    cache_code = UI[start:end]
    script = textwrap.dedent(
        f"""
        const window={{_renderUserMarkdown:false}}; let renders=0;
        function renderMd(text){{renders++;return 'render:'+text.slice(250,251);}}
        function _renderUserFencedBlocks(text){{renders++;return 'user:'+text;}}
        function _stripXmlToolCallsDisplay(text){{return text;}}
        {cache_code}
        const first='x'.repeat(20)+'A'.repeat(560)+'z'.repeat(20);
        const second='x'.repeat(20)+'B'.repeat(560)+'z'.repeat(20);
        const values=[_getCachedRender(first,false),_getCachedRender(second,false),_getCachedRender(first,false)];
        console.log(JSON.stringify({{values,renders,sameKey:_renderCacheKey(first,false)===_renderCacheKey(second,false)}}));
        """
    )
    result = _node(script)
    assert result == {
        "values": ["render:A", "render:B", "render:A"],
        "renders": 3,
        "sameKey": True,
    }


def test_regenerate_does_not_mutate_destination_during_truncate_await():
    owner = _function(UI, "_sessionStillOwnsAsyncChatAction")
    regenerate = _function(UI, "regenerateResponse")
    script = textwrap.dedent(
        f"""
        let _loadingSessionId=null, _oldestIdx=0, sends=0, renders=0, resolveTruncate;
        const input={{value:'destination draft'}};
        const S={{busy:false,session:{{session_id:'A'}},messages:[
          {{role:'user',content:'question'}},{{role:'assistant',content:'answer'}}
        ]}};
        function msgContent(m){{return m.content;}}
        async function _ensureAllMessagesLoaded(){{}}
        function api(){{return new Promise(resolve=>{{resolveTruncate=resolve;}});}}
        function renderMessages(){{renders++;}} function $(){{return input;}}
        async function send(){{sends++;}} function setStatus(){{}} function t(){{return '';}}
        {owner}
        {regenerate}
        const btn={{closest:()=>({{dataset:{{msgIdx:'1'}}}})}};
        const pending=regenerateResponse(btn);
        setTimeout(()=>{{_loadingSessionId='B';resolveTruncate({{}});}},0);
        pending.then(()=>console.log(JSON.stringify({{
          count:S.messages.length,input:input.value,sends,renders
        }}))).catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    assert _node(script) == {
        "count": 2,
        "input": "destination draft",
        "sends": 0,
        "renders": 0,
    }


def test_reasoning_deltas_coalesce_and_boundary_flushes_exact_latest_text():
    start = MESSAGES.index("let _pendingReasoningRenderHandle=null")
    end = MESSAGES.index("function _flushReasoningToAnchor", start)
    scheduling = MESSAGES[start:end]
    script = textwrap.dedent(
        f"""
        let _terminalStateReached=false,_streamFinalized=false,activeSid='A',streamId='run';
        let liveReasoningText='one',queued=[],cancelled=[],paints=[];
        const S={{session:{{session_id:'A'}},activeStreamId:'run'}};
        function _isSessionCurrentPane(sid){{return S.session&&S.session.session_id===sid;}}
        function requestAnimationFrame(cb){{queued.push(cb);return queued.length;}}
        function cancelAnimationFrame(id){{cancelled.push(id);}}
        function clearTimeout(){{}}
        function _liveThinkingText(){{return liveReasoningText;}}
        function _upsertAnchorReasoning(text){{paints.push(text);return true;}}
        function _updateLiveThinkingCard(){{}}
        {scheduling}
        _scheduleReasoningRender(); _scheduleReasoningRender(); _scheduleReasoningRender();
        const firstQueued=queued.length; queued.shift()();
        liveReasoningText='one two'; _scheduleReasoningRender(); _flushPendingReasoningRender();
        console.log(JSON.stringify({{firstQueued,paints,cancelled}}));
        """
    )
    result = _node(script)
    assert result["firstQueued"] == 1
    assert result["paints"] == ["one", "one two"]
    assert result["cancelled"] == [1]


def test_attachment_preview_url_is_stable_then_revoked_on_remove():
    start = UI.index("const _attachmentPreviewUrls=new Map()")
    end = UI.index("function _uploadTooLargeMessage", start)
    tray_code = UI[start:end]
    script = textwrap.dedent(
        f"""
        let creates=0,revokes=[]; const pagehide=[];
        const URL={{createObjectURL:()=>`blob:${{++creates}}`,revokeObjectURL:url=>revokes.push(url)}};
        const window={{addEventListener:(name,fn)=>pagehide.push(fn)}};
        const button={{onclick:null}};
        function chip(){{return {{className:'',dataset:{{}},innerHTML:'',querySelector:()=>button}};}}
        const tray={{children:[],classList:{{add(){{}},remove(){{}}}},set innerHTML(v){{this.children=[];}},appendChild(v){{this.children.push(v);}}}};
        const document={{createElement:()=>chip()}};
        const file={{name:'photo.png'}}; const S={{pendingFiles:[file]}};
        const _IMAGE_EXTS=/\\.png$/i,_SVG_EXTS=/\\.svg$/i;
        function _mediaKindForName(){{return 'image';}} function $(){{return tray;}}
        function updateSendBtn(){{}} function esc(v){{return v;}} function t(v){{return v;}} function li(){{return 'x';}}
        {tray_code}
        renderTray(); renderTray(); button.onclick();
        console.log(JSON.stringify({{creates,revokes,pending:S.pendingFiles.length}}));
        """
    )
    assert _node(script) == {"creates": 1, "revokes": ["blob:1"], "pending": 0}


def test_attachment_preview_survives_bfcache_but_releases_on_real_pagehide():
    start = UI.index("const _attachmentPreviewUrls=new Map()")
    end = UI.index("function _uploadTooLargeMessage", start)
    tray_code = UI[start:end]
    script = textwrap.dedent(
        f"""
        let creates=0,revokes=[]; const listeners={{}};
        const URL={{createObjectURL:()=>`blob:${{++creates}}`,revokeObjectURL:url=>revokes.push(url)}};
        const window={{addEventListener:(name,fn)=>{{listeners[name]=fn;}}}};
        const button={{onclick:null}};
        function chip(){{return {{className:'',dataset:{{}},innerHTML:'',querySelector:()=>button}};}}
        const tray={{children:[],classList:{{add(){{}},remove(){{}}}},set innerHTML(v){{this.children=[];}},appendChild(v){{this.children.push(v);}}}};
        const document={{createElement:()=>chip()}};
        const file={{name:'photo.png'}}; const S={{pendingFiles:[file]}};
        const _IMAGE_EXTS=/\\.png$/i,_SVG_EXTS=/\\.svg$/i;
        function _mediaKindForName(){{return 'image';}} function $(){{return tray;}}
        function updateSendBtn(){{}} function esc(v){{return v;}} function t(v){{return v;}} function li(){{return 'x';}}
        {tray_code}
        renderTray();
        listeners.pagehide({{persisted:true}});
        const afterFrozen={{revokes:[...revokes],cached:_attachmentPreviewUrls.has(file)}};
        listeners.pagehide({{persisted:false}});
        console.log(JSON.stringify({{
          creates,afterFrozen,afterTeardown:{{revokes,cached:_attachmentPreviewUrls.has(file)}}
        }}));
        """
    )
    assert _node(script) == {
        "creates": 1,
        "afterFrozen": {"revokes": [], "cached": True},
        "afterTeardown": {"revokes": ["blob:1"], "cached": False},
    }


def test_async_slash_branches_revalidate_owner_after_awaits():
    body = _function(MESSAGES, "send")
    for awaited in (
        "await handlePetSlashCommand",
        "await getAgentCommandMetadata",
        "await executeAgentCommand",
        "await executeAgentPluginCommand",
        "await api('/api/commands/moa/resolve')",
        "await getBundleCommandMetadata",
        "await resolveBundleCommand",
    ):
        index = body.index(awaited)
        guard = body.index("_slashOwnerCurrent()", index)
        assert guard - index < 500, awaited


def test_async_skills_command_cannot_paint_during_sidebar_navigation():
    owner = _function(COMMANDS, "createCommandOwnerContext")
    current = _function(COMMANDS, "commandOwnerCurrent")
    command = _function(COMMANDS, "cmdSkills")
    script = textwrap.dedent(
        f"""
        let _loadingSessionId=null,resolveApi,renders=0,toasts=[];
        const S={{session:{{session_id:'A'}},messages:[]}};
        function api(){{return new Promise(resolve=>{{resolveApi=resolve;}});}}
        function renderMessages(){{renders++;}}
        function showToast(value){{toasts.push(value);}}
        function t(value){{return value;}}
        {owner}
        {current}
        {command}
        const pending=cmdSkills('',createCommandOwnerContext());
        _loadingSessionId='B';
        resolveApi({{skills:[{{name:'unsafe',description:'must not paint'}}]}});
        pending.then(()=>console.log(JSON.stringify({{
          messages:S.messages.length,renders,toasts
        }}))).catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    assert _node(script) == {"messages": 0, "renders": 0, "toasts": []}


def test_async_title_posts_owner_sid_but_does_not_mutate_destination():
    owner = _function(COMMANDS, "createCommandOwnerContext")
    current = _function(COMMANDS, "commandOwnerCurrent")
    command = _function(COMMANDS, "cmdTitle")
    script = textwrap.dedent(
        f"""
        let _loadingSessionId=null,resolveApi,renders=0,syncs=0,posted=null;
        const S={{session:{{session_id:'A',title:'A title'}},messages:[]}};
        function api(_url,options){{posted=JSON.parse(options.body);return new Promise(resolve=>{{resolveApi=resolve;}});}}
        function renderMessages(){{renders++;}} function renderSessionList(){{renders++;}}
        function syncTopbar(){{syncs++;}} function showToast(){{}}
        function t(value){{return value;}}
        {owner}
        {current}
        {command}
        const pending=cmdTitle('renamed',createCommandOwnerContext());
        _loadingSessionId='B';
        resolveApi({{session:{{session_id:'A',title:'renamed'}}}});
        pending.then(()=>console.log(JSON.stringify({{
          posted,title:S.session.title,renders,syncs
        }}))).catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    assert _node(script) == {
        "posted": {"session_id": "A", "title": "renamed"},
        "title": "A title",
        "renders": 0,
        "syncs": 0,
    }


def test_workspace_false_is_handled_not_slash_fallthrough():
    owner = _function(COMMANDS, "createCommandOwnerContext")
    current = _function(COMMANDS, "commandOwnerCurrent")
    command = _function(COMMANDS, "cmdWorkspace")
    script = textwrap.dedent(
        f"""
        let _loadingSessionId=null,switches=0;
        const S={{session:{{session_id:'A'}},messages:[]}};
        async function api(){{return {{workspaces:[{{name:'repo',path:'/repo'}}]}};}}
        async function switchToWorkspace(){{switches++;return false;}}
        function showToast(){{}} function t(value){{return value;}}
        {owner}
        {current}
        {command}
        cmdWorkspace('repo',createCommandOwnerContext()).then(result=>console.log(JSON.stringify({{
          handled:result!==false,switches
        }}))).catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    assert _node(script) == {"handled": True, "switches": 1}


def test_created_session_is_not_adopted_when_sidebar_wins():
    current = _function(MESSAGES, "_sendPreprocessStillOwnsSession")
    create = _function(MESSAGES, "_createSessionForSendOwner")
    script = textwrap.dedent(
        f"""
        let _loadingSessionId=null,renders=0,resolveCreate;
        const S={{session:null}};
        function newSession(){{return new Promise(resolve=>{{resolveCreate=resolve;}});}}
        async function renderSessionList(){{renders++;}}
        {current}
        {create}
        const pending=_createSessionForSendOwner();
        S.session={{session_id:'B'}};_loadingSessionId='B';
        resolveCreate({{session_id:'A'}});
        pending.then(result=>console.log(JSON.stringify({{
          adopted:!!result,current:S.session.session_id,renders
        }}))).catch(error=>{{console.error(error);process.exit(1);}});
        """
    )
    assert _node(script) == {"adopted": False, "current": "B", "renders": 0}


def test_accepted_chat_start_is_preserved_without_mutating_destination_pane():
    preserve = _function(MESSAGES, "_preserveAcceptedChatStartForBackgroundSession")
    send = _function(MESSAGES, "send")
    script = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'B',title:'Destination'}},messages:[{{role:'user',content:'B'}}],activeStreamId:'B-run'}};
        const INFLIGHT={{A:{{messages:[{{role:'user',content:'A'}}],uploaded:[],toolCalls:[]}}}};
        let saved=null,titleUpdate=null,renders=0;
        function applySessionTitleUpdate(...args){{titleUpdate=args;}}
        function saveInflightState(sid,state){{saved={{sid,state}};}}
        function renderSessionList(){{renders++;}}
        {preserve}
        _preserveAcceptedChatStartForBackgroundSession(
          'A','A-run',{{title:'Accepted A'}},INFLIGHT.A.messages,['upload.txt']
        );
        console.log(JSON.stringify({{
          pane:S,
          inflight:INFLIGHT.A,
          saved,
          titleUpdate,
          renders,
        }}));
        """
    )
    result = _node(script)
    assert result["pane"] == {
        "session": {"session_id": "B", "title": "Destination"},
        "messages": [{"role": "user", "content": "B"}],
        "activeStreamId": "B-run",
    }
    assert result["inflight"]["streamId"] == "A-run"
    assert result["inflight"]["reattach"] is True
    assert result["inflight"]["journalReplayFromStart"] is True
    assert result["saved"]["sid"] == "A"
    assert result["saved"]["state"]["journalReplayFromStart"] is True
    assert result["titleUpdate"][:2] == ["A", "Accepted A"]
    assert result["renders"] == 1

    await_start = send.index("const startData=await api('/api/chat/start'")
    ownership = send.index("if(!_sendPreprocessStillOwnsSession(activeSid))", await_start)
    post_start_assignment = send.index("postStartData = startData", await_start)
    assert await_start < ownership < post_start_assignment
    assert "_preserveAcceptedChatStartForBackgroundSession(" in send[ownership:post_start_assignment]
    assert "return;" in send[ownership:post_start_assignment]


def test_rejected_chat_start_after_navigation_cleans_only_original_session():
    settle = _function(MESSAGES, "_settleRejectedChatStartForBackgroundSession")
    send = _function(MESSAGES, "send")
    script = textwrap.dedent(
        f"""
        const S={{session:{{session_id:'B'}},messages:[{{role:'user',content:'destination'}}],activeStreamId:'B-run'}};
        const INFLIGHT={{A:{{messages:[{{role:'user',content:'optimistic'}}]}},B:{{streamId:'B-run'}}}};
        const calls=[];
        function clearInflightState(sid){{calls.push(['clear',sid]);}}
        function stopApprovalPollingForSession(sid){{calls.push(['approval',sid]);}}
        function stopClarifyPollingForSession(sid){{calls.push(['clarify',sid]);}}
        function _restoreComposerDraftAfterFailedSend(text,files,sid){{calls.push(['restore',sid,text,files.length]);}}
        function clearOptimisticSessionStreaming(sid){{calls.push(['optimistic',sid]);}}
        function renderSessionList(){{calls.push(['render']);}}
        {settle}
        _settleRejectedChatStartForBackgroundSession('A','draft',[{{name:'a.txt'}}],null);
        console.log(JSON.stringify({{pane:S,inflight:INFLIGHT,calls}}));
        """
    )
    result = _node(script)
    assert result["pane"] == {
        "session": {"session_id": "B"},
        "messages": [{"role": "user", "content": "destination"}],
        "activeStreamId": "B-run",
    }
    assert "A" not in result["inflight"]
    assert result["inflight"]["B"] == {"streamId": "B-run"}
    assert result["calls"] == [
        ["clear", "A"],
        ["approval", "A"],
        ["clarify", "A"],
        ["restore", "A", "draft", 1],
        ["optimistic", "A"],
        ["render"],
    ]

    catch = send.index("}catch(e){", send.index("const startData=await api('/api/chat/start'"))
    ownership = send.index("if(!_sendPreprocessStillOwnsSession(activeSid))", catch)
    active_error_mutation = send.index("S.messages.push({role:'assistant'", catch)
    assert catch < ownership < active_error_mutation
    assert "_settleRejectedChatStartForBackgroundSession(" in send[ownership:active_error_mutation]
    assert "return;" in send[ownership:active_error_mutation]


def test_btw_stream_cannot_create_row_in_destination_session():
    attach = _function(MESSAGES, "attachBtwStream")
    script = textwrap.dedent(
        f"""
        let _loadingSessionId=null,created=0,toasts=0,source;
        const S={{session:{{session_id:'A'}}}};
        const document={{baseURI:'http://local/',createElement:()=>{{created++;return {{
          dataset:{{}},appendChild(){{}},querySelector(){{return null;}},scrollIntoView(){{}},isConnected:true
        }};}}}};
        const location={{href:'http://local/'}};
        class EventSource{{constructor(){{this.handlers={{}};source=this;}}addEventListener(n,fn){{this.handlers[n]=fn;}}close(){{}}}}
        function $(){{return {{appendChild(){{}}}};}} function t(v){{return v;}}
        function renderMd(v){{return v;}} function showToast(){{toasts++;}}
        {attach}
        attachBtwStream('A','stream','question');
        _loadingSessionId='B';
        source.handlers.token({{data:JSON.stringify({{text:'late'}})}});
        source.handlers.done({{data:JSON.stringify({{answer:'late'}})}});
        console.log(JSON.stringify({{created,toasts}}));
        """
    )
    assert _node(script) == {"created": 0, "toasts": 0}
