"""Best-effort publication of live Hermes session state to Talaria Relay."""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import logging
import os
import queue
import threading
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import load_pem_private_key

logger = logging.getLogger(__name__)


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


@dataclass(frozen=True)
class RelayConfig:
    url: str
    publisher_id: str
    key_id: str
    private_key_path: Path

    @classmethod
    def from_environment(cls) -> "RelayConfig | None":
        values = {
            "url": os.environ.get("HERMES_WEBUI_TALARIA_RELAY_URL", "").strip().rstrip("/"),
            "publisher_id": os.environ.get("HERMES_WEBUI_TALARIA_PUBLISHER_ID", "").strip(),
            "key_id": os.environ.get("HERMES_WEBUI_TALARIA_KEY_ID", "").strip(),
            "private_key_path": os.environ.get("HERMES_WEBUI_TALARIA_PRIVATE_KEY_PATH", "").strip(),
        }
        if not any(values.values()):
            return None
        if not all(values.values()):
            raise ValueError("all HERMES_WEBUI_TALARIA_* settings are required")
        parsed = urllib.parse.urlsplit(values["url"])
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in ("", "/")
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("HERMES_WEBUI_TALARIA_RELAY_URL must be an HTTP(S) origin")
        return cls(
            url=values["url"],
            publisher_id=values["publisher_id"],
            key_id=values["key_id"],
            private_key_path=Path(values["private_key_path"]).expanduser(),
        )


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
        self._last_revision = 0
        self._terminal_lock = threading.Lock()
        self._terminal: dict[str, dict] = {}

    @staticmethod
    def _load_key(path: Path) -> Ed25519PrivateKey:
        key = load_pem_private_key(path.read_bytes(), password=None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ValueError("Talaria publisher key must be an Ed25519 private key")
        return key

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="talaria-relay", daemon=True)
        self._thread.start()
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
        while not self._stop.is_set():
            try:
                self._changes.get(timeout=1)
            except queue.Empty:
                continue
            if self._stop.is_set():
                return
            try:
                self.publish_snapshot()
            except Exception:
                logger.warning("Talaria relay snapshot failed", exc_info=True)
                if not self._stop.wait(5):
                    self.changed()

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
        with self._opener(request, timeout=10) as response:
            if not 200 <= response.status < 300:
                raise OSError(f"Talaria relay returned HTTP {response.status}")


_publisher: TalariaRelayPublisher | None = None
_start_attempted = False


def start_talaria_relay_publisher() -> bool:
    global _publisher, _start_attempted
    if _publisher is not None:
        return True
    if _start_attempted:
        return False
    _start_attempted = True
    try:
        config = RelayConfig.from_environment()
        if config is None:
            return False
        _publisher = TalariaRelayPublisher(config)
        from api.session_events import add_session_list_changed_listener
        add_session_list_changed_listener(_publisher.changed)
        _publisher.start()
        atexit.register(stop_talaria_relay_publisher)
        return True
    except Exception:
        _publisher = None
        logger.warning("Talaria relay publisher disabled: invalid configuration", exc_info=True)
        return False


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
