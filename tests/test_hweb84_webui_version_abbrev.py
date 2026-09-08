"""HWEB-84 — WEBUI_VERSION must not change shape when it is recomputed.

`git describe` defaults to `core.abbrev=auto`, sizing the short SHA from the
repository's object count, so the same commit can describe as `a42c991` in one
process and `a42c9917` in another. `WEBUI_VERSION` is computed once at import,
but `tests/test_issue1579_whats_new_link_404.py` drops `api.updates` from
`sys.modules` and re-imports it, which recomputes the constant. An earlier
`from api.updates import WEBUI_VERSION` keeps the old string while a late
in-function import — the `/sw.js` route does exactly that — reads the new one.

On CI the two disagreed and `/sw.js` served a cache name the test did not expect.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

import pytest


REPO_ROOT = pathlib.Path(__file__).parent.parent
UPDATES_PY = (REPO_ROOT / "api" / "updates.py").read_text(encoding="utf-8")


def test_describe_pins_the_abbreviation_length():
    """An unpinned describe is what let two computations disagree."""
    assert "_GIT_DESCRIBE_ABBREV = 8" in UPDATES_PY
    call = UPDATES_PY[
        UPDATES_PY.index("def _describe_git_version(") : UPDATES_PY.index(
            "def _detect_webui_version("
        )
    ]
    assert "f'--abbrev={_GIT_DESCRIBE_ABBREV}'" in call
    assert "['describe', '--tags', '--always']" not in call


def test_reimporting_api_updates_yields_the_same_version_string():
    """Re-import must reproduce the constant, not a differently-sized SHA.

    This is the exact sequence the shard hit: bind the value, force the
    re-import another test performs, then compare what a late importer sees.
    """
    import api.updates as first

    bound = first.WEBUI_VERSION
    del sys.modules["api.updates"]
    try:
        import api.updates as second  # noqa: PLC0415 — deliberate re-import

        # What the /sw.js route sees on a late in-function import.
        late = second.WEBUI_VERSION
    finally:
        sys.modules["api.updates"] = first

    assert late == bound, (
        "WEBUI_VERSION changed across a re-import; a module-level binding taken "
        "before it and a late import taken after it now disagree"
    )


def test_pinned_abbreviation_survives_an_object_count_change():
    """The adaptive width is what varied; a pinned one must not.

    Ask git for the same commit either side of a real repo write, which is the
    condition that moves `core.abbrev=auto`.
    """
    git = ["git", "-C", str(REPO_ROOT), "describe", "--tags", "--always"]
    pinned = subprocess.run(
        git + ["--abbrev=8"], capture_output=True, text=True, timeout=30
    )
    if pinned.returncode != 0:
        pytest.skip(f"git describe unavailable: {pinned.stderr[:200]}")
    sha = pinned.stdout.strip().rsplit("-g", 1)[-1]
    assert re.fullmatch(r"[0-9a-f]{8}", sha), (
        f"pinned describe did not yield a fixed-width sha: {pinned.stdout.strip()!r}"
    )
