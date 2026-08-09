import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _run_node(source: str):
    result = subprocess.run(["node", "-e", source], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_new_chat_detaches_old_live_turn_only_after_create_succeeds():
    start = SESSIONS_JS.index("async function newSession(flash, options={})")
    end = SESSIONS_JS.index("function _teardownDeletedSessionBrowserOwners", start)
    body = SESSIONS_JS[start:end]
    create = body.index("const data=await api('/api/session/new'")
    ownership_after_create = body.index("if(!stillOwnsDepartingPane())", create)
    detach = body.index("closeLiveStream(departingSid)")
    assign = body.index("S.session=data.session")

    assert "snapshotLiveTurnHtmlForSession(departingSid)" in body
    assert "await _saveComposerDraftNow(" in body
    assert "departureLoadGeneration" in body
    assert "if(!stillOwnsDepartingPane()) return;" in body
    assert create < ownership_after_create < detach < assign
    assert "refreshSessionList('new-session-background')" in body
    assert "journalReplayFromStart" in MESSAGES_JS


def test_failed_new_chat_preserves_departing_session_local_state():
    start = SESSIONS_JS.index("async function newSession(flash, options={})")
    end = SESSIONS_JS.index("function _teardownDeletedSessionBrowserOwners", start)
    new_session = SESSIONS_JS[start:end]
    script = f"""
      globalThis.window=globalThis;
      globalThis.document={{createElement:()=>({{dataset:{{}},appendChild(){{}}}})}};
      globalThis.localStorage={{setItem(){{}}}};
      globalThis.history={{replaceState(){{}}}};
      let _newSessionInFlight=null,_loadSessionGeneration=0,_loadingSessionId=null;
      let _messagesTruncated=true,_oldestIdx=37,_activeProject='',_sessionSourceFilter='webui';
      const NO_PROJECT_FILTER='__none__',INFLIGHT={{}};
      const counters={{selections:0,queue:0,tools:0}};
      const input={{value:'keep draft'}};
      const modelSelect={{value:'gpt-test',selectedOptions:[{{dataset:{{provider:'openai'}}}}]}};
      const S={{
        session:{{session_id:'A',workspace:'/old',model_provider:'openai'}},
        messages:[{{role:'user',content:'old'}}],toolCalls:[{{id:'tool-A'}}],
        pendingFiles:[],activeProfile:'default',_pendingSessionToolsets:null,
        _profileSwitchWorkspace:'/switched',_profileDefaultWorkspace:null,
      }};
      window._defaultModel=null; window._activeProvider='openai';
      window._clearPendingSelections=()=>{{counters.selections++;}};
      function $(id){{return id==='msg'?input:(id==='modelSelect'?modelSelect:null);}}
      function _setNewSessionPending(){{}}
      async function _saveComposerDraftNow(){{}}
      function updateQueueBadge(){{counters.queue++;}}
      function clearLiveToolCards(){{counters.tools++;}}
      function _readEmptyComposerModelOverride(){{return null;}}
      function _readPersistedModelState(){{return null;}}
      function _modelStateForSelect(){{return {{model:'gpt-test',model_provider:'openai'}};}}
      let rejectCreate,requestBody=null;
      function api(_url,options){{
        requestBody=JSON.parse(options.body);
        return new Promise((_resolve,reject)=>{{rejectCreate=reject;}});
      }}
      {new_session}
      (async()=>{{
        const pending=newSession(false,{{}}).then(
          ()=>{{throw new Error('expected rejection');}},
          error=>String(error&&error.message||error)
        );
        await new Promise(resolve=>setTimeout(resolve,0));
        const during={{
          toolCalls:S.toolCalls.map(item=>item.id),truncated:_messagesTruncated,
          oldest:_oldestIdx,switchWorkspace:S._profileSwitchWorkspace,
          counters:{{...counters}},requestWorkspace:requestBody&&requestBody.workspace,
        }};
        rejectCreate(new Error('create failed'));
        const error=await pending;
        console.log(JSON.stringify({{
          during,error,
          after:{{
            toolCalls:S.toolCalls.map(item=>item.id),truncated:_messagesTruncated,
            oldest:_oldestIdx,switchWorkspace:S._profileSwitchWorkspace,
            counters:{{...counters}},session:S.session.session_id,
          }}
        }}));
      }})().catch(error=>{{console.error(error);process.exit(1);}});
    """
    assert _run_node(script) == {
        "during": {
            "toolCalls": ["tool-A"],
            "truncated": True,
            "oldest": 37,
            "switchWorkspace": "/switched",
            "counters": {"selections": 0, "queue": 0, "tools": 0},
            "requestWorkspace": "/switched",
        },
        "error": "create failed",
        "after": {
            "toolCalls": ["tool-A"],
            "truncated": True,
            "oldest": 37,
            "switchWorkspace": "/switched",
            "counters": {"selections": 0, "queue": 0, "tools": 0},
            "session": "A",
        },
    }


def test_successful_single_and_batch_delete_share_terminal_browser_teardown():
    helper_start = SESSIONS_JS.index("function _teardownDeletedSessionBrowserOwners")
    helper_end = SESSIONS_JS.index("/**", helper_start)
    helper = SESSIONS_JS[helper_start:helper_end]
    for contract in (
        "closeLiveStream(sid)",
        "delete INFLIGHT[sid]",
        "clearInflightState(sid)",
        "_approvalPendingBySession.delete(sid)",
        "_clarifyPendingBySession.delete(sid)",
        "stopSessionStream()",
    ):
        assert contract in helper
    assert "deletedIds.forEach(_teardownDeletedSessionBrowserOwners)" in SESSIONS_JS
    assert "_teardownDeletedSessionBrowserOwners(sid);" in SESSIONS_JS


def test_delete_fallback_does_not_override_newer_surviving_sidebar_navigation():
    helper_start = SESSIONS_JS.index("function _newerPendingNavigationSurvivesDelete")
    helper_end = SESSIONS_JS.index("/**", helper_start)
    helper = SESSIONS_JS[helper_start:helper_end]
    script = f"""
      let _loadingSessionId=null;
      {helper}
      const results=[];
      _loadingSessionId='B';
      results.push(_newerPendingNavigationSurvivesDelete(['A']));
      results.push(_newerPendingNavigationSurvivesDelete(['A','B']));
      _loadingSessionId='A';
      results.push(_newerPendingNavigationSurvivesDelete(['A']));
      _loadingSessionId=null;
      results.push(_newerPendingNavigationSurvivesDelete(['A']));
      console.log(JSON.stringify(results));
    """
    assert _run_node(script) == [True, False, False, False]

    # Both the batch and single-delete active-pane branches must consult the
    # helper and gate their fallback /api/sessions load. A pending target that
    # was itself batch-deleted returns false above, allowing a valid fallback.
    assert "_newerPendingNavigationSurvivesDelete(deletedIds)" in SESSIONS_JS
    assert "_newerPendingNavigationSurvivesDelete([sid])" in SESSIONS_JS
    assert SESSIONS_JS.count("if(!pendingNavigationSurvives){") >= 2


def test_popstate_refusal_restores_url_to_visible_session():
    start = SESSIONS_JS.index("window.addEventListener('popstate'")
    body = SESSIONS_JS[start : start + 1300]
    busy = body.index("if(S.busy)")
    restore = body.index("_setActiveSessionUrl(currentSid)", busy)
    load = body.index("loadSession(sid)")
    assert busy < restore < load
    assert "history.replaceState(null,'',_appRootPath())" in body


def test_scoped_sidebar_event_skips_unrelated_active_metadata_refresh():
    helper_start = SESSIONS_JS.index("function _sessionEventMustRefreshActiveSession")
    helper_end = SESSIONS_JS.index("// ──", helper_start)
    helper = SESSIONS_JS[helper_start:helper_end]
    script = f"""
      const S={{session:{{session_id:'active'}}}};
      function _sessionEventTargetsActiveSession(payload){{
        const eventSessionId=payload&&typeof payload.session_id==='string'?payload.session_id:'';
        return !!(eventSessionId&&S.session&&S.session.session_id===eventSessionId);
      }}
      {helper}
      console.log(JSON.stringify([
        _sessionEventMustRefreshActiveSession({{session_id:'other'}},true),
        _sessionEventMustRefreshActiveSession({{session_id:'active'}},true),
        _sessionEventMustRefreshActiveSession({{reason:'legacy'}},true),
        _sessionEventMustRefreshActiveSession(null,false),
      ]));
    """
    assert _run_node(script) == [False, True, True, True]


def test_incremental_thinking_parser_matches_final_code_aware_results_and_stays_incremental():
    start = MESSAGES_JS.index("const _thinkPairs=")
    end = MESSAGES_JS.index("if(typeof window!=='undefined')", start)
    parser_source = MESSAGES_JS[start:end]
    script = f"""
      {parser_source}
      const cases=[
        ['plain answer'],
        ['<thi','nk>plan</thi','nk>answer'],
        ['```','html\\n<think>literal</think>\\n```\\nanswer'],
        ['`'],
        ['~'],
        ['~','~'],
        ['literal\\n','`','`'],
        ['`','``html\\n','<thi'],
        ['`','<thi'],
        ['`<think>literal</think>` answer'],
        ['<think>one</think>','<think>two</think>final'],
        ['    <think>indented literal</think>\\nanswer'],
        ['    text ','<thi'],
        ['  <thi','nX remains literal'],
      ];
      const fuzzCorpus=[
        '    text <thi',
        '```html\\n<thi',
        '~~~html\\n<|channel>thou',
        '`<|turn|>think',
        '<think>body</thi',
        '<|channel>thought\\nbody<channel|',
        'literal\\n`',
        'literal\\n``',
        'literal\\n~',
        'literal\\n~~',
      ];
      for(const full of fuzzCorpus){{
        for(let split=0;split<=full.length;split++){{
          cases.push([full.slice(0,split),full.slice(split)]);
        }}
      }}
      const outputs=[];
      for(const chunks of cases){{
        const parse=_createIncrementalInlineThinkingParser();
        let text=''; let result=null;
        for(const chunk of chunks){{
          text+=chunk;
          result=parse(text,'',{{streaming:true}});
          const stepExpected=_extractInlineThinkingFromContent(text,'',{{streaming:true}});
          for(const key of ['content','reasoning','inThinking']){{
            if(result[key]!==stepExpected[key]){{
              throw new Error(`split parity ${{JSON.stringify(chunks)}} @ ${{JSON.stringify(text)}} key=${{key}} actual=${{JSON.stringify(result[key])}} expected=${{JSON.stringify(stepExpected[key])}}`);
            }}
          }}
        }}
        const expected=_extractInlineThinkingFromContent(text,'',{{streaming:true}});
        outputs.push({{
          content:result.content, reasoning:result.reasoning, inThinking:result.inThinking,
          expectedContent:expected.content, expectedReasoning:expected.reasoning,
          expectedInThinking:expected.inThinking,
        }});
      }}
      const original=_extractInlineThinkingFromContent;
      let fallbackCalls=0;
      _extractInlineThinkingFromContent=(...args)=>{{fallbackCalls++;return original(...args);}};
      const growth=_createIncrementalInlineThinkingParser();
      let growing='';
      for(let i=0;i<20000;i++){{
        growing+=(i===10?'<think>reasoning</think>':'x');
        growth(growing,'',{{streaming:true}});
      }}
      console.log(JSON.stringify({{outputs,fallbackCalls}}));
    """
    result = _run_node(script)
    for row in result["outputs"]:
        assert row["content"] == row["expectedContent"]
        assert row["reasoning"] == row["expectedReasoning"]
        assert row["inThinking"] == row["expectedInThinking"]
    assert result["fallbackCalls"] == 0


def test_inflight_snapshot_uses_incremental_parser_on_production_delta_path():
    parser_start = MESSAGES_JS.index("const _thinkPairs=")
    parser_end = MESSAGES_JS.index("if(typeof window!=='undefined')", parser_start)
    parser_source = MESSAGES_JS[parser_start:parser_end]
    sync_start = MESSAGES_JS.index("function syncInflightAssistantMessage()")
    sync_end = MESSAGES_JS.index("function recordActivityBoundary()", sync_start)
    sync_source = MESSAGES_JS[sync_start:sync_end]

    assert "_parseInflightThinkingStream(" in sync_source
    assert "_splitThinkFromContent(" not in sync_source

    script = f"""
      {parser_source}
      {sync_source}
      let fallbackCalls=0;
      const originalExtractor=_extractInlineThinkingFromContent;
      _extractInlineThinkingFromContent=(...args)=>{{
        fallbackCalls++;
        return originalExtractor(...args);
      }};
      const activeSid='stream-owner';
      const INFLIGHT={{
        [activeSid]:{{messages:[{{role:'assistant',content:'',_live:true}}]}}
      }};
      let assistantText='';
      let reasoningText='';
      let persisted=0;
      const _throttledPersist=()=>{{persisted++;}};
      const _parseInflightThinkingStream=_createIncrementalInlineThinkingParser();
      for(let index=0;index<20000;index++){{
        assistantText+=(index===10?'<think>reasoning</think>':'x');
        syncInflightAssistantMessage();
      }}
      const saved=INFLIGHT[activeSid].messages[0];
      console.log(JSON.stringify({{
        fallbackCalls,
        persisted,
        contentLength:saved.content.length,
        reasoning:saved.reasoning,
      }}));
    """
    result = _run_node(script)
    assert result["fallbackCalls"] == 0
    assert result["persisted"] == 20000
    assert result["contentLength"] == 19999
    assert result["reasoning"] == "reasoning"


def test_reasoning_segment_accumulator_is_chunked_for_many_deltas():
    from api import config

    on_reasoning_start = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8").index(
        "def on_reasoning(text):"
    )
    on_reasoning_end = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8").index(
        "def on_interim_assistant", on_reasoning_start
    )
    on_reasoning = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8")[
        on_reasoning_start:on_reasoning_end
    ]
    assert "append_stream_text_chunk(" in on_reasoning
    assert "_reasoning_segments.get(_current_reasoning_idx, '') + reasoning_delta" not in on_reasoning

    segments = {0: []}
    for _ in range(50000):
        config.append_stream_text_chunk(segments, 0, "x")

    assert isinstance(segments[0], list)
    assert len(segments[0]) == 50000
    assert config.stream_text_value(segments, 0) == "x" * 50000


def test_old_detached_active_run_still_blocks_successor():
    from api import config, routes

    stream_id = "aged-detached-stream"
    session_id = "aged-detached-session"
    config.register_active_run(
        stream_id,
        session_id=session_id,
        started_at=1.0,
        phase="cancelling",
    )
    try:
        assert routes._active_run_stream_for_session(session_id) == stream_id
    finally:
        config.unregister_active_run(stream_id)
