"""Small, provider-neutral reasoning-title fallback for WebUI clients."""

from __future__ import annotations

import json
import re


_BOLD_LINE = re.compile(r"^\s*\*\*(.+?)\*\*\s*$")
_COMMAND_LINE = re.compile(
    r"^(?:\$\s*|sudo\b|git\b|cd\b|ls\b|cat\b|python\b|node\b|curl\b|wget\b|rg\b|grep\b)",
    re.IGNORECASE,
)
_MARKDOWN_PREFIX = re.compile(r"^\s*(?:#{1,6}\s+|[-*+]\s+|\d+[.)]\s+)")


def _clean_title(value: object, *, explicit: bool = False) -> str:
    title = " ".join(str(value or "").split()).strip()
    if not title or len(title) > 400:
        return ""
    title = _MARKDOWN_PREFIX.sub("", title).strip()
    if not title or title.startswith(("```", "~~~", "<", "{", "[")):
        return ""
    if _COMMAND_LINE.match(title):
        return ""
    if not explicit:
        try:
            if isinstance(json.loads(title), (dict, list)):
                return ""
        except (TypeError, ValueError, json.JSONDecodeError):
            pass
    if len(title) <= 80:
        return title
    shortened = title[:81].rsplit(" ", 1)[0].rstrip(" ,.;:-")
    return shortened if len(shortened) >= 24 else ""


def _unique_titles(values: object, *, explicit: bool = False) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        title = _clean_title(value, explicit=explicit)
        key = title.casefold()
        if not title or key in seen:
            continue
        seen.add(key)
        out.append(title)
        if len(out) == 8:
            break
    return out


def normalize_reasoning_titles(
    text: object,
    *,
    explicit_titles: object = None,
    stable: bool = False,
) -> list[str]:
    """Return an ordered title snapshot without guessing from partial prose."""

    explicit = _unique_titles(explicit_titles, explicit=True)
    if explicit:
        return explicit

    raw = str(text or "")
    bold = []
    for line in raw.splitlines():
        match = _BOLD_LINE.match(line)
        if match:
            bold.append(match.group(1))
    titles = _unique_titles(bold)
    if titles:
        return titles

    lines = raw.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        complete = line.endswith(("\n", "\r")) or stable or index < len(lines) - 1
        if not complete:
            return []
        title = _clean_title(line)
        return [title] if title else []
    return []


def reasoning_event_payload(
    delta: object,
    cumulative_text: object,
    *,
    explicit_titles: object = None,
    stable: bool = False,
) -> dict:
    payload = {"text": str(delta or "")}
    titles = normalize_reasoning_titles(
        cumulative_text,
        explicit_titles=explicit_titles,
        stable=stable,
    )
    if titles:
        payload["titles"] = titles
    return payload
