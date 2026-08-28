import base64
import hashlib
import json
from contextlib import contextmanager

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat

from api.talaria_relay import RelayConfig, TalariaRelayPublisher


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
