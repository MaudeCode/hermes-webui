"""Best-effort publication of live Hermes session state to Talaria Relay."""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
import os
import queue
import random
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

logger = logging.getLogger(__name__)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class RelayConfig:
    url: str
    publisher_id: str
    key_id: str
    private_key_path: Path

    @staticmethod
    def _validated_origin(value: str, *, https_only: bool = False) -> str:
        value = value.strip().rstrip("/")
        parsed = urllib.parse.urlsplit(value)
        schemes = ("https",) if https_only else ("http", "https")
        if (
            parsed.scheme not in schemes
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            protocol = "HTTPS" if https_only else "HTTP(S)"
            raise ValueError(f"value must be an {protocol} origin")
        return value

    @classmethod
    def from_state(cls) -> "RelayConfig | None":
        path, _ = _state_paths()
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(values, dict):
                raise ValueError("saved relay configuration is not an object")
            return cls(
                url=cls._validated_origin(str(values["url"])),
                publisher_id=cls._validated_origin(str(values["publisher_id"])),
                key_id=str(values["key_id"]),
                private_key_path=Path(str(values["private_key_path"])),
            )
        except FileNotFoundError:
            return None


class RelayPairingError(Exception):
    def __init__(self, message: str, *, status: int):
        super().__init__(message)
        self.status = status


def _state_paths() -> tuple[Path, Path]:
    from api.config import STATE_DIR

    state_dir = Path(STATE_DIR)
    return state_dir / "talaria-relay.json", state_dir / "talaria-relay-publisher.pem"


def pair_talaria_relay(body: object, *, opener=urllib.request.urlopen) -> dict[str, str | bool]:
    if not isinstance(body, dict):
        raise RelayPairingError("Invalid pairing request", status=400)
    relay_url = body.get("relay_url")
    publisher_id = body.get("publisher_id")
    invitation = body.get("publisher_invitation")
    label = body.get("label") or "Hermes WebUI"
    if not all(isinstance(value, str) for value in (relay_url, publisher_id, invitation, label)):
        raise RelayPairingError("Missing relay pairing fields", status=400)
    try:
        relay_url = RelayConfig._validated_origin(relay_url, https_only=True)
        publisher_id = RelayConfig._validated_origin(publisher_id)
    except ValueError as exc:
        raise RelayPairingError(str(exc), status=400) from exc
    allowed_relay_url = os.environ.get(
        "HERMES_WEBUI_TALARIA_RELAY_URL",
        "https://relay.talaria.kil.dev",
    ).strip().rstrip("/")
    if relay_url != allowed_relay_url:
        raise RelayPairingError("Untrusted Talaria Relay origin", status=400)
    if not 1 <= len(invitation) <= 256 or not 1 <= len(label) <= 80:
        raise RelayPairingError("Invalid relay pairing fields", status=400)

    key = Ed25519PrivateKey.generate()
    request = urllib.request.Request(
        relay_url + "/v1/pairings/publisher/redeem",
        data=json.dumps(
            {
                "invitation": invitation,
                "publisherId": publisher_id,
                "label": label,
                "publicKey": _b64url(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
            },
            separators=(",", ":"),
        ).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener(request, timeout=10) as response:
            payload = json.loads(response.read())
            if not 200 <= response.status < 300:
                raise RelayPairingError("Talaria Relay rejected the invitation", status=502)
    except RelayPairingError:
        raise
    except Exception as exc:
        raise RelayPairingError("Could not reach Talaria Relay", status=502) from exc
    key_id = payload.get("keyId") if isinstance(payload, dict) else None
    paired_publisher_id = payload.get("publisherId") if isinstance(payload, dict) else None
    if not isinstance(key_id, str) or not key_id or not isinstance(paired_publisher_id, str):
        raise RelayPairingError("Talaria Relay returned an invalid response", status=502)
    try:
        publisher_id = RelayConfig._validated_origin(paired_publisher_id)
    except ValueError as exc:
        raise RelayPairingError("Talaria Relay returned an invalid response", status=502) from exc

    config_path, _ = _state_paths()
    key_path = config_path.parent / (
        "talaria-relay-publisher-"
        + hashlib.sha256(key_id.encode("utf-8")).hexdigest()[:16]
        + ".pem"
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    from api.paths import _atomic_write_text

    _atomic_write_text(
        key_path,
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()).decode("ascii"),
        file_mode=0o600,
    )
    config = RelayConfig(relay_url, publisher_id, key_id, key_path)
    _atomic_write_text(
        config_path,
        json.dumps(
            {
                "url": config.url,
                "publisher_id": config.publisher_id,
                "key_id": config.key_id,
                "private_key_path": str(config.private_key_path),
            },
            indent=2,
        ) + "\n",
    )
    configure_talaria_relay_publisher(config)
    return {"ok": True, "publisher_id": publisher_id}


class TalariaRelayPublisher:
    """Coalesces state changes into signed complete snapshots."""

    def __init__(self, config: RelayConfig, *, opener=urllib.request.urlopen):
        self.config = config
        self._opener = opener
        self._key = self._load_key(config.private_key_path)
        self._changes: queue.Queue[None] = queue.Queue(maxsize=1)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._revision_lock = threading.Lock()
        self._revision_path = config.private_key_path.parent / "talaria-relay-revision"
        try:
            self._last_revision = int(self._revision_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            self._last_revision = 0
        self._terminal_lock = threading.Lock()
        self._terminal: dict[str, dict] = {}

    @staticmethod
    def _load_key(path: Path) -> Ed25519PrivateKey:
        key = load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Talaria publisher key must be an Ed25519 private key")
        return key

    def start(self, *, publish_initial: bool = True) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="talaria-relay", daemon=True)
        self._thread.start()
        if publish_initial:
            self.changed()

    def stop(self) -> None:
        self._stop.set()
        self.changed()
        if self._thread is not None:
            self._thread.join(timeout=2)

    def changed(self, _profile: str | None = None) -> None:
        try:
            self._changes.put_nowait(None)
        except queue.Full:
            pass

    def note_terminal(self, stream_id: str, phase: str) -> None:
        from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK

        with ACTIVE_RUNS_LOCK:
            run = dict(ACTIVE_RUNS.get(stream_id) or {})
        sid = str(run.get("session_id") or "").strip()
        if not sid:
            return
        run["relay_phase"] = phase
        run["terminal_at"] = time.time()
        with self._terminal_lock:
            self._terminal[sid] = run
        self.changed()

    def _next_revision(self) -> int:
        with self._revision_lock:
            self._last_revision = max(self._last_revision + 1, time.time_ns() // 1_000_000)
            return self._last_revision

    def _run(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                self._changes.get(timeout=1)
            except queue.Empty:
                continue
            if self._stop.is_set():
                return
            try:
                self.publish_snapshot()
            except Exception as exc:
                if isinstance(exc, _RelayHTTPError) and not exc.retryable:
                    logger.error(
                        "Talaria relay publisher stopped after permanent HTTP %s",
                        exc.status,
                    )
                    return
                failures += 1
                delay = min(
                    5 * 2 ** min(failures - 1, 6) * random.uniform(0.8, 1.2),
                    300,
                )
                if failures == 1:
                    logger.warning(
                        "Talaria relay snapshot failed (%s); retrying in %.1fs",
                        exc,
                        delay,
                        exc_info=not isinstance(exc, _RelayHTTPError),
                    )
                else:
                    logger.debug(
                        "Talaria relay snapshot still failing (%s); retrying in %.1fs",
                        exc,
                        delay,
                    )
                if not self._stop.wait(delay):
                    self.changed()
            else:
                if failures:
                    logger.info("Talaria relay snapshot recovered")
                failures = 0

    def build_states(self) -> list[dict]:
        from api.config import ACTIVE_RUNS, ACTIVE_RUNS_LOCK
        from api.models import get_session

        with ACTIVE_RUNS_LOCK:
            runs = [dict(item) for item in ACTIVE_RUNS.values() if isinstance(item, dict)]

        cutoff = time.time() - 15 * 60
        with self._terminal_lock:
            self._terminal = {
                sid: item for sid, item in self._terminal.items()
                if float(item.get("terminal_at") or 0) >= cutoff
            }
            terminal = dict(self._terminal)

        by_session: dict[str, dict] = {}
        for run in runs:
            sid = str(run.get("session_id") or "").strip()
            if not sid:
                continue
            current = by_session.get(sid)
            if current is None or float(run.get("started_at") or 0) > float(current.get("started_at") or 0):
                by_session[sid] = run
        for sid, run in terminal.items():
            by_session.setdefault(sid, run)

        states = []
        for sid, run in by_session.items():
            revision = self._next_revision()
            try:
                title = str(get_session(sid, metadata_only=True).title or "Untitled")[:120]
            except Exception:
                title = "Hermes session"
            phase = str(run.get("relay_phase") or "running")
            try:
                from api.route_approvals import _lock as approval_lock, _pending as approvals
                with approval_lock:
                    pending = approvals.get(sid)
                    if pending and phase not in ("completed", "failed", "cancelled"):
                        phase = "waiting_for_approval"
            except Exception:
                logger.debug("Failed reading approval state for Talaria", exc_info=True)
            if phase == "running":
                try:
                    from api.clarify import has_pending
                    if has_pending(sid):
                        phase = "waiting_for_input"
                except Exception:
                    logger.debug("Failed reading clarify state for Talaria", exc_info=True)
            if phase == "running" and str(run.get("phase") or "").endswith("starting"):
                phase = "starting"
            event_id = f"snapshot:{revision}:{sid}"
            states.append({
                "sessionId": sid,
                "streamId": str(run.get("stream_id") or "") or None,
                "eventId": event_id,
                "revision": revision,
                "title": title,
                "phase": phase,
                "updatedAt": int(time.time() * 1_000),
                "deepLink": f"/sessions/{urllib.parse.quote(sid, safe='')}",
            })
        if states:
            from api.paths import _atomic_write_text

            _atomic_write_text(self._revision_path, f"{self._last_revision}\n")
        return states

    def publish_snapshot(self) -> None:
        states = self.build_states()
        body = json.dumps(
            {"snapshotId": f"webui:{uuid.uuid4().hex}", "states": states},
            separators=(",", ":"),
        ).encode("utf-8")
        publisher = urllib.parse.quote(self.config.publisher_id, safe="")
        path = f"/v1/publishers/{publisher}/snapshot"
        timestamp = str(int(time.time()))
        nonce = uuid.uuid4().hex
        signed = "\n".join(("PUT", path, timestamp, nonce, _b64url(hashlib.sha256(body).digest())))
        signature = _b64url(self._key.sign(signed.encode("utf-8")))
        request = urllib.request.Request(
            self.config.url + path,
            data=body,
            method="PUT",
            headers={
                "Content-Type": "application/json",
                "X-Talaria-Key-Id": self.config.key_id,
                "X-Talaria-Timestamp": timestamp,
                "X-Talaria-Nonce": nonce,
                "X-Talaria-Signature": signature,
            },
        )
        try:
            with self._opener(request, timeout=10) as response:
                if not 200 <= response.status < 300:
                    raise _RelayHTTPError(response.status)
        except urllib.error.HTTPError as exc:
            raise _RelayHTTPError(exc.code) from exc


class _RelayHTTPError(Exception):
    def __init__(self, status: int):
        super().__init__(f"Talaria relay returned HTTP {status}")
        self.status = status
        self.retryable = status in (408, 425, 429) or status >= 500


_publisher: TalariaRelayPublisher | None = None


def start_talaria_relay_publisher(config: RelayConfig | None = None) -> bool:
    global _publisher
    if _publisher is not None:
        return True
    try:
        config = config or RelayConfig.from_state()
        if config is None:
            return False
        _publisher = TalariaRelayPublisher(config)
        from api.session_events import add_session_list_changed_listener
        add_session_list_changed_listener(_publisher.changed)
        _publisher.start(publish_initial=False)
        atexit.register(stop_talaria_relay_publisher)
        return True
    except Exception:
        _publisher = None
        logger.warning("Talaria relay publisher disabled: invalid configuration", exc_info=True)
        return False


def configure_talaria_relay_publisher(config: RelayConfig) -> None:
    global _publisher
    candidate = TalariaRelayPublisher(config)
    try:
        candidate.publish_snapshot()
    except Exception as exc:
        raise RelayPairingError("Could not publish the initial Talaria Relay snapshot", status=502) from exc
    stop_talaria_relay_publisher()
    _publisher = candidate
    from api.session_events import add_session_list_changed_listener

    add_session_list_changed_listener(candidate.changed)
    candidate.start(publish_initial=False)
    atexit.register(stop_talaria_relay_publisher)


def stop_talaria_relay_publisher() -> None:
    global _publisher
    if _publisher is not None:
        from api.session_events import remove_session_list_changed_listener
        remove_session_list_changed_listener(_publisher.changed)
        _publisher.stop()
        _publisher = None


def note_talaria_terminal(stream_id: str, phase: str) -> None:
    if _publisher is not None and phase in ("completed", "failed", "cancelled"):
        _publisher.note_terminal(stream_id, phase)
