import io
import json
from urllib.parse import urlparse

import api.profiles as profiles
import api.routes as routes


class _FakeHandler:
    def __init__(self):
        self.status = None
        self.headers = {}
        self.wfile = io.BytesIO()

    def send_response(self, status):
        self.status = status

    def send_header(self, key, value):
        self.headers[key] = value

    def end_headers(self):
        pass

    def json_body(self):
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _session(session_id: str, source: str) -> dict:
    return {
        "session_id": session_id,
        "title": session_id,
        "profile": "default",
        "message_count": 2,
        "user_message_count": 1,
        "updated_at": 1,
        "last_message_at": 1,
        "source": source,
        "source_tag": source,
        "raw_source": source,
        "session_source": source,
        "source_label": source.title(),
        "is_cli_session": source == "cli",
    }


def test_request_visibility_overrides_do_not_use_saved_webui_preferences(monkeypatch):
    saved_settings = {
        "show_cli_sessions": False,
        "show_claude_code_sessions": False,
        "show_cron_sessions": False,
        "show_webhook_sessions": False,
        "show_kanban_sessions": False,
    }
    rows = [
        _session("cli-1", "cli"),
        _session("cron-1", "cron"),
        _session("webhook-1", "webhook"),
    ]

    monkeypatch.setattr(routes, "load_settings", lambda: dict(saved_settings))
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: [])
    monkeypatch.setattr(routes, "get_cli_sessions", lambda **_kwargs: list(rows))
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    handler = _FakeHandler()
    routes.handle_get(
        handler,
        urlparse(
            "http://example.test/api/sessions"
            "?show_cli_sessions=0&show_cron_sessions=1&show_webhook_sessions=1"
        ),
    )

    assert handler.status == 200
    assert [row["session_id"] for row in handler.json_body()["sessions"]] == [
        "cron-1",
        "webhook-1",
    ]
    assert saved_settings == {
        "show_cli_sessions": False,
        "show_claude_code_sessions": False,
        "show_cron_sessions": False,
        "show_webhook_sessions": False,
        "show_kanban_sessions": False,
    }


def test_request_without_overrides_keeps_saved_webui_preferences(monkeypatch):
    rows = [
        _session("cli-1", "cli"),
        _session("cron-1", "cron"),
        _session("webhook-1", "webhook"),
    ]

    monkeypatch.setattr(
        routes,
        "load_settings",
        lambda: {
            "show_cli_sessions": True,
            "show_claude_code_sessions": True,
            "show_cron_sessions": False,
            "show_webhook_sessions": False,
            "show_kanban_sessions": False,
        },
    )
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None: [])
    monkeypatch.setattr(routes, "get_cli_sessions", lambda **_kwargs: list(rows))
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    handler = _FakeHandler()
    routes.handle_get(handler, urlparse("http://example.test/api/sessions"))

    assert handler.status == 200
    assert [row["session_id"] for row in handler.json_body()["sessions"]] == ["cli-1"]
