#!/usr/bin/env python3
"""
Headless browser smoke test — the console-error page-load gate.

WHY THIS EXISTS
  `node --check`, ESLint, and the (mocked) pytest suite cannot see the class of
  bug that has actually bricked releases: JavaScript that parses fine but throws
  at *runtime* when a real browser executes the page. Examples that shipped:
    - a `const` reassigned at runtime (v0.51.168 "Failed to load conversation
      messages" — #3162)
    - a `function X(){}` colliding with a `window.X = {}` in classic scripts
      (#2715 / #2771)
  Every one of those throws on load or first interaction and produces a blank or
  broken page for *every* user. This smoke boots the real server.py and loads
  the key pages in headless Chromium, failing if ANY uncaught exception or
  console error fires.

SCOPE
  Deliberately AGENT-FREE so it runs in CI (which does not install hermes-agent):
  it verifies the page loads and its JS initializes cleanly — it does NOT drive a
  full chat (that needs the agent + mock provider and runs in the private QA
  harness's golden-path E2E). This is the "does the app even come up without
  throwing" gate, which is the highest-frequency brick class.

USAGE
  python tests/browser_smoke.py
  (Requires: playwright + chromium. Boots server.py on an ephemeral port with an
  isolated temp state dir and no agent.)

EXIT CODES
  0 — all pages loaded with zero console errors / uncaught exceptions
  1 — a console error or uncaught exception was detected (regression)
  2 — environment/setup failure (server didn't boot, playwright missing, etc.)
"""
import os
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error

PORT = int(os.getenv("SMOKE_PORT", "8796"))
BASE = f"http://127.0.0.1:{PORT}"

# Pages that must load cleanly. Hash routes are how the SPA exposes views.
PAGES = [
    "/",
    "/#settings",
    "/#sessions",
]

# Known-benign console noise (extend deliberately, each with a reason). Every
# entry here is a blind spot, so keep the list short.
BENIGN = [
    "favicon",          # favicon 404 in bare env — not app code
    "manifest.json",    # PWA manifest probe under headless http
    "serviceworker",    # SW registration noise under headless http
    "sw.js",            # service worker fetch noise
    "the server responded with a status of 404",  # static asset 404 in bare env
]


def _is_benign(text):
    t = text.lower()
    return any(p.lower() in t for p in BENIGN)


# HWEB-33: a tab must not hold more than two concurrent EventSource connections,
# because the browser allows six per origin and everything above ~4 starves the
# ordinary api() fetches. Wraps window.EventSource before any page script runs so
# every construction is recorded and live sockets can be counted for real.
_SSE_PROBE = """
(() => {
  const Native = window.EventSource;
  if (!Native) return;
  const all = [];
  window.__sseProbe = all;
  const Wrapped = function (url, init) {
    const es = new Native(url, init);
    all.push({ url: String(url), es: es });
    return es;
  };
  Wrapped.prototype = Native.prototype;
  Wrapped.CONNECTING = 0;
  Wrapped.OPEN = 1;
  Wrapped.CLOSED = 2;
  window.EventSource = Wrapped;
})();
"""

# readyState 2 is CLOSED; CONNECTING still owns a socket for the pending retry,
# so anything that is not CLOSED counts against the budget.
_OPEN_STREAMS = "() => (window.__sseProbe || []).filter(e => e.es.readyState !== 2).map(e => e.url)"
_ALL_STREAMS = "() => (window.__sseProbe || []).map(e => e.url)"


def _check_eventsource_budget(browser, base):
    """Assert the two-stream ceiling and the sidebar merge in a real browser.

    Returns a list of failure strings (empty when the budget holds).
    """
    failures = []
    ctx = browser.new_context(base_url=base)
    page = ctx.new_page()
    page.add_init_script(_SSE_PROBE)
    page.goto("/", wait_until="domcontentloaded")
    try:
        page.wait_for_selector("#msg, .app, body", timeout=10000)
    except Exception:
        pass
    # The sidebar stream self-gates on document.hasFocus(); make sure this page
    # is the focused one so a headless-only blur cannot fake a passing budget.
    page.bring_to_front()
    try:
        page.wait_for_function(
            "() => (window.__sseProbe || []).some(e => e.url.indexOf('api/sessions/events') !== -1)",
            timeout=10000,
        )
    except Exception:
        failures.append("  [sse-budget] sidebar stream never opened — probe saw no api/sessions/events")
        ctx.close()
        return failures

    def snapshot(label):
        open_urls = page.evaluate(_OPEN_STREAMS)
        if len(open_urls) > 2:
            failures.append(
                f"  [sse-budget] {label}: {len(open_urls)} concurrent EventSources "
                f"(budget is 2): {open_urls}"
            )
        return open_urls

    def sidebar_open(urls):
        return [u for u in urls if "api/sessions/events" in u]

    # 1. Boot state: one merged sidebar stream, and no separate gateway socket.
    booted = snapshot("chat panel")
    if len(sidebar_open(booted)) != 1:
        failures.append(f"  [sse-budget] expected exactly one open sidebar stream, got {booted}")
    if not any("gateway=1" in u for u in sidebar_open(booted)):
        failures.append(
            f"  [sse-budget] sidebar stream did not request the merged gateway half: {booted}"
        )
    ever = page.evaluate(_ALL_STREAMS)
    if any("api/sessions/gateway/stream" in u for u in ever):
        failures.append(
            f"  [sse-budget] a second EventSource was opened for the gateway feed: {ever}"
        )

    # 2. A main-view panel must release the sidebar socket for its own stream.
    page.evaluate("() => switchPanel && switchPanel('kanban')")
    try:
        page.wait_for_function(
            "() => !(window.__sseProbe || []).some("
            "e => e.url.indexOf('api/sessions/events') !== -1 && e.es.readyState !== 2)",
            timeout=5000,
        )
    except Exception:
        failures.append(
            "  [sse-budget] sidebar stream stayed open on the Kanban panel: "
            f"{page.evaluate(_OPEN_STREAMS)}"
        )
    snapshot("kanban panel")

    # 3. Returning to the session list brings it back.
    page.evaluate("() => switchPanel && switchPanel('chat')")
    try:
        page.wait_for_function(
            "() => (window.__sseProbe || []).some("
            "e => e.url.indexOf('api/sessions/events') !== -1 && e.es.readyState !== 2)",
            timeout=10000,
        )
    except Exception:
        failures.append("  [sse-budget] sidebar stream did not reopen on returning to the chat panel")
    snapshot("chat panel after return")

    if not failures:
        print("OK  EventSource budget — <=2 concurrent, sidebar merged and panel-gated")
    ctx.close()
    return failures


def _wait_for_health(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(BASE + "/health", timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            pass
        time.sleep(0.5)
    return False


def main():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("SKIP: playwright not installed", file=sys.stderr)
        return 2

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    server_py = os.path.join(repo_root, "server.py")
    if not os.path.exists(server_py):
        print(f"SETUP FAIL: server.py not found at {server_py}", file=sys.stderr)
        return 2

    state_dir = tempfile.mkdtemp(prefix="hermes-browser-smoke-")
    no_agent_dir = os.path.join(state_dir, "no-agent")
    plugins_dir = os.path.join(state_dir, "plugins")
    workspace_dir = os.path.join(state_dir, "workspace")
    os.makedirs(no_agent_dir, exist_ok=True)
    os.makedirs(plugins_dir, exist_ok=True)
    os.makedirs(workspace_dir, exist_ok=True)
    # Discovery deliberately accepts source roots, not arbitrary directories.
    # A minimal module keeps this test hermetic while still exercising the
    # supported explicit-agent-dir path.
    with open(os.path.join(no_agent_dir, "run_agent.py"), "w", encoding="utf-8") as f:
        f.write("# agent-free browser smoke sentinel\n")
    env = os.environ.copy()
    # Strip real provider keys so nothing leaks into the smoke server.
    for k in list(env):
        if k.endswith("_API_KEY"):
            env.pop(k, None)
    env.update({
        "HERMES_WEBUI_PORT": str(PORT),
        "HERMES_WEBUI_HOST": "127.0.0.1",
        "HERMES_WEBUI_STATE_DIR": state_dir,
        "HERMES_HOME": state_dir,
        "HERMES_BASE_HOME": state_dir,
        "HERMES_WEBUI_DEFAULT_WORKSPACE": workspace_dir,
        "HERMES_WEBUI_PLUGINS_DIR": plugins_dir,
        # Never inherit the operator's production auth setting: a redirect to
        # /login would make all three routes exercise the same login page rather
        # than the application shell this gate is meant to protect.
        "HERMES_WEBUI_PASSWORD": "",
        "HERMES_WEBUI_SKIP_ONBOARDING": "1",
        # Keep bootstrap on this isolated test interpreter. Without the explicit
        # override it may re-exec into a discovered production agent venv before
        # the page-load gate starts, coupling this agent-free smoke to unrelated
        # provider/plugin dependencies on the host.
        "HERMES_WEBUI_PYTHON": sys.executable,
        # Point discovery at the minimal isolated source root above. A nonexistent
        # override is intentionally skipped by startup discovery, which would fall
        # through to ~/.hermes/hermes-agent and defeat the isolation promised here.
        "HERMES_WEBUI_AGENT_DIR": no_agent_dir,
    })

    log = open(os.path.join(state_dir, "server.log"), "w")
    proc = subprocess.Popen(
        [sys.executable, server_py], cwd=repo_root, env=env,
        stdout=log, stderr=subprocess.STDOUT,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),
    )
    try:
        if not _wait_for_health(timeout=30):
            print("SETUP FAIL: server did not become healthy in 30s", file=sys.stderr)
            log.flush()
            with open(os.path.join(state_dir, "server.log")) as f:
                print(f.read()[-2000:], file=sys.stderr)
            return 2

        failures = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(
                headless=True, args=["--no-sandbox", "--disable-dev-shm-usage"]
            )
            for path in PAGES:
                ctx = browser.new_context(base_url=BASE)
                page = ctx.new_page()
                errors = []
                page.on("console", lambda m, errors=errors: errors.append(("console", m.text))
                        if m.type == "error" else None)
                page.on("pageerror", lambda e, errors=errors: errors.append(("pageerror", str(e))))

                page.goto(path, wait_until="domcontentloaded")
                # Give boot.js / view init time to run and throw if it's going to.
                try:
                    page.wait_for_selector("#msg, .app, body", timeout=10000)
                except Exception:
                    pass
                time.sleep(1.5)

                meaningful = [(kind, txt) for (kind, txt) in errors if not _is_benign(txt)]
                if meaningful:
                    for kind, txt in meaningful:
                        failures.append(f"  [{path}] {kind}: {txt}")
                else:
                    print(f"OK  {path} — no console errors")
                ctx.close()
            failures.extend(_check_eventsource_budget(browser, BASE))
            browser.close()

        if failures:
            print("\nBROWSER SMOKE FAILED — runtime JS errors detected:", file=sys.stderr)
            print("\n".join(failures), file=sys.stderr)
            return 1
        print("\nBROWSER SMOKE PASSED — all pages loaded with zero console errors")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
