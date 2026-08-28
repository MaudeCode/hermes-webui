import base64
import hashlib
import json
import stat
from contextlib import contextmanager

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
            {"status": 201, "read": lambda self: b'{"keyId":"key-created"}'},
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
    key_path = tmp_path / "talaria-relay-publisher.pem"
    assert result == {"ok": True, "publisher_id": "https://hermes.example.com"}
    assert captured["request"].full_url == "https://relay.example.com/v1/pairings/publisher/redeem"
    assert captured["timeout"] == 10
    assert request_body["invitation"] == "invite-once"
    assert len(base64.urlsafe_b64decode(request_body["publicKey"] + "==")) == 32
    assert saved["key_id"] == "key-created"
    assert stat.S_IMODE(key_path.stat().st_mode) == 0o600
    assert configured == [RelayConfig("https://relay.example.com", "https://hermes.example.com", "key-created", key_path)]


def test_pairing_rejects_an_untrusted_relay_origin():
    with pytest.raises(RelayPairingError, match="Untrusted"):
        pair_talaria_relay({
            "relay_url": "https://internal.example.com",
            "publisher_id": "https://hermes.example.com",
            "publisher_invitation": "invite-once",
        })


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
