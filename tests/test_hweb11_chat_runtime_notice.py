"""HWEB-11 — one prioritized notice stack for chat connection and runtime state.

Source-contract tests pin the single `#chatRuntimeNotice` host and the removal
of the per-condition banner hosts it replaces. Node-backed behavioral tests run
the extracted store against a stub DOM: priority ordering, coalescing, dismissal
persistence across polls, transient expiry, and announce-once semantics.
"""

from __future__ import annotations

import json
import pathlib
import shutil
import subprocess
import textwrap

import pytest


REPO_ROOT = pathlib.Path(__file__).parent.parent
UI_JS = (REPO_ROOT / "static" / "ui.js").read_text(encoding="utf-8")
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")
INDEX_HTML = (REPO_ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (REPO_ROOT / "static" / "style.css").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Source-contract tests
# ---------------------------------------------------------------------------


def test_single_notice_host_replaces_the_per_condition_banners():
    assert 'id="chatRuntimeNotice"' in INDEX_HTML
    assert 'id="chatRuntimeNoticeAlert"' in INDEX_HTML
    assert 'id="chatRuntimeNoticeStatus"' in INDEX_HTML
    for removed in ('id="reconnectBanner"', 'id="offlineBanner"', 'id="agentHealthBanner"'):
        assert removed not in INDEX_HTML, f"{removed} should be replaced by #chatRuntimeNotice"
    for removed_css in (".reconnect-banner", ".offline-banner", ".agent-health-banner"):
        assert removed_css not in STYLE_CSS, f"{removed_css} should be replaced by .chat-runtime-notice"


def test_announcement_roles_are_split_between_alert_and_status():
    alert_idx = INDEX_HTML.index('id="chatRuntimeNoticeAlert"')
    status_idx = INDEX_HTML.index('id="chatRuntimeNoticeStatus"')
    alert_tag = INDEX_HTML[alert_idx : INDEX_HTML.index(">", alert_idx)]
    status_tag = INDEX_HTML[status_idx : INDEX_HTML.index(">", status_idx)]
    assert 'role="alert"' in alert_tag and 'aria-live="assertive"' in alert_tag
    assert 'role="status"' in status_tag and 'aria-live="polite"' in status_tag
    # The visible stack itself is not a live region: re-rendering it on a poll
    # must not re-announce anything.
    host_idx = INDEX_HTML.index('id="chatRuntimeNotice"')
    host_tag = INDEX_HTML[INDEX_HTML.rindex("<div", 0, host_idx) : INDEX_HTML.index(">", host_idx)]
    assert "aria-live" not in host_tag
    assert 'role="region"' in host_tag


def test_priority_order_matches_the_ticket():
    assert (
        "const CHAT_NOTICE_PRIORITY=['thread_error','offline','agent_unavailable',"
        "'provider_failure','reconnect'];" in UI_JS
    )


def test_stack_is_bounded_and_only_the_top_notice_is_expanded():
    assert "const CHAT_NOTICE_MAX_VISIBLE=4;" in UI_JS
    assert "if(rec.detail&&!secondary){" in UI_JS
    rule_start = STYLE_CSS.index(".chat-runtime-notice{")
    rule = STYLE_CSS[rule_start : STYLE_CSS.index("}", rule_start)]
    # Fixed overlay + capped height: the stack never re-flows the transcript.
    assert "position:fixed" in rule
    assert "max-height:" in rule
    assert "overflow-y:auto" in rule


def test_narrow_and_mobile_widths_get_their_own_padding_and_action_wrap():
    rule_start = STYLE_CSS.index("@media (max-width:600px){.chat-runtime-notice-item{")
    rule = STYLE_CSS[rule_start : STYLE_CSS.index("\n", rule_start)]
    assert ".chat-runtime-notice-item{padding:10px 12px" in rule
    assert ".chat-runtime-notice-actions{flex-wrap:wrap" in rule
    # The phone composer still expands while a notice is showing — one entry now
    # instead of the three per-condition hosts.
    assert "['#chatRuntimeNotice','']," in UI_JS


def test_gateway_restart_disables_its_actions_through_the_record_not_the_dom():
    assert "let _gatewayRestartInFlight=false;" in UI_JS
    assert "label:_gatewayRestartInFlight?'Restarting...':'Restart Service'" in UI_JS
    assert "if(_gatewayRestartInFlight) return;" in UI_JS
    # A successful restart resolves the condition, and so does a heartbeat that
    # resolved healthy while the request was in flight — only a still-down state
    # restores the row, so a failed restart cannot resurrect a stale outage.
    assert "if(restarted||_agentHealthLastState!=='down') _hideAgentHealthAlert();" in UI_JS
    assert "else _showAgentHealthAlert();" in UI_JS
    # And a poll must not land mid-restart and clear the alert under its buttons.
    poll = UI_JS[UI_JS.index("async function pollAgentHealth(){") :]
    assert "if(_gatewayRestartInFlight) return;" in poll[: poll.index("try{")]
    # The old direct-DOM juggling is gone, so a re-render can't strip the state.
    assert "btn.textContent = 'Restarting...'" not in UI_JS


def test_notice_stack_follows_the_active_palette_tokens():
    rule_start = STYLE_CSS.index(".chat-runtime-notice{")
    rule = STYLE_CSS[rule_start : STYLE_CSS.index("}", rule_start)]
    assert "background:color-mix(in srgb,var(--surface) 88%,var(--accent))" in rule
    assert "var(--warning" not in rule


def test_every_condition_publishes_into_the_shared_stack():
    # offline / reconnect / agent-health in ui.js …
    assert "kind:'offline'," in UI_JS
    assert "kind:'reconnect'," in UI_JS
    assert "kind:'agent_unavailable'," in UI_JS
    assert "clearChatRuntimeNotice('offline')" in UI_JS
    assert "clearChatRuntimeNotice('agent_unavailable')" in UI_JS
    # … provider failure / thread error in messages.js.
    assert "kind:_isProviderFailure?'provider_failure':'thread_error'," in MESSAGES_JS
    assert "runId:streamId||''," in MESSAGES_JS
    # A new turn resolves the previous turn's terminal failure.
    assert "clearChatRuntimeNotice('provider_failure',sid);" in UI_JS
    assert "clearChatRuntimeNotice('thread_error',sid);" in UI_JS


def test_restart_wait_publishes_both_messages_without_a_dangling_dom_reference():
    """The restart poller no longer pokes #reconnectMsg, including on timeout.

    Regression guard: consolidating the banner deleted the `msgEl` binding, and
    the timeout branch still referenced it — a ReferenceError on the one path
    nothing else covers. It also calls into the notice layer, so the call must
    be guarded the way the sibling `reconnectSidebarSSE`/`refreshSession` calls
    in this file are, since the function is extracted and run on its own.
    """
    start = UI_JS.index("async function _waitForServerThenReload(")
    end = UI_JS.index("function _topbarMessageMetaText(")
    body = UI_JS[start:end]
    assert "msgEl" not in body, "restart poller still references the removed #reconnectMsg node"
    assert "if(typeof publishChatRuntimeNotice!=='function') return;" in body
    assert "_publishRestartNotice('runtime_notice_restarting_title'" in body
    assert "_publishRestartNotice('runtime_notice_restart_slow_title'" in body
    # The timeout copy tells the user to click Reload, so the row must carry it.
    assert "id:'btnRestartTimeoutReload',label:'Reload',onClick:()=>refreshSession()" in body


def test_restart_wait_timeout_path_runs_without_throwing():
    """Drive the extracted poller to its deadline with no healthy response."""
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for the restart-poller harness")
    wait_fn = UI_JS[
        UI_JS.index("async function _waitForServerThenReload(") : UI_JS.index(
            "function _topbarMessageMetaText("
        )
    ]
    for helper in ("_normalizeHealthServerIdentity", "_healthResponseServerIdentity"):
        h_start = UI_JS.index(f"function {helper}(")
        h_end = UI_JS.index("\nfunction ", h_start + 1)
        wait_fn = UI_JS[h_start:h_end] + "\n" + wait_fn
    script = textwrap.dedent(
        """\
        let now = 0, reloads = 0, published = [], actions = [];
        global.window = {};
        global.document = { baseURI: 'http://127.0.0.1:8788/' };
        global.location = { reload: () => { reloads += 1; } };
        global.Date = { now: () => now };
        global.setTimeout = (cb, ms) => { now += ms || 0; cb(); return 0; };
        global.fetch = async () => { throw new Error('server down'); };
        global.publishChatRuntimeNotice = rec => {
          published.push(rec.title);
          actions.push((rec.actions || []).map(a => a.label));
        };
        global.t = key => key;
        global.refreshSession = () => {};
        """
    ) + wait_fn + textwrap.dedent(
        """
        (async () => {
          await _waitForServerThenReload({ interval: 1, maxMs: 5 });
          process.stdout.write(JSON.stringify({reloads, published, actions}) + '\\n');
        })().catch(err => { console.error(err.stack || err.message); process.exit(1); });
        """
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"restart poller threw: {proc.stderr[:400]}"
    result = json.loads(proc.stdout.strip())
    assert result["reloads"] == 0
    assert result["published"] == [
        "runtime_notice_restarting_title",
        "runtime_notice_restart_slow_title",
    ]
    # In-progress carries no action; the timeout state offers the Reload it asks for.
    assert result["actions"] == [[], ["Reload"]]


def test_rotated_session_error_is_keyed_to_the_continuation_session():
    """Compression rotation reassigns S.session *after* the notice is published.

    Reading S.session at the publish site would key the record to the archived
    parent, so it would hide from the continuation the user is actually in, and
    the next turn's setBusy() clear (which uses the current session id) would
    never match it.
    """
    publish_idx = MESSAGES_JS.index("kind:_isProviderFailure?'provider_failure':'thread_error',")
    owner = MESSAGES_JS[publish_idx : MESSAGES_JS.index("runId:", publish_idx)]
    assert "sessionId:continuationSid||" in owner
    # The continuation id must already be resolved above the publish site.
    continuation_idx = MESSAGES_JS.rindex(
        "const continuationSid=(d.session&&d.session.session_id)", 0, publish_idx
    )
    adopt_idx = MESSAGES_JS.index("S.session=d.session;", continuation_idx)
    assert continuation_idx < publish_idx < adopt_idx, (
        "publish must sit after continuationSid is resolved but before S.session is reassigned"
    )


def test_cancelled_and_interrupted_turns_raise_no_failure_notice():
    idx = MESSAGES_JS.index("_isProviderFailure")
    guard = MESSAGES_JS[MESSAGES_JS.rindex("if(", 0, idx) : idx]
    assert "!isCancelled" in guard
    assert "!isInterrupted" in guard
    assert "!isRecoveryControlMessage" in guard


# ---------------------------------------------------------------------------
# Node-backed behavioral harness
# ---------------------------------------------------------------------------


def _extract_store(ui_src: str) -> str:
    start = ui_src.index("const CHAT_NOTICE_PRIORITY=")
    end = ui_src.index("const OFFLINE_RECHECK_MS=2500;")
    return ui_src[start:end]


_STUB = textwrap.dedent(
    """\
    'use strict';
    const _announced = [];
    function _mkEl(id) {
      return {
        id, hidden: false, className: '', dataset: {}, type: '', disabled: false,
        children: [], _text: '',
        get textContent() { return this._text; },
        set textContent(v) { this._text = v; this.children = []; if (id === 'chatRuntimeNoticeAlert' || id === 'chatRuntimeNoticeStatus') { if (v) _announced.push(id + ':' + v); } },
        appendChild(child) { this.children.push(child); return child; },
        setAttribute(k, v) { this[k] = v; },
        addEventListener(_evt, fn) { this._onClick = fn; },
      };
    }
    const _els = {
      chatRuntimeNotice: _mkEl('chatRuntimeNotice'),
      chatRuntimeNoticeAlert: _mkEl('chatRuntimeNoticeAlert'),
      chatRuntimeNoticeStatus: _mkEl('chatRuntimeNoticeStatus'),
    };
    global.document = {
      getElementById(id) { return _els[id] || null; },
      createElement(tag) { const el = _mkEl(''); el.tag = tag; return el; },
    };
    const $ = id => document.getElementById(id);
    // Identity t(): the store's own strings go through i18n, so the harness
    // needs the helper. Returning the key keeps assertions readable.
    global.t = key => key;
    global.S = { session: null };
    """
)

_CAPTURE = textwrap.dedent(
    """\
    const _host = _els.chatRuntimeNotice;
    const _rowText = row => {
      const copy = row.children[0] || { children: [] };
      return copy.children.map(c => c._text);
    };
    process.stdout.write(JSON.stringify({
      hidden: _host.hidden,
      kinds: _host.children.map(r => r.dataset.noticeKind),
      classes: _host.children.map(r => r.className),
      rows: _host.children.map(_rowText),
      buttons: _host.children.map(r => (r.children[1] ? r.children[1].children.map(b => [b._text, !!b.disabled]) : [])),
      announced: _announced,
      activeKinds: _activeChatRuntimeNotices().map(rec => rec.kind),
    }) + '\\n');
    process.exit(0);
    """
)


def _run(action: str, stub_extra: str = "") -> dict:
    if shutil.which("node") is None:
        pytest.skip("Node.js is required for the notice-store harness")
    script = (
        _STUB
        + stub_extra
        + "\n"
        + _extract_store(UI_JS)
        + "\nPromise.resolve().then(async () => {\n"
        + action
        + "\n}).then(() => {\n"
        + _CAPTURE
        + "\n}).catch(err => { console.error(err && err.stack || err); process.exit(1); });"
    )
    proc = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, f"node exit {proc.returncode}: {proc.stderr[:800]}"
    return json.loads(proc.stdout.strip())


def _publish(kind: str, title: str, **extra) -> str:
    payload = {"kind": kind, "title": title, "tone": "error"}
    payload.update(extra)
    return f"publishChatRuntimeNotice({json.dumps(payload)});"


def test_empty_stack_stays_hidden_and_reserves_no_space():
    result = _run("renderChatRuntimeNotices();")
    assert result["hidden"] is True
    assert result["kinds"] == []


def test_highest_priority_problem_renders_first_and_expanded():
    result = _run(
        _publish("reconnect", "Reload messages?", tone="info", detail="left mid-turn")
        + _publish("agent_unavailable", "Agent down", detail="heartbeat failed")
        + _publish("offline", "Connection lost", detail="device offline")
    )
    assert result["kinds"] == ["offline", "agent_unavailable", "reconnect"]
    # Only the top row keeps its detail line; the rest collapse to title only.
    assert result["rows"][0] == ["Connection lost", "device offline"]
    assert result["rows"][1] == ["Agent down"]
    assert "chat-runtime-notice-secondary" not in result["classes"][0]
    assert "chat-runtime-notice-secondary" in result["classes"][1]


def test_thread_error_outranks_offline():
    result = _run(
        "S.session = {session_id: 's1'};"
        + _publish("offline", "Connection lost")
        + _publish("thread_error", "Error", sessionId="s1")
    )
    assert result["kinds"][0] == "thread_error"


def test_duplicate_symptoms_from_one_condition_coalesce():
    result = _run(
        _publish("offline", "Connection lost", detail="one") * 1
        + _publish("offline", "Connection lost", detail="two")
        + _publish("offline", "Connection lost", detail="three")
    )
    assert result["kinds"] == ["offline"]
    assert result["rows"][0] == ["Connection lost", "three"]


def test_same_kind_different_run_ids_do_not_coalesce():
    result = _run(
        "S.session = {session_id: 's1'};"
        + _publish("provider_failure", "Rate limit reached", sessionId="s1", runId="r1")
        + _publish("provider_failure", "Out of credits", sessionId="s1", runId="r2")
    )
    assert result["activeKinds"] == ["provider_failure", "provider_failure"]


def test_polling_the_same_condition_announces_once():
    result = _run(
        _publish("offline", "Connection lost", detail="device offline") * 1
        + _publish("offline", "Connection lost", detail="device offline")
        + _publish("offline", "Connection lost", detail="device offline")
    )
    assert result["announced"] == ["chatRuntimeNoticeAlert:Connection lost. device offline"]


def test_blocking_failures_use_alert_and_recovery_uses_status():
    result = _run(
        _publish("offline", "Connection lost")
        + "clearChatRuntimeNotice('offline');"
        + _publish("reconnect", "Connection restored", tone="info", runId="recovered")
    )
    assert result["announced"] == [
        "chatRuntimeNoticeAlert:Connection lost",
        "chatRuntimeNoticeStatus:Connection restored",
    ]


def test_dismissal_survives_the_next_poll_of_the_same_condition():
    result = _run(
        _publish("agent_unavailable", "Agent down", dismissible=True)
        + "dismissChatRuntimeNotice(_chatNoticeKey('agent_unavailable','',''));"
        + _publish("agent_unavailable", "Agent down", dismissible=True)
    )
    assert result["activeKinds"] == []
    assert result["hidden"] is True


def test_resolving_a_condition_lets_a_later_recurrence_show_again():
    result = _run(
        _publish("agent_unavailable", "Agent down", dismissible=True)
        + "dismissChatRuntimeNotice(_chatNoticeKey('agent_unavailable','',''));"
        + "clearChatRuntimeNotice('agent_unavailable');"
        + _publish("agent_unavailable", "Agent down", dismissible=True)
    )
    assert result["activeKinds"] == ["agent_unavailable"]


def test_action_required_failure_stays_visible_across_renders():
    result = _run(
        _publish("offline", "Connection lost", actions=[{"label": "Check now"}])
        + "renderChatRuntimeNotices();renderChatRuntimeNotices();"
    )
    assert result["kinds"] == ["offline"]
    assert result["buttons"][0] == [["Check now", False]]


def test_transient_recovery_notice_expires_without_leaving_empty_space():
    result = _run(
        _publish("reconnect", "Connection restored", tone="info", ttlMs=30)
        + "await new Promise(r => setTimeout(r, 120));"
    )
    assert result["activeKinds"] == []
    assert result["hidden"] is True


def test_a_notice_from_another_session_is_not_shown_in_this_chat():
    result = _run(
        "S.session = {session_id: 'other'};"
        + _publish("thread_error", "Error", sessionId="other")
        + "S.session = {session_id: 'current'};renderChatRuntimeNotices();"
    )
    assert result["activeKinds"] == []
    assert result["hidden"] is True


def test_a_scoped_notice_is_hidden_once_no_session_is_active():
    """Deleting the session or opening a new chat sets S.session to null.

    Without an owning chat there is nothing for the record to belong to, so it
    must not fall back to showing over the empty/new-chat screen.
    """
    result = _run(
        "S.session = {session_id: 's1'};"
        + _publish("thread_error", "Error", sessionId="s1")
        + "S.session = null;renderChatRuntimeNotices();"
    )
    assert result["activeKinds"] == []
    assert result["hidden"] is True


def test_a_new_chats_notice_evicts_the_previous_chats_record_of_that_kind():
    """A record for an abandoned or deleted chat must not be retained forever."""
    result = _run(
        _publish("thread_error", "Error in old chat", sessionId="s1")
        + "S.session = {session_id: 's2'};"
        + _publish("thread_error", "Error in new chat", sessionId="s2")
        + "renderChatRuntimeNotices();"
    )
    assert result["activeKinds"] == ["thread_error"]
    assert result["rows"][0] == ["Error in new chat", ""] or result["rows"][0][0] == "Error in new chat"


def test_offline_recovery_replaces_the_failure_with_a_transient_status_row():
    result = _run(
        _publish("offline", "Connection lost", detail="device offline")
        + "clearChatRuntimeNotice('offline');"
        + _publish("reconnect", "Connection restored", tone="info", runId="recovered", ttlMs=30)
        + "await new Promise(r => setTimeout(r, 120));"
    )
    assert result["activeKinds"] == []
    assert result["hidden"] is True
    assert result["announced"] == [
        "chatRuntimeNoticeAlert:Connection lost. device offline",
        "chatRuntimeNoticeStatus:Connection restored",
    ]


def test_dismissing_one_notice_leaves_the_others_in_the_stack():
    result = _run(
        _publish("offline", "Connection lost")
        + _publish("provider_failure", "Rate limit reached", sessionId="s1", runId="r1", dismissible=True)
        + "S.session = {session_id: 's1'};"
        + "dismissChatRuntimeNotice(_chatNoticeKey('provider_failure','s1','r1'));"
    )
    assert result["activeKinds"] == ["offline"]
    assert result["hidden"] is False


def test_stack_is_capped_at_the_visible_maximum():
    result = _run(
        "S.session = {session_id: 's1'};"
        + _publish("thread_error", "Error", sessionId="s1")
        + _publish("offline", "Connection lost")
        + _publish("agent_unavailable", "Agent down")
        + _publish("provider_failure", "Rate limit", sessionId="s1", runId="r1")
        + _publish("provider_failure", "Out of credits", sessionId="s1", runId="r2")
        + _publish("reconnect", "Reload messages?", tone="info")
    )
    assert len(result["kinds"]) == 4
    assert result["kinds"][0] == "thread_error"
