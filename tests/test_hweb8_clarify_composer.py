"""HWEB-8: the chat composer answers clarification prompts.

The card above the composer keeps the question, choices, queue position and
progress; the typed answer is ``#msg`` itself. These tests drive the real
``showClarifyCard`` / ``respondClarify`` / composer-lock code through a small
DOM stub and check the routing contract: the ordinary draft survives untouched,
the answer never reaches ``send()``, and a multi-question request is answered
one question at a time with one ``{"answers": ...}`` submit at the end.
"""

import re

import pytest

from tests._clarify_composer_harness import (
    MESSAGES_JS, NODE, ROOT, UI_JS, block, run_clarify_harness,
)

INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
STYLE_CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")
BOOT_JS = (ROOT / "static" / "boot.js").read_text(encoding="utf-8")
I18N_JS = (ROOT / "static" / "i18n.js").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


# ── The card owns no text field any more ────────────────────────────────────

def test_card_has_no_input_or_submit_of_its_own():
    for needle in ('id="clarifyInput"', 'id="clarifySubmit"', 'id="clarifyQuestions"', "clarify-q-input", "clarify-submit", "clarify-input"):
        assert needle not in INDEX_HTML, needle
        assert needle not in MESSAGES_JS, needle
        assert needle not in STYLE_CSS, needle
    assert '$("clarifyInput")' not in MESSAGES_JS and '$("clarifySubmit")' not in MESSAGES_JS
    assert 'id="clarifyProgress"' in INDEX_HTML and 'id="clarifyProgress"' in MESSAGES_JS
    assert 'aria-controls="clarifyCounter clarifyProgress clarifyQuestion clarifyChoices clarifyHint"' in INDEX_HTML


def test_composer_footer_hides_message_only_controls_while_a_clarification_is_active():
    rule = re.search(r"\.composer-box\.clarify-active [^{]+\{display:none;\}", STYLE_CSS)
    assert rule, "no .composer-box.clarify-active hide rule"
    for part in (".composer-left", ".attach-tray", ".mic-status", ".voice-mode-bar", ".composer-mobile-config-panel"):
        assert f".composer-box.clarify-active {part}" in rule.group(0), part


def test_new_composer_strings_exist_in_every_locale():
    locale_blocks = I18N_JS.count("clarify_heading:")
    assert locale_blocks == 15
    for key in ("clarify_composer_placeholder", "clarify_composer_placeholder_choices",
                "clarify_progress", "composer_clarify", "composer_clarify_next"):
        assert I18N_JS.count(f"{key}:") == locale_blocks, key
    assert "clarify_input_placeholder" not in I18N_JS


# ── Nothing typed as an answer can reach a message path ─────────────────────

def test_send_routes_to_the_answer_while_a_clarification_owns_the_composer():
    send = block(MESSAGES_JS, "async function send(){", "_sendInProgress = true;")
    guard = "if(typeof isClarifyComposerActive==='function'&&isClarifyComposerActive()){"
    assert guard in send
    assert "await respondClarify();" in send
    # The guard is the first statement: it runs before the in-flight re-queue branch.
    assert send.index(guard) < send.index("if (_sendInProgress)")
    # The queue drain hands a queued message to send() by writing the textarea;
    # it must re-queue instead while the composer is a clarification answer.
    drain = block(UI_JS, "function setBusy(v){", "// ── Queue chip display")
    assert "isClarifyComposerActive())){\n          queueSessionMessage(sid,next);" in drain




def test_composer_input_and_paste_route_around_message_features():
    input_handler = block(BOOT_JS, "$('msg').addEventListener('input',()=>{", "// #5514/#5515")
    guard = input_handler.index("isClarifyComposerActive()")
    assert guard < input_handler.index("_saveComposerDraft("), "the answer must not be saved as the session draft"
    assert guard < input_handler.index("getSlashAutocompleteMatches"), "slash autocomplete is message-only"
    assert "onClarifyComposerInput" in input_handler
    paste = block(BOOT_JS, "$('msg').addEventListener('paste',e=>{", "window.addEventListener('resize'")
    assert paste.index("isClarifyComposerActive()") < paste.index("addFiles(")
    add_files = block(UI_JS, "function addFiles(files){", "const _uploadPendingFilesProgressBySession")
    assert "isClarifyComposerActive()) return;" in add_files


# ── Single question ──────────────────────────────────────────────────────────

def test_single_question_uses_the_composer_and_restores_the_draft_after_success():
    result = run_clarify_harness("""
    const msg = $('msg');
    msg.value = 'my half-written message';
    S.pendingFiles = [{name: 'a.png'}];
    showClarifyCard({question: 'Which branch?', choices_offered: ['main', 'dev'], clarify_id: 'c1'});
    const during = {
      value: msg.value,
      placeholder: msg.placeholder,
      label: msg.getAttribute('aria-label'),
      boxClass: $('composerBox').className,
      action: $('btnSend').dataset.action,
      title: $('btnSend').title,
      focused: document.activeElement === msg,
      choices: $('clarifyChoices').querySelectorAll('.clarify-choice').map(b => b.dataset.choice),
      progressHidden: $('clarifyProgress').hidden,
    };
    msg.value = 'a release tag';
    updateSendBtn();
    const armed = {action: $('btnSend').dataset.action, title: $('btnSend').title};
    await send();
    await tick();
    console.log(JSON.stringify({
      during, armed, apiCalls, sendCalls,
      after: {
        value: msg.value, placeholder: msg.placeholder, label: msg.getAttribute('aria-label'),
        boxClass: $('composerBox').className, files: S.pendingFiles, cardVisible: $('clarifyCard').classList.contains('visible'),
        echo: S.messages, active: isClarifyComposerActive(),
      },
    }));
    """)
    assert result["during"] == {
        "value": "",
        "placeholder": "clarify_composer_placeholder_choices",
        "label": "clarify_heading: Which branch?",
        "boxClass": "clarify-active",
        "action": "disabled",
        "title": "Respond to the clarification request",
        "focused": True,
        "choices": ["main", "dev", "other"],
        "progressHidden": True,
    }
    assert result["armed"] == {"action": "clarify", "title": "Send answer"}
    assert result["apiCalls"] == [{"path": "/api/clarify/respond",
                                   "body": {"session_id": "s1", "response": "a release tag", "clarify_id": "c1"}}]
    assert result["sendCalls"] == [], "Enter must never reach the chat path of send()"
    assert result["after"]["value"] == "my half-written message"
    assert result["after"]["placeholder"] == "Message Hermes…"
    assert result["after"]["label"] is None
    assert result["after"]["boxClass"] == ""
    assert result["after"]["files"] == [{"name": "a.png"}]
    assert result["after"]["cardVisible"] is False
    assert result["after"]["active"] is False
    assert [m["content"] for m in result["after"]["echo"]] == ["a release tag"]


def test_empty_answer_does_not_submit_and_primary_button_ignores_stop():
    result = run_clarify_harness("""
    const msg = $('msg');
    showClarifyCard({question: 'Which branch?', clarify_id: 'c1'});
    msg.value = '   ';
    updateSendBtn();
    document.activeElement = null;
    await send();
    await handleComposerPrimaryAction();
    await tick();
    console.log(JSON.stringify({
      apiCalls, sendCalls, action: $('btnSend').dataset.action, placeholder: msg.placeholder,
      refocused: document.activeElement === msg, stillActive: isClarifyComposerActive(),
    }));
    """)
    assert result["apiCalls"] == []
    assert result["sendCalls"] == []
    assert result["action"] == "disabled"
    assert result["placeholder"] == "clarify_composer_placeholder"
    assert result["refocused"] is True
    assert result["stillActive"] is True


def test_choice_click_submits_that_choice_and_other_focuses_the_composer():
    result = run_clarify_harness("""
    const msg = $('msg');
    showClarifyCard({question: 'Which branch?', choices_offered: ['main', 'dev'], clarify_id: 'c1'});
    document.activeElement = null;
    const buttons = $('clarifyChoices').querySelectorAll('.clarify-choice');
    buttons[2].onclick();
    const otherFocused = document.activeElement === msg;
    buttons[1].onclick();
    await tick();
    console.log(JSON.stringify({otherFocused, apiCalls, value: msg.value}));
    """)
    assert result["otherFocused"] is True
    assert result["apiCalls"][0]["body"]["response"] == "dev"
    assert result["value"] == ""


# ── Multi-question: one at a time, one submit ───────────────────────────────

BATCH = """
    showClarifyCard({clarify_id: 'b1', questions: [
      {qid: 'q0', id: 'budget', question: 'How much?', choices: ['500', '1000']},
      {qid: 'q1', id: 'when', question: 'When?'},
      {qid: 'q2', id: 'who', question: 'Who?', choices: ['ann', 'bo'], multi_select: true},
    ]});
    const msg = $('msg');
    const snap = () => ({
      question: $('clarifyQuestion').textContent,
      progress: $('clarifyProgress').textContent,
      progressHidden: $('clarifyProgress').hidden,
      label: msg.getAttribute('aria-label'),
      placeholder: msg.placeholder,
      value: msg.value,
      action: $('btnSend').dataset.action,
      title: $('btnSend').title,
      pressed: $('clarifyChoices').querySelectorAll('.clarify-choice[aria-pressed="true"]').map(b => b.dataset.choice),
    });
"""


def test_batch_walks_the_questions_and_submits_qid_keyed_answers_once():
    result = run_clarify_harness(BATCH + """
    const steps = {};
    steps.first = snap();
    // Single-select: the pick advances by itself.
    $('clarifyChoices').querySelectorAll('.clarify-choice')[1].onclick();
    await tick();
    steps.second = snap();
    steps.apiAfterPick = apiCalls.length;
    // Empty required answer: Enter does nothing.
    await send();
    await tick();
    steps.stillSecond = snap();
    msg.value = '  next week  ';
    updateSendBtn();
    steps.secondArmed = snap();
    await send();
    await tick();
    steps.third = snap();
    // Multi-select: picks toggle and stay until the composer action advances.
    const who = $('clarifyChoices').querySelectorAll('.clarify-choice');
    who[0].onclick(); who[1].onclick();
    steps.thirdPicked = snap();
    who[1].onclick(); who[1].onclick();
    await send();
    await tick();
    console.log(JSON.stringify({steps, apiCalls, echo: S.messages.map(m => m.content), active: isClarifyComposerActive()}));
    """)
    s = result["steps"]
    assert s["first"]["question"] == "How much?"
    assert s["first"]["progress"] == "clarify_progress:1/3" and s["first"]["progressHidden"] is False
    assert s["first"]["label"] == "clarify_heading: How much?"
    assert s["first"]["placeholder"] == "clarify_composer_placeholder_choices"
    assert s["first"]["action"] == "disabled"
    assert s["second"]["question"] == "When?" and s["second"]["progress"] == "clarify_progress:2/3"
    assert s["second"]["placeholder"] == "clarify_composer_placeholder"
    assert s["second"]["value"] == ""
    assert s["apiAfterPick"] == 0, "a pick on a non-final question must not submit"
    assert s["stillSecond"]["question"] == "When?", "an empty required answer must not advance"
    assert s["secondArmed"] == {**s["secondArmed"], "action": "clarify", "title": "Next question"}
    assert s["third"]["question"] == "Who?" and s["third"]["progress"] == "clarify_progress:3/3"
    assert s["third"]["action"] == "disabled"
    assert s["thirdPicked"]["pressed"] == ["ann", "bo"]
    assert s["thirdPicked"]["action"] == "clarify" and s["thirdPicked"]["title"] == "Send answer"
    assert len(result["apiCalls"]) == 1
    body = result["apiCalls"][0]["body"]
    assert body["clarify_id"] == "b1"
    import json
    assert json.loads(body["response"]) == {"answers": {"q0": "1000", "q1": "next week", "q2": ["ann", "bo"]}}, \
        "answers are keyed by qid, never by the model-supplied id"
    assert result["echo"] == ["How much?\n1000\n\nWhen?\nnext week\n\nWho?\nann, bo"]
    assert result["active"] is False


def test_typed_answer_overrides_picks_and_a_pick_clears_typed_text():
    result = run_clarify_harness(BATCH + """
    $('clarifyChoices').querySelectorAll('.clarify-choice')[0].onclick();
    await tick();
    msg.value = 'whenever';
    await send();
    await tick();
    const who = () => $('clarifyChoices').querySelectorAll('.clarify-choice');
    who()[0].onclick();
    msg.value = 'the whole team';
    onClarifyComposerInput();
    const afterTyping = snap();
    who()[1].onclick();
    const afterPick = snap();
    who()[1].onclick();
    msg.value = 'the whole team';
    onClarifyComposerInput();
    await send();
    await tick();
    console.log(JSON.stringify({afterTyping, afterPick, response: JSON.parse(apiCalls[0].body.response)}));
    """)
    assert result["afterTyping"]["pressed"] == [], "typing must clear the picks it overrides"
    assert result["afterPick"]["value"] == "" and result["afterPick"]["pressed"] == ["bo"]
    assert result["response"]["answers"]["q2"] == ["the whole team"], "a custom multi-select answer stays an array"


def test_same_prompt_redelivery_keeps_the_answer_and_a_new_id_resets_it():
    result = run_clarify_harness(BATCH + """
    msg.value = 'parked draft';
    hideClarifyCard(true, 'session');
    msg.value = 'ordinary draft';
    showClarifyCard({clarify_id: 'b1', questions: [{qid: 'q0', question: 'How much?'}, {qid: 'q1', question: 'When?'}]});
    msg.value = '700';
    await send();
    await tick();
    msg.value = 'half an ans';
    // The 3s poll re-delivers the identical prompt.
    showClarifyCard({clarify_id: 'b1', questions: [{qid: 'q0', question: 'How much?'}, {qid: 'q1', question: 'When?'}]});
    const redelivered = {...snap(), answers: _clarifyBatch.answers, draft: clarifyComposerDraft()};
    // The queue head is replaced by a different clarification.
    showClarifyCard({clarify_id: 'b2', question: 'Deploy?', choices_offered: ['yes', 'no']});
    const replaced = {...snap(), batch: _clarifyBatch, draft: clarifyComposerDraft()};
    hideClarifyCard(true, 'cancelled');
    console.log(JSON.stringify({redelivered, replaced, restored: msg.value, active: isClarifyComposerActive()}));
    """)
    assert result["redelivered"]["question"] == "When?"
    assert result["redelivered"]["value"] == "half an ans"
    assert result["redelivered"]["answers"] == {"q0": "700"}
    assert result["redelivered"]["draft"] == "ordinary draft"
    assert result["replaced"]["question"] == "Deploy?"
    assert result["replaced"]["value"] == ""
    assert result["replaced"]["batch"] is None
    assert result["replaced"]["progressHidden"] is True
    assert result["replaced"]["draft"] == "ordinary draft"
    assert result["restored"] == "ordinary draft"
    assert result["active"] is False


# ── The ordinary draft is never the answer ──────────────────────────────────

def test_server_draft_restore_lands_in_the_parked_draft_not_the_answer():
    result = run_clarify_harness("""
    const msg = $('msg');
    showClarifyCard({question: 'Which branch?', clarify_id: 'c1'});
    msg.value = 'typing an answer';
    _restoreComposerDraft({text: 'draft synced from another tab', files: []}, 's1');
    const midway = {value: msg.value, parked: clarifyComposerDraft()};
    _restoreComposerDraft({text: '', files: []}, 's1');
    const cleared = {value: msg.value, parked: clarifyComposerDraft()};
    hideClarifyCard(true, 'cancelled');
    console.log(JSON.stringify({midway, cleared, restored: msg.value}));
    """)
    assert result["midway"] == {"value": "typing an answer", "parked": "draft synced from another tab"}
    assert result["cleared"] == {"value": "typing an answer", "parked": ""}
    assert result["restored"] == ""


def test_expiry_rescues_typed_and_recorded_answers_after_restoring_the_draft():
    result = run_clarify_harness(BATCH.replace("showClarifyCard(", "$('msg').value = 'keep me';\n    showClarifyCard(") + """
    $('clarifyChoices').querySelectorAll('.clarify-choice')[0].onclick();
    await tick();
    msg.value = 'in a fortnight';
    hideClarifyCard(true, 'expired');
    console.log(JSON.stringify({value: msg.value, active: isClarifyComposerActive(), toasts}));
    """)
    assert result["value"] == "keep me\n\nHow much?\n500\n\nWhen?\nin a fortnight"
    assert result["active"] is False
    assert result["toasts"] == ["Clarification timed out. Your draft was kept in the composer."]


def test_retryable_failure_keeps_the_answer_in_the_composer():
    result = run_clarify_harness("""
    const msg = $('msg');
    msg.value = 'ordinary draft';
    showClarifyCard({question: 'Which branch?', clarify_id: 'c1'});
    msg.value = 'a release tag';
    apiImpl = async () => { throw new Error('network down'); };
    await respondClarify();
    const failed = {
      value: msg.value, active: isClarifyComposerActive(), submitting: _clarifySubmitting,
      cardVisible: $('clarifyCard').classList.contains('visible'), action: (updateSendBtn(), $('btnSend').dataset.action),
    };
    apiImpl = async () => ({ok: true});
    await respondClarify();
    console.log(JSON.stringify({failed, calls: apiCalls.map(c => c.body.response), restored: msg.value}));
    """)
    assert result["failed"] == {"value": "a release tag", "active": True, "submitting": False,
                                "cardVisible": True, "action": "clarify"}
    assert result["calls"] == ["a release tag", "a release tag"]
    assert result["restored"] == "ordinary draft"


def test_stale_409_dismisses_the_card_and_rescues_the_answer_into_the_draft():
    result = run_clarify_harness("""
    const msg = $('msg');
    msg.value = 'ordinary draft';
    showClarifyCard({question: 'Which branch?', clarify_id: 'c1'});
    msg.value = 'a release tag';
    apiImpl = async () => { const e = new Error('expired'); e.status = 409; throw e; };
    await respondClarify();
    console.log(JSON.stringify({value: msg.value, active: isClarifyComposerActive(),
      cardVisible: $('clarifyCard').classList.contains('visible'), toasts}));
    """)
    assert result["value"] == "ordinary draft\n\na release tag"
    assert result["active"] is False
    assert result["cardVisible"] is False
    assert result["toasts"] == ["Clarification timed out. Your draft was kept in the composer."]
