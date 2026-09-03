import io
import json
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import api.models as models
import api.profiles as profiles
import api.routes as routes
import api.streaming as streaming
import pytest


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


@pytest.mark.parametrize(
    (
        "home_override_installed",
        "skill_modules_dynamic",
        "profile_is_named",
        "secret_scope_installed",
        "expected",
    ),
    [
        (True, True, True, True, False),
        (True, True, False, False, False),
        (True, True, True, False, True),
        (True, False, True, True, True),
        (False, True, True, True, True),
    ],
)
def test_streaming_process_env_fallback_is_limited_to_legacy_capabilities(
    home_override_installed,
    skill_modules_dynamic,
    profile_is_named,
    secret_scope_installed,
    expected,
):
    assert streaming._streaming_requires_process_env_fallback(
        home_override_installed=home_override_installed,
        skill_modules_dynamic=skill_modules_dynamic,
        profile_is_named=profile_is_named,
        secret_scope_installed=secret_scope_installed,
    ) is expected


@pytest.mark.parametrize(
    "terminal_kwargs",
    [
        {"terminal_context_installed": False},
        {"terminal_process_env_required": True},
    ],
)
def test_streaming_process_env_fallback_requires_safe_terminal_context(
    terminal_kwargs,
):
    assert streaming._streaming_requires_process_env_fallback(
        home_override_installed=True,
        skill_modules_dynamic=True,
        profile_is_named=True,
        secret_scope_installed=True,
        **terminal_kwargs,
    ) is True


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
    monkeypatch.setattr(models, "get_last_workspace", lambda **_kwargs: tmp_path)
    monkeypatch.setattr(models.Session, "load_metadata_only", lambda _sid: None)
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    models.clear_cli_sessions_cache()
    routes._session_list_cache_clear()


@pytest.mark.parametrize("member_home_resolves", [False, True])
def test_talaria_session_list_never_reads_owner_home(
    monkeypatch, tmp_path, member_home_resolves
):
    import api.auth as auth
    import api.auth_oidc as auth_oidc

    owner_home = tmp_path / "owner-default"
    member_home = tmp_path / "profiles" / "member"
    member_home.mkdir(parents=True)
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
    if member_home_resolves:
        monkeypatch.setattr(
            profiles, "get_hermes_home_for_profile", lambda _profile: member_home
        )
    else:
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
    if member_home_resolves:
        assert load_calls == [(member_home, member_home / "state.db", "member")]
    else:
        assert load_calls == [], "a failed member lookup must never read the owner database"


def test_stale_session_list_cache_rebuild_pins_captured_profile(monkeypatch):
    captured = []
    rebuilt = threading.Event()

    def fake_get_cli_sessions(
        source_filter=None,
        *,
        all_profiles=False,
        include_claude_code=True,
        profile=None,
    ):
        captured.append(profile)
        rebuilt.set()
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
    monkeypatch.setattr(routes, "_session_list_cache_source_stamp", lambda _key: ("stable",))
    routes._session_list_cache_clear()

    key = routes._session_list_cache_key(
        active_profile="member",
        all_profiles=False,
        show_cli_sessions=True,
        show_previous_messaging_sessions=False,
        show_cron_sessions=True,
        show_claude_code_sessions=True,
        show_webhook_sessions=True,
        visible_only=True,
        request_visibility_overrides=True,
    )
    stale = {"sessions": [], "active_profile": "member", "all_profiles": False}
    routes._session_list_cache_set(key, stale)
    with routes._SESSIONS_CACHE_LOCK:
        _timestamp, stamp, payload = routes._SESSIONS_CACHE[key]
        routes._SESSIONS_CACHE[key] = (time.monotonic() - 60, stamp, payload)

    def rebuild():
        return routes._build_session_list_cache_payload(
            active_profile="member",
            all_profiles=False,
            show_cli_sessions=True,
            show_previous_messaging_sessions=False,
            show_cron_sessions=True,
            show_claude_code_sessions=True,
            show_webhook_sessions=True,
            visible_only=True,
            request_visibility_overrides=True,
        )

    try:
        returned = routes._get_cached_session_list_payload(key=key, builder=rebuild)
        assert returned == stale
        assert rebuilt.wait(2), "stale cache did not rebuild"
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with routes._SESSIONS_CACHE_LOCK:
                if key not in routes._SESSIONS_CACHE_INFLIGHT:
                    break
            time.sleep(0.01)
        with routes._SESSIONS_CACHE_LOCK:
            assert key not in routes._SESSIONS_CACHE_INFLIGHT
        rebuilt_payload, fresh = routes._session_list_cache_get(key)
        assert fresh is True
        assert [row["session_id"] for row in rebuilt_payload["sessions"]] == [
            "member-session"
        ]
    finally:
        routes._session_list_cache_clear()

    assert captured == ["member"]


def test_get_cli_sessions_rejects_unknown_explicit_profile(monkeypatch):
    monkeypatch.setattr(profiles, "_is_root_profile", lambda name: name == "default")
    monkeypatch.setattr(
        models,
        "_load_cli_sessions_uncached",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("profile failure reached the database reader")
        ),
    )

    assert models.get_cli_sessions(profile=None) == []
    assert models.get_cli_sessions(profile="../owner") == []


def test_detached_cli_projection_scopes_ambient_helpers(monkeypatch, tmp_path):
    member_home = tmp_path / "profiles" / "member"
    member_home.mkdir(parents=True)
    _isolate_cli_projection(monkeypatch, tmp_path)
    monkeypatch.setattr(profiles, "_active_profile", "default")
    monkeypatch.setattr(
        profiles, "get_hermes_home_for_profile", lambda _profile: member_home
    )

    observed_profiles = []

    def fake_loader(*_args, **_kwargs):
        observed_profiles.append(profiles.get_active_profile_name())
        return []

    monkeypatch.setattr(models, "_load_cli_sessions_uncached", fake_loader)

    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(
            models.get_cli_sessions,
            profile="member",
            include_claude_code=False,
        ).result()

    assert result == []
    assert observed_profiles == ["member"]


def test_detached_cli_projection_does_not_wait_for_profile_env_lock(
    monkeypatch, tmp_path
):
    member_home = tmp_path / "profiles" / "member"
    member_home.mkdir(parents=True)
    _isolate_cli_projection(monkeypatch, tmp_path)
    monkeypatch.setattr(
        profiles, "get_hermes_home_for_profile", lambda _profile: member_home
    )

    observed_profiles = []
    completed = threading.Event()

    def fake_loader(*_args, **_kwargs):
        observed_profiles.append(profiles.get_active_profile_name())
        return []

    monkeypatch.setattr(models, "_load_cli_sessions_uncached", fake_loader)

    def load():
        try:
            models.get_cli_sessions(profile="member", include_claude_code=False)
        finally:
            completed.set()

    profiles._PROFILE_ENV_SCOPE_LOCK.acquire()
    worker = threading.Thread(target=load)
    worker.start()
    try:
        completed_while_locked = completed.wait(0.1)
    finally:
        profiles._PROFILE_ENV_SCOPE_LOCK.release()
        worker.join(2)

    assert completed_while_locked is True
    assert worker.is_alive() is False
    assert observed_profiles == ["member"]


def test_native_bootstrap_routes_stay_responsive_while_profile_env_is_busy(
    monkeypatch, tmp_path
):
    import api.config as config
    from concurrent.futures import wait

    member_home = tmp_path / "profiles" / "member"
    process_home = tmp_path / "process-home"
    member_home.mkdir(parents=True)
    monkeypatch.setenv("HERMES_HOME", str(process_home))
    _isolate_cli_projection(monkeypatch, tmp_path)
    monkeypatch.setattr(profiles, "get_active_hermes_home", lambda: member_home)
    monkeypatch.setattr(
        profiles, "get_hermes_home_for_profile", lambda _profile: member_home
    )
    monkeypatch.setattr(models, "_load_cli_sessions_uncached", lambda *_a, **_k: [])
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
    monkeypatch.setattr(
        profiles,
        "list_profiles_api",
        lambda: [{"name": "default"}, {"name": "member"}],
    )
    routes._ensure_agent_cron_import_path()
    import cron.jobs as cron_jobs

    def list_jobs(*, include_disabled=False):
        assert include_disabled is True
        assert cron_jobs.get_cron_output_dir() == member_home / "cron" / "output"
        return [
            {
                "id": "member-job",
                "name": "Member job",
                "last_run_at": "2026-09-03T18:00:00Z",
                "last_status": "success",
            }
        ]

    monkeypatch.setattr(cron_jobs, "list_jobs", list_jobs)

    config_path = tmp_path / "config.yaml"
    config_path.write_text("{}", encoding="utf-8")
    cache_path = tmp_path / "models_cache.member.json"
    cache_path.write_text("{}", encoding="utf-8")
    stale_catalog = {
        "active_provider": None,
        "default_model": "",
        "groups": [],
        "aliases": {},
    }
    monkeypatch.setattr(config, "_LIVE_REBUILD_BUDGET_SECONDS", 0.02)
    monkeypatch.setattr(config, "_available_models_cache", None)
    monkeypatch.setattr(config, "_available_models_cache_ts", 0.0)
    monkeypatch.setattr(config, "_available_models_cache_source_fingerprint", None)
    monkeypatch.setattr(config, "_cache_build_in_progress", True)
    monkeypatch.setattr(config, "_get_config_path", lambda: config_path)
    monkeypatch.setattr(config, "_cfg_path", config_path)
    monkeypatch.setattr(config, "_cfg_mtime", config_path.stat().st_mtime)
    monkeypatch.setattr(config, "_get_models_cache_path", lambda: cache_path)
    monkeypatch.setattr(config, "_load_models_cache_from_disk", lambda: None)
    monkeypatch.setattr(
        config, "_load_stale_models_cache_from_disk", lambda: stale_catalog
    )

    holder_ready = threading.Event()
    release_holder = threading.Event()

    def hold_profile_env():
        with profiles._PROFILE_ENV_SCOPE_LOCK:
            holder_ready.set()
            release_holder.wait(2)

    def get(path):
        profiles.set_request_profile("member")
        try:
            handler = _Handler()
            routes.handle_get(handler, urlparse(f"http://example.test{path}"))
            return handler.status, handler.json_body()
        finally:
            profiles.clear_request_profile()

    holder = threading.Thread(target=hold_profile_env)
    holder.start()
    assert holder_ready.wait(2)
    paths = [
        "/api/sessions?show_cli_sessions=1&show_claude_code_sessions=1"
        "&show_cron_sessions=1&show_webhook_sessions=1",
        "/api/models",
        "/api/profiles",
        "/api/crons",
        "/api/crons/output?job_id=test",
        "/api/crons/history?job_id=test",
        "/api/crons/run?job_id=test&filename=missing.md",
        "/api/crons/recent?since=0",
        "/api/crons/recent?since=0",
        "/api/crons/recent?since=0",
        "/api/crons/recent?since=0",
        "/api/crons/status",
        "/api/crons/delivery-options",
        "/health",
    ]
    with ThreadPoolExecutor(max_workers=len(paths)) as executor:
        futures = [executor.submit(get, path) for path in paths]
        finished, unfinished = wait(futures, timeout=0.5)
        release_holder.set()
        responses = [future.result(timeout=2) for future in futures]
    holder.join(2)

    assert unfinished == set()
    assert len(finished) == len(paths)
    assert [status for status, _body in responses] == [
        200, 200, 200, 200, 200, 200, 404, 200, 200, 200, 200, 200, 200, 200
    ]
    assert all(
        response[1]["completions"][0]["job_id"] == "member-job"
        for response in responses[7:11]
    )
    assert os.environ["HERMES_HOME"] == str(process_home)
    assert holder.is_alive() is False


@pytest.mark.parametrize(
    "default_scope_name",
    [
        "profile_env_for_background_worker",
        "profile_env_for_active_request",
        "profile_env_for_active_request_readonly",
    ],
)
def test_default_environment_scope_waits_for_named_profile_scope(
    monkeypatch, tmp_path, default_scope_name
):
    member_home = tmp_path / "profiles" / "member"
    member_home.mkdir(parents=True)
    monkeypatch.setattr(
        profiles, "get_hermes_home_for_profile", lambda _profile: member_home
    )
    monkeypatch.setattr(profiles, "get_profile_runtime_env", lambda _home: {})
    monkeypatch.setattr(
        profiles, "filter_runtime_env_for_gateway_parity", lambda env: env
    )
    monkeypatch.setattr(profiles, "_profile_secret_env_names", lambda _home: set())
    monkeypatch.setattr(profiles, "get_active_profile_name", lambda: "default")

    named_entered = threading.Event()
    release_named = threading.Event()
    default_entered = threading.Event()

    def named_worker():
        with profiles.profile_env_for_background_worker(
            "member", "named holder", scope_skill_modules=False
        ):
            named_entered.set()
            assert release_named.wait(2)

    def default_worker():
        default_scope = getattr(profiles, default_scope_name)
        if default_scope_name == "profile_env_for_background_worker":
            context = default_scope(
                "default", "default waiter", scope_skill_modules=False
            )
        else:
            context = default_scope("default waiter")
        with context:
            default_entered.set()

    named = threading.Thread(target=named_worker)
    default = threading.Thread(target=default_worker)
    named.start()
    assert named_entered.wait(2)
    default.start()
    assert default_entered.wait(0.1) is False
    release_named.set()
    assert default_entered.wait(2)
    named.join(2)
    default.join(2)
    assert named.is_alive() is False
    assert default.is_alive() is False


def test_explicit_profile_cli_caches_do_not_cross_under_concurrency(monkeypatch, tmp_path):
    owner_home = tmp_path / "owner-default"
    member_home = tmp_path / "profiles" / "member"
    _make_state_db(owner_home, "owner-session")
    _make_state_db(member_home, "member-session")
    _isolate_cli_projection(monkeypatch, tmp_path)

    homes = {"default": owner_home, "member": member_home}
    def active_profile():
        profile = getattr(profiles._tls, "profile", None)
        if profile:
            return profile
        raise AssertionError("explicit profile was ignored")

    monkeypatch.setattr(profiles, "get_active_profile_name", active_profile)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", homes.__getitem__)

    loads = []
    active_loads = 0
    max_active_loads = 0
    load_lock = threading.Lock()
    original_loader = models._load_cli_sessions_uncached

    def recording_loader(home, db_path, profile, *args, **kwargs):
        nonlocal active_loads, max_active_loads
        with load_lock:
            loads.append((Path(home), Path(db_path), profile))
            active_loads += 1
            max_active_loads = max(max_active_loads, active_loads)
        try:
            time.sleep(0.01)
            return original_loader(home, db_path, profile, *args, **kwargs)
        finally:
            with load_lock:
                active_loads -= 1

    monkeypatch.setattr(models, "_load_cli_sessions_uncached", recording_loader)

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
    assert max_active_loads >= 2

    expected_loads = {
        (owner_home, owner_home / "state.db", "default"),
        (member_home, member_home / "state.db", "member"),
    }
    assert set(loads) == expected_loads

    cached_load_count = len(loads)
    assert load("default") == {"owner-session-0", "owner-session-1"}
    assert load("member") == {"member-session-0", "member-session-1"}
    assert len(loads) == cached_load_count

    monkeypatch.setattr(models, "_CLI_SESSIONS_CACHE_TTL_SECONDS", 0.0)
    models.clear_cli_sessions_cache()
    assert load("default") == {"owner-session-0", "owner-session-1"}
    assert load("member") == {"member-session-0", "member-session-1"}


def test_concurrent_cli_projection_uses_profile_owned_workspace_snapshot(
    monkeypatch, tmp_path
):
    import api.config as config
    import api.workspace as workspace

    homes = {
        "alpha": tmp_path / "profiles" / "alpha",
        "beta": tmp_path / "profiles" / "beta",
    }
    workspaces = {
        name: tmp_path / f"{name}-workspace" for name in homes
    }
    for name, home in homes.items():
        _make_state_db(home, f"{name}-session")
        workspaces[name].mkdir()
        (home / "config.yaml").write_text(
            f"workspace: {workspaces[name]}\n", encoding="utf-8"
        )
    _isolate_cli_projection(monkeypatch, tmp_path)
    monkeypatch.setattr(models, "get_last_workspace", workspace.get_last_workspace)
    monkeypatch.setattr(profiles, "get_hermes_home_for_profile", homes.__getitem__)
    monkeypatch.setattr(workspace, "_GLOBAL_LW_FILE", tmp_path / "missing-global")
    monkeypatch.setattr(models, "_CLI_SESSIONS_CACHE_TTL_SECONDS", 0.0)

    shared_config = {}
    readers = threading.Barrier(2)

    def racing_get_config():
        name = profiles.get_active_profile_name()
        shared_config.clear()
        shared_config["workspace"] = str(workspaces[name])
        readers.wait(2)
        return shared_config

    monkeypatch.setattr(config, "get_config", racing_get_config)

    def load(name):
        return models.get_cli_sessions(profile=name, include_claude_code=False)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = dict(
            zip(homes, executor.map(load, homes), strict=True)
        )

    for name, rows in results.items():
        assert {row["workspace"] for row in rows} == {str(workspaces[name])}
