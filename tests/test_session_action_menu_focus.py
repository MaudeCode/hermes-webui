"""Browser regression coverage for the portaled conversation-actions menu."""
from pathlib import Path
import re

import pytest


SESSIONS_JS = (Path(__file__).resolve().parents[1] / "static" / "sessions.js").read_text(
    encoding="utf-8"
)


def _function_source(name: str) -> str:
    marker = f"function {name}"
    start = SESSIONS_JS.find(marker)
    assert start >= 0, f"{name} not found"
    signature_end = re.search(r"\)\s*\{", SESSIONS_JS[start:])
    assert signature_end, f"{name} signature did not close"
    brace = start + signature_end.end() - 1
    depth = 1
    index = brace + 1
    while depth and index < len(SESSIONS_JS):
        if SESSIONS_JS[index] == "{":
            depth += 1
        elif SESSIONS_JS[index] == "}":
            depth -= 1
        index += 1
    assert depth == 0, f"{name} body did not close"
    return SESSIONS_JS[start:index]


def _fixture_script() -> str:
    """Run the production menu lifecycle in a small real-DOM fixture.

    The menu is deliberately portaled to body in production. This fixture stubs
    only positioning/animation helpers, so the browser verifies the real focus,
    ARIA, and keyboard lifecycle without needing a live agent session.
    """
    return "\n".join(
        [
            "let _sessionActionMenu = null;",
            "let _sessionActionAnchor = null;",
            "let _sessionActionSessionId = null;",
            "let _sessionActionPreviousFocus = null;",
            "let _sessionActionMenuRenderPending = false;",
            "let _sessionActionMenuRepaints = 0;",
            "let _sessionActionMenuReplaceRowsOnRender = false;",
            "let _sessionActionMenuReplacementFocusTarget = null;",
            "function renderSessionListFromCache(){",
            "  _sessionActionMenuRepaints += 1;",
            "  if(!_sessionActionMenuReplaceRowsOnRender) return;",
            "  const oldRow=document.querySelector('.session-item[data-sid=focus-refresh]');",
            "  if(!oldRow) return;",
            "  const replacement=document.createElement('div');",
            "  replacement.className='session-item';",
            "  replacement.dataset.sid='focus-refresh';",
            "  const trigger=document.createElement('button');",
            "  trigger.className='session-actions-trigger';",
            "  replacement.appendChild(trigger);",
            "  oldRow.replaceWith(replacement);",
            "  _sessionActionMenuReplacementFocusTarget=trigger;",
            "}",
            "const esc = value => String(value);",
            "function _positionSessionActionMenu(){}",
            "function _playSessionActionMenuEntrance(){}",
            _function_source("_focusSessionActionMenuRestoreTarget"),
            _function_source("closeSessionActionMenu"),
            _function_source("_buildSessionAction"),
            _function_source("_mountSessionActionMenu"),
            """
            window.__sessionActionMenuFocusResult = () => {
              const row = document.createElement('div');
              row.className = 'session-item';
              const trigger = document.createElement('button');
              trigger.className = 'session-actions-trigger';
              trigger.setAttribute('aria-haspopup', 'menu');
              trigger.setAttribute('aria-expanded', 'false');
              trigger.setAttribute('aria-label', 'Conversation actions');
              row.appendChild(trigger);
              document.body.appendChild(row);
              trigger.focus();

              const menu = document.createElement('div');
              menu.className = 'session-action-menu';
              menu.id = 'sessionActionMenu-browser-test';
              menu.setAttribute('role', 'menu');
              menu.setAttribute('aria-label', 'Conversation actions');
              menu.appendChild(_buildSessionAction('Copy conversation link', '', '', () => {}));
              menu.appendChild(_buildSessionAction('Rename conversation', '', '', () => {}));
              menu.appendChild(_buildSessionAction('Delete conversation', '', '', () => {}));
              _mountSessionActionMenu(menu, {session_id: 'browser-focus-test'}, trigger);

              const result = {
                expandedOnOpen: trigger.getAttribute('aria-expanded'),
                controlsOnOpen: trigger.getAttribute('aria-controls'),
                menuRole: menu.getAttribute('role'),
                firstActionFocused: document.activeElement === menu.querySelector('.session-action-opt'),
                firstActionRole: document.activeElement.getAttribute('role'),
              };
              menu.dispatchEvent(new KeyboardEvent('keydown', {key: 'ArrowDown', bubbles: true}));
              result.arrowDownText = document.activeElement.textContent.trim();
              menu.dispatchEvent(new KeyboardEvent('keydown', {key: 'End', bubbles: true}));
              result.endText = document.activeElement.textContent.trim();
              menu.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));
              result.menuRemovedOnEscape = !document.querySelector('.session-action-menu');
              result.focusRestoredOnEscape = document.activeElement === trigger;
              result.expandedAfterEscape = trigger.getAttribute('aria-expanded');
              result.controlsAfterEscape = trigger.getAttribute('aria-controls');
              row.remove();
              return result;
            };

            window.__sessionActionMenuNonFocusableOpenerResult = () => {
              const priorFocus = document.createElement('button');
              priorFocus.textContent = 'Prior keyboard focus';
              const row = document.createElement('div');
              row.className = 'session-item';
              row.textContent = 'Non-focusable session row';
              document.body.append(priorFocus, row);
              priorFocus.focus();

              const menu = document.createElement('div');
              menu.className = 'session-action-menu';
              menu.id = 'sessionActionMenu-nonfocusable-opener-test';
              menu.setAttribute('role', 'menu');
              menu.setAttribute('aria-label', 'Conversation actions');
              menu.appendChild(_buildSessionAction('Rename conversation', '', '', () => {}));
              _mountSessionActionMenu(menu, {session_id: 'nonfocusable-opener-test'}, row);
              menu.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', bubbles: true}));

              const result = {
                menuRemovedOnEscape: !document.querySelector('.session-action-menu'),
                focusReturnedToPreviousControl: document.activeElement === priorFocus,
              };
              priorFocus.remove();
              row.remove();
              return result;
            };

            window.__sessionActionMenuPendingRenderResult = () => {
              _sessionActionMenuRepaints = 0;
              const row = document.createElement('div');
              row.className = 'session-item';
              const trigger = document.createElement('button');
              trigger.className = 'session-actions-trigger';
              row.appendChild(trigger);
              document.body.appendChild(row);
              const menu = document.createElement('div');
              menu.className = 'session-action-menu';
              menu.id = 'sessionActionMenu-pending-render-test';
              menu.appendChild(_buildSessionAction('Rename conversation', '', '', () => {}));
              _mountSessionActionMenu(menu, {session_id: 'pending-render-test'}, trigger);
              _sessionActionMenuRenderPending = true;
              closeSessionActionMenu();
              const result = {
                menuRemoved: !document.querySelector('.session-action-menu'),
                pendingCleared: !_sessionActionMenuRenderPending,
                repaintCount: _sessionActionMenuRepaints,
              };
              row.remove();
              return result;
            };

            window.__sessionActionMenuReplaceResult = () => {
              _sessionActionMenuRepaints = 0;
              const rowA = document.createElement('div');
              rowA.className = 'session-item';
              const triggerA = document.createElement('button');
              triggerA.className = 'session-actions-trigger';
              rowA.appendChild(triggerA);
              const rowB = document.createElement('div');
              rowB.className = 'session-item';
              const triggerB = document.createElement('button');
              triggerB.className = 'session-actions-trigger';
              rowB.appendChild(triggerB);
              document.body.append(rowA, rowB);
              const menuA = document.createElement('div');
              menuA.id = 'menu-a';
              menuA.appendChild(_buildSessionAction('A', '', '', () => {}));
              _mountSessionActionMenu(menuA, {session_id: 'a'}, triggerA);
              _sessionActionMenuRenderPending = true;
              closeSessionActionMenu({flushPendingRender:false});
              const beforeB = {
                repaintCount: _sessionActionMenuRepaints,
                pending: _sessionActionMenuRenderPending,
                anchorConnected: triggerB.isConnected,
              };
              const menuB = document.createElement('div');
              menuB.id = 'menu-b';
              menuB.appendChild(_buildSessionAction('B', '', '', () => {}));
              _mountSessionActionMenu(menuB, {session_id: 'b'}, triggerB);
              closeSessionActionMenu();
              const result = {
                beforeB,
                finalRepaintCount: _sessionActionMenuRepaints,
                pendingCleared: !_sessionActionMenuRenderPending,
              };
              rowA.remove();
              rowB.remove();
              return result;
            };

            window.__sessionActionMenuRepaintFocusResult = () => {
              _sessionActionMenuRepaints = 0;
              _sessionActionMenuReplaceRowsOnRender = true;
              _sessionActionMenuReplacementFocusTarget = null;
              const row = document.createElement('div');
              row.className = 'session-item';
              row.dataset.sid = 'focus-refresh';
              const trigger = document.createElement('button');
              trigger.className = 'session-actions-trigger';
              row.appendChild(trigger);
              document.body.appendChild(row);
              trigger.focus();
              const menu = document.createElement('div');
              menu.id = 'menu-focus-refresh';
              menu.appendChild(_buildSessionAction('A', '', '', () => {}));
              _mountSessionActionMenu(menu, {session_id: 'focus-refresh'}, trigger);
              _sessionActionMenuRenderPending = true;
              closeSessionActionMenu({restoreFocus:true});
              const result = {
                repaintCount: _sessionActionMenuRepaints,
                replacementFocused: document.activeElement === _sessionActionMenuReplacementFocusTarget,
              };
              _sessionActionMenuReplaceRowsOnRender = false;
              const replacementRow=document.querySelector('.session-item[data-sid=focus-refresh]');
              if(replacementRow) replacementRow.remove();
              return result;
            };
            """,
        ]
    )


def test_session_action_menu_focus_lifecycle_in_browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run the session action menu browser test")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_script_tag(content=_fixture_script())
        result = page.evaluate("window.__sessionActionMenuFocusResult()")
        browser.close()

    assert result == {
        "expandedOnOpen": "true",
        "controlsOnOpen": "sessionActionMenu-browser-test",
        "menuRole": "menu",
        "firstActionFocused": True,
        "firstActionRole": "menuitem",
        "arrowDownText": "Rename conversation",
        "endText": "Delete conversation",
        "menuRemovedOnEscape": True,
        "focusRestoredOnEscape": True,
        "expandedAfterEscape": "false",
        "controlsAfterEscape": None,
    }


def test_session_action_menu_returns_to_prior_focus_for_nonfocusable_opener_in_browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run the session action menu browser test")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_script_tag(content=_fixture_script())
        result = page.evaluate("window.__sessionActionMenuNonFocusableOpenerResult()")
        browser.close()

    assert result == {
        "menuRemovedOnEscape": True,
        "focusReturnedToPreviousControl": True,
    }


def test_session_action_menu_close_flushes_deferred_sidebar_render_in_browser():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run the session action menu browser test")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_script_tag(content=_fixture_script())
        result = page.evaluate("window.__sessionActionMenuPendingRenderResult()")
        browser.close()

    assert result == {
        "menuRemoved": True,
        "pendingCleared": True,
        "repaintCount": 1,
    }


def test_render_guard_records_that_action_menu_suppressed_a_repaint():
    source = _function_source("renderSessionListFromCache")
    guard = source.index("if(_sessionActionMenu)")
    assert "_sessionActionMenuRenderPending" in source[guard : guard + 180]


def test_replacing_action_menu_carries_deferred_render_until_new_menu_closes():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run the session action menu browser test")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_script_tag(content=_fixture_script())
        result = page.evaluate("window.__sessionActionMenuReplaceResult()")
        browser.close()

    assert result == {
        "beforeB": {"repaintCount": 0, "pending": True, "anchorConnected": True},
        "finalRepaintCount": 1,
        "pendingCleared": True,
    }

    open_source = _function_source("_openSessionActionMenu")
    assert "closeSessionActionMenu({flushPendingRender:false})" in open_source


def test_deferred_repaint_restores_focus_to_the_replacement_trigger():
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # pragma: no cover - dependency missing path
        pytest.skip("playwright is unavailable; run the session action menu browser test")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = browser.new_page()
        page.set_content("<!doctype html><html><body></body></html>")
        page.add_script_tag(content=_fixture_script())
        result = page.evaluate("window.__sessionActionMenuRepaintFocusResult()")
        browser.close()

    assert result == {"repaintCount": 1, "replacementFocused": True}
