from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for index in range(brace, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : index]
    raise AssertionError(f"unterminated function: {signature}")


def test_send_owner_switch_guard_saves_the_original_draft_without_touching_new_pane():
    helper = _function_body(MESSAGES, "function _abortSendAfterOwnerSwitch(")

    assert "visibleSid===activeSid" in helper
    assert "_restoreComposerDraftAfterFailedSend(draftText, filesSnapshot, activeSid, clearPromise)" in helper
    assert "S.messages" not in helper
    assert "setBusy" not in helper


def test_send_rechecks_owner_after_each_prestart_await():
    body = _function_body(MESSAGES, "async function send(")
    upload_wait = body.index("await uploadPendingFiles(")
    skill_wait = body.index("await _pending.promise")
    optimistic_write = body.index("S.messages.push(userMsg)")

    first_guard = body.index("_abortSendAfterOwnerSwitch(", upload_wait)
    second_guard = body.index("_abortSendAfterOwnerSwitch(", skill_wait)
    assert upload_wait < first_guard < skill_wait
    assert skill_wait < second_guard < optimistic_write

