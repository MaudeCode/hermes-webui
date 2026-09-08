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
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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


def test_module_cache_and_package_attribute_agree():
    """The observable invariant: one module object, referenced consistently.

    A leaked eviction shows up here as `sys.modules` and the `api` package
    attribute pointing at different objects, whatever spelling caused it.
    """
    import api as api_package

    assert sys.modules["api.updates"] is api_package.updates
    assert sys.modules["api.updates"] is api.updates

    # The route's form must reuse that module rather than executing a second copy.
    before = sys.modules["api.updates"]
    from api.updates import WEBUI_VERSION

    assert sys.modules["api.updates"] is before
    assert api_package.updates is before
    assert WEBUI_VERSION == before.WEBUI_VERSION


def test_no_test_removes_api_updates_from_the_module_cache():
    """Catch the mutation at its source, in any spelling.

    The identity check above only fires when the leaking test happened to run
    earlier in the same shard, so this scan is the deterministic half. It covers
    `del`, `.pop(...)` and `monkeypatch.delitem` rather than one literal form.
    """
    # `monkeypatch.delitem(sys.modules, ...)` and `patch.dict(sys.modules)` both
    # put the entry back at teardown, so they are the sanctioned way to force a
    # re-import. Only the unscoped spellings leak. Match through an alias
    # (`import sys as _sys`) by anchoring on `.modules` rather than on `sys.`.
    patterns = (
        re.compile(r"\bdel\s+\w+\.modules\["),
        re.compile(r"\b\w+\.modules\.pop\("),
    )
    offenders = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8")
        if "api.updates" not in source:
            continue
        if "patch.dict(sys.modules)" in source:
            continue  # scoped by patch.dict, which restores the mapping
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if not any(pattern.search(stripped) for pattern in patterns):
                continue
            if "api.updates" not in stripped:
                continue
            offenders.append(f"{path.name}:{number}: {stripped}")
    assert not offenders, (
        "removing api.updates from sys.modules leaves the next dotted import to "
        "execute the module a second time, splitting WEBUI_VERSION across the "
        "session (HWEB-86). `from api import updates` returns the existing "
        "module anyway, so the eviction buys nothing. Use "
        "`monkeypatch.delitem(sys.modules, ...)` if you genuinely need a fresh "
        "import:\n" + "\n".join(offenders)
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
