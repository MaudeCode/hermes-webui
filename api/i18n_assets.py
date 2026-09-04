"""Serve static/i18n.js as an English core plus one on-demand locale bundle.

`static/i18n.js` is authored as one ~1.7 MB file holding every locale. That is
the right shape for editing (one file, one key list, one place to add a
language) and the wrong shape for the wire: a client uses one language, so
~93% of the largest asset in the shell was dead weight on every load (HWEB-37).

The file stays the single source of truth. This module slices it at request
time:

``/static/i18n.js?v=...``
    Core: the ``en`` block, a *stub* for every other locale carrying only
    ``_lang``/``_label``/``_speech``/``_stub`` — enough for ``resolveLocale()``
    and the settings language picker to keep working unchanged — plus all the
    helper code that follows the ``LOCALES`` literal.

``/static/i18n.js?v=...&lang=de``
    Just the ``de`` block, merged into the ``window.LOCALES`` the core already
    published, followed by a re-resolve so the new strings take effect.

Slicing is textual and structural, never a JS parse: locale entries are the
only two-space-indented object keys inside the ``LOCALES`` literal, so each
block runs from its own header line to the next block's header line (or to the
``};`` that closes the literal).
``tests/test_hweb37_shell_cache_and_locale_split.py`` pins that convention and
checks every locale round-trips through core + bundle.
"""

import re
import threading
from pathlib import Path

# `  en: {` or `  'zh-Hant': {`.
_BLOCK_RE = re.compile(r"^  (?:'([^']+)'|([A-Za-z][A-Za-z0-9_-]*)): \{$", re.M)
# The three metadata keys every locale block carries; the only ones a stub needs.
_META_RE = re.compile(r"^    _(?:lang|label|speech):.*$", re.M)

# Rendered variants, keyed by (path, size, mtime_ns, variant). `variant` is the
# resolved locale code, "" for the core, or "?" for an unresolvable `lang=`
# value — a bounded set, so a hostile query string cannot grow this cache.
_CACHE: dict = {}
_LOCK = threading.Lock()


def _blocks(src: str):
    """Return ``(codes, {code: (start, end)}, locales_end_index)``."""
    matches = list(_BLOCK_RE.finditer(src))
    if len(matches) < 2:
        raise ValueError("static/i18n.js: could not find the LOCALES locale blocks")
    codes = [m.group(1) or m.group(2) for m in matches]
    if codes[0] != "en":
        raise ValueError("static/i18n.js: LOCALES must open with the `en` block")
    # The LOCALES literal closes at the first column-0 `};` after the last block.
    end = src.index("\n};\n", matches[-1].start()) + 1
    bounds = [
        (m.start(), matches[i + 1].start() if i + 1 < len(matches) else end)
        for i, m in enumerate(matches)
    ]
    return codes, dict(zip(codes, bounds, strict=True)), end


def _stub(block: str) -> str:
    """A locale entry with metadata only, so resolution and the picker work."""
    key = block[2 : block.index(":")]  # preserves quoting, e.g. `'zh-Hant'`
    meta = "\n".join(m.group(0) for m in _META_RE.finditer(block))
    return f"  {key}: {{\n{meta}\n    _stub: true,\n  }},\n"


def _render(src: str, variant: str) -> str:
    if variant == "?":
        return "/* i18n: unknown locale, English core already loaded */\n"
    codes, bounds, end = _blocks(src)
    if variant:
        a, b = bounds[variant]
        return (
            f"// HWEB-37: on-demand locale bundle for {variant}.\n"
            "if (window.LOCALES) {\n"
            "  Object.assign(window.LOCALES, {\n"
            f"{src[a:b]}"
            "  });\n"
            "  if (typeof loadLocale === 'function') loadLocale();\n"
            "  if (typeof applyLocaleToDOM === 'function') applyLocaleToDOM();\n"
            "}\n"
        )
    head = src[: bounds[codes[1]][0]]  # everything through the `en` block
    stubs = "".join(_stub(src[slice(*bounds[c])]) for c in codes[1:])
    return head + stubs + src[end:]


def locale_codes(path: Path) -> list:
    """Every locale code declared in the source file, `en` first."""
    return _blocks(path.read_text(encoding="utf-8"))[0]


def resolve_variant(path: Path, lang: str) -> str:
    """Map a raw ``?lang=`` value to a cache variant.

    Mirrors `resolveLocale()` in static/i18n.js. Returns "" when no `lang` was
    requested (serve the core), the resolved code when it matches a locale, and
    "?" — a no-op body — when a value was given but resolves to nothing or to
    `en`, which the core already carries in full.
    """
    if not lang:
        return ""
    codes = locale_codes(path)
    if lang in codes:
        return "?" if lang == "en" else lang
    low = lang.lower().replace("_", "-")
    by_low = {c.lower(): c for c in codes}
    if low in by_low:
        return by_low[low]
    if low == "zh" or low.startswith(("zh-cn", "zh-sg", "zh-hans")):
        resolved = by_low.get("zh")
    elif low.startswith(("zh-tw", "zh-hk", "zh-mo", "zh-hant")):
        resolved = by_low.get("zh-hant")
    else:
        resolved = by_low.get(low.split("-")[0])
    return "?" if (not resolved or resolved == "en") else resolved


def render(path: Path, variant: str, sig: tuple) -> bytes:
    """Return the rendered bytes for ``variant``, cached against ``sig``."""
    key = (str(path), sig, variant)
    with _LOCK:
        hit = _CACHE.get(key)
    if hit is not None:
        return hit
    body = _render(path.read_text(encoding="utf-8"), variant).encode("utf-8")
    with _LOCK:
        # A redeploy changes `sig`; drop entries for every older signature so
        # the cache stays proportional to one build's locale count.
        for stale in [k for k in _CACHE if k[0] == str(path) and k[1] != sig]:
            del _CACHE[stale]
        _CACHE[key] = body
    return body
