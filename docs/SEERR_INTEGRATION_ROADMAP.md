# Seerr Integration Roadmap

Status: M0-M3 complete; M4 shared request creation awaits operator confirmation

Primary branch: `feat/seerr-integration`

Base: `main` at `ae81d26` (`v0.9.2`)

Compatibility target: Seerr `>=3.1.0`; development/soak baseline `3.4.1`

> This is a branch-only engineering roadmap. Update it at each milestone, then
> remove it before merge and carry the durable operator/API material into the
> normal documentation set. Engineering roadmaps are not part of RelayTV's
> public documentation tree.

## Objective

Add a first-class Seerr experience to RelayTV for discovery, search, request
status, and safe media requests. Keep Seerr responsible for request policy and
Radarr/Sonarr orchestration, keep Jellyfin/Emby responsible for playback, and
keep every Seerr credential out of the browser.

The intended user flow is:

```text
Settings -> configure/test Seerr -> open Seerr shell
         -> discover/search -> inspect movie or series
         -> see availability/request state
         -> request safely, or play through the existing Jellyfin integration
```

The initial integration must remain useful without the unmerged realtime
transport branch. Before implementation begins, update this branch from the
then-current `main`; if the realtime work has landed, local Seerr mutations can
also publish a transport-neutral refresh hint.

## Recommendation

Build a narrow, native RelayTV adapter over Seerr's documented API:

- `seerr_client.py` owns HTTP transport, authentication headers, timeouts, and
  upstream error decoding.
- `seerr_service.py` owns RelayTV-facing product models, status normalization,
  request policy, and the optional Jellyfin playback bridge.
- `routes/seerr.py` owns the public RelayTV endpoint surface and request models.
- `static/ui/seerr.js` and `static/ui/seerr.css` own a responsive Seerr shell
  consistent with the existing Jellyfin and IPTV surfaces.
- RelayTV exposes an allowlisted product API, not an arbitrary Seerr proxy.

Do not embed Seerr in an iframe. An iframe would duplicate navigation, inherit
Seerr's cookie/CSP/reverse-proxy constraints, and make a TV-first workflow
worse. Do not send the global API key to browser JavaScript or use it in a URL.

## Confirmed Seerr Constraints

The plan was checked on 2026-08-22 against Seerr's official OpenAPI definition
and the local Seerr `3.4.1` deployment.

- Seerr supports cookie authentication and a global `X-Api-Key` header.
- The global API key grants administrator access and acts as the first/root
  user. It is not a scoped integration credential.
- `POST /api/v1/request` accepts an optional `userId`, but request policy such
  as auto-approval still follows the authenticated API-key owner. Setting a
  target user therefore changes attribution, not the effective caller.
- Per-user API keys are not currently available.
- Cookie-authenticated calls do preserve the actual Seerr user's permissions,
  visibility, quotas, and request behavior.
- Search, discovery, item detail, request listing, request creation, and
  Jellyfin Quick Connect authentication are present in the `3.4.1` API.
- Movie/series results use TMDB IDs. Available Jellyfin-backed items may include
  `mediaInfo.jellyfinMediaId`, which can be bridged to RelayTV playback only
  after validating that it resolves to the same media item on RelayTV's active
  Jellyfin/Emby server.

Relevant upstream references:

- [Seerr OpenAPI definition](https://github.com/seerr-team/seerr/blob/develop/seerr-api.yml)
- [Seerr API-key warning](https://docs.seerr.dev/using-seerr/settings/general/)
- [Create request API](https://docs.seerr.dev/api/create-new-request/)
- [Seerr user permissions and limits](https://docs.seerr.dev/using-seerr/settings/users/)
- [Open per-user API-key request](https://github.com/seerr-team/seerr/issues/2582)
- [Request attribution versus API-key permissions](https://github.com/seerr-team/seerr/issues/2678)

## Authentication Strategy

### Stage 1: server-level API key

Use the Seerr URL and global API key configured by an operator. The key stays
inside RelayTV and is attached only as an `X-Api-Key` header to the configured
Seerr origin.

This mode supports the complete read experience. Writes are deliberately
gated by a separate `seerr_shared_requests_enabled` setting that defaults to
`false`. When enabled, the UI must say that requests use Seerr's administrator
API identity and may auto-approve regardless of the attributed user's normal
permissions.

An optional operator-selected `seerr_request_user_id` may set request
attribution. It is selected from sanitized user records and is never accepted
as a free caller-controlled value on a request route. This does not claim to
enforce that user's quota or approval policy.

### Stage 2: caller-specific sessions

Add this after the server-level experience is stable. It remains in scope for
the roadmap even if delivered in a follow-up PR.

Use Seerr's Jellyfin Quick Connect flow instead of collecting or persisting a
Jellyfin password:

1. RelayTV initiates Quick Connect with Seerr and returns only the approval code
   and an opaque RelayTV flow identifier.
2. The user approves the code through Jellyfin.
3. RelayTV completes authentication with Seerr and captures the resulting
   Seerr session cookie.
4. RelayTV stores that upstream cookie only in memory, keyed by a cryptographic
   opaque session identifier sent to the browser as `HttpOnly`, `SameSite=Strict`.
5. Allowlisted Seerr calls use that caller's upstream session. Logout, expiry,
   server reconfiguration, and process shutdown retire it.

Caller sessions become the preferred write path and correctly defer quotas,
permissions, request visibility, and auto-approval to Seerr. The global API key
remains available for health checks and the explicitly enabled shared mode.
No upstream session secret is persisted, returned in JSON, logged, or sent to a
different configured Seerr origin.

## Configuration Contract

Persist the following settings using the existing settings bus and atomic
`settings.json` writer:

- `seerr_enabled: bool` — default `false`
- `seerr_server_url: str` — origin/base URL, without credentials
- `seerr_api_key: str` — secret; write-only through `/settings`
- `seerr_shared_requests_enabled: bool` — default `false`
- `seerr_request_user_id: int | null` — optional shared-mode attribution

Operator environment defaults:

- `RELAYTV_SEERR_ENABLED`
- `RELAYTV_SEERR_SERVER_URL`
- `RELAYTV_SEERR_API_KEY`
- `RELAYTV_SEERR_SHARED_REQUESTS_ENABLED`
- `RELAYTV_SEERR_REQUEST_USER_ID`

`GET /settings` returns an empty `seerr_api_key`, plus
`seerr_api_key_configured: bool`. Saving an empty key preserves the existing
secret unless an explicit clear action is requested. The environment inventory
must be regenerated rather than edited by hand.

URL normalization accepts HTTP or HTTPS, strips a trailing slash and an
accidental trailing `/api/v1`, rejects embedded credentials/fragments, and
keeps all subsequent requests on the configured origin. Redirects must not
forward `X-Api-Key` to a different origin.

Settings apply live. A test must prove that an in-flight response from the old
configuration cannot populate state for the replacement configuration. The
preferred implementation is an immutable configuration snapshot per call and
no mutable global response cache, not another background lifecycle.

## RelayTV API Contract

The initial route surface should be small and semantic:

```text
GET  /integrations/seerr/status
POST /integrations/seerr/test
GET  /integrations/seerr/users

GET  /seerr/discover
GET  /seerr/search
GET  /seerr/item/{media_type}/{media_id}
GET  /seerr/requests
POST /seerr/requests
GET  /seerr/image/{size}/{image_path:path}
```

Proposed behavior:

- `status` returns enabled/configured/reachable state, Seerr version,
  application title, media-server type, active auth mode, and whether writes
  are allowed. It never returns a key, cookie, upstream user token, email, or
  raw upstream object.
- `test` probes public status/settings and authenticated `/auth/me`, returning
  a sanitized identity summary and actionable configuration errors.
- `discover` accepts an allowlisted section (`trending`, `movies`, `tv`) and a
  bounded page number.
- `search` accepts a bounded query and page number. Person results are omitted
  in the first UI unless a person-detail experience is intentionally added.
- `item` requires `movie` or `tv` plus a positive TMDB ID and returns a stable,
  normalized RelayTV detail model.
- `requests` lists normalized request records. Shared mode defaults to the
  configured attribution user when present; caller mode relies on Seerr's own
  visibility rules.
- request creation accepts only `media_type`, `media_id`, `seasons`, and `is_4k`.
  The first release omits advanced server, quality-profile, root-folder, tag,
  arbitrary-user, and `ignoreQuota` fields. Seerr defaults remain authoritative.
- `image` accepts only fixed image sizes and a validated TMDB image path, then
  uses Seerr's image proxy/cache when enabled. It must preserve useful cache
  validators while preventing path traversal and arbitrary upstream fetches.

Do not initially expose approve, decline, retry, delete, user-management,
Radarr/Sonarr settings, or a generic path passthrough. These are administrator
operations and are not needed for the TV request workflow.

All POST routes remain behind RelayTV's existing optional
`RELAYTV_API_TOKEN` write guard. That token remains env-only and is unrelated
to the Seerr API key.

## Normalized Product Model

Browser code should not depend on Seerr's raw numeric enums or large nested
payloads. The service maps them to stable strings and a small allowlist.

Media status mapping for Seerr:

| Upstream | RelayTV value |
| --- | --- |
| `1` | `unknown` |
| `2` | `pending` |
| `3` | `processing` |
| `4` | `partially_available` |
| `5` | `available` |
| `6` | `blocklisted` |
| `7` | `deleted` |

Request status mapping:

| Upstream | RelayTV value |
| --- | --- |
| `1` | `pending` |
| `2` | `approved` |
| `3` | `declined` |
| `4` | `failed` |
| `5` | `completed` |

Unknown future values map to `unknown` and retain no raw secret-bearing
payload. Tests pin these mappings because related Overseerr variants do not
always assign the same numeric values.

Normalized cards include only the fields the UI uses: media type, TMDB ID,
title, original title, year/date, overview excerpt, poster/backdrop URLs,
rating, media status, request summary, and a boolean indicating whether a
validated RelayTV playback bridge is available.

## Jellyfin/Emby Playback Bridge

Seerr discovery is not a new playback provider. Available content continues
through `jellyfin_service.py` and `playback_service.py`.

When Seerr returns `jellyfinMediaId`:

1. Resolve that item through RelayTV's configured Jellyfin/Emby receiver.
2. Verify the returned provider metadata matches the Seerr media type and TMDB
   ID; a title-only match is insufficient.
3. Return a RelayTV-owned playback capability/reference, not a Seerr token or
   stream URL.
4. Delegate play/queue actions to the existing Jellyfin product service and
   playback transition service.

If the ID is absent, the servers differ, or validation fails, keep the Seerr
item request-only. Do not guess a Jellyfin item by title and do not let
`seerr_service.py` write playback globals.

## Browser Experience

Add a Seerr launch tile/button only when the integration is enabled. The shell
uses separate assets and follows existing modal/navigation ownership.

Initial sections:

- Discover: mixed trending cards
- Movies: paginated movie discovery
- Series: paginated TV discovery
- Search: debounced cross-media search
- Requests: recent requests with filters for all/pending/processing/available

The detail panel shows overview, year, runtime, genres, availability, existing
request state, and TV seasons. Its primary action is state-aware:

- `Play` / `Queue` only after the Jellyfin bridge validates
- `Request movie`
- `Request selected seasons` / `Request all seasons`
- non-actionable `Pending`, `Processing`, `Available`, `Blocklisted`, or
  `Shared requests disabled` states as appropriate

The browser aborts retired search/page/detail requests, debounces search, loads
one pagination request at a time, and ignores late results from a closed shell
or superseded query. It polls request state only while the Seerr shell is
visible (target: 30 seconds), refreshes immediately after a local mutation,
and pauses when the document is hidden.

The settings modal includes:

- enable toggle
- Seerr server URL
- write-only API key with configured/clear state
- `Test connection` action
- shared request toggle with an administrator/auto-approval warning
- optional sanitized request-attribution user selector
- connection/version/auth-mode status

## Error and Availability Contract

Translate upstream failures to a stable RelayTV error body with a short code
and safe message:

- invalid configuration -> `400`
- disabled/not configured -> `503`
- upstream authentication failure -> `502` with `seerr_auth_failed`
- upstream permission failure -> `403`
- missing item/request -> `404`
- duplicate request -> `409`
- no requestable seasons (`202` upstream) -> a successful semantic response
  with `created: false` and an explanatory reason
- timeout/unreachable/upstream `5xx` -> `502` or `504`

Do not return arbitrary upstream HTML, stack traces, headers, or full user
objects. Logs include operation, configured host, latency, status category, and
safe error code; they never include the API key, cookie, request headers,
credentials, or query strings containing user input.

## Milestones

Each completed milestone updates this document and lands as a focused
Conventional Commit. Run the proportional tests at each milestone and the full
quality gate before declaring the branch ready.

| Milestone | Deliverable | Status |
| --- | --- | --- |
| M0 | API/security review and implementation roadmap | Complete |
| M1 | Immutable client/config model, secret-safe settings, status/test routes | Complete |
| M2 | Normalized discover/search/detail/request-read API | Complete |
| M3 | Responsive Seerr browser shell and settings UX | Complete |
| M4 | Explicitly gated shared request creation and TV season selection | Pending |
| M5 | Validated Seerr-to-Jellyfin play/queue bridge | Pending |
| M6 | Caller-specific Quick Connect sessions (follow-up permitted) | Pending |
| M7 | Compatibility, security, field soak, operator/API docs, rollout decision | Pending |

### Milestone log

- **M1 — 2026-08-23:** Added immutable runtime configuration snapshots, a
  bounded secret-safe HTTP client with cross-origin redirect rejection,
  normalized/sanitized upstream failures, write-only API-key settings with an
  explicit clear action, live settings-bus application, and Seerr status/test
  routes. Added client, route, settings, startup-sync, route-inventory, and
  environment-inventory coverage. No background lifecycle or mutable response
  cache was introduced, so a response remains confined to the configuration
  snapshot that initiated it.
- **M2 — 2026-08-23:** Added bounded, allowlisted discovery, search, movie/TV
  detail, request-list, and image routes. The product service now maps media
  and request enums into stable RelayTV values, omits people and raw upstream
  objects, bounds text and pagination, and keeps playback explicitly
  unavailable until M5 validates the Jellyfin bridge. Images traverse only
  Seerr's TMDB proxy at fixed sizes with a validated single-file path; RelayTV
  forwards the image body and useful cache validators without exposing the API
  key, cookies, or arbitrary upstream headers. Added client, service, route,
  validation, sanitization, image-proxy, and public-route-inventory coverage.
- **M3 — 2026-08-23:** Added a responsive native Seerr shell for trending,
  movie, series, search, detail, and recent-request browsing, plus a settings
  section for enablement, write-only API-key replacement/clear, connection
  testing, shared-request warning, and sanitized attribution selection. Browser
  requests are bounded, abortable, and generation-checked; search is debounced,
  request polling runs only in the visible Requests view, and upstream strings
  are assigned through DOM text nodes rather than raw HTML. Added a sanitized
  user-selector endpoint and a Playwright smoke scenario covering phone/desktop
  layout, detail navigation, retired-search rejection, request state, overflow,
  and nested-interactive controls. The local environment lacks the Playwright
  package, so the script is syntax-checked and retained for deployment smoke.

## Verification Plan

### Automated

- Client tests with a controlled fake upstream for URL construction, fixed
  timeouts, no cross-origin redirect credential forwarding, JSON parsing, and
  safe error mapping.
- Service tests for media/request status enums, pagination bounds, item
  normalization, TV season selection, shared-write gating, user attribution,
  and unknown future fields/statuses.
- Secret tests proving the API key is absent from `/settings`, status/test
  payloads, logs, exception strings, URLs, and browser assets.
- Configuration-generation tests proving old-server results are discarded or
  remain confined to their originating HTTP response after a live settings
  change.
- Route tests for disabled, unauthenticated, duplicate, no-seasons, timeout,
  and upstream failure cases.
- Existing API-token tests extended to cover every Seerr write route.
- Route inventory updated intentionally.
- Environment inventory regenerated with
  `PYTHONPATH=app python3 tests/test_env_inventory.py --write`.
- Browser smoke for shell structure, focus/escape behavior, request-state
  actions, late-response rejection, mobile layout, and nested-interactive
  controls.
- Jellyfin bridge tests proving mismatched/missing provider IDs never play and
  successful actions still flow through the established playback boundary.

### Live against Seerr 3.4.1

- Configure and test through the RelayTV settings modal without exposing the
  key in browser network responses.
- Verify trending, movies, series, search, poster/backdrop loading, detail, and
  request listing on desktop and phone widths.
- Verify existing available Jellyfin media offers Play only after ID/provider
  validation and that playback reports progress normally.
- Keep shared requests disabled and prove every request action is unavailable.
- Enable shared requests deliberately, request an operator-selected disposable
  title, verify the warning and attribution, then clean up through Seerr/Radarr
  using the normal operator workflow. Do not use an arbitrary title during an
  automated soak because the admin API identity may auto-approve it.
- Complete Quick Connect with two non-admin users and verify their visibility,
  quotas, approval behavior, session separation, logout, expiry, and restart
  behavior.
- Change Seerr URL/key during a delayed request and verify no old-origin result
  becomes current state.
- Review RelayTV and Seerr logs for secrets and unexpected request duplication.

### Repository quality gate

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
```

## Rollout and Rollback

- Default disabled; upgrading RelayTV changes no existing behavior.
- Read-only discovery can soak before shared writes are enabled.
- Shared requests remain a second explicit operator opt-in.
- Caller sessions can be disabled independently while retaining read-only
  server-key browsing.
- The existing Jellyfin/Emby UI and playback routes remain unchanged and are
  the rollback path for available media.
- Before merge, replace this roadmap with durable additions to `API.md`,
  `ARCHITECTURE.md`, an operator runbook, and (if warranted) a release highlight.

## Inputs Needed Before M4/M6

No input is required to implement M1-M3 safely. Before enabling request writes,
confirm whether this deployment intentionally wants shared API-key requests to
auto-approve. Before M6, choose the caller-session lifetime and whether a
browser should remain signed in across a RelayTV process restart; the
recommended first implementation is memory-only and requires Quick Connect
again after restart.
