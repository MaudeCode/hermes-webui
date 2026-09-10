"""Background health checks for configured MCP servers (HWEB-62).

The WebUI already exposes read-only MCP runtime status through
``api.routes._mcp_runtime_status_by_name()``. That map only reports what the
Hermes Agent registry already knows (``connected``/``tools``), which cannot tell
"this server is down" apart from "this server's token expired" — two states that
need different user actions. This module adds that missing signal.

Design constraints, in order:

- **Never block a request.** ``refresh_async()`` only schedules; every probe runs
  on a short-lived daemon thread and the caller reads whatever
  ``snapshot()`` already has (possibly ``unknown``).
- **No long-lived process.** Checks are demand-driven: the MCP endpoints call
  ``refresh_async()`` and a server is re-probed at most once per
  ``HEALTH_INTERVAL_S``.
- **Bounded and non-accumulating.** A server already in flight is never
  scheduled again, so a slow server cannot pile up overlapping probes.
- **Contained failures.** One server's probe raising or timing out leaves that
  server in a known state and never touches another server's entry.

Health states: ``healthy``, ``needs_auth``, ``unhealthy``, ``unknown``.
``unknown`` means "we could not determine it" — it is never treated as healthy.
"""

from __future__ import annotations

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
# name -> {"health": str, "detail": str, "checked_at": float}
_STATE: dict[str, dict] = {}
_IN_FLIGHT: set[str] = set()
# name -> monotonic timestamp of the last *started* probe. Started, not finished,
# so a slow probe still holds its slot in the interval budget.
_STARTED_AT: dict[str, float] = {}


def _status_result(code: int) -> tuple[str, str]:
    detail = f"HTTP {code}"
    if code in _AUTH_STATUSES:
        return NEEDS_AUTH, detail
    if 200 <= code < 300:
        return HEALTHY, detail
    if code in _PROTOCOL_MISMATCH_STATUSES:
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
        with urllib_request.urlopen(request, timeout=PROBE_TIMEOUT_S) as response:
            return _status_result(int(getattr(response, "status", None) or response.getcode()))
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


def _run_check(name: str, cfg: dict) -> None:
    try:
        try:
            health, detail = probe_server(name, cfg)
        except Exception:
            logger.debug("MCP health check for %r failed", name, exc_info=True)
            health, detail = UNKNOWN, "health check failed"
        with _LOCK:
            _STATE[name] = {"health": health, "detail": detail, "checked_at": time.time()}
    finally:
        with _LOCK:
            _IN_FLIGHT.discard(name)


def refresh_async(servers: dict) -> None:
    """Schedule background probes for ``servers`` and return immediately.

    ``servers`` is the set of servers that *should* be checked — the caller has
    already dropped disabled ones. State for anything outside that set is
    discarded, so toggling a server off both stops checking it and clears its
    stale verdict.
    """
    if not isinstance(servers, dict):
        servers = {}
    now = time.monotonic()
    due: list[tuple[str, dict]] = []
    with _LOCK:
        wanted = {str(name) for name in servers}
        for stale in set(_STATE) - wanted:
            _STATE.pop(stale, None)
        for stale in set(_STARTED_AT) - wanted:
            _STARTED_AT.pop(stale, None)
        for raw_name, cfg in servers.items():
            name = str(raw_name)
            if name in _IN_FLIGHT:
                continue
            started = _STARTED_AT.get(name)
            if started is not None and (now - started) < HEALTH_INTERVAL_S:
                continue
            _IN_FLIGHT.add(name)
            _STARTED_AT[name] = now
            due.append((name, cfg if isinstance(cfg, dict) else {}))
    for name, cfg in due:
        try:
            threading.Thread(
                target=_run_check,
                args=(name, cfg),
                name=f"mcp-health-{name}"[:60],
                daemon=True,
            ).start()
        except Exception:
            logger.debug("could not start MCP health thread for %r", name, exc_info=True)
            with _LOCK:
                _IN_FLIGHT.discard(name)


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
