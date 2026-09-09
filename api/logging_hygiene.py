"""Logging boundaries for dependencies embedded in the WebUI process."""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)


# Hermes Agent 2026.8.3 can ask a TypeScript language server that does not
# implement textDocument/diagnostic to pull diagnostics for every open file.
# The agent records each expected -32601 response at DEBUG. If any embedded
# component has lowered the process root logger to DEBUG, hundreds of thousands
# of identical records are synchronously written to the WebUI's stderr log and
# can starve unrelated HTTP request threads. WebUI-created agents run in quiet
# mode, so DEBUG from this dependency is never part of the browser contract.
_WEBUI_DEPENDENCY_LOG_FLOORS = {
    "agent.lsp.client": logging.INFO,
}


def install_webui_dependency_log_floors() -> None:
    """Suppress dependency DEBUG floods without hiding warnings or errors.

    Preserve any stricter operator-configured level. Setting the named logger
    (rather than only filtering a handler) makes ``logger.debug`` return before
    allocating and formatting a ``LogRecord``, which is the important hot-path
    protection when the embedded agent emits thousands of repeats per second.
    """

    for logger_name, floor in _WEBUI_DEPENDENCY_LOG_FLOORS.items():
        dependency_logger = logging.getLogger(logger_name)
        configured_level = dependency_logger.level
        if configured_level == logging.NOTSET or configured_level < floor:
            dependency_logger.setLevel(floor)


# ── WebUI log rotation ──────────────────────────────────────────────────────
# bootstrap.py starts the server with stdout/stderr redirected into
# ``{STATE_DIR}/bootstrap-{PORT}.log`` at the file-descriptor level, so nothing
# in-process owns that sink and no ``RotatingFileHandler`` can bound it. On a
# server that runs for weeks the log grows without limit (HWEB-45).
_WEBUI_LOG_MAX_BYTES_ENV = "HERMES_WEBUI_LOG_MAX_BYTES"
_WEBUI_LOG_FILE_ENV = "HERMES_WEBUI_LOG_FILE"
_WEBUI_LOG_DEFAULT_MAX_BYTES = 32 * 1024 * 1024


def webui_log_path() -> Path:
    """The file this server's stdout/stderr is redirected into, or ``None``.

    Two launchers, two sinks. ``bootstrap.py``'s detached path writes
    ``{STATE_DIR}/bootstrap-{PORT}.log``; ``ctl.sh start`` — the documented
    daemon path — runs ``bootstrap.py --foreground``, which never creates that
    file, and redirects into ``${HERMES_HOME}/webui.log`` instead. ``ctl.sh``
    exports its resolved path as ``HERMES_WEBUI_LOG_FILE``, so prefer that and
    fall back to the bootstrap sink.
    """
    from api.config import PORT, STATE_DIR

    configured = os.environ.get(_WEBUI_LOG_FILE_ENV, "").strip()
    if configured:
        return Path(configured).expanduser()
    return Path(STATE_DIR) / f"bootstrap-{PORT}.log"


def _webui_log_max_bytes() -> int:
    raw = os.environ.get(_WEBUI_LOG_MAX_BYTES_ENV, str(_WEBUI_LOG_DEFAULT_MAX_BYTES))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return _WEBUI_LOG_DEFAULT_MAX_BYTES


def rotate_webui_log(
    *,
    path: Path | None = None,
    max_bytes: int | None = None,
) -> bool:
    """Copy-truncate the WebUI log once it exceeds its size cap.

    Copy-truncate, not rename: the writers hold an inherited ``O_APPEND``
    descriptor on the open file. Renaming would leave every one of them writing
    into the rotated inode while the new path stayed empty forever. Truncating
    in place is the only rotation a raw inherited descriptor honours — with
    ``O_APPEND`` the kernel recomputes the offset from the file size before each
    write, so the log resumes at zero with no sparse gap.

    One previous generation is kept as ``<log>.1``. Lines written during the
    copy are lost, which is the same trade ``logrotate``'s ``copytruncate``
    makes. Set ``HERMES_WEBUI_LOG_MAX_BYTES`` to ``0`` to disable. Returns
    ``True`` when a rotation happened.
    """
    target = Path(path) if path is not None else webui_log_path()
    limit = _webui_log_max_bytes() if max_bytes is None else int(max_bytes)
    if limit <= 0:
        return False
    try:
        size = target.stat().st_size
    except OSError:
        return False
    if size <= limit:
        return False
    previous = target.with_name(target.name + ".1")
    try:
        with open(target, "rb") as src, open(previous, "wb") as dst:
            shutil.copyfileobj(src, dst)
        os.truncate(target, 0)
    except OSError:
        logger.warning("Could not rotate the WebUI log at %s", target, exc_info=True)
        return False
    logger.info(
        "Rotated the WebUI log at %s (%d bytes) into %s", target, size, previous
    )
    return True
