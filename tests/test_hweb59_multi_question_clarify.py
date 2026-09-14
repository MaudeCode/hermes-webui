"""HWEB-59 — multi-question (batch) clarify payloads.

The agent's ``clarify`` tool can ask up to five independent questions in one
call (``questions``, agent issue #18450). These cover the WebUI half of that
wire contract: normalization, whole-set dedupe identity, and the one-form /
one-submit rendering keyed by ``qid``.
"""

import json
from pathlib import Path

import pytest

from api import clarify

ROOT = Path(__file__).resolve().parents[1]
MESSAGES_JS = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")
STREAMING_PY = (ROOT / "api" / "streaming.py").read_text(encoding="utf-8")


def _block(source: str, start_marker: str, end_marker: str) -> str:
    start = source.index(start_marker)
    end = source.index(end_marker, start)
    return source[start:end]


@pytest.fixture(autouse=True)
def _clean_clarify_state():
    clarify._gateway_queues.clear()
    clarify._pending.clear()
    yield
    clarify._gateway_queues.clear()
    clarify._pending.clear()


# ══════════════════════════════════════════════════════════════════════════
# normalize_questions — the wire contract mirrored from clarify_tool.py
# ══════════════════════════════════════════════════════════════════════════
def test_batch_normalizes_to_qid_keyed_entries():
    batch = clarify.normalize_questions([
        {"id": "budget", "question": "How much?", "choices": ["a", "b"]},
        {"question": "When?"},
        {"id": "who", "question": "Who?", "choices": ["x"], "multi_select": True},
    ])

    assert [q["qid"] for q in batch] == ["q0", "q1", "q2"]
    # The model-supplied id rides along for the result JSON but is never the key.
    assert [q["id"] for q in batch] == ["budget", None, "who"]
    assert [q["question"] for q in batch] == ["How much?", "When?", "Who?"]
    assert batch[1]["choices"] is None
    assert batch[2]["multi_select"] is True


def test_empty_questions_array_falls_back_to_single_question_path():
    """``_normalize_questions`` returns (None, None) for [] — not an error."""
    assert clarify.normalize_questions([]) is None
    assert clarify.normalize_questions(None) is None
    assert clarify.normalize_questions("Which one?") is None


def test_bare_string_items_normalize_to_question_objects():
    batch = clarify.normalize_questions(["Q1?", "Q2?"])

    assert batch == [
        {"qid": "q0", "id": None, "question": "Q1?", "choices": None,
         "choices_offered": None, "multi_select": False},
        {"qid": "q1", "id": None, "question": "Q2?", "choices": None,
         "choices_offered": None, "multi_select": False},
    ]


def test_over_max_questions_is_rejected_like_the_agent_rejects_it():
    """The agent refuses a >5 batch before any surface sees it; so do we."""
    assert clarify.MAX_QUESTIONS == 5
    at_limit = [f"Q{i}?" for i in range(clarify.MAX_QUESTIONS)]
    assert len(clarify.normalize_questions(at_limit)) == clarify.MAX_QUESTIONS
    assert clarify.normalize_questions(at_limit + ["Q5?"]) is None


def test_over_max_batch_still_shows_the_user_a_question():
    """Rejecting the form must not leave an empty card the user cannot act on."""
    impl = _block(
        STREAMING_PY,
        "def _clarify_callback_impl(",
        "entry = _submit_clarify_pending(sid, data)",
    )
    assert "_normalize_clarify_questions(questions[:1])" in impl
    assert "data['question'] = head[0]['question']" in impl


def test_unrecognized_items_are_surfaced_not_dropped():
    batch = clarify.normalize_questions([
        {"question": "Readable?"},
        {"prompt": "no question key"},
        42,
        {"question": "Also readable?"},
    ])

    assert len(batch) == 4, "no question may be discarded without a visible surface"
    assert [q["qid"] for q in batch] == ["q0", "q1", "q2", "q3"]
    assert all(q["question"].strip() for q in batch)
    # The unreadable entries render as their raw JSON rather than vanishing.
    assert json.loads(batch[1]["question"]) == {"prompt": "no question key"}
    assert batch[2]["question"] == "42"


def test_dict_shaped_choices_are_flattened_and_capped():
    batch = clarify.normalize_questions([{
        "question": "Pick",
        "choices": [{"label": "one"}, "two", {"nope": "x"}, "three", "four", "five"],
    }])

    assert batch[0]["choices"] == ["one", "two", "three", "four"]


# ══════════════════════════════════════════════════════════════════════════
# Dedupe identity — the whole question set, not one string
# ══════════════════════════════════════════════════════════════════════════
def _submit_batch(sid, questions):
    return clarify.submit_pending(sid, {
        "question": "",
        "session_id": sid,
        "kind": "clarify",
        "questions": clarify.normalize_questions(questions),
    })


def test_identical_batch_is_deduped():
    questions = ["Which branch?", "Which runtime?"]
    first = _submit_batch("s-dedupe", questions)
    second = _submit_batch("s-dedupe", questions)

    assert second is first
    assert clarify.pending_count("s-dedupe") == 1


def test_batch_differing_in_any_single_question_is_not_deduped():
    first = _submit_batch("s-diff", ["Which branch?", "Which runtime?"])
    second = _submit_batch("s-diff", ["Which branch?", "Which region?"])

    assert second is not first
    assert clarify.pending_count("s-diff") == 2


def test_batch_differing_only_in_choices_is_not_deduped():
    base = [{"question": "Which branch?", "choices": ["main"]}]
    other = [{"question": "Which branch?", "choices": ["main", "dev"]}]

    assert _submit_batch("s-choices", base) is not _submit_batch("s-choices", other)
    assert clarify.pending_count("s-choices") == 2


# ══════════════════════════════════════════════════════════════════════════
# The single-question path is untouched
# ══════════════════════════════════════════════════════════════════════════
def test_single_question_payload_is_unchanged():
    """An older agent build sends and stores exactly what it did before."""
    data = {
        "question": "Which branch?",
        "choices_offered": ["main", "dev"],
        "session_id": "s-single",
        "kind": "clarify",
    }
    entry = clarify.submit_pending("s-single", dict(data))
    pending = clarify.get_pending("s-single")

    assert "questions" not in pending
    assert pending["question"] == "Which branch?"
    assert pending["choices_offered"] == ["main", "dev"]

    # Identical single questions still collapse; a different one still queues.
    assert clarify.submit_pending("s-single", dict(data)) is entry
    assert clarify.pending_count("s-single") == 1
    clarify.submit_pending("s-single", dict(data, question="Which runtime?"))
    assert clarify.pending_count("s-single") == 2


def test_batch_and_single_identities_never_collide():
    single = {"question": "Which branch?", "choices_offered": ["main"]}
    batch = {"question": "Which branch?",
             "questions": clarify.normalize_questions([{"question": "Which branch?",
                                                       "choices": ["main"]}])}

    assert clarify._dedupe_identity(single) != clarify._dedupe_identity(batch)


def test_batch_differing_only_in_multi_select_is_not_deduped():
    """A scalar answer cannot stand in for an array one (codex P2)."""
    single = [{"question": "Which files?", "choices": ["a", "b"]}]
    multi = [{"question": "Which files?", "choices": ["a", "b"], "multi_select": True}]

    assert _submit_batch("s-mode", single) is not _submit_batch("s-mode", multi)
    assert clarify.pending_count("s-mode") == 2


def test_callback_advertises_the_questions_keyword():
    """clarify_tool._callback_accepts_questions inspects the signature."""
    assert "lambda question, choices, questions=None: _clarify_callback_impl(" in STREAMING_PY
    assert "def _clarify_callback_impl(question, choices, sid, cancel_evt, put_event, questions=None):" in STREAMING_PY


# ══════════════════════════════════════════════════════════════════════════
# Frontend — one question at a time, one submit keyed by qid (HWEB-8 moved
# the typed answer into the chat composer; the wire contract is unchanged)
# ══════════════════════════════════════════════════════════════════════════
from tests._clarify_composer_harness import run_clarify_harness


def test_batch_answers_are_keyed_by_qid_and_submitted_once():
    result = run_clarify_harness("""
    showClarifyCard({clarify_id: 'b1', questions: [
      {qid: 'q0', id: 'budget', question: 'How much?', choices: ['500', '1000']},
      {qid: 'q1', id: 'when', question: 'When?'},
      {qid: 'q2', id: 'who', question: 'Who?', choices: ['ann', 'bo'], multi_select: true},
    ]});
    $('clarifyChoices').querySelectorAll('.clarify-choice')[0].onclick();
    await tick();
    $('msg').value = '  next week  ';
    await respondClarify();
    const who = $('clarifyChoices').querySelectorAll('.clarify-choice');
    who[0].onclick(); who[1].onclick();
    await respondClarify();
    console.log(JSON.stringify({calls: apiCalls.length, body: apiCalls[0].body, echo: S.messages[0].content}));
    """)
    assert result["calls"] == 1, "one request carries every answer"
    assert result["body"]["clarify_id"] == "b1"
    assert json.loads(result["body"]["response"]) == {"answers": {
        "q0": "500",
        "q1": "next week",
        "q2": ["ann", "bo"],
    }}, "answers are keyed by qid, never by the model-supplied id"
    assert "How much?" in result["echo"] and "next week" in result["echo"]


def test_unanswered_question_blocks_progress_instead_of_being_omitted():
    """Every question is required: an empty answer neither advances nor submits."""
    result = run_clarify_harness("""
    showClarifyCard({clarify_id: 'b1', questions: [{qid: 'q0', question: 'A?'}, {qid: 'q1', question: 'B?'}]});
    await respondClarify();
    const stuck = $('clarifyQuestion').textContent;
    $('msg').value = 'only A';
    await respondClarify();
    await respondClarify();
    console.log(JSON.stringify({stuck, now: $('clarifyQuestion').textContent, calls: apiCalls.length}));
    """)
    assert result == {"stuck": "A?", "now": "B?", "calls": 0}


def test_single_question_submission_is_byte_identical_without_a_batch():
    result = run_clarify_harness("""
    showClarifyCard({question: 'Which branch?', choices_offered: ['main (Recommended)'], clarify_id: 'c1'});
    $('msg').value = '  typed answer  ';
    await respondClarify();
    showClarifyCard({question: 'Which branch?', choices_offered: ['main (Recommended)'], clarify_id: 'c2'});
    $('clarifyChoices').querySelectorAll('.clarify-choice')[0].onclick();
    await tick();
    console.log(JSON.stringify(apiCalls.map(c => c.body.response)));
    """)
    assert result == ["typed answer", "main (Recommended)"]


def test_multi_select_scalar_and_array_answers_never_mix():
    """A typed custom answer to a multi-select question is still an array (codex P2)."""
    result = run_clarify_harness("""
    showClarifyCard({clarify_id: 'b1', questions: [{qid: 'q0', question: 'Files?', choices: ['a', 'b'], multi_select: true}]});
    $('clarifyChoices').querySelectorAll('.clarify-choice')[0].onclick();
    $('msg').value = 'and README';
    onClarifyComposerInput();
    await respondClarify();
    console.log(JSON.stringify(JSON.parse(apiCalls[0].body.response)));
    """)
    assert result == {"answers": {"q0": ["and README"]}}
