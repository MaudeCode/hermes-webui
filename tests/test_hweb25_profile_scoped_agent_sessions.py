import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import api.models as models
import api.profiles as profiles
import api.routes as routes


class _Handler:
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


def _make_state_db(home, prefix):
    home.mkdir(parents=True)
    db = home / "state.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            source TEXT,
            session_source TEXT,
            title TEXT,
            model TEXT,
            started_at REAL NOT NULL,
            message_count INTEGER DEFAULT 0,
            parent_session_id TEXT,
            ended_at REAL,
            end_reason TEXT
        );
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            session_id TEXT,
            role TEXT,
            content TEXT,
            timestamp REAL
        );
        """
    )
    for index in range(2):
        sid = f"{prefix}-{index}"
        conn.execute(
            "INSERT INTO sessions VALUES (?, 'tui', NULL, ?, 'test-model', ?, 2, NULL, NULL, NULL)",
            (sid, f"{prefix} private {index}", float(index + 1)),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, 'user', 'private', ?)",
            (f"{sid}-user", sid, float(index + 1)),
        )
        conn.execute(
            "INSERT INTO messages VALUES (?, ?, 'assistant', 'private', ?)",
            (f"{sid}-assistant", sid, float(index + 2)),
        )
    conn.commit()
    conn.close()
    return db


def _isolate_cli_projection(monkeypatch, tmp_path):
    monkeypatch.setattr(models, "get_claude_code_sessions", lambda: [])
    monkeypatch.setattr(models, "get_last_workspace", lambda: tmp_path)
    monkeypatch.setattr(models.Session, "load_metadata_only", lambda _sid: None)
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    models.clear_cli_sessions_cache()
    routes._session_list_cache_clear()


def test_talaria_session_list_fails_closed_when_member_home_cannot_resolve(
    monkeypatch, tmp_path
):
    import api.auth as auth
    import api.auth_oidc as auth_oidc

    owner_home = tmp_path / "owner-default"
    _make_state_db(owner_home, "owner-session")
    _isolate_cli_projection(monkeypatch, tmp_path)

    monkeypatch.setenv("HERMES_HOME", str(owner_home))
    monkeypatch.setattr(auth, "is_auth_enabled", lambda: True)
    monkeypatch.setattr(auth_oidc, "oidc_session_binding_is_current", lambda _info: True)
    monkeypatch.setattr(
        profiles,
        "get_active_hermes_home",
        lambda: (_ for _ in ()).throw(RuntimeError("profile lookup failed")),
    )
    monkeypatch.setattr(
        profiles,
        "get_hermes_home_for_profile",
        lambda _profile: (_ for _ in ()).throw(RuntimeError("profile lookup failed")),
    )
    monkeypatch.setattr(routes, "all_sessions", lambda diag=None, **_kwargs: [])
    monkeypatch.setattr(
        routes,
        "load_settings",
        lambda: {
            "show_cli_sessions": False,
            "show_claude_code_sessions": False,
            "show_cron_sessions": False,
            "show_webhook_sessions": False,
            "show_kanban_sessions": False,
            "api_redact_enabled": False,
        },
    )

    load_calls = []
    original_loader = models._load_cli_sessions_uncached

    def recording_loader(home, db_path, profile, *args, **kwargs):
        load_calls.append((home, db_path, profile))
        return original_loader(home, db_path, profile, *args, **kwargs)

    monkeypatch.setattr(models, "_load_cli_sessions_uncached", recording_loader)

    parsed = urlparse(
        "http://example.test/api/sessions"
        "?show_cli_sessions=1&show_claude_code_sessions=1"
        "&show_cron_sessions=1&show_webhook_sessions=1"
    )
    cookie = auth.create_session(
        auth_type="oidc",
        username="member@example.test",
        bound_profile="member",
        oidc_binding={
            "mapping_fingerprint": "mapping-fingerprint",
            "profile_identity": "profile-identity",
        },
    )
    handler = _Handler()
    handler.headers["Cookie"] = f"{auth.COOKIE_NAME}={cookie}"

    try:
        assert auth.check_auth(handler, parsed) is True
        routes.handle_get(handler, parsed)
    finally:
        auth.invalidate_session(cookie)
        profiles.clear_request_profile()

    assert handler.status == 200
    assert handler.json_body()["active_profile"] == "member"
    assert handler.json_body()["sessions"] == []
    assert load_calls == [], "a failed member lookup must never read the owner database"


def test_background_session_list_builder_pins_cli_lookup_to_captured_profile(monkeypatch):
    captured = []

    def fake_get_cli_sessions(
        source_filter=None,
        *,
        all_profiles=False,
        include_claude_code=True,
        profile=None,
    ):
        captured.append(profile)
        return [
            {
                "session_id": "member-session",
                "profile": "member",
                "title": "Member session",
                "message_count": 1,
            }
        ]

    monkeypatch.setattr(routes, "all_sessions", lambda diag=None, **_kwargs: [])
    monkeypatch.setattr(routes, "get_cli_sessions", fake_get_cli_sessions)
    monkeypatch.setattr(routes, "_enrich_sidebar_lineage_metadata", lambda _rows: None)

    with ThreadPoolExecutor(max_workers=1) as executor:
        payload = executor.submit(
            routes._build_session_list_cache_payload,
            active_profile="member",
            all_profiles=False,
            show_cli_sessions=True,
            show_previous_messaging_sessions=False,
            show_cron_sessions=True,
            show_claude_code_sessions=True,
            show_webhook_sessions=True,
            visible_only=True,
            request_visibility_overrides=True,
        ).result()

    assert captured == ["member"]
    assert [row["session_id"] for row in payload["sessions"]] == ["member-session"]


def test_explicit_profile_cli_caches_do_not_cross_under_concurrency(monkeypatch, tmp_path):
    owner_home = tmp_path / "owner-default"
    member_home = tmp_path / "profiles" / "member"
    _make_state_db(owner_home, "owner-session")
    _make_state_db(member_home, "member-session")
    _isolate_cli_projection(monkeypatch, tmp_path)

    homes = {"default": owner_home, "member": member_home}
    monkeypatch.setattr(
        profiles,
        "get_active_profile_name",
        lambda: (_ for _ in ()).throw(AssertionError("explicit profile was ignored")),
    )
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", homes.__getitem__)

    def load(profile):
        return {
            row["session_id"]
            for row in models.get_cli_sessions(
                profile=profile,
                include_claude_code=False,
            )
        }

    requested = ["default", "member"] * 8
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(load, requested))

    for profile, session_ids in zip(requested, results, strict=True):
        prefix = "member" if profile == "member" else "owner"
        assert session_ids == {f"{prefix}-session-0", f"{prefix}-session-1"}
