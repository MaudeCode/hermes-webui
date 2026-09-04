# SSE streams and capability signaling

Cross-client reference for the server-sent events (SSE) endpoints Hermes WebUI
exposes. Browser and non-browser clients (Android wrapper, CLI observers)
should integrate against this page so every client describes the same
behavior.

All endpoints below are served by the WebUI origin and sit behind the same
authentication as every other `/api/*` route: when a WebUI password or OIDC
is configured, clients must authenticate before opening any stream.

## Endpoint inventory

| Endpoint | Availability | Purpose |
|---|---|---|
| `GET /api/chat/stream?stream_id=<id>` | Always on | Live agent-turn relay (tokens, tool calls, approvals, `done`, `stream_end`). Falls back to run-journal replay when the in-memory stream is gone. Resume cursors: `after_event_id` / `after_seq` query params, with the standard `Last-Event-ID` header as fallback. Journal-backed events carry `id: <stream_id>:<seq>`; opaque runner cursors are emitted in an owner-bound `runner-v1` envelope so automatic reconnect cannot apply one run's cursor to another. An invalid/foreign/ahead-of-stream cursor is honored as replay-from-start rather than silently skipping events. |
| `GET /api/session/stream?session_id=<id>` | Always on | Persistent per-session channel that survives across agent turns (`initial`, `server_turn_started`, `session-updated`, `bg_task_complete`). This is the stream the WebUI frontend keeps open per session and the stream non-browser clients should prefer for background session updates. |
| `GET /api/sessions/events` | Always on | Global session-list invalidation (`sessions_changed` + keepalives). A signal to re-read `/api/sessions`, not a per-session lifecycle feed. With `?gateway=1` the same connection also carries the gateway feed — see [Merged sidebar stream](#merged-sidebar-stream). |
| `GET /api/sessions/{session_id}/events` | Always on | Per-session run-journal relay with `Last-Event-ID` / `after_event_id` resume and snapshot fallback. See `docs/rfcs/session-sse-contract-v1.md` for the contract and its proof gates. |
| `GET /api/sessions/gateway/stream` | Optional | Real-time updates for CLI/TUI/messaging (agent) sessions merged into the sidebar. Only streams when the **Agent sessions** setting (`show_cli_sessions`) is enabled and the gateway watcher thread is running. Still served for clients that subscribe to it directly; the browser frontend now takes this feed over `/api/sessions/events?gateway=1` instead. |

The authoritative `event:` names on `/api/chat/stream` are listed in the
**Authoritative emitted events** table of
[`docs/rfcs/session-sse-contract-v1.md`](rfcs/session-sse-contract-v1.md).

## Merged sidebar stream

A browser allows six concurrent HTTP/1.1 connections per origin, and every SSE
endpoint above holds one for as long as it is open. `/api/sessions/events` and
`/api/sessions/gateway/stream` are both global sidebar concerns that a tab
opens together, so `?gateway=1` serves them over a single connection:

```
GET /api/sessions/events?gateway=1
```

- Event names and payloads are unchanged from the two separate endpoints. Every
  frame gains a `stream` discriminator — `"sessions"` for session-list
  invalidation, `"gateway"` for watcher frames — so a client routes each frame
  to the handler it already had.
- The connection opens with a `gateway_status` event carrying the same payload
  as `?probe=1` below. Because the session-events half stays healthy when the
  gateway half cannot attach, an unusable gateway never surfaces as a transport
  error; this frame is what tells a client to start its `fallback_poll_ms`
  polling instead.
- When the gateway watcher is replaced (a profile switch restarts it), the
  server ends the response rather than downgrading the gateway half in place, so
  the client's automatic reconnect resubscribes both halves against the live
  watcher registry.
- Without `?gateway=1` the endpoint behaves exactly as before, so existing
  subscribers are unaffected.

Clients that prefer two connections can keep using
`/api/sessions/gateway/stream`; it is unchanged.

## Gateway probe scope (important for non-browser clients)

`GET /api/sessions/gateway/stream?probe=1` returns a JSON capability payload
for the **optional gateway stream only** instead of holding an SSE connection:

```json
{
  "enabled": false,
  "ok": false,
  "watcher_running": false,
  "fallback_poll_ms": 30000,
  "error": "agent sessions not enabled",
  "scope": "gateway_sessions",
  "session_stream_available": true,
  "session_stream_path": "/api/session/stream"
}
```

- `404` + `error: "agent sessions not enabled"` means only that the optional
  gateway/agent-sessions stream is disabled on this server.
- `503` + `error: "watcher not started"` means the setting is on but the
  gateway watcher thread is not running.
- `200` with `ok: true` means gateway SSE is usable.

**A negative gateway probe result must not be treated as "SSE unavailable".**
The persistent per-session stream (`/api/session/stream`) and the chat-turn
relay (`/api/chat/stream`) are always on and are not gated by the Agent
sessions setting. The `scope`, `session_stream_available`, and
`session_stream_path` fields make this explicit so clients do not need to
infer it from the status code. Clients that only need session updates should
use `/api/session/stream` directly; the gateway probe is only relevant for
clients that display CLI/TUI/messaging sessions.

## Heartbeats and proxy behavior

- All long-lived streams emit SSE keepalive comment lines on the
  `_SSE_HEARTBEAT_INTERVAL_SECONDS` cadence (currently 5 seconds), which is
  short enough to survive typical reverse-proxy idle timeouts.
- Handlers send `X-Accel-Buffering: no` so nginx-style proxies pass events
  through unbuffered.
- Deployments behind buffering proxies that read-until-close (notably
  Tornado-based `jupyter-server-proxy`) can set `HERMES_WEBUI_SSE_CHUNKED=1`
  to frame each event as an HTTP/1.1 chunk. The default wire format is
  unchanged when the flag is unset.
