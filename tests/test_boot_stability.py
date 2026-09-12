"""Boot-stability guards for the redesign layer (static/hub.js + index.html).

The first paint must be the final layout, and the `booting` suppression class
must be released by the app's own boot flag, not a timer. Regression for the
release check reading `window.S` (S is a top-level `let`, invisible on window),
which left the context line hidden and the welcome state suppressed for 12s.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HUB = (ROOT / "static" / "hub.js").read_text(encoding="utf-8")
INDEX = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "static" / "style.css").read_text(encoding="utf-8")


def test_index_sets_boot_classes_before_stylesheet():
    assert "h.classList.add('booting')" in INDEX
    assert "h.classList.add('boot-session')" in INDEX
    assert INDEX.index("classList.add('booting')") > INDEX.index('href="static/style.css')


def test_hub_release_reads_S_by_name_not_window():
    assert "window.S" not in HUB, "S is a top-level let; window.S is always undefined"
    assert "typeof S !== 'undefined'" in HUB
    assert "S._bootReady" in HUB


def test_hub_release_removes_both_boot_classes():
    assert "classList.remove('booting')" in HUB
    assert "classList.remove('boot-session')" in HUB


def test_css_boot_suppression_is_scoped_to_booting():
    for rule in (
        "html.booting *,html.booting *::before,html.booting *::after{transition:none!important;}",
        "html.booting.boot-session .empty-state{display:none!important;}",
        "html.booting .chat-context{visibility:hidden;}",
    ):
        assert rule in CSS, rule
    assert "html .chat-context{visibility:hidden" not in CSS


def test_static_markup_provides_first_paint_chrome():
    assert 'class="rail-brand"' in INDEX
    assert 'id="topbarTitle"' in INDEX
    assert 'class="panel-head-btn sidebar-search-toggle"' in INDEX
    assert 'class="panel-head-btn source-menu-btn"' in INDEX
    assert 'rel="preload" href="static/vendor/inter/InterVariable.woff2"' in INDEX


def test_empty_session_memo_drives_first_paint():
    # Inline head script clears the session-boot flag for a session remembered as empty,
    # so the hero layout paints first instead of the composer jumping up after load.
    assert "hermes-webui-session-empty" in INDEX
    assert INDEX.index("hermes-webui-session-empty") < INDEX.index("classList.add('booting')")
    assert "EMPTY_KEY = 'hermes-webui-session-empty'" in HUB


def test_empty_memo_hooks_the_app_empty_state_switches():
    assert "'showConversationEmptyState', 'hideConversationEmptyState'" in HUB
    assert "_hubWrapped" in HUB


def test_boot_snapshots_restore_during_parse_and_loaders_respect_them():
    assert "localStorage.getItem('hermes-boot:sidebar')" in INDEX
    assert "localStorage.getItem('hermes-boot:transcript')" in INDEX
    assert INDEX.index("hermes-boot:sidebar") > INDEX.index('id="sessionList"')
    assert INDEX.index("hermes-boot:transcript") > INDEX.index('id="msgInner"')
    assert "SIDEBAR_KEY = 'hermes-boot:sidebar'" in HUB and "TRANSCRIPT_KEY = 'hermes-boot:transcript'" in HUB
    sessions = (ROOT / "static" / "sessions.js").read_text(encoding="utf-8")
    assert sessions.count("dataset.bootSnapshot") >= 2, "both loading-placeholder writes must skip a restored snapshot"


def test_snapshot_clicks_are_queued_and_replayed():
    assert "window.__hermesPendingSid=sid" in INDEX
    assert "window.__hermesPendingSid" in HUB and "loadSession(pending)" in HUB
    assert "delete el.dataset.bootSnapshot" in HUB


def test_sidebar_renders_are_deferred_while_snapshot_is_shown():
    assert "window.renderSessionList = wrapped" in HUB
    assert "sidebarRenderOrig()" in HUB
    assert "snap.hero" in INDEX
