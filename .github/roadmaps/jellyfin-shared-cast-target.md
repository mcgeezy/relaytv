# Jellyfin Shared Cast Target Roadmap

Status: in progress (M1 complete)

Branch: `fix/jellyfin-shared-cast-target`

Primary implementation area: `app/relaytv_app/integrations/` and the Settings
modal

This is an engineering implementation ledger, not an operator runbook. Update
it in the same commit that completes each milestone. The final operator-facing
behavior belongs in `docs/JELLYFIN_OPERATIONS.md`.

## Outcome

Let an operator configure a Jellyfin server API key in RelayTV Settings so the
RelayTV receiver registers as a shared, userless cast target instead of a
session belonging only to RelayTV's catalog login.

The on-device Jellyfin catalog remains user-scoped: username/password
authentication supplies catalog preferences, permissions, resume positions,
and media URLs when it is configured. The API key owns the receiver control
plane. A later round will address caller-specific catalog resolution and watch
history for commands cast by other users.

## Server Constraint

Jellyfin treats an API key as an administrator credential without a user
identity. Its userless session is visible as a shared device only to users
whose Jellyfin policy allows shared-device control. RelayTV cannot override a
server policy that disables shared-device control for a user.

The Settings copy and operations documentation must therefore say "all users
allowed to control shared devices," not unconditionally "all users."

Upstream references:

- [API keys authenticate with the administrator role](https://github.com/jellyfin/jellyfin/blob/master/Jellyfin.Api/Auth/CustomAuthenticationHandler.cs)
- [Jellyfin filters controllable sessions by shared-device and remote-control policy](https://github.com/jellyfin/jellyfin/blob/master/Emby.Server.Implementations/Session/SessionManager.cs)
- [Play commands include the controlling user ID](https://github.com/jellyfin/jellyfin/blob/master/MediaBrowser.Model/Session/PlayRequest.cs)

## Current-State Findings

1. `jellyfin_api_key` already exists in persisted settings, startup runtime
   configuration, receiver state, and environment inventory.
2. The Settings request model and both browser save paths omit the API key.
   Settings reads redact the value but do not return a configured-state flag.
3. Live Settings apply requires username/password and passes `api_key=""` to
   `jellyfin_receiver.connect()`.
4. The receiver has one request token, preferring the authenticated user token
   over the API key. Registration, websocket identity, playback reporting,
   catalog requests, metadata, and media URLs consequently share one identity.
5. Adding only the Settings field would not fix cast-target scope: a successful
   username/password authentication would still replace the API key as the
   websocket and registration credential.

## Credential Model

Every network operation must capture its credential together with the current
configuration generation. No operation may read mutable token state after it
starts.

| Purpose | Preferred credential | Fallback | Notes |
| --- | --- | --- | --- |
| Cast websocket | server API key | login-session token | API key creates the shared/userless target |
| Capability registration/readback | server API key | login-session token | Must match the websocket identity |
| Playing/progress/stopped reports | server API key | login-session token | Must keep the receiver session userless when shared mode is active |
| Server detection | server API key | login-session token | Either credential is sufficient; use the control identity for consistency |
| Catalog browse and search | login-session token | server API key | Preserve the configured catalog user's permissions and preferences |
| Metadata, playback info, images, media URLs | login-session token | server API key | Preserve user-scoped resume and media policy where possible |

Proposed immutable context fields:

- `control_token`: `_API_KEY or _ACCESS_TOKEN`
- `catalog_token`: `_ACCESS_TOKEN or _API_KEY`
- existing configuration generation, server identity, device identity, and
  catalog user ID

Avoid a generic `active_token()` after this split. Call sites should name the
identity they require.

## Settings Contract

Add a password-style `Server API key` field to the Jellyfin/Emby Settings
group, with copy similar to:

> Makes RelayTV a shared Jellyfin cast target for users allowed to control
> shared devices. Create and rotate this administrator-level key in Jellyfin.

Secret behavior must match the existing password contract:

- `GET /settings` returns `jellyfin_api_key: ""` and
  `jellyfin_api_key_configured: true|false`.
- An omitted API-key property preserves the stored value.
- A non-empty value replaces it.
- The explicit clear control submits an empty value and removes it.
- The key is never returned by Settings/status endpoints and never logged.
- Both the Jellyfin-only Apply button and the modal-wide Apply button implement
  identical preserve/replace/clear behavior.

Readiness becomes two related states:

- shared cast target: server URL plus API key;
- legacy user-scoped target/catalog: server URL plus username/password.

When both credential types exist, RelayTV should start the shared cast target
with the API key immediately and authenticate the catalog user independently.
An API-key-only configuration may run the cast target without requiring a
username or password. Catalog behavior in that mode uses the preferred user ID
when supplied and otherwise remains limited to what the server returns without
a selected user profile.

## Milestones and Commit Boundaries

Each milestone ends with its focused tests passing, this ledger updated, and a
separate Conventional Commit. Do not combine milestones merely because their
working changes are adjacent.

### M0 — Record the design and acceptance contract

Status: complete

Deliverables:

- Create the dedicated fix branch.
- Record credential roles, Jellyfin permission constraints, milestone
  boundaries, verification, and the caller-specific follow-up.

Commit: `docs: plan Jellyfin shared cast target`

### M1 — Add API-key Settings lifecycle

Status: complete

Deliverables:

- Add `jellyfin_api_key` to the Settings request model.
- Add redacted configured-state output.
- Add the modal field, configured-state hint, and explicit clear control.
- Update both browser save paths.
- Mirror changes to `RELAYTV_JELLYFIN_API_KEY` through `runtime_config`.
- Include the API key in the live Jellyfin configuration transaction.
- Allow API-key-only enablement without username/password validation.

Exit tests:

- Settings GET redaction/configured-state coverage.
- Preserve, replace, and clear coverage at route and browser-payload seams.
- API-key-only live apply and both Apply-button paths.
- Existing username/password behavior remains green.

Planned commit: `fix: configure Jellyfin cast API key in settings`

### M2 — Separate control and catalog credentials

Status: pending

Deliverables:

- Replace the single request-context token with explicit control and catalog
  tokens captured under `_LOCK` with the configuration generation.
- Route websocket identity, registration, readback, detection, and playback
  reports through the control credential.
- Route catalog, metadata, playback-info, image, and media URL work through the
  catalog credential.
- Replace or narrow ambiguous `active_token()`/`access_token()` helpers.
- Preserve generation checks for authentication, registration, retries, and
  late network results.

Exit tests:

- With both credentials configured, websocket and registration always use the
  API key, including after user authentication completes.
- Catalog and metadata use the login-session token when available.
- API-key fallback works when no login session exists.
- Playback reports do not relabel a shared target as the catalog user.
- Token rotation/reconfiguration retires the old socket and rejects stale
  network publications.

Planned commit: `fix: separate Jellyfin cast and catalog credentials`

### M3 — Make shared-target state observable

Status: pending

Deliverables:

- Publish only non-secret status describing credential source and scope, for
  example `control_auth_source` and `cast_target_scope`.
- Distinguish cast readiness from catalog authentication in Settings status.
- Ensure failed catalog login cannot take down a healthy API-key cast target.
- Keep legacy login-only mode identified as user-scoped.

Exit tests:

- Status contains no credential values or token fingerprints.
- API-key cast readiness survives catalog authentication failure.
- Clear/replace transitions report the correct scope and socket state.

Planned commit: `fix: report Jellyfin cast target scope`

### M4 — Verify multi-user behavior and lifecycle safety

Status: pending

Deliverables:

- Add regression coverage for rapid enable/disable, server switch, key
  rotation, and login completion while the shared socket is running.
- Verify one live heartbeat generation, one control socket, and one stable
  device ID after configuration churn.
- Perform live Jellyfin acceptance with at least two users:
  - the RelayTV catalog user;
  - a different non-admin user allowed to control shared devices.
- Record the expected negative case for a user whose shared-device control is
  disabled.

Live acceptance:

1. Both permitted users see the same RelayTV device in Jellyfin Cast.
2. The second user can send Play, PlayPause, Seek, volume, and Stop.
3. Renaming RelayTV changes only the display name, not the device ID.
4. Catalog browsing still reflects the configured RelayTV catalog user.
5. No duplicate progress posts, orphan sockets, or stale-generation writes
   occur during reconnect/key-rotation cycles.

Planned commit: `test: cover shared Jellyfin cast lifecycle`

### M5 — Complete operator documentation

Status: pending

Deliverables:

- Update `docs/JELLYFIN_OPERATIONS.md` with setup, secret handling, shared
  device permission requirements, status interpretation, fallback behavior,
  rotation, and troubleshooting.
- Regenerate machine-checked inventories only if their public surfaces change.
- Decide whether this user-visible fix warrants a release highlight; do not
  edit `CHANGELOG.md`.
- Mark this roadmap complete with final test and live-verification evidence.

Planned commit: `docs: document shared Jellyfin cast targets`

## Round 2 — Caller-Specific Playback Attribution

Status: deferred, retained in roadmap

The first round makes the device visible and controllable across permitted
users. It does not promise that a cast from another user updates that user's
resume position, played state, favorites, or personalized media policy.

Jellyfin sends `ControllingUserId` with Play and Playstate commands. A follow-up
should evaluate carrying that ID through a command-scoped context while the
receiver's socket and reporting identity remain API-key/userless.

Questions to resolve before implementation:

1. Which catalog and playback-info endpoints safely accept an API key plus the
   controlling user ID without bypassing that user's library restrictions?
2. Does Jellyfin expose a supported way to attribute playback reports from a
   shared/userless receiver session to the controlling user?
3. If attribution requires session mutation, can users switch controllers
   during playback without corrupting watch history or shared visibility?
4. How should RelayTV behave when `ControllingUserId` is absent, stale, or
   changes between playlist commands?
5. What are the equivalent semantics on Emby, whose compatibility must not be
   assumed from Jellyfin behavior?

Proposed follow-up milestones:

- C1: preserve and validate `ControllingUserId` at websocket ingress;
- C2: add command-scoped user-aware metadata/playback-info resolution;
- C3: prototype and verify watch-state attribution against supported Jellyfin
  APIs;
- C4: cover controller handoff, missing-user fallback, permission denial, and
  Emby compatibility;
- C5: document the exact attribution guarantees and limitations.

This round should use its own branch and PR after the shared-target work is
stable. Do not make M1–M5 depend on unresolved attribution behavior.

## Required Quality Gates

Run before every implementation milestone commit:

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
```

Run focused Jellyfin and Settings tests during development before the full
suite. If an intentional public inventory changes, regenerate it with the
matching test's `--write` mode instead of hand-editing the generated section.

## Completion Record

Update this table in the commit that completes each milestone.

| Milestone | Status | Commit | Tests / evidence |
| --- | --- | --- | --- |
| M0 Design | complete | roadmap commit | document review; `git diff --check` |
| M1 Settings | complete | this commit | 253 focused tests; full suite; Ruff; diff check |
| M2 Credential split | pending | — | — |
| M3 Observability | pending | — | — |
| M4 Multi-user verification | pending | — | — |
| M5 Operator docs | pending | — | — |
| Round 2 Caller attribution | deferred | separate branch/PR | retained above |
