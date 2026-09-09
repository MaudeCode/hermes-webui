"""HWEB-44: in-flight state persists on a bounded cadence, not once per tool event.

`saveInflightState` re-reads, deep-copies, truncates and re-stringifies the whole
stored map (budget 1.5 MB) synchronously on the main thread. Token updates were
already throttled; the tool-event path called `persistInflightState()` directly,
so a 40-tool turn did 40 of those cycles.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent
MESSAGES_JS = (REPO_ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _function_body(source: str, signature: str) -> str:
    start = source.index(signature)
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[brace + 1 : i]
    raise AssertionError(f"unterminated body for {signature}")


def test_tool_event_path_uses_the_throttle_not_a_direct_persist():
    body = _function_body(MESSAGES_JS, "function upsertLiveToolCall(d, phase)")

    assert "_throttledPersist()" in body, (
        "the tool-event upsert must route its in-flight write through the 2s throttle"
    )
    assert "persistInflightState()" not in body, (
        "the tool-event upsert must not bypass the throttle with a direct persist"
    )


def test_terminal_paths_flush_instead_of_discarding_the_pending_write():
    # Cancelling the timer on a terminal event would drop the last tool result
    # of the turn now that tool events ride the throttle.
    assert "if(_persistTimer){clearTimeout(_persistTimer);_persistTimer=null;}" not in MESSAGES_JS
    assert MESSAGES_JS.count("_flushPersist();") >= 6, (
        "every terminal stream path (fallback, done, apperror, cancel, settled, error) must flush"
    )
    assert "window.addEventListener('pagehide',_flushPendingInflightPersists)" in MESSAGES_JS


NODE_SCRIPT = r"""
const fs = require('fs');
const vm = require('vm');

const ui = fs.readFileSync(process.argv[2], 'utf8');
const msgs = fs.readFileSync(process.argv[3], 'utf8');

function slice(src, from, to, label) {
  const a = src.indexOf(from);
  const b = src.indexOf(to, a + 1);
  if (a < 0 || b < 0) throw new Error('block not found: ' + label);
  return src.slice(a, b);
}

const storage = slice(ui, 'const INFLIGHT_STATE_KEY = ', '// ─── Todo state', 'inflight storage');
const registry = slice(msgs, 'const _INFLIGHT_PERSIST_FLUSHERS=new Set();', 'function attachLiveStream(', 'flusher registry');
const throttle = slice(msgs, '  let _persistTimer=null;', '  function _closeSource(', 'persist throttle');

function assert(cond, msg) { if (!cond) throw new Error(msg); }

function harness() {
  const store = {};
  let setItemCalls = 0;
  const timers = new Map();
  let nextTimer = 1;
  // The live in-flight snapshot the stream closure would own.
  const inflight = { streamId: 'stream-1', messages: [], toolCalls: [] };
  const pagehide = [];

  const context = {
    localStorage: {
      getItem: (k) => (k in store ? store[k] : null),
      setItem: (k, v) => { setItemCalls++; store[k] = String(v); },
      removeItem: (k) => { delete store[k]; },
    },
    window: {
      addEventListener: (name, fn) => { if (name === 'pagehide') pagehide.push(fn); },
    },
    setTimeout: (fn, ms) => { const id = nextTimer++; timers.set(id, fn); return id; },
    clearTimeout: (id) => { timers.delete(id); },
  };
  context.persistInflightState = () => {
    context.saveInflightState('sess-1', {
      streamId: inflight.streamId,
      messages: inflight.messages,
      toolCalls: inflight.toolCalls,
    });
  };
  vm.createContext(context);
  vm.runInContext(storage + '\n' + registry + '\n' + throttle +
    '\nthis._pendingFlushCount = () => _INFLIGHT_PERSIST_FLUSHERS.size;', context);

  return {
    inflight,
    pagehide,
    setItemCount: () => setItemCalls,
    toolEvent: (name) => {
      inflight.toolCalls.push({ tid: name, name, done: true });
      context._throttledPersist();
    },
    runTimers: () => { const fns = Array.from(timers.values()); timers.clear(); fns.forEach((f) => f()); },
    flush: () => context._flushPersist(),
    firePagehide: () => pagehide.forEach((f) => f({ persisted: false })),
    load: () => context.loadInflightState('sess-1', 'stream-1'),
    pending: () => context._pendingFlushCount(),
  };
}

// 1. Forty tool events inside one throttle window cost one write, not forty.
{
  const h = harness();
  for (let i = 0; i < 40; i++) h.toolEvent('tool-' + i);
  assert(h.setItemCount() === 0, 'no write should happen before the throttle window elapses');
  h.runTimers();
  assert(h.setItemCount() === 1, '40 tool events must collapse to one setItem, got ' + h.setItemCount());
  assert(h.load().toolCalls.length === 40, 'the single write must carry every tool call');
}

// 2a. Turn end flushes the pending write immediately.
{
  const h = harness();
  h.toolEvent('read');
  h.toolEvent('write');
  assert(h.setItemCount() === 0, 'still inside the throttle window');
  h.flush();
  assert(h.setItemCount() === 1, 'turn end must write the pending snapshot');
  assert(h.pending() === 0, 'flushing must deregister the stream from the pagehide set');
  h.runTimers();
  assert(h.setItemCount() === 1, 'the cancelled timer must not write a second time');
}

// 2b. pagehide flushes the pending write.
{
  const h = harness();
  h.toolEvent('bash');
  h.firePagehide();
  assert(h.setItemCount() === 1, 'pagehide must write the pending snapshot');
  assert(h.load().toolCalls[0].tid === 'bash', 'pagehide write must contain the tool call');
  h.firePagehide();
  assert(h.setItemCount() === 1, 'a second pagehide with nothing pending must not write');
}

// 3. Reload recovery still restores tool state from a turn interrupted mid-flight.
{
  const h = harness();
  h.toolEvent('grep');
  h.runTimers();
  h.inflight.messages.push({ role: 'assistant', content: 'partial' });
  h.toolEvent('edit');
  h.firePagehide();  // tab closed mid-turn
  const restored = h.load();
  assert(restored, 'reload must find the in-flight snapshot');
  assert(restored.streamId === 'stream-1', 'reload must match the interrupted stream');
  assert(restored.toolCalls.map((t) => t.tid).join(',') === 'grep,edit', 'reload must restore both tool calls');
  assert(restored.messages.length === 1, 'reload must restore the partial assistant message');
}
"""


@pytest.mark.skipif(shutil.which("node") is None, reason="node is required for the persist throttle behavior test")
def test_inflight_persist_is_throttled_flushed_and_recoverable(tmp_path):
    script_path = tmp_path / "inflight_persist_throttle_test.js"
    script_path.write_text(NODE_SCRIPT, encoding="utf-8")
    result = subprocess.run(
        [
            "node",
            str(script_path),
            str(REPO_ROOT / "static" / "ui.js"),
            str(REPO_ROOT / "static" / "messages.js"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
