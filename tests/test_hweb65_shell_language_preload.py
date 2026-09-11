"""HWEB-65: the shell preloads the *server* locale, not a stale localStorage one.

HWEB-37 emits the persisted locale's bundle with the shell so first paint is
localized. That preload read localStorage only, while boot.js gives the server
``language`` setting precedence once settings load — so when the two stores
diverged (new browser profile, another tab changed the setting, localStorage
cleared) the page fetched and painted the stale locale, then a third bundle.

The server now emits its resolved language in ``__HERMES_CONFIG__``; the
preload and ``loadLocale()`` read it first, mirroring boot.js's precedence,
without depending on localStorage being readable or writable.
"""
import json
import re
import urllib.request
from pathlib import Path

import pytest

from api import i18n_assets
from tests._pytest_port import BASE
from tests.test_login_locale import post


ROOT = Path(__file__).resolve().parents[1]
I18N = ROOT / "static" / "i18n.js"
_BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]
_CONFIG_LANG = re.compile(r"window\.__HERMES_CONFIG__=\{[^}]*language:(\"[^\"]*\")")


def _shell():
    with urllib.request.urlopen(BASE + "/", timeout=20) as r:
        return r.read().decode("utf-8")


@pytest.fixture
def server_language():
    """Set the server `language` for one test; hand it back afterwards."""
    def _set(lang):
        _, status = post("/api/settings", {"language": lang})
        assert status == 200, status
    yield _set
    post("/api/settings", {"language": "en"})


# ── 1. The shell carries the server language, resolved like the client ───────

@pytest.mark.parametrize(
    "stored,emitted",
    [
        ("de", "de"),
        ("DE-at", "de"),       # case + region resolve like resolveLocale()
        ("zh-CN", "zh"),       # alias
        ("en", "en"),          # English is a real answer, not "nothing"
        ("xx-YY", ""),         # unresolvable -> fall back client-side
    ],
)
def test_shell_emits_the_resolved_server_language(server_language, stored, emitted):
    server_language(stored)
    m = _CONFIG_LANG.search(_shell())
    assert m, "index.html must carry `language:` in __HERMES_CONFIG__"
    assert json.loads(m.group(1)) == emitted


def test_shell_language_reflects_a_settings_change_without_restart(server_language):
    server_language("de")
    assert json.loads(_CONFIG_LANG.search(_shell()).group(1)) == "de"
    server_language("it")
    assert json.loads(_CONFIG_LANG.search(_shell()).group(1)) == "it"


def test_locale_codes_are_read_once_per_file_generation(tmp_path, monkeypatch):
    """The shell route resolves the language per navigation; that must not
    re-read the 1.5 MB source each time."""
    src = tmp_path / "i18n.js"
    src.write_text(I18N.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(i18n_assets, "_CODES", ())
    reads = []
    real = Path.read_text

    def counting(self, *a, **kw):
        if self == src:
            reads.append(self)
        return real(self, *a, **kw)

    monkeypatch.setattr(Path, "read_text", counting)
    first = i18n_assets.resolve_code(src, "de")
    for _ in range(5):
        assert i18n_assets.resolve_code(src, "de") == first == "de"
    assert len(reads) == 1

    # A redeploy (new content) is picked up on the next call.
    text = real(src, encoding="utf-8")
    src.write_text(text.replace("  de: {", "  dx: {", 1), encoding="utf-8")
    assert i18n_assets.resolve_code(src, "dx") == "dx"
    assert len(reads) == 2


# ── 2. Browser: divergent stores, first paint, unresolvable server value ─────

_RESOURCES = """() => performance.getEntriesByType('resource')
    .map(e => e.name).filter(n => /\\/static\\/i18n\\.js\\b/.test(n))"""


def _load_with_local_storage(browser, lang, init_script=None):
    """Load `/` with `hermes-lang` already set, the way a stale profile has it."""
    ctx = browser.new_context(viewport={"width": 1280, "height": 800})
    ctx.add_init_script(
        init_script or f"localStorage.setItem('hermes-lang', {json.dumps(lang)})"
    )
    page = ctx.new_page()
    # Deferred scripts — the core and the preloaded bundle — have run by
    # DOMContentLoaded; the settings fetch that lets boot.js re-apply the
    # server preference has not resolved yet. This is first paint.
    page.goto(BASE + "/", wait_until="domcontentloaded")
    return page


def _settle(page):
    # boot.js publishes window._sendKey as soon as /api/settings resolves.
    page.wait_for_function("() => typeof window._sendKey === 'string'", timeout=15000)
    # boot.js's setLocale() after settings is async when it must fetch; give a
    # would-be third bundle time to show up in the resource log.
    page.wait_for_timeout(500)


def test_server_locale_wins_over_a_stale_local_storage_locale(server_language):
    pw = pytest.importorskip("playwright.sync_api")
    server_language("de")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = _load_with_local_storage(browser, "it")
            # First paint is the server's German, not the profile's Italian.
            assert page.evaluate("() => document.documentElement.lang") == "de-DE"
            assert page.evaluate("() => t('copy')") == "Kopieren"

            _settle(page)
            bundles = page.evaluate(_RESOURCES)
            assert len(bundles) <= 2, f"expected at most 2 locale bundles, got {bundles}"
            assert any("lang=de" in b for b in bundles), bundles
            assert not any("lang=it" in b for b in bundles), (
                f"the stale localStorage locale must not be fetched: {bundles}"
            )
            # Settings resolved to the same locale: nothing was re-applied.
            assert page.evaluate("() => document.documentElement.lang") == "de-DE"
            assert page.evaluate("() => t('copy')") == "Kopieren"
            assert page.evaluate("() => localStorage.getItem('hermes-lang')") == "de"
        finally:
            browser.close()


def test_unresolvable_server_language_falls_back_to_local_storage(server_language):
    pw = pytest.importorskip("playwright.sync_api")
    server_language("xx-YY")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = _load_with_local_storage(browser, "it")
            assert page.evaluate("() => document.documentElement.lang") == "it-IT"
            _settle(page)
            bundles = page.evaluate(_RESOURCES)
            assert len(bundles) <= 2, bundles
            assert any("lang=it" in b for b in bundles), bundles
            assert not any("lang=xx" in b for b in bundles), (
                f"an unresolvable server language must not produce a request: {bundles}"
            )
            assert page.evaluate("() => t('copy')") == "Copia"

            # ...and with nothing in localStorage either, English, one bundle.
            page2 = browser.new_page()
            page2.goto(BASE + "/", wait_until="domcontentloaded")
            assert page2.evaluate("() => document.documentElement.lang") == "en-US"
            _settle(page2)
            bundles = page2.evaluate(_RESOURCES)
            assert len(bundles) == 1, bundles
            assert page2.evaluate("() => t('copy')") == "Copy"
        finally:
            browser.close()


def test_server_locale_paints_when_storage_is_unavailable(server_language):
    """The authoritative locale must not depend on localStorage being writable
    or even readable (storage disabled, quota exceeded, third-party context)."""
    pw = pytest.importorskip("playwright.sync_api")
    server_language("de")
    blocked = """Object.defineProperty(window, 'localStorage', {
        get() { throw new DOMException('blocked', 'SecurityError'); }
    });"""
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = _load_with_local_storage(browser, None, init_script=blocked)
            assert page.evaluate("() => document.documentElement.lang") == "de-DE"
            assert page.evaluate("() => t('copy')") == "Kopieren"
            # Give the settings round-trip and any would-be extra fetch time to land.
            page.wait_for_timeout(1500)
            bundles = page.evaluate(_RESOURCES)
            assert len(bundles) <= 2, bundles
            assert any("lang=de" in b for b in bundles), bundles
            assert page.evaluate("() => t('copy')") == "Kopieren"
            assert page.evaluate("() => !LOCALES.de._stub") is True
        finally:
            browser.close()
