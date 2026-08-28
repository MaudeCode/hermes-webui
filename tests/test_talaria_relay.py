import base64
import hashlib
import json
import logging
import os
import stat
import urllib.error
from contextlib import contextmanager
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from api.talaria_relay import RelayConfig, RelayPairingError, TalariaRelayPublisher, pair_talaria_relay


def test_pairing_persists_private_key_and_starts_publisher(tmp_path, monkeypatch):
    from api import config, talaria_relay

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_TALARIA_RELAY_URL", "https://relay.example.com")
    configured = []
    monkeypatch.setattr(talaria_relay, "configure_talaria_relay_publisher", configured.append)
    captured = {}

    @contextmanager
    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        yield type(
            "Response",
            (),
            {
                "status": 201,
                "read": lambda self: b'{"keyId":"key-created","publisherId":"https://hermes.example.com"}',
            },
        )()

    result = pair_talaria_relay(
        {
            "relay_url": "https://relay.example.com/",
            "publisher_id": "https://hermes.example.com",
            "publisher_invitation": "invite-once",
            "label": "Home Hermes",
        },
        opener=opener,
    )

    request_body = json.loads(captured["request"].data)
    saved = json.loads((tmp_path / "talaria-relay.json").read_text())
    key_path = Path(saved["private_key_path"])
    assert result == {"ok": True, "publisher_id": "https://hermes.example.com"}
    assert captured["request"].full_url == "https://relay.example.com/v1/pairings/publisher/redeem"
    assert captured["timeout"] == 10
    assert request_body["invitation"] == "invite-once"
    assert len(base64.urlsafe_b64decode(request_body["publicKey"] + "==")) == 32
    assert saved["key_id"] == "key-created"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert configured == [RelayConfig("https://relay.example.com", "https://hermes.example.com", "key-created", key_path)]


def test_pairing_private_key_is_0600_before_any_post_write_failure(tmp_path, monkeypatch):
    from api import config, talaria_relay

    monkeypatch.setattr(config, "STATE_DIR", tmp_path)
    monkeypatch.setenv("HERMES_WEBUI_TALARIA_RELAY_URL", "https://relay.example.com")
    monkeypatch.setattr(
        talaria_relay,
        "configure_talaria_relay_publisher",
        lambda _config: (_ for _ in ()).throw(RuntimeError("startup failed")),
    )
    monkeypatch.setattr(
        type(tmp_path),
        "chmod",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("late chmod")),
    )

    @contextmanager
    def opener(_request, timeout):
        assert timeout == 10
        yield type(
            "Response",
            (),
            {
                "status": 201,
                "read": lambda self: b'{"keyId":"key-created","publisherId":"https://hermes.example.com"}',
            },
        )()

    previous_umask = os.umask(0)
    try:
        with pytest.raises(RuntimeError, match="startup failed"):
            pair_talaria_relay(
                {
                    "relay_url": "https://relay.example.com",
                    "publisher_id": "https://hermes.example.com",
                    "publisher_invitation": "invite-once",
                },
                opener=opener,
            )
    finally:
        os.umask(previous_umask)

    saved = json.loads((tmp_path / "talaria-relay.json").read_text())
    assert stat.S_IMODE(Path(saved["private_key_path"]).stat().st_mode) == 0o600


def test_pairing_rejects_an_untrusted_relay_origin():
    with pytest.raises(RelayPairingError, match="Untrusted"):
        pair_talaria_relay({
            "relay_url": "https://internal.example.com",
            "publisher_id": "https://hermes.example.com",
            "publisher_invitation": "invite-once",
        })


def test_pairing_confirms_initial_snapshot_before_switching_publishers(monkeypatch):
    from api import session_events, talaria_relay

    events = []

    class Candidate:
        changed = object()

        def __init__(self, config):
            events.append(("create", config.key_id))

        def publish_snapshot(self):
            events.append(("publish", None))

        def start(self, *, publish_initial):
            events.append(("start", publish_initial))

    monkeypatch.setattr(talaria_relay, "TalariaRelayPublisher", Candidate)
    monkeypatch.setattr(talaria_relay, "stop_talaria_relay_publisher", lambda: events.append(("stop", None)))
    monkeypatch.setattr(session_events, "add_session_list_changed_listener", lambda _listener: events.append(("listen", None)))
    monkeypatch.setattr(talaria_relay.atexit, "register", lambda _callback: None)

    talaria_relay.configure_talaria_relay_publisher(
        RelayConfig("https://relay.example", "https://hermes.example", "key-1", Path("key.pem"))
    )

    assert events == [
        ("create", "key-1"),
        ("publish", None),
        ("stop", None),
        ("listen", None),
        ("start", False),
    ]


def test_profile_bound_session_cannot_pair_relay(monkeypatch):
    from api import auth, routes

    monkeypatch.setattr(auth, "ensure_trusted_auth_session", lambda _handler: {"bound_profile": "work"})
    handler = type("Handler", (), {"headers": {}})()
    responses = []
    monkeypatch.setattr(routes, "bad", lambda _handler, message, status: responses.append((status, message)) or True)
    monkeypatch.setattr(routes, "_check_csrf", lambda _handler: True)
    monkeypatch.setattr(routes, "_handle_extension_sidecar_proxy", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(routes, "read_body", lambda _handler: {})
    monkeypatch.setattr(routes, "_guard_request_session_visibility", lambda *_args, **_kwargs: True)

    assert routes.handle_post(handler, type("Parsed", (), {"path": "/api/talaria/relay/pair"})()) is True
    assert responses == [(403, "Talaria Relay pairing requires an operator session")]


def test_signed_snapshot_contains_all_active_sessions(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    captured = {}

    @contextmanager
    def opener(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        yield type("Response", (), {"status": 200})()

    from api import config
    from api import models

    monkeypatch.setattr(models, "get_session", lambda sid, metadata_only: type("S", (), {"title": f"Title {sid}"})())
    with config.ACTIVE_RUNS_LOCK:
        previous = dict(config.ACTIVE_RUNS)
        config.ACTIVE_RUNS.clear()
        config.ACTIVE_RUNS.update({
            "stream-a": {"stream_id": "stream-a", "session_id": "a", "started_at": 1, "phase": "running"},
            "stream-b": {"stream_id": "stream-b", "session_id": "b", "started_at": 2, "phase": "starting"},
        })
    try:
        publisher = TalariaRelayPublisher(
            RelayConfig("https://relay.example", "publisher/id", "key-1", key_path),
            opener=opener,
        )
        publisher.publish_snapshot()
    finally:
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()
            config.ACTIVE_RUNS.update(previous)

    request = captured["request"]
    body = json.loads(request.data)
    assert request.full_url == "https://relay.example/v1/publishers/publisher%2Fid/snapshot"
    assert captured["timeout"] == 10
    assert {state["sessionId"] for state in body["states"]} == {"a", "b"}
    assert next(state for state in body["states"] if state["sessionId"] == "b")["phase"] == "starting"
    timestamp = request.headers["X-talaria-timestamp"]
    nonce = request.headers["X-talaria-nonce"]
    body_hash = base64.urlsafe_b64encode(hashlib.sha256(request.data).digest()).rstrip(b"=").decode()
    signature = request.headers["X-talaria-signature"]
    signature += "=" * (-len(signature) % 4)
    key.public_key().verify(
        base64.urlsafe_b64decode(signature),
        f"PUT\n/v1/publishers/publisher%2Fid/snapshot\n{timestamp}\n{nonce}\n{body_hash}".encode(),
    )


def test_terminal_state_survives_active_run_teardown(tmp_path, monkeypatch):
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    from api import config, models
    monkeypatch.setattr(models, "get_session", lambda sid, metadata_only: type("S", (), {"title": "Done"})())
    publisher = TalariaRelayPublisher(RelayConfig("https://relay.example", "publisher", "key", key_path))
    with config.ACTIVE_RUNS_LOCK:
        previous = dict(config.ACTIVE_RUNS)
        config.ACTIVE_RUNS.clear()
        config.ACTIVE_RUNS["stream"] = {"stream_id": "stream", "session_id": "session", "started_at": 1}
    try:
        publisher.note_terminal("stream", "completed")
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()
        states = publisher.build_states()
    finally:
        with config.ACTIVE_RUNS_LOCK:
            config.ACTIVE_RUNS.clear()
            config.ACTIVE_RUNS.update(previous)
    assert [(state["sessionId"], state["phase"]) for state in states] == [("session", "completed")]
    restarted = TalariaRelayPublisher(RelayConfig("https://relay.example", "publisher", "key", key_path))
    assert restarted._next_revision() > states[0]["revision"]


def test_publisher_stops_after_permanent_http_failure(tmp_path, caplog):
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    attempts = 0

    def opener(request, timeout):
        nonlocal attempts
        attempts += 1
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    publisher = TalariaRelayPublisher(
        RelayConfig("https://relay.example", "publisher", "key", key_path),
        opener=opener,
    )
    publisher.changed()
    with caplog.at_level(logging.WARNING, logger="api.talaria_relay"):
        publisher._run()

    assert attempts == 1
    assert "stopped after permanent HTTP 401" in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


def test_publisher_retries_transient_failures_with_capped_backoff(tmp_path, monkeypatch, caplog):
    key = Ed25519PrivateKey.generate()
    key_path = tmp_path / "publisher.pem"
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    attempts = 0

    class Stop:
        stopped = False

        def __init__(self):
            self.waits = []

        def is_set(self):
            return self.stopped

        def wait(self, delay):
            self.waits.append(delay)
            return False

        def set(self):
            self.stopped = True

    stop = Stop()

    @contextmanager
    def opener(request, timeout):
        nonlocal attempts
        attempts += 1
        if attempts < 9:
            raise urllib.error.HTTPError(request.full_url, 503, "Unavailable", {}, None)
        stop.set()
        yield type("Response", (), {"status": 200})()

    publisher = TalariaRelayPublisher(
        RelayConfig("https://relay.example", "publisher", "key", key_path),
        opener=opener,
    )
    publisher._stop = stop
    monkeypatch.setattr("api.talaria_relay.random.uniform", lambda _low, _high: 1.2)
    publisher.changed()
    with caplog.at_level(logging.DEBUG, logger="api.talaria_relay"):
        publisher._run()

    assert attempts == 9
    assert stop.waits == [6, 12, 24, 48, 96, 192, 300, 300]
    assert caplog.text.count("Talaria relay snapshot failed") == 1
    assert "Talaria relay snapshot recovered" in caplog.text
