"""HWEB-37: the JS shell is browser-cacheable and only one locale is shipped.

Two independent regressions made every page load re-transfer ~4.4 MB of
JavaScript:

1. `static/sw.js` forced `cache: 'no-store'` on every shell asset, so the
   fingerprinted `?v=` URLs and their `immutable` Cache-Control bought nothing.
2. `static/i18n.js` inlined all 15 locale bundles; ~93% of the largest asset in
   the shell was dead weight for any given client.

These tests fail against either regression: they assert the service worker no
longer opts out of the HTTP cache, that a repeat visit fetches nothing over the
network, that a page load carries at most two locale bundles, and that
switching to a locale absent at first paint still works.
"""
import json
import re
import subprocess
import urllib.request
from pathlib import Path

import pytest

from tests._pytest_port import BASE


ROOT = Path(__file__).resolve().parents[1]
SW = ROOT / "static" / "sw.js"
I18N = ROOT / "static" / "i18n.js"
INDEX = ROOT / "static" / "index.html"

_BROWSER_ARGS = ["--no-sandbox", "--disable-dev-shm-usage"]


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return r.headers, r.read().decode("utf-8")


# ── 1. Service worker no longer bypasses the browser HTTP cache ──────────────

def test_sw_does_not_force_no_store_on_shell_assets():
    src = SW.read_text(encoding="utf-8")
    assert "cache: 'no-store'" not in src, (
        "sw.js must not wrap requests in `new Request(req, {cache:'no-store'})`; "
        "that bypasses the HTTP cache and re-transfers the whole versioned shell "
        "on every load"
    )
    assert "fetch(event.request)" in src, "shell/navigation fetches must pass the request through"


def test_versioned_shell_assets_are_served_immutable():
    """The `?v=` fingerprint is what makes dropping no-store safe."""
    headers, _ = _get("/static/boot.js?v=test")
    assert "immutable" in headers.get("Cache-Control", ""), (
        "fingerprinted assets must be immutable so a plain fetch() is a cache hit"
    )
    headers, _ = _get("/static/boot.js")
    assert "immutable" not in headers.get("Cache-Control", ""), (
        "unversioned assets must stay revalidatable"
    )
    # `unknown` is _detect_webui_version()'s last-resort fallback (a copied
    # source deployment with no git and no generated version file). It repeats
    # across upgrades, so it is not a fingerprint: caching it immutable would
    # strand clients on stale JS with no way to bust it.
    headers, _ = _get("/static/boot.js?v=unknown")
    assert "immutable" not in headers.get("Cache-Control", ""), (
        "?v=unknown is not a fingerprint and must not be cached immutable"
    )


# ── 2. i18n.js is served split: English core + one on-demand locale ──────────

def _locale_codes():
    from api.i18n_assets import locale_codes

    return locale_codes(I18N)


def test_i18n_core_ships_english_plus_stubs_only():
    codes = _locale_codes()
    _, core = _get("/static/i18n.js?v=test")
    assert len(core) < len(I18N.read_text(encoding="utf-8")) / 4, (
        "the served i18n core must be a fraction of the authored file"
    )
    # English is complete...
    assert "    copy: 'Copy'," in core
    # ...every other locale is present as a metadata stub, so resolveLocale()
    # and the settings language picker keep seeing the full locale list...
    for code in codes[1:]:
        key = f"'{code}'" if "-" in code else code
        assert f"  {key}: {{" in core, f"core must declare a stub for {code}"
    assert core.count("\n    _stub: true,\n  },\n") == len(codes) - 1
    # ...but none of their strings are on the wire.
    assert "Kopieren" not in core, "German strings must not ship in the English core"


def test_i18n_locale_bundle_carries_one_locale():
    _, bundle = _get("/static/i18n.js?v=test&lang=de")
    assert "Kopieren" in bundle
    assert "Object.assign(window.LOCALES" in bundle
    assert "'Copia'" not in bundle, "a lang= bundle must carry exactly one locale"
    assert "function t(" not in bundle, "helpers belong to the core, not the bundle"


@pytest.mark.parametrize(
    "requested,expected_marker",
    [
        ("zh-CN", "Object.assign(window.LOCALES"),   # alias resolves to zh
        ("DE-at", "Kopieren"),                       # case + region fall back to de
        ("xx-YY", "unknown locale"),                 # unresolvable -> inert no-op
        ("en", "unknown locale"),                    # already in the core
    ],
)
def test_i18n_lang_param_resolution_mirrors_the_client(requested, expected_marker):
    _, body = _get(f"/static/i18n.js?v=test&lang={requested}")
    assert expected_marker in body


def test_i18n_core_and_bundle_do_not_share_an_etag():
    core_headers, _ = _get("/static/i18n.js?v=test")
    de_headers, _ = _get("/static/i18n.js?v=test&lang=de")
    assert core_headers["ETag"] != de_headers["ETag"], (
        "core and locale bundles are served from the same path; a shared ETag "
        "would let a conditional GET return the wrong variant"
    )


def test_split_is_faithful_to_the_authored_file():
    """core + bundle must reconstruct each locale exactly, key for key.

    Slicing is textual, so this is the guard that a future edit to i18n.js's
    block layout cannot silently drop or corrupt translations.
    """
    node = subprocess.run(["node", "--version"], capture_output=True)
    if node.returncode != 0:
        pytest.skip("node is not available")

    from api.i18n_assets import _render

    src = I18N.read_text(encoding="utf-8")
    codes = _locale_codes()
    harness = r"""
const fs = require('fs'), vm = require('vm');
function ctx() {
  const store = {};
  const el = {lang:'', setAttribute(){}, getAttribute(){return null;},
              hasAttribute(){return false;}, removeAttribute(){}};
  const doc = {documentElement: el, head: {appendChild(){}},
               querySelectorAll: () => [], createElement: () => ({})};
  const win = {document: doc, localStorage: {
    getItem: k => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); }}};
  win.window = win;
  const c = vm.createContext(win);
  c.document = doc; c.localStorage = win.localStorage;
  c.Promise = Promise; c.encodeURIComponent = encodeURIComponent;
  return c;
}
const run = (c, f) => vm.runInContext(fs.readFileSync(f, 'utf8'), c, {filename: f});
const dump = o => JSON.stringify(o, (k, v) => typeof v === 'function' ? '[fn]' : v);
const [srcFile, coreFile, ...bundles] = process.argv.slice(2);
const a = ctx(); run(a, srcFile);
const out = {codes: Object.keys(a.LOCALES), locales: {}};
for (const b of bundles) {
  const code = b.match(/bundle-(.+)\.js$/)[1];
  const c = ctx(); run(c, coreFile); run(c, b);
  out.codes_after = Object.keys(c.LOCALES);
  const {_stub, ...merged} = c.LOCALES[code];
  const sibling = Object.keys(c.LOCALES).find(k => k !== 'en' && k !== code);
  out.locales[code] = [dump(a.LOCALES[code]) === dump(merged), !!_stub,
                       !!c.LOCALES[sibling]._stub];
}
console.log(JSON.stringify(out));
"""
    tmp = Path(__file__).parent / "_hweb37_tmp"
    tmp.mkdir(exist_ok=True)
    try:
        core = tmp / "core.js"
        core.write_text(_render(src, ""), encoding="utf-8")
        (tmp / "harness.js").write_text(harness, encoding="utf-8")
        args = [str(I18N), str(core)]
        for code in codes[1:]:
            f = tmp / f"bundle-{code}.js"
            f.write_text(_render(src, code), encoding="utf-8")
            args.append(str(f))
        proc = subprocess.run(
            ["node", str(tmp / "harness.js"), *args],
            capture_output=True, text=True, cwd=ROOT,
        )
        assert proc.returncode == 0, proc.stderr
        out = json.loads(proc.stdout)
    finally:
        for f in tmp.glob("*"):
            f.unlink()
        tmp.rmdir()

    assert out["codes"] == out["codes_after"], "the core must declare every locale code"
    for code in codes[1:]:
        faithful, still_stub, sibling_is_stub = out["locales"][code]
        assert faithful, f"{code} did not round-trip through core + bundle"
        assert not still_stub, f"{code}'s bundle must replace its stub"
        assert sibling_is_stub, f"loading {code} must not pull in other locales"


# ── 3. Browser behaviour: repeat visit, bundle count, late locale switch ─────

def _new_page(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => typeof setLocale === 'function' && typeof applyLocaleToDOM === 'function'"
        " && typeof api === 'function'",
        timeout=15000,
    )
    return page


_RESOURCES = """() => performance.getEntriesByType('resource')
    .filter(e => e.initiatorType === 'script' || e.initiatorType === 'link')
    .map(e => ({name: e.name, transferSize: e.transferSize}))"""

# Persist a language the way the settings panel does — server setting *and*
# localStorage — via the page's own api() helper so CSRF is handled for us.
_SET_LANGUAGE = """async (lang) => {
    await api('/api/settings', {method: 'POST', body: JSON.stringify({language: lang})});
    localStorage.setItem('hermes-lang', lang);
}"""


def test_second_load_serves_the_shell_without_network_fetches():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = _new_page(browser)  # populates the HTTP cache
            # Wait for the service worker to take control, otherwise this only
            # measures the plain browser cache and never exercises sw.js.
            page.wait_for_function(
                "() => !!navigator.serviceWorker.controller", timeout=15000
            )
            # CDP, not Resource Timing: a service-worker-mediated response
            # reports transferSize 0 either way, so only `fromDiskCache` tells
            # a cache hit apart from a re-transfer.
            cdp = page.context.new_cdp_session(page)
            cdp.send("Network.enable")
            responses = []
            cdp.on(
                "Network.responseReceived",
                lambda e: responses.append(e["response"]),
            )
            page.goto(BASE + "/", wait_until="load")
            page.wait_for_function(
                "() => typeof setLocale === 'function'", timeout=15000
            )
            assert page.evaluate("() => !!navigator.serviceWorker.controller"), (
                "the repeat visit must be served through the service worker"
            )
            through_sw = [
                r for r in responses
                if r.get("fromServiceWorker") and "/static/" in r["url"] and "v=" in r["url"]
            ]
            assert through_sw, "second load fetched no versioned shell assets through sw.js"
            re_transferred = [
                r["url"].rsplit("/", 1)[-1] for r in through_sw if not r.get("fromDiskCache")
            ]
            assert not re_transferred, (
                "versioned shell assets must come from the browser cache on a "
                f"repeat visit, but these were re-transferred: {re_transferred}"
            )
        finally:
            browser.close()


def test_page_load_transfers_at_most_two_locale_bundles():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = _new_page(browser)
            # Model a real German user: the settings panel persists the choice
            # both server-side and in localStorage, and boot.js gives the server
            # value precedence. Setting only localStorage would be overwritten
            # back to English the moment settings load.
            page.evaluate(_SET_LANGUAGE, "de")
            page.goto(BASE + "/", wait_until="load")
            page.wait_for_function(
                "() => typeof t === 'function' && t('copy') === 'Kopieren'",
                timeout=15000,
            )
            bundles = [
                r["name"] for r in page.evaluate(_RESOURCES)
                if re.search(r"/static/i18n\.js\b", r["name"])
            ]
            assert len(bundles) <= 2, f"expected at most 2 locale bundles, got {bundles}"
            assert any("lang=de" in b for b in bundles), (
                f"the persisted locale must be emitted with the shell: {bundles}"
            )
            # The count alone would pass on the old single-file bundle, so pin
            # the payload too: English and German are live, every other locale
            # arrived as a metadata stub with no strings.
            others = page.evaluate(
                """() => Object.entries(LOCALES)
                    .filter(([c]) => c !== 'en' && c !== 'de')
                    .filter(([, b]) => !b._stub)
                    .map(([c]) => c)"""
            )
            assert others == [], f"these locales were shipped but never used: {others}"
            # And the German strings are live on first paint, not after a repaint.
            assert page.evaluate("() => document.documentElement.lang") == "de-DE"
        finally:
            # The test server is session-scoped, so hand the language setting
            # back before any sibling test reads it.
            try:
                page.evaluate(_SET_LANGUAGE, "en")
            except Exception:
                pass
            browser.close()


def test_switching_to_a_locale_absent_at_first_paint():
    pw = pytest.importorskip("playwright.sync_api")
    with pw.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True, args=_BROWSER_ARGS)
        try:
            page = _new_page(browser)
            # First paint is English, so `it` arrived only as a metadata stub —
            # but the language picker must still list it by name.
            assert page.evaluate("() => LOCALES.it._label") == "Italiano"
            assert page.evaluate("() => !!LOCALES.it._stub") is True
            assert page.evaluate("() => t('copy')") == "Copy"

            page.evaluate("async () => { await setLocale('it'); applyLocaleToDOM(); }")
            assert page.evaluate("() => t('copy')") == "Copia"
            assert page.evaluate("() => !LOCALES.it._stub") is True

            # A second switch, and back again, still resolves from cache.
            page.evaluate("async () => { await setLocale('de'); }")
            assert page.evaluate("() => t('copy')") == "Kopieren"
            page.evaluate("async () => { await setLocale('it'); }")
            assert page.evaluate("() => t('copy')") == "Copia"
        finally:
            browser.close()


def test_index_emits_the_persisted_locale_before_the_ui_bundles():
    """Ordering is what keeps first paint free of a flash of English."""
    src = INDEX.read_text(encoding="utf-8")
    core = src.find('src="static/i18n.js?v=__WEBUI_VERSION__"')
    preload = src.find("__HERMES_I18N_PRELOAD__")
    boot = src.find('src="static/boot.js?v=__WEBUI_VERSION__"')
    assert -1 < core < preload < boot, (
        "the locale bundle must be emitted after i18n.js (which publishes "
        "window.LOCALES) and before the UI bundles that read translations"
    )
    assert "&amp;lang=" in src, (
        "a bare `&lang=` in document.write output parses as the &lang; HTML "
        "entity and produces a broken URL"
    )
