"""HWEB-86: one `api.updates` module per session, and `/sw.js` serves its version.

`tests/test_static_asset_resolver.py::test_service_worker_and_favicon_follow_selected_static_root`
failed intermittently in CI on a one-character version mismatch
(`13e27baa` served vs `13e27ba` expected). The cause was two live `api.updates`
module objects: `tests/test_issue1579_whats_new_link_404.py` dropped the module
from `sys.modules` and re-imported it, so a module that had bound
`WEBUI_VERSION` at import time held a value from the first instance while
product code read the second one's. `WEBUI_VERSION` is computed once per module
execution from `git describe --tags --always`, and on CI's tagless checkout that
falls back to a bare abbreviated SHA whose length git picks from prefix
ambiguity — so the two instances could disagree by one hex digit.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import api.routes as routes
import api.updates

ROOT = Path(__file__).resolve().parent.parent


def test_evicting_api_updates_restores_the_original_module():
    """An unrestored eviction splits the module the next time anything imports it.

    `from api import updates` reads the attribute off the `api` package and does
    NOT re-execute, so the evicting test passes and the missing `sys.modules`
    entry goes unnoticed. The split happens later: product code doing a dotted
    `from api.updates import WEBUI_VERSION` finds nothing in `sys.modules` and
    re-executes the module, recomputing the version from a fresh `git describe`.
    """
    original = sys.modules["api.updates"]
    with patch.dict(sys.modules):
        sys.modules.pop("api.updates", None)
        from api import updates as attribute_import

        assert attribute_import is original, (
            "`from api import updates` reads the package attribute; it cannot "
            "detect the eviction, which is why this leak stayed invisible"
        )
    assert sys.modules["api.updates"] is original

    # The route's form, run after a properly restored eviction, must reuse the
    # one live module rather than re-executing it.
    from api.updates import WEBUI_VERSION as after_eviction

    assert sys.modules["api.updates"] is original, (
        "a dotted import re-executed api.updates: WEBUI_VERSION would be "
        "recomputed and could disagree with every value bound before now"
    )
    assert after_eviction == original.WEBUI_VERSION


def test_no_test_evicts_api_updates_without_restoring_it():
    """`del sys.modules['api.updates']` outside a restoring context is the bug."""
    offenders = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        if "sys.modules" not in source:
            continue
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("del sys.modules[") and "api.updates" in stripped:
                offenders.append(f"{path.name}: {stripped}")
    assert not offenders, (
        "evict api.updates through `with patch.dict(sys.modules):` so the "
        "original module is restored; leaking a second instance splits "
        "WEBUI_VERSION across the session (HWEB-86):\n" + "\n".join(offenders)
    )


def test_sw_js_serves_the_version_the_app_currently_reports(tmp_path, monkeypatch):
    """/sw.js must stamp the live WEBUI_VERSION, not one captured earlier."""
    import api.config as api_config

    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "sw.js").write_text(
        "const version = '__WEBUI_VERSION__';\n", encoding="utf-8"
    )
    monkeypatch.setattr(api_config, "get_static_root", lambda: static_root)
    monkeypatch.setattr(api.updates, "WEBUI_VERSION", "vTEST-hweb86")

    handler = _get("/sw.js")
    assert handler.status == 200
    assert bytes(handler.body) == b"const version = 'vTEST-hweb86';\n"


def test_tagless_checkout_still_yields_a_version():
    """The CI-only code path: no tags, so `git describe` returns a bare SHA.

    Locally the repo has tags and resolves to a descriptor, so this branch is
    never otherwise exercised — which is why the split only ever showed up in CI.
    """
    out = subprocess.run(
        ["git", "describe", "--tags", "--always"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    assert out.stdout.strip(), "git describe --always must always produce a value"


# Reuse the request harness from the sibling resolver test rather than rebuilding it.
from tests.test_static_asset_resolver import _get  # noqa: E402
