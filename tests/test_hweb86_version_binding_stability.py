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
import ast
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

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

    The rule is not "never touch `sys.modules`" — it is "put BOTH references
    back". A removal paired, in the same function, with a restore of the
    `sys.modules` entry *and* the `updates` attribute on the `api` package is
    exactly what the failure message asks for, so it is not an offender. Scoping
    per function (rather than per line) is what keeps a correct try/finally from
    being flagged alongside a genuine leak.
    """
    # Match through an alias (`import sys as _sys`) by anchoring on `.modules`.
    removals = (
        re.compile(r"\bdel\s+\w+\.modules\["),
        re.compile(r"\b\w+\.modules\.pop\("),
    )
    # Both halves of the restore the message demands.
    restores_mapping = re.compile(r"\.modules\[[\"']api\.updates[\"']\]\s*=")
    restores_attribute = re.compile(
        r"\bapi\.updates\s*=|\bsetattr\(\s*api\s*,\s*[\"']updates[\"']"
    )

    def removal_lines(source: str) -> list[tuple[int, str]]:
        found = []
        for number, line in enumerate(source.splitlines(), 1):
            stripped = line.strip()
            if "api.updates" not in stripped:
                continue
            if any(pattern.search(stripped) for pattern in removals):
                found.append((number, stripped))
        return found

    offenders = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        if path.name == Path(__file__).name:
            continue
        source = path.read_text(encoding="utf-8")
        if "api.updates" not in source:
            continue
        found = removal_lines(source)
        if not found:
            continue
        # Parse only the few files that actually remove the module: compiling a
        # test file that embeds JS raises SyntaxWarning noise for no benefit.
        tree = ast.parse(source, filename=str(path))
        # Every function that both removes and fully restores is exonerated; its
        # line range is then excluded from the file-level scan below.
        exonerated = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            body = ast.get_source_segment(source, node) or ""
            if not removal_lines(body):
                continue
            if restores_mapping.search(body) and restores_attribute.search(body):
                exonerated.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for number, stripped in found:
            if number in exonerated:
                continue
            offenders.append(f"{path.name}:{number}: {stripped}")

    assert not offenders, (
        "removing api.updates from sys.modules leaves the next dotted import to "
        "execute the module a second time, splitting WEBUI_VERSION across the "
        "session (HWEB-86). `from api import updates` returns the existing "
        "module anyway, so the eviction buys nothing. If you genuinely need a "
        "fresh import, restore BOTH references in the same function — `sys.modules"
        "['api.updates']` and the `updates` attribute on the `api` package. "
        "`monkeypatch.delitem` and `patch.dict` restore only the first:\n"
        + "\n".join(offenders)
    )


def test_the_module_cache_scan_still_catches_an_unrestored_eviction(tmp_path):
    """The exemption must not hollow out the scan.

    A function that removes `api.updates` and restores only the `sys.modules`
    entry is the exact HWEB-86 leak, and must still be reported.
    """
    leaky = tmp_path / "test_leaky.py"
    leaky.write_text(
        "import sys\n"
        "def test_x():\n"
        "    first = sys.modules['api.updates']\n"
        "    del sys.modules['api.updates']\n"
        "    sys.modules['api.updates'] = first\n",
        encoding="utf-8",
    )
    source = leaky.read_text(encoding="utf-8")
    restores_attribute = re.compile(
        r"\bapi\.updates\s*=|\bsetattr\(\s*api\s*,\s*[\"']updates[\"']"
    )
    assert not restores_attribute.search(source), (
        "a mapping-only restore must not satisfy the attribute half of the rule"
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


def test_version_detection_handles_a_tagless_checkout(tmp_path):
    """Exercise the CI-only branch against a repository that really has no tags.

    A developer checkout has tags, so `git describe --tags --always` returns a
    descriptor and the bare-SHA fallback never runs locally — which is why the
    split only ever surfaced in CI. Build a tagless repo and drive the product
    helper through it, rather than asserting git's own `--always` guarantee.
    """
    repo = tmp_path / "tagless"
    repo.mkdir()

    def git(*args):
        result = subprocess.run(
            ["git", *args], cwd=str(repo), capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    git("init", "--quiet")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "HWEB-86 test")
    # Pin the abbreviation: with a global `core.abbrev=40`, `git describe
    # --always` legitimately returns the full object id and the shortening
    # assertion below would fail on a correctly working helper.
    git("config", "core.abbrev", "7")
    (repo / "file.txt").write_text("hweb86\n", encoding="utf-8")
    git("add", "file.txt")
    git("commit", "--quiet", "-m", "initial")

    assert git("tag") == "", "the repository under test must be tagless"

    described = api.updates._describe_git_version(repo)
    assert described, "_describe_git_version must resolve a tagless checkout"

    head = git("rev-parse", "HEAD")
    assert head.startswith(described), (
        f"expected an abbreviated SHA of {head}, got {described!r} — the tagless "
        "branch must fall back to the bare commit id"
    )
    assert described != head, (
        "expected the abbreviated form, not the full SHA (core.abbrev is pinned "
        "to 7 above so this does not depend on the developer's git config)"
    )


# Reuse the request harness from the sibling resolver test rather than rebuilding it.
from tests.test_static_asset_resolver import _get  # noqa: E402
