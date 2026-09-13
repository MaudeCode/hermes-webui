"""HWEB-103: the transcript-window request overlaps the metadata request.

``loadSession()`` issues ``messages=0``, immediately starts the
``messages=1&msg_limit=N`` request, and only then awaits the metadata.
``_ensureMessagesLoaded()`` consumes that stashed request when the session
id, load generation, and URL still match; anything else is ignored so the
existing stale-load guards keep deciding what lands on screen.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SESSIONS_JS = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")


def _function(source: str, name: str) -> str:
    markers = (f"async function {name}(", f"function {name}(")
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


def test_load_session_starts_the_window_fetch_before_awaiting_metadata():
    body = _function(SESSIONS_JS, "loadSession")
    metadata = body.index("const _metadataRequest = api(`/api/session?session_id=${encodeURIComponent(sid)}&messages=0&resolve_model=0`);")
    prefetch = body.index("_prefetchSessionMessages(sid,_loadGeneration);")
    awaited = body.index("data = await _metadataRequest;")
    assert metadata < prefetch < awaited
    # An INFLIGHT snapshot restores the transcript without a fetch; do not prefetch then.
    assert "!(typeof INFLIGHT!=='undefined'&&INFLIGHT&&INFLIGHT[sid])) _prefetchSessionMessages" in body


def test_ensure_messages_loaded_consumes_the_stash_before_fetching():
    body = _function(SESSIONS_JS, "_ensureMessagesLoaded")
    take = body.index("_takeSessionMessagesPrefetch(sid, _loadGeneration, messagesUrl)")
    fetch = body.index("data = await api(messagesUrl, {timeoutMs:120000});")
    assert take < fetch
    assert "if (settled.error) throw settled.error;" in body
    # A window snapshotted before the accepted metadata is refetched, never applied.
    assert "if (_prefetchOlderThanMetadata(data, S.session)) data = await api(messagesUrl, {timeoutMs:120000});" in body


NODE_SCRIPT = r"""
let _sessionMessagesPrefetch=null;
const _INITIAL_MSG_LIMIT=30, _msgLimitMax=500, _FORCE_RELOAD_MSG_LIMIT=80;
let _reloadLimit=30;
function _messageReloadLimitForSession(){ return _reloadLimit; }
const apiCalls=[]; const pending=[];
let rejectNext=false;
function api(url){ apiCalls.push(url); return new Promise((resolve,reject)=>{ pending.push({url,resolve,reject}); }); }
let unhandled=0; process.on('unhandledRejection',()=>{ unhandled++; });
__HELPERS__
(async()=>{
  const out={};
  // 1. The prefetch and _ensureMessagesLoaded build the same URL, and it is issued at once.
  _prefetchSessionMessages('sid-a', 1);
  const inlineUrl=(()=>{
    const sid='sid-a';
    const reloadLimit = _messageReloadLimitForSession(sid);
    const boundedReloadLimit = Math.max(_INITIAL_MSG_LIMIT, Math.min(Number(reloadLimit)||_INITIAL_MSG_LIMIT, _msgLimitMax, _FORCE_RELOAD_MSG_LIMIT));
    return `/api/session?session_id=${encodeURIComponent(sid)}&messages=1&resolve_model=0&msg_limit=${boundedReloadLimit}&expand_renderable=1`;
  })();
  out.urlMatches = apiCalls[0]===inlineUrl;
  out.issuedImmediately = apiCalls.length===1;
  // 2. A newer generation for another session ignores and discards the stash.
  out.otherSessionNewerGeneration = _takeSessionMessagesPrefetch('sid-b', 2, inlineUrl.replace('sid-a','sid-b'))===null;
  out.stashDiscarded = _sessionMessagesPrefetch===null;
  // 3. Matching sid + generation + url consumes the promise exactly once.
  _prefetchSessionMessages('sid-a', 3);
  const taken=_takeSessionMessagesPrefetch('sid-a', 3, inlineUrl);
  out.takenIsPromise = !!taken && typeof taken.then==='function';
  out.secondTakeNull = _takeSessionMessagesPrefetch('sid-a', 3, inlineUrl)===null;
  pending[1].resolve({session:{session_id:'sid-a'}});
  out.settledData = (await taken).data.session.session_id;
  // 4. An older generation neither consumes nor discards a newer stash.
  _prefetchSessionMessages('sid-a', 5);
  out.olderGenerationNull = _takeSessionMessagesPrefetch('sid-a', 4, inlineUrl)===null;
  out.stashKeptForNewer = _sessionMessagesPrefetch!==null && _sessionMessagesPrefetch.generation===5;
  // 5. Rejections are folded into the settled value; nothing is unhandled.
  const rejected=_takeSessionMessagesPrefetch('sid-a', 5, inlineUrl);
  pending[2].reject(new Error('boom'));
  const settled=await rejected;
  out.errorFolded = settled.error instanceof Error && settled.error.message==='boom';
  // 6. A URL drift (different reload limit) is not consumed and the stale stash is dropped.
  _prefetchSessionMessages('sid-a', 6);
  out.urlDriftNull = _takeSessionMessagesPrefetch('sid-a', 6, inlineUrl.replace('msg_limit=30','msg_limit=80'))===null;
  out.driftDiscarded = _sessionMessagesPrefetch===null;
  // 7. A prefetch older than the accepted metadata is refused.
  const meta={session_id:'sid-a', message_count:11, updated_at:200};
  out.olderCountRefused = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:10, updated_at:200}}, meta)===true;
  out.olderUpdatedRefused = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:11, updated_at:199}}, meta)===true;
  out.sameAccepted = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:11, updated_at:200}}, meta)===false;
  out.newerAccepted = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:12, updated_at:201}}, meta)===false;
  out.otherSessionIgnored = _prefetchOlderThanMetadata({session:{session_id:'sid-b', message_count:1, updated_at:1}}, meta)===false;
  const metaRev={session_id:'sid-a', message_count:11, updated_at:200, _load_revision:'r2'};
  out.revisionMismatchRefused = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:11, updated_at:200, _load_revision:'r1'}}, metaRev)===true;
  out.revisionMatchAccepted = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:11, updated_at:200, _load_revision:'r2'}}, metaRev)===false;
  out.revisionAbsentFallsBack = _prefetchOlderThanMetadata({session:{session_id:'sid-a', message_count:11, updated_at:200}}, metaRev)===false;
  // 8. An abandoned prefetch that rejects never surfaces as an unhandled rejection.
  _prefetchSessionMessages('sid-z', 9);
  pending[4].reject(new Error('abandoned'));
  await new Promise(r=>setTimeout(r,10));
  out.unhandled = unhandled;
  console.log(JSON.stringify(out));
})();
"""


def test_prefetch_stash_semantics(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node not available")
    helpers = "\n".join(
        _function(SESSIONS_JS, name)
        for name in (
            "_sessionMessagesLoadUrl",
            "_prefetchSessionMessages",
            "_prefetchOlderThanMetadata",
            "_takeSessionMessagesPrefetch",
        )
    )
    script = tmp_path / "prefetch.js"
    script.write_text(NODE_SCRIPT.replace("__HELPERS__", helpers), encoding="utf-8")
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out == {
        "urlMatches": True,
        "issuedImmediately": True,
        "otherSessionNewerGeneration": True,
        "stashDiscarded": True,
        "takenIsPromise": True,
        "secondTakeNull": True,
        "settledData": "sid-a",
        "olderGenerationNull": True,
        "stashKeptForNewer": True,
        "errorFolded": True,
        "urlDriftNull": True,
        "driftDiscarded": True,
        "olderCountRefused": True,
        "olderUpdatedRefused": True,
        "sameAccepted": True,
        "newerAccepted": True,
        "otherSessionIgnored": True,
        "revisionMismatchRefused": True,
        "revisionMatchAccepted": True,
        "revisionAbsentFallsBack": True,
        "unhandled": 0,
    }
