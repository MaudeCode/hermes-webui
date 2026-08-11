"""Logging boundaries for dependencies embedded in the WebUI process."""

from __future__ import annotations

import logging


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
