# Filesystem lock ownership

Process-wide locks may protect an atomic local-state transaction, but they must
not cover network, model, tool, subprocess, callback, queue-wait, join, or sleep
work. The remaining local-I/O scopes are intentionally narrow:

| State owner | Lock and contention domain | I/O kept inside and why |
|---|---|---|
| Agent configuration | `api.config._cfg_lock`; configuration writers for all HTTP threads | One `config.yaml` read-modify-atomic-write transaction. Releasing between read and replace would lose a concurrent setting update. YAML cache misses parse outside `_yaml_file_cache_lock`; no external I/O runs here. |
| Session index | `api.models._INDEX_WRITE_LOCK`; session metadata writers | `_index.json` read/repair/atomic-write must observe one ordered session mutation. Transcript resolution and Agent database reads are outside this lock. |
| Zero-message orphan tombstones | `api.models._WEBUI_ZERO_MESSAGE_ORPHAN_TOMBSTONE_LOCK`; sidebar prune and session-save writers | One bounded tombstone load, record/clear mutation, and fsync/atomic-replace or final unlink. The transaction prevents concurrent pruning and revival from losing each other's decision. |
| Deleted-session tombstones | `api.models._WEBUI_DELETED_SESSION_TOMBSTONE_LOCK`; delete and session-recreation writers | One bounded tombstone load, record/clear mutation, and fsync/atomic-replace or final unlink. Keeping the whole transaction ordered prevents a concurrent save/import from resurrecting an explicitly deleted WebUI session. |
| Composer drafts | Per-session lock from `api.session_drafts._draft_lock`; one conversation's draft readers/writers | One bounded draft-sidecar read or version-check/write/fsync/atomic-replace/delete transaction. Unrelated sessions use different weakly held locks; serialization prevents stale autosaves from overwriting a newer revision or reviving a deleted draft. |
| Auth sessions | `api.auth._SESSIONS_LOCK`; in-memory cookie lookup/mutation only | No file I/O remains under this lock. `_SESSIONS_PERSIST_LOCK` serializes an atomic write from a fresh snapshot after releasing state ownership, so slow storage cannot block authentication reads. |
| Login attempts | `api.auth._LOGIN_ATTEMPTS_LOCK`; login rate-limit decisions for all request threads | One `.login_attempts.json` prune/append/clear read-modify-atomic-write keeps the persisted limiter consistent with the decision just returned. The file contains at most the bounded active-window attempts per client; password hashing and external identity work are outside this lock. |
| Passkey challenges | `api.passkeys._CHALLENGES_LOCK`; challenge writers/consumers | One bounded challenge-file load, expiry prune, mutation, and atomic write. The lock makes each challenge single-use and prevents concurrent stores from losing entries; cryptographic verification runs after release. |
| Public share snapshots | `api.shares._SHARE_LOCK`; share create/revoke mutations | One token file read-modify-atomic-write. The snapshot is already sanitized and bounded before lock acquisition; readers do not take the mutation lock. |
| Extension state | `api.extensions._EXTENSION_STATE_LOCK`; administrator extension mutations | Manifest/state read-modify-atomic-write. Inputs are capped (`64 KiB` manifest, `32 KiB` state); registry/download/sidecar I/O is outside this lock. |
| Extension sidecar tokens | `api.extension_sidecar_auth._LOCK`; token mint/reset for all extensions | One bounded stable read or create/reset transaction, including temp write, fsync, atomic link, permission hardening, and final reread. The lock prevents duplicate in-process minting; ordinary cached reads perform stable file I/O outside it. |
| Media snapshots | `api.media_snapshots._LOCK`; snapshot capture writers | One bounded source open/hash/copy, digest binding update, and quota scan/eviction. Serialization preserves the authorized file-handle-to-digest binding and prevents concurrent quota writers from losing sidecar state; snapshot reads do not take this lock. |
| Provider cost snapshots | `api.providers._COST_SNAPSHOT_LOCK`; daily cost-history writers | One provider snapshot read-modify-atomic-write, nested with its per-provider filesystem lock for cross-process ordering. Network usage fetches and delta calculation run outside the lock. |
| Run journals | Per-journal path lock from `api.run_journal._WRITER_LOCKS` | Append/flush ordering for one `(session, stream, path)` only. Unrelated sessions use different locks; global guards protect lock/cache dictionaries without file I/O. |
| Project metadata | `api.models._PROJECTS_MUTATION_LOCK`; project/cron/webhook metadata writers | One `projects.json` lookup-or-create/read-modify-atomic-write transaction prevents parallel profile projections from overwriting each other. Session scanning and external work remain outside. |
| In-place compatibility writes | `api.paths._IN_PLACE_WRITE_LOCK`; rare config/profile targets that cannot use atomic rename | One verified-inode truncate/write/mode-restore/fsync sequence. Normal writes use same-directory temp files and atomic replace without this global lock; the compatibility lock exists only to prevent byte interleaving when inode preservation is mandatory. |

When a local filesystem operation fails, its owning API must return or log an
explicit degraded/error result; it must not silently use another profile's state
or report an uncommitted mutation as successful.
