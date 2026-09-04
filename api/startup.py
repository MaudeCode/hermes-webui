"""Hermes Web UI -- startup helpers."""
from __future__ import annotations
import os, stat, subprocess, sys, threading
from pathlib import Path

# Startup readiness: server.py binds the listening socket before this module's
# run_deferred_startup() scans sessions, so a restart answers /health and serves
# the UI shell immediately instead of hiding behind a full session scan.
# Requests that read recovered session state wait on this event instead of
# racing it.
#
# Starts SET, and only start_deferred_startup() clears it. A process that never
# arms a deferred startup — a test driving Handler directly, an embedder calling
# into the routes — has no recovery pass to wait for, so the gate must fail open
# for it rather than stall every /api/ request for the full readiness bound.
STARTUP_READY = threading.Event()
STARTUP_READY.set()
STARTUP_WAIT_SECONDS = 10.0
# Dependency repair is a second, later readiness dimension. Recovery releasing
# STARTUP_READY lets sessions be read, but agent-dependent actions must not be
# treated as permanently broken while auto_install_agent_deps() is still
# mutating the environment (its pip call has a 120s timeout) — the import can
# succeed once it lands. Same fail-open default as STARTUP_READY.
AGENT_DEPS_READY = threading.Event()
AGENT_DEPS_READY.set()
# How long a chat turn waits on an in-flight repair before reporting it. Well
# under the pip timeout: the point is an accurate, retryable message, not
# holding an SSE worker for the whole install.
AGENT_DEPS_WAIT_SECONDS = 30.0
# Cap on requests allowed to block on the gate at once. Each waiter occupies one
# of HTTPWorkerBudgetMixin's request-worker slots (max_request_workers // 2 = 64),
# and process_request() acquires those non-blocking — so an uncapped wait lets
# reconnecting tabs fill the whole request budget and get /health, the app shell
# and /api/health/restart overflow-rejected at accept time, recreating the
# unavailable startup window this gate exists to remove. Excess waiters get the
# same 503 immediately rather than queueing behind the bound.
# ponytail: fixed cap; derive it from the server's request budget if that ratio
# ever stops holding.
STARTUP_WAIT_SLOT_COUNT = 8
STARTUP_WAIT_SLOTS = threading.BoundedSemaphore(STARTUP_WAIT_SLOT_COUNT)
# Session recovery is the only phase the readiness event gates; dependency
# repair, the watcher and plugins run after it and never hold a request.
STARTUP_PHASE = 'session recovery'
# Machine-readable marker on the startup 503, mirroring the worker-overflow
# response's "condition" field. Clients retry on this rather than treating a
# startup 503 as a real failure.
STARTUP_RECOVERY_CONDITION = 'startup_recovery'
# Every /api/ route may touch session state, so all of them wait (fail closed).
# These two must answer during startup: a restart trigger and a browser beacon.
STARTUP_IMMEDIATE_PATHS = frozenset({'/api/health/restart', '/api/csp-report'})

# Credential files that should never be world-readable
_SENSITIVE_FILES = (
    '.env',
    'google_token.json',
    'google_client_secret.json',
    '.signing_key',
    'auth.json',
)


def fix_credential_permissions() -> None:
    """Ensure sensitive files in HERMES_HOME have safe permissions.

    Respects:
      - HERMES_SKIP_CHMOD=1  → bypass entirely
      - HERMES_HOME_MODE     → group bits are allowed if set by the operator,
                               only world-readable/world-writable files are fixed
    """
    if os.environ.get('HERMES_SKIP_CHMOD', '').strip() in ('1', 'true'):
        return

    # Parse operator-declared mode to know if group bits are intentional
    declared_mode = None
    raw_mode = os.environ.get('HERMES_HOME_MODE', '').strip()
    if raw_mode:
        try:
            declared_mode = int(raw_mode, 8)
        except ValueError:
            pass

    hermes_home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
    if not hermes_home.is_dir():
        return
    for name in _SENSITIVE_FILES:
        fpath = hermes_home / name
        if not fpath.exists():
            continue
        try:
            current = stat.S_IMODE(fpath.stat().st_mode)
            # If operator declared a mode, allow group bits but still fix world bits
            if declared_mode is not None:
                if current & 0o007:  # other bits set (world-readable/writable)
                    fpath.chmod(current & ~0o007)
                    print(f'  [security] removed world bits on {fpath.name} ({oct(current)} -> {oct(current & ~0o007)})', flush=True)
            else:
                if current & 0o077:  # group or other bits set
                    fpath.chmod(0o600)
                    print(f'  [security] fixed permissions on {fpath.name} ({oct(current)} -> 0600)', flush=True)
        except OSError:
            pass  # best-effort; don't abort startup


def _agent_dir() -> Path | None:
    hermes_home = Path(os.environ.get('HERMES_HOME', str(Path.home() / '.hermes')))
    for raw in [os.environ.get('HERMES_WEBUI_AGENT_DIR', '').strip(), str(hermes_home / 'hermes-agent')]:
        if not raw:
            continue
        p = Path(raw).expanduser()
        if p.is_dir():
            return p.resolve()
    return None

def _trusted_agent_dir(agent_dir: Path) -> bool:
    """Return True if agent_dir passes ownership and permission checks.

    Validates that the directory is not world- or group-writable and,
    on POSIX systems, is owned by the current process user.

    Intentionally does NOT enforce a canonical path (i.e. does not require
    the dir to be ~/.hermes/hermes-agent), so custom HERMES_WEBUI_AGENT_DIR
    paths work correctly when HERMES_WEBUI_AUTO_INSTALL=1 is set.
    """
    try:
        st = agent_dir.stat()
        if stat.S_IMODE(st.st_mode) & 0o022:
            # World- or group-writable — untrusted
            return False
        if hasattr(os, 'getuid') and st.st_uid != os.getuid():
            # Not owned by current user (POSIX only; Windows fallback skips)
            return False
        return True
    except OSError:
        return False


def auto_install_agent_deps() -> bool:
    enabled = os.environ.get('HERMES_WEBUI_AUTO_INSTALL', '').strip().lower() in ('1', 'true', 'yes')
    if not enabled:
        print('[!!] Auto-install disabled. Set HERMES_WEBUI_AUTO_INSTALL=1 to enable.', flush=True)
        return False
    agent_dir = _agent_dir()
    if agent_dir is None:
        print('[!!] Auto-install skipped: agent directory not found.', flush=True)
        return False
    if not _trusted_agent_dir(agent_dir):
        print('[!!] Auto-install skipped: agent directory failed trust check (check ownership/permissions).', flush=True)
        return False
    req_file = agent_dir / 'requirements.txt'
    pyproject = agent_dir / 'pyproject.toml'
    if req_file.exists():
        install_args = [sys.executable, '-m', 'pip', 'install', '--quiet', '-r', str(req_file)]
        print(f'     Installing from {req_file} ...', flush=True)
    elif pyproject.exists():
        install_args = [sys.executable, '-m', 'pip', 'install', '--quiet', str(agent_dir)]
        print(f'     Installing from {agent_dir} (pyproject.toml) ...', flush=True)
    else:
        print('[!!] Auto-install skipped: no requirements.txt or pyproject.toml in agent dir.', flush=True)
        return False
    try:
        result = subprocess.run(install_args, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            print(f'[!!] pip install failed (exit {result.returncode}):', flush=True)
            for line in (result.stderr or '').splitlines()[-10:]:
                print(f'     {line}', flush=True)
            return False
        print('[ok] pip install completed.', flush=True)
        return True
    except subprocess.TimeoutExpired:
        print('[!!] Auto-install timed out after 120s.', flush=True)
        return False
    except Exception as e:
        print(f'[!!] Auto-install error: {e}', flush=True)
        return False


def _startup_exempt(path: str) -> bool:
    """Return True for /api/ paths that must answer while recovery runs.

    The public surface is reused from api.auth.is_public_path() rather than
    hand-listed: those paths are public precisely because they authenticate a
    caller or serve a standalone snapshot instead of reading session state, so
    none of them depend on recovery. Gating them would lock users out of an
    authenticated deployment for the whole recovery window, and would break
    public share links — static/login.js and static/share.js both use a bare
    fetch() and never see api()'s startup retry.

    Note this deliberately uses the predicate, not PUBLIC_PATHS: share reads and
    static assets are prefix rules, so an exact-match copy would miss them.
    """
    if path in STARTUP_IMMEDIATE_PATHS:
        return True
    from api.auth import is_public_path
    return is_public_path(path)


def _send_still_starting(handler) -> None:
    """Answer a gated request with a retryable startup 503."""
    from api.helpers import j

    # The gate runs before the route reads the body, so a rejected POST/PUT/
    # PATCH/DELETE leaves its JSON or upload bytes unread. On an HTTP/1.1
    # keep-alive connection BaseHTTPRequestHandler would parse those bytes as
    # the next request line — blocking until the 30s handler timeout or
    # answering a spurious 400/501, and holding a request-worker slot the whole
    # time, which is exactly the budget STARTUP_WAIT_SLOTS protects. Close
    # instead, matching REQUEST_WORKER_OVERFLOW_RESPONSE's Connection: close.
    handler.close_connection = True
    j(
        handler,
        {
            'error': f'Server is still starting: {STARTUP_PHASE}',
            'phase': STARTUP_PHASE,
            # Distinguishes this from the worker-overflow 503 so clients can
            # retry it instead of committing fallback state (static/workspace.js).
            'condition': STARTUP_RECOVERY_CONDITION,
        },
        status=503,
        extra_headers={'Retry-After': '5'},
    )


def await_startup_ready(handler, parsed) -> bool:
    """Return True if the request may proceed, else emit 503 and return False."""
    if STARTUP_READY.is_set():
        return True
    path = parsed.path
    if not path.startswith('/api/') or _startup_exempt(path):
        return True
    # Bounded wait, and only for a bounded number of requests at a time, so the
    # gate can never consume the request-worker budget the exempt routes need.
    if not STARTUP_WAIT_SLOTS.acquire(blocking=False):
        _send_still_starting(handler)
        return False
    try:
        if STARTUP_READY.wait(timeout=STARTUP_WAIT_SECONDS):
            return True
    finally:
        STARTUP_WAIT_SLOTS.release()
    _send_still_starting(handler)
    return False


def start_deferred_startup() -> threading.Thread:
    """Arm the readiness gate and run the deferred startup work on a thread.

    Call this once, after the listening socket is bound. Arming here — rather
    than at import — keeps the gate closed only while a recovery pass is
    genuinely in flight.
    """
    STARTUP_READY.clear()
    AGENT_DEPS_READY.clear()
    thread = threading.Thread(
        target=run_deferred_startup,
        name="webui-deferred-startup",
        daemon=True,
    )
    thread.start()
    return thread


def run_deferred_startup() -> None:
    """Run recovery, dependency repair and background workers behind the bind.

    Everything here used to run in server.py's main() in front of
    ``QuietHTTPServer(...)``, so a restart could leave the port unreachable for
    minutes (worst case: a pip install with a 120s timeout). Session recovery
    runs first and releases the readiness event as soon as it settles — success
    or failure — so a slow pip install or plugin import can never hold a
    request.
    """
    from api.config import SESSION_DIR, verify_hermes_imports, _HERMES_FOUND

    try:
        from api.models import _active_state_db_path
        from api.session_recovery import recover_all_sessions_on_startup
        result = recover_all_sessions_on_startup(
            SESSION_DIR,
            rebuild_index=True,
            state_db_path=_active_state_db_path(),
        )
        if result.get("restored"):
            print(f"[recovery] Restored {result['restored']}/{result['scanned']} sessions from .bak (see #1558).", flush=True)
    except Exception as exc:
        # Recovery is best-effort; never block server startup.
        print(f"[recovery] startup recovery failed: {exc}", flush=True)
    finally:
        # Released on every exit path, so a raising recovery cannot wedge every
        # /api/ request behind the readiness bound for the process lifetime.
        STARTUP_READY.set()

    try:
        ok, missing, errors = verify_hermes_imports()
        if not ok and _HERMES_FOUND:
            # pip only runs when an agent import actually failed.
            print(f'[!!] Warning: Hermes agent found but missing modules: {missing}', flush=True)
            for mod, err in errors.items():
                print(f'     {mod}: {err}', flush=True)
            print('     Attempting to install missing dependencies from agent requirements.txt...', flush=True)
            auto_install_agent_deps()
            ok, missing, errors = verify_hermes_imports()
            if not ok:
                print(f'[!!] Still missing after install attempt: {missing}', flush=True)
                for mod, err in errors.items():
                    print(f'     {mod}: {err}', flush=True)
                print('     Agent features may not work correctly.', flush=True)
            else:
                print('[ok] Agent dependencies installed successfully.', flush=True)
    finally:
        # Released on every exit path, so a raising verify/install cannot leave
        # agent-dependent actions waiting for a repair that is no longer running.
        AGENT_DEPS_READY.set()

    try:
        from api.gateway_watcher import start_watcher

        def _start_watcher_safe():
            try:
                start_watcher()
            except Exception as e:
                print(f'[!!] WARNING: Gateway watcher failed to start: {e}', flush=True)

        threading.Thread(target=_start_watcher_safe, daemon=True).start()
    except Exception as e:
        print(f'[!!] WARNING: Gateway watcher failed to start: {e}', flush=True)

    try:
        from api.background_process import start_drain_thread
        if start_drain_thread():
            print('[ok] bg_task_complete drain thread started', flush=True)
    except Exception as e:
        print(f'[!!] WARNING: bg_task_complete drain failed to start: {e}', flush=True)

    try:
        from api.background_process import start_session_channel_reaper
        if start_session_channel_reaper():
            print('[ok] SessionChannel reaper thread started', flush=True)
    except Exception as e:
        print(f'[!!] WARNING: SessionChannel reaper failed to start: {e}', flush=True)

    try:
        from api.plugins import load_plugins
        load_plugins()
        from api.talaria_relay import start_talaria_relay_publisher
        start_talaria_relay_publisher()
    except Exception as e:
        print(f'[!!] WARNING: Plugin loading failed: {e}', flush=True)
