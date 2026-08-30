# Native app OIDC handoff

Hermes WebUI can hand a successful browser OIDC login to a native app without
putting a WebUI cookie or an OIDC token in the app callback URL. The handoff is
available only when `GET /api/auth/status` reports both `oidc_enabled` and
`oidc_native_handoff_enabled` as `true`.

## Flow

1. The app creates a random state and PKCE verifier/challenge, then posts
   `callback_url`, `state`, `code_challenge`, and `code_challenge_method: S256`
   to `/api/auth/oidc/native/start`.
2. The response supplies `flow_id`, `authorization_url`, `server_id`, and
   `expires_in`. The app opens `authorization_url` in an external system
   authentication session.
3. WebUI runs its existing OIDC authorization-code flow with its own state,
   nonce, and PKCE values. Provider state is distinct from app state.
4. After provider validation and allowlist enforcement, WebUI redirects to the
   registered app callback with a short-lived `code`, plus the original app
   `state`, `flow_id`, and `server_id`. Provider failures return the same binding
   values with a generic `error` instead of provider detail.
5. The app validates the callback fields, then posts `flow_id`, `code`, `state`,
   and `code_verifier` to `/api/auth/oidc/native/exchange`. A successful exchange
   returns `{"ok": true}` and sets the normal HttpOnly WebUI session cookie.
6. An app that abandons the browser flow posts `flow_id` and `state` to
   `/api/auth/oidc/native/cancel`.

## Security contract

- App callbacks must use a non-HTTP custom scheme, the exact host
  `oidc-callback`, and no credentials, port, path, query, or fragment in the
  registered URL. PKCE protects an authorization code intercepted by another
  app that claims the same custom scheme.
- App state and flow identity are checked at callback and exchange. The exchange
  is also bound to the exact WebUI origin that created it.
- Exchange codes are random, single-use, and expire after 60 seconds. Native
  flows expire after 10 minutes and all pending maps are bounded.
- A failed server, state, flow, or PKCE check consumes that exchange code. A
  cancelled flow invalidates pending provider and exchange phases.
- Callback URLs never contain the WebUI session cookie, password, OIDC token,
  provider error detail, or any reusable credential.
- Native pending state is process-local, matching the existing browser OIDC
  state model. Multi-process deployments require session affinity through the
  callback and exchange.

Password login, passkeys, trusted-header auth, and browser OIDC keep their
existing behavior. Native clients must fail closed when the capability flag is
missing or false.
