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

# ── 2. i18n.js is served split: English core + one on-demand locale ──────────

def _locale_codes():
    from api.i18n_assets import locale_codes

    return locale_codes(I18N)


# ── 3. Browser behaviour: repeat visit, bundle count, late locale switch ─────

def _new_page(browser):
    page = browser.new_page(viewport={"width": 1280, "height": 800})
    page.goto(BASE + "/", wait_until="domcontentloaded")
    page.wait_for_function(
        "() => typeof setLocale === 'function' && typeof applyLocaleToDOM === 'function'"
        " && typeof api === 'function' && S && S._bootReady === true",
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
