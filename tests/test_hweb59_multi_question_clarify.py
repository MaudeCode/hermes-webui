"""HWEB-59 — multi-question (batch) clarify payloads.

The agent's ``clarify`` tool can ask up to five independent questions in one
call (``questions``, agent issue #18450). These cover the WebUI half of that
wire contract: normalization, whole-set dedupe identity, and the one-form /
one-submit rendering keyed by ``qid``.
"""

import json
import shutil
import subprocess
import textwrap
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


def test_callback_advertises_the_questions_keyword():
    """clarify_tool._callback_accepts_questions inspects the signature."""
    assert "lambda question, choices, questions=None: _clarify_callback_impl(" in STREAMING_PY
    assert "def _clarify_callback_impl(question, choices, sid, cancel_evt, put_event, questions=None):" in STREAMING_PY


# ══════════════════════════════════════════════════════════════════════════
# Frontend — one form with N fields, one submit keyed by qid
# ══════════════════════════════════════════════════════════════════════════
def _run_clarify_dom_harness(body: str):
    node = shutil.which("node")
    if not node:  # pragma: no cover - environment dependent
        pytest.skip("node not available")

    functions = _block(
        MESSAGES_JS,
        "function _clarifyBatchChoiceButton(",
        "function showClarifyCard(pending) {",
    )
    harness = _MINI_DOM + functions + "\n" + textwrap.dedent(body)
    proc = subprocess.run(
        [node, "--input-type=module", "-e", harness],
        capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stderr or proc.stdout
    return json.loads(proc.stdout)


# A DOM stub covering exactly what the clarify batch helpers touch. jsdom is
# not a dependency of this repo and the app ships no bundler.
_MINI_DOM = """
class El {
  constructor(tag) {
    this.tagName = String(tag).toUpperCase();
    this.children = [];
    this.dataset = {};
    this.attrs = {};
    this.classNameValue = '';
    this._text = '';
    this.value = '';
    this.hidden = false;
    this.disabled = false;
    this.onclick = null;
    this.onkeydown = null;
    this.placeholder = '';
    this.autocomplete = '';
    this.type = '';
  }
  set className(v) { this.classNameValue = String(v); }
  get className() { return this.classNameValue; }
  set textContent(v) { this._text = String(v); this.children = []; }
  get textContent() {
    return this.children.length ? this.children.map(c => c.textContent).join('') : this._text;
  }
  set innerHTML(v) { if (!v) this.children = []; }
  get innerHTML() { return this.children.length ? '<child>' : ''; }
  appendChild(child) { this.children.push(child); child.parent = this; return child; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return Object.prototype.hasOwnProperty.call(this.attrs, k) ? this.attrs[k] : null; }
  _classes() { return this.classNameValue.split(/\\s+/).filter(Boolean); }
  _matches(sel) {
    const attr = sel.match(/^(\\.[-\\w]+)\\[([-\\w]+)="([^"]*)"\\]$/);
    if (attr) return this._matches(attr[1]) && this.getAttribute(attr[2]) === attr[3];
    if (sel.startsWith('.')) return this._classes().includes(sel.slice(1));
    return this.tagName === sel.toUpperCase();
  }
  _descendants() {
    return this.children.flatMap(c => [c, ...c._descendants()]);
  }
  querySelectorAll(selector) {
    const parts = selector.split(',').map(s => s.trim()).filter(Boolean);
    return this._descendants().filter(el => parts.some(p => el._matches(p)));
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
}
const document = {createElement: (tag) => new El(tag)};
const _els = {};
function $(id) { return _els[id] || null; }
function t(key) { return key; }
function respondClarify() {}
"""


def test_batch_renders_one_field_per_question_and_submits_qid_keyed_answers():
    result = _run_clarify_dom_harness("""
    const container = new El('div');
    container.className = 'clarify-questions';
    _els.clarifyQuestions = container;

    _renderClarifyBatch(container, [
      {qid: 'q0', id: 'budget', question: 'How much?', choices: ['500', '1000']},
      {qid: 'q1', id: 'when', question: 'When?'},
      {qid: 'q2', id: 'who', question: 'Who?', choices: ['ann', 'bo'], multi_select: true},
    ]);

    const blocks = container.querySelectorAll('.clarify-q');
    const fields = container.querySelectorAll('.clarify-q-input');

    // Single-select: pick the second choice, then re-pick the first.
    const first = blocks[0].querySelectorAll('.clarify-choice');
    _toggleClarifyBatchChoice(blocks[0], first[1]);
    _toggleClarifyBatchChoice(blocks[0], first[0]);
    // Open-ended: type an answer.
    fields[1].value = '  next week  ';
    // Multi-select: both choices stay pressed.
    const third = blocks[2].querySelectorAll('.clarify-choice');
    _toggleClarifyBatchChoice(blocks[2], third[0]);
    _toggleClarifyBatchChoice(blocks[2], third[1]);

    const submission = _clarifyResolveSubmission();
    console.log(JSON.stringify({
      blocks: blocks.length,
      fields: fields.length,
      questions: blocks.map(b => b.querySelector('.clarify-question').textContent),
      submission: JSON.parse(submission.value),
      echo: submission.echo,
    }));
    """)

    assert result["blocks"] == 3
    assert result["fields"] == 3, "every question gets its own answer field"
    assert result["questions"] == ["How much?", "When?", "Who?"]
    assert result["submission"] == {"answers": {
        "q0": "500",
        "q1": "next week",
        "q2": ["ann", "bo"],
    }}, "answers are keyed by qid, never by the model-supplied id"
    assert "How much?" in result["echo"] and "next week" in result["echo"]


def test_unanswered_batch_questions_are_omitted_and_empty_batch_blocks_submit():
    result = _run_clarify_dom_harness("""
    const container = new El('div');
    container.className = 'clarify-questions';
    _els.clarifyQuestions = container;
    _renderClarifyBatch(container, [
      {qid: 'q0', question: 'A?'},
      {qid: 'q1', question: 'B?'},
    ]);

    const nothingAnswered = _clarifyResolveSubmission();
    container.querySelectorAll('.clarify-q-input')[1].value = 'only B';
    const partial = _clarifyResolveSubmission();

    console.log(JSON.stringify({
      nothingAnswered,
      partial: JSON.parse(partial.value),
    }));
    """)

    assert result["nothingAnswered"] is None, "an empty form must not submit"
    assert result["partial"] == {"answers": {"q1": "only B"}}


def test_single_question_submission_is_byte_identical_without_a_batch():
    result = _run_clarify_dom_harness("""
    const input = new El('input');
    _els.clarifyInput = input;
    const container = new El('div');
    container.hidden = true;
    _els.clarifyQuestions = container;

    input.value = '  typed answer  ';
    const typed = _clarifyResolveSubmission();
    const clicked = _clarifyResolveSubmission('main (Recommended)');
    input.value = '   ';
    const blank = _clarifyResolveSubmission(undefined);

    console.log(JSON.stringify({typed, clicked, blank}));
    """)

    assert result["typed"] == {"value": "typed answer", "echo": "typed answer"}
    assert result["clicked"] == {"value": "main (Recommended)", "echo": "main (Recommended)"}
    assert result["blank"] is None


def test_batch_card_hides_the_shared_single_question_controls():
    show = _block(MESSAGES_JS, "function showClarifyCard(pending) {", "async function respondClarify(")

    assert "const isBatch = batchQuestions.length > 0;" in show
    assert "questionEl.hidden = isBatch;" in show
    assert "input.hidden = isBatch;" in show
    assert "if (isBatch && !sameClarify) _renderClarifyBatch(questionsEl, batchQuestions);" in show
    # The signature must carry the whole set so a changed batch re-renders.
    assert "questions: batchQuestions," in show
    assert 'card.setAttribute("aria-describedby", isBatch ? "clarifyQuestions clarifyHint"' in show
