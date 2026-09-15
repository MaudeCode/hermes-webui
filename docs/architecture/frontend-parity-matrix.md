# Frontend parity matrix (HWEB-100)

This document is the checked-in inventory of every user-visible capability the
legacy frameworkless frontend (`static/*.js`, `static/index.html`,
`static/style.css`) provided, and the owner, route, and verification of each
capability in the TanStack Start / React / TypeScript frontend under
`frontend/`. It is a contract document: a row may not be removed silently. A
capability that is intentionally different or deferred must say so in the
`Status` column with the approval reference.

Status vocabulary:

| Status | Meaning |
|---|---|
| `pass` | Behaviour reproduced and covered by the named verification. |
| `partial` | Core behaviour reproduced; a listed sub-behaviour is reduced. The `Notes` column names it. |
| `deferred` | Not reproduced in this migration. Requires the separate product approval named in `Notes` before merge. |

Verification vocabulary: `vitest` (unit / reducer / contract), `rtl` (React
Testing Library behaviour test), `pw` (Node Playwright end-to-end against the
built frontend and the Python server), `py` (pytest route / contract test),
`manual` (documented manual check in `TESTING.md`).

## 1. Authentication and identity

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| A1 | Password login form with locale strings, invalid-password and connection-failed messages | `routes.py` `_LOGIN_PAGE_HTML`, `static/login.js` | `frontend/src/routes/login.tsx`, `features/auth/LoginForm.tsx` | `/login` | rtl, pw, py | pass | Server still 302s unauthenticated app routes to `/login?next=`. |
| A2 | Safe `?next=` redirect after login (rejects protocol-relative, control chars, nested login chains) | `static/login.js` `_safeNextPath` | `lib/safeNextPath.ts` | `/login` | vitest | pass | Same rules; server-side `_safe_login_redirect_path` unchanged. |
| A3 | OIDC login link and callback bounce | `routes.py` `_oidc_login_html` | `LoginForm.tsx` reads `bootstrap.auth.oidcEnabled` | `/login` | py, rtl | pass | Callback remains server-owned at `/api/auth/oidc/callback`. |
| A4 | Passkey login (WebAuthn get) and passkey registration in Settings | `static/login.js`, `panels.js` | `features/auth/passkeys.ts`, `LoginForm.tsx`, `settings/SecuritySection.tsx` | `/login`, `/settings/system` | vitest (b64url codec), manual | pass | WebAuthn ceremony itself needs a real authenticator (manual). |
| A5 | Logout clears boot snapshots and returns to login | `boot.js` | `features/auth/useLogout.ts` | any | pw | pass | Validated-JSON persisted state cleared by key prefix. |
| A6 | Auth-disabled acknowledgement banner | `ui.js` | `features/shell/AuthDisabledNotice.tsx` | app shell | rtl | pass | |
| A7 | CSRF header on same-origin unsafe requests, exempting login and CSP report | inline fetch monkeypatch in `index.html` | `api/client.ts` adds `X-Hermes-CSRF-Token` from `/api/bootstrap` | all | vitest, py | pass | No fetch monkeypatch; the single client module is the only HTTP path. |
| A8 | Stale-client detection and hard refresh banner | `ui.js` `staleClientBanner` | `features/shell/StaleClientBanner.tsx` | app shell | rtl | pass | Version compared from `bootstrap.webuiVersion` against `/api/settings.webui_version`. |

## 2. Onboarding and profiles

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| B1 | First-run onboarding wizard (status, probe, provider setup, OAuth start/poll/cancel, complete) | `static/onboarding.js` | `routes/onboarding.tsx`, `features/onboarding/*` (TanStack Form + Zod) | `/onboarding` | rtl, py | pass | Explicit route; `/` redirects here while `onboarding.status.needs_onboarding`. |
| B2 | Workspace add during onboarding | `onboarding.js` | `features/onboarding/WorkspaceStep.tsx` | `/onboarding` | rtl | pass | |
| B3 | Profile list, active profile, switch, create, delete | `panels.js`, `boot.js` titlebar dropdown | `routes/profiles.tsx`, `features/profiles/*`, `shell/ProfileMenu.tsx` (Base UI Menu) | `/profiles`, titlebar | rtl, pw | pass | Profile switch invalidates all Query caches and tears down the stream. |
| B4 | Single-profile (isolated) mode hides switcher | `ui.js` | `shell/ProfileMenu.tsx` | app shell | rtl | pass | |
| B5 | Profile-scoped session visibility (409 `session_profile_mismatch`) | `sessions.js` | `features/chat/useSessionQuery.ts` | `/session/$id` | vitest, py | pass | Mapped to a typed error state with a switch-profile action. |

## 3. Sessions and navigation

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| C1 | Canonical `/` and `/session/<encoded-id>` URLs; history restore; last-session restore | `sessions.js` `_appRootPath`, inline boot scripts | TanStack Router routes `index.tsx`, `session.$sessionId.tsx`; `lib/persisted.ts` (validated JSON) | `/`, `/session/$id` | pw, vitest | pass | Restores by validated `hermes-webui-session` key; no `innerHTML` snapshots. |
| C2 | Legacy `?session=` / `?session_id=` query and `#settings` hash launch flows | inline scripts, `boot.js` | `routes/__root.tsx` `beforeLoad` redirects | `/` | vitest, pw | pass | Redirects to canonical routes. |
| C3 | PWA launch flows `?source=pwa&action=new-chat` | `pwa-startup.js`, `boot.js` | `routes/index.tsx` validated search schema | `/` | vitest | pass | |
| C4 | New chat (titlebar, rail brand, sidebar) | `sessions.js` `newChat` | `features/sessions/useNewChat.ts` | app shell | pw | pass | |
| C5 | Session list: grouping, pin, archive, project groups, search filter, source filters (CLI, Claude Code, cron, webhook, kanban), all-profiles toggle | `sessions.js` | `features/sessions/SessionList.tsx`, `sessionListQuery.ts` | sidebar | rtl, pw | pass | Long lists virtualized with TanStack Virtual. |
| C6 | Live sidebar sync via `GET /api/session/stream` (`sessions_changed`) | `sessions.js` | `features/sessions/useSessionListStream.ts` | sidebar | vitest | pass | Invalidates the session list query; bounded to one EventSource. |
| C7 | Rename, duplicate, delete, move to project, pin, archive, export, import (JSON and CLI), regenerate title, branch/fork, truncate, undo, retry | `sessions.js`, context menu | `features/sessions/sessionMutations.ts`, `SessionContextMenu.tsx` (Base UI Menu) | sidebar, chat header | rtl, py | pass | |
| C8 | Project groups create/rename/delete | `sessions.js` | `features/sessions/projects.ts` | sidebar | rtl | pass | |
| C9 | Sidebar collapse, resize handle, mobile drawer, hidden/reordered tabs | `boot.js`, inline scripts | `shell/Sidebar.tsx`, `shell/Rail.tsx`, `lib/persisted.ts` | app shell | rtl, pw | pass | Persisted as validated JSON; applied before first paint via a blocking module-less CSS class strategy (`data-*` on `<html>` set by the entry module before render). |
| C10 | Composer drafts per session (`/api/session/draft`) | `sessions.js` | `features/composer/useDraft.ts` | chat | vitest | pass | |
| C11 | Session status polling, stream reattach on return, bfcache reattach | `messages.js` | `stream/connection.ts`, `stream/useStreamLifecycle.ts` | chat | vitest, pw | pass | See chat lifecycle rows. |
| C12 | Public share create/revoke and read-only share page | `panels.js`, `share.html`, `share.js` | `routes/share.$token.tsx`, `features/share/*` | `/share/$token` | rtl, py | pass | Share page is a route of the same SPA; unauthenticated shell allowed for `/share/*`. |
| C13 | Handoff summary and compression-recovery cards | `messages.js`, `sessions.js` | `features/chat/RecoveryCards.tsx` | chat | rtl | pass | |
| C14 | Worktree status/remove for worktree sessions | `sessions.js` | `features/sessions/WorktreeBadge.tsx` | chat header | rtl | pass | |
| C15 | Session search (`/api/sessions/search`) | `sessions.js` | `features/sessions/SessionSearch.tsx` | sidebar | rtl | pass | |
| C16 | Unknown paths return HTTP 404 from Python; unknown nested client paths render the not-found route | `routes.py` catch-all | `routes.py` SPA allowlist; `routes/__root.tsx` `notFoundComponent` | any | py, pw | pass | SPA shell never shadows `/api/*`, auth callbacks, `/extensions/*`, `/plugins/*`, `/static/*`, `/sw.js`, `/manifest.json`, `/health`. |
| C17 | Subpath mount support for shell, assets, manifest, service worker, API, sessions, extension panels | `<base href>` inline script, `/session/static/` alias | Server-emitted `<base href>` depth prefix; router `basepath` from `document.baseURI`; relative asset base | all | py, pw | pass | Verified with a `/mount/` prefixed Playwright scenario. |

## 4. Chat transcript and streaming

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| D1 | Send message via `POST /api/chat/start`, optimistic user row adopting server turn identity | `messages.js` `send()` | `stream/actions.ts`, `stream/reducer.ts` | chat | vitest, pw | pass | |
| D2 | Live token streaming (`token`, `interim_assistant`, `already_streamed`, `reasoning_echo`) | `messages.js` | `stream/reducer.ts` | chat | vitest | pass | Ordering and idempotency proven in reducer tests. |
| D3 | Reasoning / thinking blocks with titles, collapsed by default, reduced-motion aware | `messages.js`, `assistant_turn_anchors.js` | `features/chat/blocks/ReasoningBlock.tsx` | chat | rtl | pass | |
| D4 | Tool call cards (`tool`, `tool_complete`), worklog summary, transparent stream mode, event timestamps | `ui.js`, `assistant_turn_anchors.js` | `features/chat/blocks/ToolCard.tsx`, `Worklog.tsx` | chat | rtl, vitest | pass | Activity scene projection reduced to compact worklog / transparent stream / final answer per `stable-assistant-turn-anchors.md`. |
| D5 | Approval card (`approval` event, once/session/always/deny/skip-all, pending counter, collapse, keyboard Enter) | `index.html` `#approvalCard`, `messages.js` | `features/chat/ApprovalCard.tsx` (Base UI AlertDialog semantics inline) | chat | rtl, py | pass | |
| D6 | Clarification card (`clarify` event, choices, countdown, custom answer) | `#clarifyCard`, `messages.js` | `features/chat/ClarifyCard.tsx` | chat | rtl, py | pass | |
| D7 | Terminal exits: `done`, `stream_end`, `apperror` (typed), `cancel`, `error` legacy | `messages.js` | `stream/reducer.ts` | chat | vitest | pass | Every exit clears in-flight state and releases the EventSource. |
| D8 | Cancel (Stop button, `/stop`), interrupt, queue, steer, stop-and-send, leftover steer | `messages.js`, `boot.js` `cancelStream` | `stream/actions.ts`, `features/composer/BusyControls.tsx` | chat | vitest, rtl, py | pass | |
| D9 | Reconnect and journal replay (`/api/chat/stream/status`, `replay=1`, `after_seq`, `after_event_id`) | `messages.js` | `stream/connection.ts` | chat | vitest, pw | pass | Dedupe by event id; reconnect proven in deterministic gateway scenario. |
| D10 | Session replacement / profile change tears down the live stream without cancelling the backend run | `messages.js` `closeLiveStream` | `stream/useStreamLifecycle.ts` | chat | vitest | pass | |
| D11 | Compression (`compressing`, `compressed`, continuation session redirect) | `messages.js` | `stream/reducer.ts`, `features/chat/CompressionCard.tsx` | chat | vitest, rtl | pass | |
| D12 | Context window ring, `context_status`, `metering`, TPS badge, cost | `ui.js` | `features/composer/ContextRing.tsx` | composer | rtl, vitest | pass | |
| D13 | Title updates (`title`, `title_status`) and adaptive titlebar | `messages.js`, `ui.js` | `stream/reducer.ts`, `shell/Titlebar.tsx` | chat | vitest | pass | |
| D14 | Goals (`goal`, `goal_continue`) and todos (`todo_state`) panels | `messages.js`, `panels.js` | `features/chat/GoalCard.tsx`, `routes/todos.tsx` | chat, `/todos` | rtl | pass | |
| D15 | Background completion notifications (`bg_task_complete`, `/api/bg-task-complete-ack`, `/api/process-complete-ack`) | `messages.js` | `features/notifications/*` | app shell | vitest | pass | |
| D16 | Subagent / delegated session cards and view-only subagent sessions | `ui.js`, `sessions.js` | `features/chat/blocks/SubagentCard.tsx` | chat | rtl | pass | |
| D17 | Historical transcript hydration with windowed load (`msg_limit`, `msg_before`), jump-to-start, scroll-to-bottom, auto-follow toggle | `sessions.js`, `messages.js` | `features/chat/Transcript.tsx` (TanStack Virtual), `useTranscriptWindow.ts` | chat | pw, rtl | pass | |
| D18 | Edit, regenerate, fork from message, copy, select-text reply/refine | `messages.js` | `features/chat/MessageActions.tsx` | chat | rtl | pass | |
| D19 | Attachments: click, drag/drop, paste image and text, tray, upload rollback, size limit from bootstrap | `boot.js`, `messages.js`, `/api/upload` | `features/composer/Attachments.tsx`, `api/client.ts` upload | composer | rtl, py | pass | |
| D20 | Media snapshots and image lightbox, Mermaid lightbox, export | `ui.js` | `features/chat/Lightbox.tsx` (Base UI Dialog) | chat | rtl | pass | |
| D21 | Voice: dictation (SpeechRecognition), voice mode, TTS (browser, Edge TTS via `/api/tts`, extension TTS capability) | `boot.js`, `ui.js` | `features/voice/*` | composer | rtl (feature detection), manual | partial | Turn-based voice mode reproduced; extension TTS now arrives through the capability protocol. Browser speech APIs need manual verification. |
| D22 | Workspace terminal panel (xterm, fit, web links, resize, dock, restart) | `terminal.js`, CDN xterm | `features/terminal/TerminalPanel.tsx` with bundled `@xterm/xterm` | composer | rtl (mount), manual | pass | CDN dependency removed. |
| D23 | Runtime notice stack (offline, reconnect, agent unavailable, provider failure, thread error) with live regions | `ui.js` HWEB-11 | `features/notices/RuntimeNoticeStack.tsx` | chat | rtl | pass | Same priority order and single-slot rules. |
| D24 | Server-stopped overlay and cross-tab shutdown broadcast | `boot.js` | `features/shell/ServerStopped.tsx` | app shell | rtl | pass | |
| D25 | Update banner (check/apply/force/clear lock, summary, permissions) | `panels.js`, `ui.js` | `features/updates/UpdateBanner.tsx` | app shell | rtl | pass | |
| D26 | Conversation outline / minimap | `outline.js` | `features/chat/Outline.tsx` | chat | rtl | pass | |
| D27 | Selection context chips (named context blocks) | `messages.js` | `features/composer/SelectionChips.tsx` | composer | rtl | pass | |
| D28 | Slash commands: registry, parser, autocomplete dropdown, bundles, MoA, `/api/commands/exec` | `commands.js` | `features/composer/commands/*` (Base UI Combobox behaviour) | composer | vitest, rtl | pass | |
| D29 | Saved prompts popup | `messages.js` | `features/composer/SavedPrompts.tsx` (Base UI Menu) | composer | rtl | pass | |
| D30 | YOLO pill and per-session yolo toggle | `boot.js` | `features/composer/YoloPill.tsx` | composer | rtl | pass | |
| D31 | Model chip and dropdown (groups, provider, live refresh, explicit pick), reasoning effort chip, toolsets chip, personality | `ui.js`, `panels.js` | `features/composer/ModelMenu.tsx`, `ReasoningMenu.tsx`, `ToolsetsMenu.tsx` (Base UI Select/Menu) | composer | rtl | pass | |
| D32 | Workspace chip and dropdown, workspace files panel toggle | `workspace.js` | `features/composer/WorkspaceMenu.tsx` | composer | rtl | pass | |
| D33 | Provider quota chip | `ui.js` | `features/composer/ProviderQuotaChip.tsx` | composer | rtl | pass | |
| D34 | Send key preference (Enter vs Ctrl+Enter), Shift+Enter newline, busy input modes | `boot.js` | `features/composer/Composer.tsx` | composer | rtl | pass | |
| D35 | Hero composer (empty state) docking after first message, workspace-aware headline | `ui.js` HWEB-1 | `features/chat/EmptyState.tsx` | chat | rtl, pw | pass | |
| D36 | Keyboard shortcuts (new chat, focus composer, toggle sidebar, escape) | `boot.js` | `shell/useShortcuts.ts` | app shell | rtl | pass | Same key map. |

## 5. Rendering

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| E1 | GitHub-flavoured Markdown, incomplete streamed Markdown | `ui.js` `renderMd`, `smd.min.js` | Streamdown (`features/chat/Markdown.tsx`) | chat, share | vitest (differential corpus), rtl | pass | Corpus in `frontend/src/features/chat/__fixtures__/markdown/`. |
| E2 | Code blocks: Shiki highlighting, language label, copy, download, wrapping and overflow containment | `ui.js`, Prism CDN | `@streamdown/code` with Shiki; `CodeBlockControls.tsx` | chat | rtl | pass | Prism and CDN removed. |
| E3 | Tables: header/cell spacing, pipe protection, copy as TSV/Markdown, CSV rendering | `ui.js` | Streamdown table with `TableControls.tsx` | chat | rtl, vitest | pass | |
| E4 | Task lists, links (safe target/rel), images (data URLs allowed, remote per CSP), blockquotes | `ui.js` | Streamdown | chat | vitest | pass | |
| E5 | Math via KaTeX (vendored) | `ui.js`, `static/vendor/katex` | `@streamdown/math` (bundled KaTeX CSS/fonts) | chat | rtl | pass | Fonts bundled; CSP `font-src 'self'`. |
| E6 | Mermaid diagrams with toolbar and lightbox | `ui.js` | `@streamdown/mermaid` + `MermaidControls.tsx` | chat | rtl | pass | Mermaid rendered only on user request for untrusted content in the same way as before (render button). |
| E7 | CJK-aware rendering | none explicit | `@streamdown/cjk` | chat | vitest | pass | |
| E8 | Hostile input: raw HTML escaped, `javascript:` links stripped, no `innerHTML` outside the reviewed adapter | `ui.js` sanitizer | Streamdown default sanitization; `RenderingAdapter.tsx` is the single reviewed `dangerouslySetInnerHTML`-free path | chat | vitest hostile corpus | pass | ESLint rule forbids `dangerouslySetInnerHTML` outside `features/chat/render/`. |
| E9 | Render user Markdown toggle | `ui.js` | `features/chat/UserMessage.tsx` | chat | rtl | pass | |
| E10 | Data image renderer, file links (`/api/file/raw`), office document preview | `ui.js`, `workspace.js` | `features/chat/blocks/FileLink.tsx`, `features/workspace/Preview.tsx` | chat, workspace | rtl | pass | |
| E11 | Large Markdown preview performance (lazy worklog render, render cache) | `ui.js` | React memoisation + Streamdown block cache | chat | pw perf budget | pass | |

## 6. Panels

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| F1 | Tasks (cron jobs): list, create, update, delete, run, pause, resume, history, output, delivery options, gateway notice, all-profiles toggle | `panels.js` | `routes/tasks.tsx`, `features/tasks/*` (TanStack Form) | `/tasks`, `/tasks/$jobId` | rtl, py | pass | |
| F2 | Kanban: boards, board switch, filters, summary, task detail, log, comments, links, dispatch, bulk, config, events stream | `panels.js` | `routes/kanban.tsx`, `features/kanban/*` | `/kanban` | rtl | pass | |
| F3 | Skills: list, search, categories, content, save, delete, toggle, usage stats | `panels.js`, `hub.js` | `routes/skills.tsx`, `features/skills/*` | `/skills`, `/skills/$name` | rtl, py | pass | |
| F4 | Memory: MEMORY.md, USER.md, SOUL.md, project context, write | `panels.js` | `routes/memory.tsx`, `features/memory/*` | `/memory` | rtl, py | pass | |
| F5 | Workspaces: list, add, remove, rename, reorder, suggest, git badge, terminal remote backend flag | `panels.js`, `workspace.js` | `routes/workspaces.tsx`, `features/workspaces/*` | `/workspaces` | rtl, py | pass | |
| F6 | Workspace files panel: tree, hidden files toggle, preview, create/rename/move/delete, save, reveal, open in VS Code, folder download, git status/diff/stage/commit/push/pull/branches/stash | `workspace.js` | `features/workspace/*` | chat side panel | rtl, py | partial | Office document editing preview reproduced as read-only preview plus download; office save remains available through the API and is exposed behind the same Save action. |
| F7 | Profiles hub | `panels.js` | see B3 | `/profiles` | rtl | pass | |
| F8 | Todos panel | `panels.js` | `routes/todos.tsx` | `/todos` | rtl | pass | |
| F9 | Insights (usage by day, provider cost history, wiki status/browse) | `panels.js` | `routes/insights.tsx`, `features/insights/*` | `/insights` | rtl | pass | Charts rendered as accessible tables plus inline SVG bars with tokens. |
| F10 | Logs (file select, tail, refresh, hint) | `panels.js` | `routes/logs.tsx` | `/logs` | rtl, py | pass | |
| F11 | Settings: appearance (theme, skin, font size, full width, RTL), conversation, preferences (send key, language, bot name, visibility filters, auto-scroll), providers (list, quotas, self-hosted, delete, model default, auxiliary models, MCP servers/tools), plugins, extensions, system (health, updates channel, password set/clear, passkeys, shutdown/restart), help | `panels.js`, `index.html` `#panelSettings` | `routes/settings.tsx`, `routes/settings.$section.tsx`, `features/settings/*` (TanStack Form + Zod) | `/settings`, `/settings/$section` | rtl, py | pass | Section guard: unknown section renders not-found. |
| F12 | Hermes Dashboard link (rail) | `boot.js` `openHermesDashboard` | `shell/Rail.tsx` from `/api/dashboard/status` | rail | rtl | pass | |
| F13 | Dashboard plugins (`/plugins/<name>`, manifest tabs) | `api/plugins.py`, `panels.js` | Unified extension platform: plugin manifests are surfaced as extension manifests with one iframe panel | `/ext/$extensionId` | py, rtl | pass | Legacy IIFE injection into the core page is gone; the plugin UI runs in a sandboxed iframe. |
| F14 | Hub layout (collection as the main view, detail with Back) | `hub.js` | Route layouts under `routes/` | native routes | pw | pass | |
| F15 | Notes sources / search / item | `panels.js` | `features/memory/Notes.tsx` | `/memory` | rtl | pass | |
| F16 | Rollback checkpoints list/diff/restore | `workspace.js` | `features/workspace/Rollback.tsx` | workspace | rtl | pass | |
| F17 | Escape hatch file browser (`/api/escape/*`) | `workspace.js` | `features/workspace/Escape.tsx` | workspace | rtl | pass | |

## 7. Appearance, localisation, platform

| ID | Capability | Legacy source | New owner | Route | Verification | Status | Notes |
|---|---|---|---|---|---|---|---|
| G1 | Theme axis (light, dark, system) and legacy theme aliases (`slate`, `solarized`, `monokai`, `nord`, `oled`) | inline `<head>` script, `boot.js` | `frontend/src/theme/boot.ts` runs first in the entry module; `theme/tokens.css` | all | vitest, pw screenshot | pass | No inline script: the entry module applies the class before React renders and CSS `color-scheme` avoids a flash under `prefers-color-scheme`. |
| G2 | Skin axis (all 21 skins) as CSS custom properties consumed by Tailwind utilities | `style.css` | `theme/tokens.css` (carried forward verbatim), `tailwind.css` `@theme` mapping | all | pw screenshot | pass | |
| G3 | Font size preference, full-width chat | inline scripts, `style.css` | `theme/boot.ts`, tokens | all | rtl | pass | |
| G4 | RTL: persisted state, `dir` attribute, mirrored layout | inline script, `boot.js` | `theme/boot.ts`, logical CSS properties | all | rtl, pw screenshot | pass | |
| G5 | Languages: en, it, ja, ru, es, de, zh, zh-Hant, pt, ko, fr, cs, tr, pl, vi with fallback to English, interpolation, plural helpers, runtime switch, server `language` setting and `hermes-lang` persistence | `i18n.js`, `api/i18n_assets.py` | Paraglide JS (`frontend/messages/*.json`, `project.inlang/`), `i18n/runtime.ts` | all | vitest parity test, build gate | pass | Build fails on missing English keys, placeholder mismatch, or locale key drift. Locale bundles are code-split per locale. |
| G6 | Speech locale (`_speech`) per language | `i18n.js` | `i18n/locales.ts` | voice | vitest | pass | |
| G7 | PWA: manifest, install prompt, standalone classes, offline shell, update activation, scope and subpath | `manifest.json`, `sw.js`, `pwa-startup.js` | `vite-plugin-pwa` `injectManifest`, `frontend/src/sw.ts`, `features/pwa/*` | all | vitest (sw helpers), pw, py | pass | Hashed precache; obsolete caches cleaned on activate; `sw.js` served with `Service-Worker-Allowed`. |
| G8 | Responsive: desktop rail + sidebar, narrow, mobile drawer and composer config sheet, safe areas, touch targets | `style.css`, `boot.js` | Tailwind utilities on tokens, `shell/*` | all | pw screenshots at 1280 and 390 widths | pass | |
| G9 | Accessibility: accessible names, live regions, focus management, keyboard menus/dialogs, reduced motion | `index.html`, `ui.js` | Base UI primitives, `a11y/Announcer.tsx` | all | rtl | pass | |
| G10 | Presence lease (HWEB-97) | `presence.js` | `features/presence/usePresence.ts` | app shell | vitest | pass | |
| G11 | Client event log (`/api/client-events/log`) and CSP report | `ui.js` | `lib/clientEvents.ts` | all | vitest | pass | |
| G12 | Extensions: settings storage, configure registrations, turn lifecycle subscriptions, skins, TTS engines, session-open handlers, sidecar consent and proxy | `extension_settings.js`, `boot.js`, `api/extensions.py` | Unified sandboxed protocol: `frontend/src/extensions/*`, `contracts/extension.ts`, `docs/architecture/extension-protocol-v1.md` | `/ext/$id`, `/settings/extensions` | vitest (hostile), rtl, py | pass | Intentional break documented in the migration guide; no compatibility shim. |
| G13 | Shell unavailable page when the template fails to render | `routes.py` `_serve_shell_unavailable` | unchanged server path | `/` | py | pass | |

## 8. States

Every route and panel covers: empty, loading, success, error (typed
`ApiError`), cancellation (aborted Query), stale (Query `isStale` with
refresh), unauthorized (401 redirect to `/login?next=` handled once in the
client module), disconnected (offline notice), and malformed payload (Zod
failure rendered as a typed error with a retry action, never as a blank pane).
Root and route error boundaries (`routes/__root.tsx`, per-route
`errorComponent`) offer retry and reload. A failed lazy chunk triggers the
route error boundary with a reload action.

## 9. Intentional differences

| Ref | Difference | Reason | Approval |
|---|---|---|---|
| X1 | Injected extension scripts/styles, `window.hermesExt`, `registerHermesSkin`, `registerHermesTtsEngine`, and dashboard plugin IIFE injection are removed | Required by HWEB-100 (unified sandboxed protocol) | HWEB-100 ticket text |
| X2 | Prism, `smd.min.js`, CDN xterm are removed in favour of bundled Streamdown, Shiki, `@xterm/xterm` | Required by HWEB-100 | HWEB-100 ticket text |
| X3 | Cached HTML boot snapshots (`hermes-boot:*` `innerHTML`) are removed; the boot restores validated JSON state and renders through React | Required by HWEB-100 | HWEB-100 ticket text |
| X4 | Login and share pages are routes of the same SPA shell instead of separate server-rendered HTML | Explicit routes required by HWEB-100 | HWEB-100 ticket text |
