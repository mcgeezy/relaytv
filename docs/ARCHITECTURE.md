# RelayTV Architecture

Current-state architecture reference: module ownership, service
boundaries, the machine-checked guardrails that keep them honest, and the
open follow-ups. Distilled from the 2026-06/07 architecture review and
its six-phase refactor (PRs #21–#26, merged 2026-07-03/04); the per-phase
milestone logs live in git history.

## Module Ownership

- `app/relaytv_app/routes/`: FastAPI route modules and compatibility
  aggregation. Domain routers own public endpoint registration; the
  aggregate package (`routes/__init__.py`) still owns shared route
  helpers and cross-domain glue.
- `app/relaytv_app/static/ui/`: browser UI assets loaded by `/ui`.
  `app.js`/`app.css` own the shared remote, `jellyfin.js`/`jellyfin.css` own
  the Jellyfin/Emby browse shell, `seerr.js`/`seerr.css` own the Seerr discovery
  and request shell, and `peers.js`/`peers.css` own the send-to-device sheet.
  These controllers consume public route payloads and do not own catalog,
  request-policy, or playback product behavior.
- `app/relaytv_app/config.py`: runtime config service — typed env
  parsing, the settings bus, and the explicit subprocess env-mirroring
  boundary. Runtime code reads configuration through it instead of
  mutating `os.environ`.
- `app/relaytv_app/playback_service.py`: playback transition commands
  (play-now, queue, close, advance, resume, natural end, stop) — the
  only writer of playback session state outside `state.py`. Also owns the
  read-only handoff snapshot (what is playing, and where) that device transfer
  sends to a peer.
- `app/relaytv_app/player.py`: playback process/runtime adapter (mpv
  lifecycle, Qt shell, CEC, track/property control).
- `app/relaytv_app/postlive_relay.py`: local relay for still-processing
  YouTube replays (see `POSTLIVE_REPLAY.md`).
- `app/relaytv_app/state.py`: persisted queue, history, session, and
  settings data — owns the globals, their setters, and persistence.
- `app/relaytv_app/device_identity.py`: this install's stable identity —
  `device_id` (persisted outside `settings.json` so it survives a settings
  reset), display name, LAN address, and the identity payload advertised to
  peers and over mDNS.
- `app/relaytv_app/realtime.py`: versioned realtime protocol primitives and the
  process-local publication hub. Producers publish transport-neutral events;
  the hub performs thread-to-event-loop handoff, bounded delivery, snapshot
  coalescing, and subscriber accounting. Route adapters own SSE/WebSocket wire
  framing, while playback and integration product behavior stay in their
  established services.
- `app/relaytv_app/discovery_mdns.py`: mDNS advertising and browsing for
  RelayTV devices. Browser callbacks only enqueue names; a worker thread
  resolves them and re-resolves known services on an interval, because
  state-change callbacks alone do not keep entries fresh.
- `app/relaytv_app/peers.py`: peer device product behavior — registry
  persistence, address validation, reachability probes, and the wire form of a
  transferred queue item. Peers exchange display-safe items and the receiver
  rebuilds playable items from the URL, so resolved streams and provider
  tokens never cross devices. A peer's API token is stored only in the
  mode-0600 peer file, never returned by an endpoint, and never logged.
- `app/relaytv_app/resolver.py`: URL validation, provider
  classification, and stream resolution.
- `app/relaytv_app/integrations/jellyfin_service.py`: Jellyfin/Emby
  product behavior — command ingress, stream selection and transcode
  policy, track preferences, metadata enrichment, stopped/progress
  payloads.
- `app/relaytv_app/integrations/jellyfin_receiver.py`: Jellyfin/Emby
  transport, auth, server-type detection, status, catalog cache, and
  progress/stopped calls.
- `app/relaytv_app/integrations/jellyfin_ws.py`: the control socket that
  makes the device a cast target. Transport only, like the receiver: it
  normalizes inbound `Play`/`Playstate`/`GeneralCommand` messages and hands
  them to a command sink the routes package registers, so the socket reuses
  the same ingress as `POST /integrations/jellyfin/command` rather than
  carrying a second copy of command handling. Each connection generation
  owns its threads, queue, and stop flag, and configuration changes run as
  transactions that suspend the socket — a retired generation can never be
  restarted, publish status, or reach the player.
- `app/relaytv_app/integrations/seerr_client.py`: immutable Seerr configuration
  snapshots and secret-safe HTTP transport. It bounds requests and response
  bodies, rejects cross-origin redirects, and maps upstream failures into safe
  RelayTV errors.
- `app/relaytv_app/integrations/seerr_service.py`: Seerr product behavior —
  discovery, search, item/request normalization, request policy, and the
  validated Jellyfin playback bridge. It exposes an allowlisted product model,
  never a generic upstream proxy.
- `app/relaytv_app/integrations/seerr_sessions.py`: bounded, memory-only
  caller sessions created through Seerr's Jellyfin Quick Connect flow. The
  browser receives only an opaque RelayTV cookie; upstream cookies and flow
  secrets never leave the server.
- `app/relaytv_app/routes/seerr.py`: the public Seerr endpoint surface and
  request models. Playback enters through the Jellyfin command sink and the
  established playback service; neither the route nor Seerr service writes
  playback globals.
- Jellyfin shared-cast commands expose an initiating controller identity, but
  RelayTV does not yet switch catalog/watch-state attribution per caller.
  `jellyfin_auth_mode=user_login` is one operator-configured account, not a
  caller session; dynamic caller attribution remains an open product follow-up.
- `scripts/`: install, doctor, host operations, and release support.

## Machine-Checked Guardrails

Each boundary above is pinned by a test; three of them regenerate a
companion inventory doc (rerun with the test's `--write` mode after
intentional changes and commit the diff):

- Route surface: `tests/test_route_inventory.py` (hand-maintained
  method/path/function list; public paths and aliases must stay stable).
- Env/config boundary: `tests/test_env_inventory.py` →
  `ENV_INVENTORY.md`. Also pins the subprocess mirroring contract:
  settings-bus values are consumed in-process, and only pinned
  operator-provided variables are mirrored to child processes.
- Playback transition writers: `tests/test_transition_inventory.py` →
  `TRANSITION_INVENTORY.md`. Playback session globals are written only
  by `state.py` and `playback_service.py`.
- Jellyfin route surface: `tests/test_jellyfin_inventory.py` →
  `JELLYFIN_INVENTORY.md`. Product logic belongs in
  `jellyfin_service.py`; the receiver stays transport-only.
- Runtime profiles: `tests/test_runtime_matrix.py` →
  `OPERATIONS_TEST_MATRIX.md` decision table.

## API Trust Boundary

RelayTV assumes a trusted LAN by default. Write endpoints
(POST/PUT/PATCH/DELETE) can be protected with optional bearer auth via
`RELAYTV_API_TOKEN` (env-only: never persisted to settings, never
returned by `/settings`, never logged). Reads — health, status, assets,
UI — stay open. See `app/relaytv_app/api_auth.py` and
`tests/test_api_auth.py` for the contract.

## Realtime Compatibility Boundary

The versioned WebSocket routes are read-only notification channels. Playback,
queue, settings, and integration commands remain HTTP writes behind the API
trust boundary above; adding commands to a socket requires a separate
authenticated protocol design. Native clients may send bearer credentials in
an `Authorization` header, but credentials must never appear in WebSocket URLs
or query strings.

`GET /realtime/capabilities` is the transport-negotiation boundary. A `404`
identifies a server that predates capability discovery, so companion clients
must retain their legacy SSE and HTTP-polling paths. The compatible
`/ui/events` and `/x11/overlay/events` SSE routes have no scheduled removal.
Reconsider removal only after supported Home Assistant and Android releases no
longer need them, browser and proxy fallback telemetry is stable, and minimum
versions plus migration paths are documented. Retaining the thin SSE adapters
indefinitely is preferable to breaking slowly updated companion installations.

## Open Follow-Ups

Carried from the review, in rough value order:

1. Complete the temporary application-wide functionality remediation tracked
   in `FUNCTIONALITY_AUDIT.md`, then remove that working document and retain
   only genuinely open architecture follow-ups here.
2. Extend the checked-in browser smoke beyond the Jellyfin, IPTV, and
   send-to-device shells to settings, general queue actions, and the remaining
   `/ui` surfaces.
3. Versioned models for queue/history/session/settings — migrations
   remain implicit.
4. Continue shrinking `routes/__init__.py` (shared helpers, overlay/idle
   behavior, status payload construction).
5. Consolidate provider/URL classification, still duplicated between
   resolver and player paths.
6. Keep splitting `test_smoke.py` by behavior.

## Non-Goals

- No FastAPI replacement; no frontend framework unless plain static
  assets become limiting.
- No removal of endpoint aliases until companion apps and Home Assistant
  integrations have migration paths.
- No playback backend rewrite ahead of transition-ownership needs.
