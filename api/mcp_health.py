"""Background health checks for configured MCP servers (HWEB-62).

The WebUI already exposes read-only MCP runtime status through
``api.routes._mcp_runtime_status_by_name()``. That map only reports what the
Hermes Agent registry already knows (``connected``/``tools``), which cannot tell
"this server is down" apart from "this server's token expired" — two states that
need different user actions. This module adds that missing signal.

Design constraints, in order:

- **Never block a request.** ``refresh_and_read()`` only schedules; every probe
  runs on a short-lived daemon thread and the caller reads back whatever the
  cache already has (possibly ``unknown``).
- **No long-lived process.** Checks are demand-driven: the MCP endpoints call
  ``refresh_and_read()`` and a server is re-probed at most once per
  ``HEALTH_INTERVAL_S``.
- **A verdict names the config it measured.** Entries carry a fingerprint of the
  server config, so an edited server or a profile whose same-named server points
  elsewhere never inherits the previous server's health.
- **Bounded and non-accumulating.** A server already in flight is never
  scheduled again, so a slow server cannot pile up overlapping probes.
- **Contained failures.** One server's probe raising or timing out leaves that
  server in a known state and never touches another server's entry.

Health states: ``healthy``, ``needs_auth``, ``unhealthy``, ``unknown``.
``unknown`` means "we could not determine it" — it is never treated as healthy.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import threading
import time
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

logger = logging.getLogger(__name__)

# ponytail: demand-driven refresh (the MCP endpoints tick it) instead of a
# daemon loop, because AGENTS.md asks for no new long-lived processes. If health
# must be fresh while the MCP panel is closed, hook this into a periodic ticker.
HEALTH_INTERVAL_S: float = 120.0
PROBE_TIMEOUT_S: float = 8.0
_MAX_PROBE_BODY_BYTES = 64 * 1024

HEALTHY = "healthy"
NEEDS_AUTH = "needs_auth"
UNHEALTHY = "unhealthy"
UNKNOWN = "unknown"

# Statuses that mean "your credentials, not the server". Kept distinct from
# UNHEALTHY because re-authenticating and restarting a server are different acts.
_AUTH_STATUSES = frozenset({401, 403, 407})
# The probe speaks streamable HTTP. A server that answers but rejects this shape
# (legacy SSE transports, gateways that only route specific paths) tells us
# nothing about its health, so it stays UNKNOWN rather than being called down.
_PROTOCOL_MISMATCH_STATUSES = frozenset({404, 405, 406, 415})

_INITIALIZE_REQUEST = {
    "jsonrpc": "2.0",
    "id": "hermes-webui-health",
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "hermes-webui-health", "version": "1"},
    },
}

_LOCK = threading.Lock()
# name -> {"health": str, "detail": str, "checked_at": float, "fingerprint": str}
_STATE: dict[str, dict] = {}
_IN_FLIGHT: set[str] = set()
# name -> (monotonic timestamp, fingerprint) of the last *started* probe. Started,
# not finished, so a slow probe still holds its slot in the interval budget.
_STARTED_AT: dict[str, tuple[float, str]] = {}


class _NoRedirect(urllib_request.HTTPRedirectHandler):
    """Refuse redirects so a probe never forwards ``Authorization`` to a new host.

    ``urllib`` copies the original request headers onto the redirected request,
    so following a redirect out of a config-supplied URL would hand the server's
    bearer token to whatever host the redirect names. Returning ``None`` makes
    urllib surface the 3xx as an ``HTTPError`` instead, which we report as
    ``unknown`` — we did not learn whether the server is healthy.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_OPENER = urllib_request.build_opener(_NoRedirect())


def _urlopen(request):
    return _OPENER.open(request, timeout=PROBE_TIMEOUT_S)


def _jsonrpc_from_body(raw: bytes) -> dict | None:
    """Pull the JSON-RPC envelope out of an ``initialize`` response body.

    Streamable HTTP answers with a bare JSON object; the SSE form wraps the same
    object in a ``data:`` line. Anything else — an HTML login page, a reverse
    proxy's catch-all, an empty 200 — yields ``None``.
    """
    text = raw.decode("utf-8", "replace").strip()
    if not text:
        return None
    candidates = [text]
    candidates.extend(
        line.strip()[5:].strip()
        for line in text.splitlines()
        if line.strip().startswith("data:")
    )
    for candidate in candidates:
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("jsonrpc") == "2.0":
            return payload
    return None


def _ok_result(raw: bytes, code: int) -> tuple[str, str]:
    """Grade a 2xx body. A 2xx alone proves only that *something* answered.

    Healthy verdicts are deliberately silent in the panel, so calling an HTML
    login page or a proxy catch-all healthy would hide a server that cannot
    serve a single tool. Only a real ``initialize`` result earns HEALTHY.
    """
    payload = _jsonrpc_from_body(raw)
    if payload is None:
        return UNKNOWN, f"HTTP {code}, not an MCP response"
    if isinstance(payload.get("error"), dict):
        # It speaks MCP and refused to initialize: a real, actionable failure.
        return UNHEALTHY, f"HTTP {code}, initialize rejected"
    result = payload.get("result")
    if isinstance(result, dict) and any(
        key in result for key in ("protocolVersion", "serverInfo", "capabilities")
    ):
        return HEALTHY, f"HTTP {code}"
    return UNKNOWN, f"HTTP {code}, unrecognized initialize result"


def _status_result(code: int, raw: bytes | None = None) -> tuple[str, str]:
    detail = f"HTTP {code}"
    if code in _AUTH_STATUSES:
        return NEEDS_AUTH, detail
    if 200 <= code < 300:
        return _ok_result(raw or b"", code)
    if 300 <= code < 400 or code in _PROTOCOL_MISMATCH_STATUSES:
        return UNKNOWN, detail
    return UNHEALTHY, detail


def _transport_detail(exc: BaseException) -> str:
    reason = getattr(exc, "reason", None)
    if isinstance(exc, TimeoutError) or isinstance(reason, TimeoutError):
        return "timed out"
    return "unreachable"


def _probe_http(url: str, cfg: dict) -> tuple[str, str]:
    # Validate at the point of use: config is a trust boundary here, and
    # urlopen() would happily follow file:// or ftp:// out of a config file.
    scheme = urllib_parse.urlsplit(url).scheme.lower()
    if scheme not in {"http", "https"}:
        return UNHEALTHY, "unsupported url scheme"
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    configured = cfg.get("headers")
    if isinstance(configured, dict):
        for key, value in configured.items():
            if isinstance(key, str) and isinstance(value, str):
                headers[key] = value
    request = urllib_request.Request(
        url,
        data=json.dumps(_INITIALIZE_REQUEST).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with _urlopen(request) as response:
            code = int(getattr(response, "status", None) or response.getcode())
            # Bounded read: an initialize result is small, and a health probe
            # must never be the thing that pulls a huge body into memory.
            return _status_result(code, response.read(_MAX_PROBE_BODY_BYTES))
    except urllib_error.HTTPError as exc:
        return _status_result(int(exc.code))
    except (urllib_error.URLError, OSError, ValueError) as exc:
        return UNHEALTHY, _transport_detail(exc)


def _probe_stdio(command: str) -> tuple[str, str]:
    if shutil.which(command):
        # Spawning the server to speak MCP at it would be a side effect, not a
        # check. ``connected`` from the agent registry is the real liveness
        # signal for stdio; the caller folds that in.
        return UNKNOWN, "stdio server not probed"
    return UNHEALTHY, f"command not found: {os.path.basename(command) or command}"


def probe_server(name: str, cfg: dict) -> tuple[str, str]:
    """Return ``(health, detail)`` for one server config. Never raises for bad config."""
    if not isinstance(cfg, dict):
        return UNHEALTHY, "invalid config"
    url = cfg.get("url")
    if isinstance(url, str) and url.strip():
        return _probe_http(url.strip(), cfg)
    command = cfg.get("command")
    if isinstance(command, str) and command.strip():
        return _probe_stdio(command.strip())
    return UNHEALTHY, "invalid config"


def _fingerprint(cfg) -> str:
    """Identify the exact config a verdict was measured against.

    Two servers that share a name but not a url/token are different servers, and
    editing one in place makes the old verdict describe something that no longer
    exists. Identical configs deliberately collide: that really is one server.
    """
    try:
        canonical = json.dumps(cfg, sort_keys=True, default=str)
    except Exception:
        canonical = repr(cfg)
    return hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()[:16]


def _run_check(name: str, cfg: dict, fingerprint: str) -> None:
    try:
        try:
            health, detail = probe_server(name, cfg)
        except Exception:
            logger.debug("MCP health check for %r failed", name, exc_info=True)
            health, detail = UNKNOWN, "health check failed"
        with _LOCK:
            _STATE[name] = {
                "health": health,
                "detail": detail,
                "checked_at": time.time(),
                "fingerprint": fingerprint,
            }
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(name)


def refresh_and_read(servers: dict) -> dict[str, dict]:
    """Schedule due probes and return the verdicts that match ``servers`` *now*.

    ``servers`` is the set of servers that *should* be checked — the caller has
    already dropped disabled ones. State for anything outside that set is
    discarded, so toggling a server off both stops checking it and clears its
    stale verdict.

    A verdict is only ever returned for the exact config it was measured
    against. Entries carry the fingerprint of the config that produced them, so
    editing a server's url/headers/command, or switching to a profile whose
    same-named server points somewhere else, neither returns the old server's
    verdict nor waits out the interval before re-probing. A probe already in
    flight when the config changes still publishes, but its fingerprint no
    longer matches and it is filtered out here rather than shown as the new
    server's health.
    """
    if not isinstance(servers, dict):
        servers = {}
    now = time.monotonic()
    due: list[tuple[str, dict, str]] = []
    current = {str(name): _fingerprint(cfg) for name, cfg in servers.items()}
    configs = {str(name): (cfg if isinstance(cfg, dict) else {}) for name, cfg in servers.items()}
    with _LOCK:
        for stale in set(_STATE) - set(current):
            _STATE.pop(stale, None)
        for stale in set(_STARTED_AT) - set(current):
            _STARTED_AT.pop(stale, None)
        for name, fingerprint in current.items():
            stored = _STATE.get(name)
            if stored is not None and stored.get("fingerprint") != fingerprint:
                # The config changed under us; the old verdict describes a
                # different server. Drop it and re-probe now, not in 120s.
                _STATE.pop(name, None)
                _STARTED_AT.pop(name, None)
            if name in _IN_FLIGHT:
                continue
            started = _STARTED_AT.get(name)
            if (started is not None and started[1] == fingerprint
                    and (now - started[0]) < HEALTH_INTERVAL_S):
                continue
            _IN_FLIGHT.add(name)
            _STARTED_AT[name] = (now, fingerprint)
            due.append((name, configs[name], fingerprint))
        readable = {
            name: {k: v for k, v in row.items() if k != "fingerprint"}
            for name, row in _STATE.items()
            if row.get("fingerprint") == current.get(name)
        }
    for name, cfg, fingerprint in due:
        try:
            threading.Thread(
                target=_run_check,
                args=(name, cfg, fingerprint),
                name=f"mcp-health-{name}"[:60],
                daemon=True,
            ).start()
        except Exception:
            logger.debug("could not start MCP health thread for %r", name, exc_info=True)
            with _LOCK:
                _IN_FLIGHT.discard(name)
    return readable


def snapshot() -> dict[str, dict]:
    """Return a copy of the current per-server health verdicts."""
    with _LOCK:
        return {name: dict(row) for name, row in _STATE.items()}


def in_flight() -> set[str]:
    with _LOCK:
        return set(_IN_FLIGHT)


def reset() -> None:
    """Drop all cached health state (tests, and config reloads that rename servers)."""
    with _LOCK:
        _STATE.clear()
        _STARTED_AT.clear()
