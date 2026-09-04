# Filesystem lock ownership

Process-wide locks may protect an atomic local-state transaction, but they must
not cover network, model, tool, subprocess, callback, queue-wait, join, or sleep
work. The remaining local-I/O scopes are intentionally narrow:

| State owner | Lock and contention domain | I/O kept inside and why |
|---|---|---|
| Agent configuration | `api.config._cfg_lock`; configuration writers for all HTTP threads | One `config.yaml` read-modify-atomic-write transaction. Releasing between read and replace would lose a concurrent setting update. YAML cache misses parse outside `_yaml_file_cache_lock`; no external I/O runs here. |
| Session index | `api.models._INDEX_WRITE_LOCK`; session metadata writers | `_index.json` read/repair/atomic-write must observe one ordered session mutation. Transcript resolution and Agent database reads are outside this lock. |
| Auth sessions | `api.auth._SESSIONS_LOCK`; in-memory cookie lookup/mutation only | No file I/O remains under this lock. `_SESSIONS_PERSIST_LOCK` serializes an atomic write from a fresh snapshot after releasing state ownership, so slow storage cannot block authentication reads. |
| Passkey challenges | `api.passkeys._CHALLENGES_LOCK`; in-memory challenge map | None. Challenge persistence and cryptographic verification do not run under the map lock. |
| Public share snapshots | `api.shares._SHARE_LOCK`; share create/revoke mutations | One token file read-modify-atomic-write. The snapshot is already sanitized and bounded before lock acquisition; readers do not take the mutation lock. |
| Extension state | `api.extensions._EXTENSION_STATE_LOCK`; administrator extension mutations | Manifest/state read-modify-atomic-write. Inputs are capped (`64 KiB` manifest, `32 KiB` state); registry/download/sidecar I/O is outside this lock. |
| Run journals | Per-journal path lock from `api.run_journal._WRITER_LOCKS` | Append/flush ordering for one `(session, stream, path)` only. Unrelated sessions use different locks; global guards protect lock/cache dictionaries without file I/O. |
| Project metadata | `api.models._PROJECTS_MUTATION_LOCK`; project/cron/webhook metadata writers | One `projects.json` lookup-or-create/read-modify-atomic-write transaction prevents parallel profile projections from overwriting each other. Session scanning and external work remain outside. |

When a local filesystem operation fails, its owning API must return or log an
explicit degraded/error result; it must not silently use another profile's state
or report an uncommitted mutation as successful.
