"""Behavioral coverage for EventSource reconnect ownership.

A status probe from an old attach generation can resolve after the user leaves
and returns to the same session. It must not replace (or close) the newer
generation's EventSource.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MESSAGES = (ROOT / "static" / "messages.js").read_text(encoding="utf-8")


def _ownership_helpers() -> str:
    start = MESSAGES.index("const LIVE_STREAMS={};")
    end = MESSAGES.index("const _STREAM_NOTIFICATION_BACKGROUND={};", start)
    return MESSAGES[start:end]


def test_old_probe_cannot_replace_or_close_newer_leave_return_source():
    node = shutil.which("node")
    if not node:  # pragma: no cover
        pytest.skip("node not available")
    harness = textwrap.dedent(
        """
        %(helpers)s
        function source(name){
          return {name, readyState:1, closeCount:0, close(){this.closeCount+=1;this.readyState=2;}};
        }
        const oldSource=source('old');
        const oldGeneration=_claimLiveStreamOwner('session-a','stream-1');
        const oldInstalled=_installOwnedLiveStreamSource(
          'session-a','stream-1',oldGeneration,oldSource,null
        );

        // Leave A, then return. The return owns a new lifecycle generation and
        // has already installed a healthy source when the old status await ends.
        _releaseLiveStreamOwner('session-a','stream-1',oldGeneration);
        delete LIVE_STREAMS['session-a'];
        const newGeneration=_claimLiveStreamOwner('session-a','stream-1');
        const newSource=source('new');
        const newInstalled=_installOwnedLiveStreamSource(
          'session-a','stream-1',newGeneration,newSource,null
        );

        const staleCandidate=source('stale-candidate');
        const staleInstalled=_installOwnedLiveStreamSource(
          'session-a','stream-1',oldGeneration,staleCandidate,oldSource
        );
        console.log(JSON.stringify({
          oldInstalled,newInstalled,staleInstalled,
          current:LIVE_STREAMS['session-a'].source.name,
          newCloseCount:newSource.closeCount,
          staleCloseCount:staleCandidate.closeCount,
        }));
        """
    ) % {"helpers": _ownership_helpers()}
    proc = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip())
    assert out == {
        "oldInstalled": True,
        "newInstalled": True,
        "staleInstalled": False,
        "current": "new",
        "newCloseCount": 0,
        "staleCloseCount": 1,
    }


def test_same_generation_probe_uses_expected_source_compare_and_swap():
    """Two recovery paths in one attach generation can race too. Once one path
    installs a replacement, the other path's old expected source is stale."""
    node = shutil.which("node")
    if not node:  # pragma: no cover
        pytest.skip("node not available")
    harness = textwrap.dedent(
        """
        %(helpers)s
        function source(name){
          return {name, readyState:1, closeCount:0, close(){this.closeCount+=1;this.readyState=2;}};
        }
        const generation=_claimLiveStreamOwner('session-a','stream-1');
        const original=source('original');
        _installOwnedLiveStreamSource('session-a','stream-1',generation,original,null);
        const winner=source('winner');
        const winnerInstalled=_installOwnedLiveStreamSource(
          'session-a','stream-1',generation,winner,original
        );
        const loser=source('loser');
        const loserInstalled=_installOwnedLiveStreamSource(
          'session-a','stream-1',generation,loser,original
        );
        console.log(JSON.stringify({
          winnerInstalled,loserInstalled,current:LIVE_STREAMS['session-a'].source.name,
          winnerCloseCount:winner.closeCount,loserCloseCount:loser.closeCount,
        }));
        """
    ) % {"helpers": _ownership_helpers()}
    proc = subprocess.run([node, "-e", harness], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout.strip())
    assert out == {
        "winnerInstalled": True,
        "loserInstalled": False,
        "current": "winner",
        "winnerCloseCount": 0,
        "loserCloseCount": 1,
    }
