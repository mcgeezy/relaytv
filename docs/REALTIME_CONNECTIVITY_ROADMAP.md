# Realtime Connectivity Roadmap

Status: M5 complete; cross-repository verification next
Primary branch: `feat/realtime-connectivity`  
Repositories: `relaytv`, `relaytv-ha`, `relaytv-android`

## Objective

Move RelayTV's live state delivery toward a versioned WebSocket protocol while
preserving reliable operation across browsers, the Home Assistant integration,
the Android application, older RelayTV servers, and restrictive reverse
proxies.

Every client will automatically choose the best transport the server and
network support:

```text
WebSocket -> Server-Sent Events -> adaptive HTTP polling
```

The migration must improve the server's concurrency model rather than copy the
current SSE implementation into a WebSocket handler. In particular, it will
introduce thread-safe fan-out, bounded backpressure, a shared state sampler,
version negotiation, lifecycle ownership, and explicit security rules.

## Non-Goals

- Do not move playback commands onto the initial WebSocket protocol. Writes
  remain authenticated HTTP requests.
- Do not put `RELAYTV_API_TOKEN` in a URL, query string, persisted RelayTV
  settings, event payload, or log.
- Do not remove `/ui/events` or `/x11/overlay/events` during the initial rollout.
- Do not make WebSocket availability a requirement for controlling RelayTV.
- Do not redesign the playback service, Jellyfin control socket, or X11 overlay
  playback polling as part of transport parity.
- Do not introduce an external message broker for the appliance's current
  single-process deployment model.

## Current State

### RelayTV server and browser

- `GET /ui/events` publishes `hello`, `ping`, `playback`, `status`, `queue`, and
  `jellyfin` SSE events.
- Each UI subscriber independently samples playback every 750 ms and constructs
  full status approximately every five seconds.
- `GET /x11/overlay/events` carries overlay hello, ping, and toast messages.
- Subscriber queues are module globals in `routes/__init__.py`. Synchronous
  producers call `asyncio.Queue.put_nowait()` without an explicit event-loop
  handoff, and queue overflow currently retires the subscriber.
- The browser uses EventSource, reconnects stale streams, and falls back to an
  eight-second HTTP refresh loop.
- The X11 overlay uses SSE for toasts but continues to poll playback state.

### Home Assistant

- `RelayTVCoordinator` bootstraps from `/status`, consumes `/ui/events`, and
  falls back to a three-second coordinator poll.
- Its event dispatch already distinguishes authoritative `status` snapshots,
  compact `playback` snapshots, and refresh hints.
- Transport state and lifecycle names are SSE-specific.
- The manifest declares `local_polling`; this should become `local_push` when
  the generic push coordinator is released.

### Android

- The main activity embeds RelayTV's server-hosted browser UI, so browser
  transport changes automatically apply to the WebView.
- `MediaControlService` is a separate native consumer. It polls `/status` every
  three seconds while playing, five seconds while paused, and ten seconds while
  idle.
- The shared OkHttp client has finite call and read timeouts suitable for normal
  API requests but not long-lived streams.
- The current checkout must be updated from `origin/main` and moved to a new
  feature branch before Android implementation begins.

## Protocol and Capability Contract

### Capability discovery

Add an open, static capability endpoint:

```http
GET /realtime/capabilities
```

Initial response shape:

```json
{
  "protocol_version": 1,
  "preferred_transport": "websocket",
  "websocket": {
    "enabled": true,
    "ui": "/ui/ws",
    "overlay": "/x11/overlay/ws",
    "subprotocol": "relaytv.realtime.v1"
  },
  "sse": {
    "enabled": true,
    "ui": "/ui/events",
    "overlay": "/x11/overlay/events"
  },
  "heartbeat_sec": 5,
  "replay": false
}
```

The endpoint contains no credentials or dynamic subscriber data and advertises
only transports implemented by that server generation. A `404`
means the server predates capability discovery; compatible clients then select
the legacy SSE endpoint or HTTP polling without repeatedly probing WebSocket.

### WebSocket routes

- `/ui/ws`: browser UI, Home Assistant, and Android native media controls.
- `/x11/overlay/ws`: X11 overlay toast delivery.
- Subprotocol: `relaytv.realtime.v1`.

### Message envelope

```json
{
  "version": 1,
  "event": "playback",
  "sequence": 42,
  "timestamp": 1787420000.0,
  "data": {}
}
```

Rules:

- `hello` confirms the negotiated version and supplies heartbeat/replay
  metadata.
- `status` remains the authoritative full snapshot.
- `playback` remains a compact fast-path snapshot.
- `queue` and `jellyfin` remain refresh hints even when they carry snapshots.
- `ping` is an application-level JSON heartbeat because browser JavaScript
  cannot observe protocol-level ping/pong frames.
- Sequence numbers detect missed or coalesced delivery. They do not imply a
  replay log. A gap causes the client to refresh `/status`.
- A new connection receives `hello` followed by initial authoritative state.
- SSE adapters preserve the existing event name and payload shape; they do not
  expose the WebSocket envelope to existing SSE consumers.

## Security Contract

The initial sockets are read-only notification channels.

- Ignore or reject client application messages; do not add command handling.
- Browser handshakes include `Origin`. Validate it against the effective public
  host and scheme, accounting deliberately for configured reverse-proxy
  headers.
- Native Home Assistant and OkHttp clients may omit `Origin`. Accept an absent
  origin for the read-only route; browsers cannot normally suppress theirs.
- The optional API token remains unnecessary for reads under RelayTV's
  local-first API contract.
- Native clients may send their configured bearer header, but the server must
  never require browsers to place the token in the WebSocket URL.
- A future bidirectional command protocol requires a separate authenticated
  design, such as a short-lived HTTP-minted connection ticket.

## Server Architecture

Introduce a transport-neutral realtime hub outside the routes aggregate.

Responsibilities:

- typed event publication by channel;
- explicit handoff from producer threads to each subscriber's owning event
  loop;
- subscription registration and cleanup;
- bounded queues and delivery counters;
- latest-wins coalescing for `playback` and `status`;
- defined overflow behavior for hints and non-replayable overlay toasts;
- active subscriber counts by channel and transport;
- clean lifespan startup and shutdown.

Introduce one application-level UI snapshot sampler:

- run only while UI-channel subscribers exist;
- sample compact playback state at the current hot-state cadence;
- build full status at the current cadence or when playback/queue shape changes;
- publish only changed snapshots;
- avoid blocking the event loop during expensive synchronous status work;
- serve all SSE and WebSocket subscribers from the same results.

The existing Jellyfin outbound control socket remains independent. It is not
the inbound realtime hub and must not acquire route or playback product logic.

## Client Selection State Machine

All clients implement the same logical policy:

1. Bootstrap from `/status` when a full state snapshot is required.
2. Load `/realtime/capabilities` once per server/network generation.
3. Connect the advertised WebSocket and require the versioned `hello`.
4. On unsupported protocol or repeated handshake failure, activate SSE.
5. On SSE failure, activate the existing adaptive polling behavior.
6. Periodically re-evaluate a better transport after a cooldown or immediately
   after an explicit network/visibility recovery signal.
7. Keep exactly one push transport active.
8. Give every connection attempt a generation number. Retired callbacks may
   close their own resources but may not publish state or schedule reconnects.
9. Refresh `/status` after reconnect, sequence gap, or transport downgrade.

Backoff uses exponential delay with jitter and a bounded maximum. Capability
responses that explicitly omit WebSocket suppress futile probes until the
capability cache is refreshed.

## Repository Changes

### `relaytv`

- Add capability and WebSocket routes with route-inventory coverage.
- Add the realtime protocol, hub, shared sampler, and lifecycle integration.
- Adapt both SSE endpoints to the hub without breaking their wire contract.
- Replace raw overlay subscriber-set checks with hub metrics.
- Refactor browser event handling into a transport-independent controller.
- Prefer WebSocket, then EventSource, then the current HTTP refresh fallback.
- Preserve visibility, `online`, and `pageshow` recovery behavior.
- Add active-transport and delivery telemetry without exposing client data.
- Update API, proxy, architecture, and native-runtime documentation.
- Add nginx WebSocket upgrade headers while retaining SSE buffering guidance.

### `relaytv-ha`

- Add capability lookup and WebSocket URL construction to `RelayTVApi`.
- Rename SSE-specific coordinator state to generic push-transport state.
- Use the shared Home Assistant aiohttp session for WebSocket connections.
- Pass decoded WS and SSE events through one protocol-independent dispatcher.
- Preserve `/status` bootstrap, three-second polling fallback, material-state
  deduplication, and command-triggered refreshes.
- Track the active transport, connection generation, and last sequence.
- Close the owned socket/response before coordinator unload or reconfigure.
- Change manifest `iot_class` from `local_polling` to `local_push`.
- Update integration documentation and add stream lifecycle/fallback tests.

### `relaytv-android`

- Start from updated `origin/main` on a new feature branch.
- Add a native `RelayRealtimeClient` with a dedicated streaming OkHttp client.
- Add OkHttp SSE support for compatibility fallback.
- Integrate the client into `MediaControlService`; do not duplicate it in
  `MainActivity` because the WebView receives the browser implementation.
- Bootstrap with `/status`, merge compact playback patches into the last full
  native status, and refresh on reconnect or sequence gaps.
- Preserve the 700 ms command confirmation and current polling intervals when
  neither push transport is healthy.
- Increment generation and close owned work on server switch, service destroy,
  media-control disable, and network change.
- Retain the last valid snapshot during a bounded reconnect window rather than
  reporting idle after one transport failure.
- Keep the existing five-minute idle service shutdown policy.
- Add MockWebServer tests and update privacy/release verification docs.

### X11 overlay

- Migrate toast delivery after the main UI is stable.
- Prefer `/x11/overlay/ws` and retain `/x11/overlay/events` fallback.
- Keep `/x11/overlay/client_state` as HTTP telemetry during parity work.
- Keep playback-state polling separate until a later, measured optimization.

## Milestones

Milestone changes update this document and land as focused Conventional Commit
commits. A milestone is complete only after its listed tests pass.

| Milestone | Repository | Deliverable | Status |
| --- | --- | --- | --- |
| M0 | `relaytv` | Protocol, capabilities, security contract, and test fixtures | Complete |
| M1 | `relaytv` | Thread-safe hub and SSE adapters; no client behavior change | Complete |
| M2 | `relaytv` | `/ui/ws`, shared sampler, and browser automatic selection | Complete |
| M3 | `relaytv-ha` | WS/SSE/poll coordinator and `local_push` metadata | Complete |
| M4 | `relaytv-android` | Native media-service WS/SSE/poll client | Complete |
| M5 | `relaytv` | X11 overlay WebSocket transport | Complete |
| M6 | all | Cross-version soak, documentation, and rollout decision | Planned |

## Verification Plan

### RelayTV server

- capability response and protocol-version tests;
- route inventory including WebSocket routes;
- allowed same-origin browser handshake;
- rejected foreign browser origin;
- accepted origin-less native read client;
- unsupported subprotocol and inbound-message rejection;
- initial hello/playback/status order;
- thread-to-event-loop publication;
- slow consumer, coalescing, overflow, and disconnect cleanup;
- one sampler serving multiple transports;
- shutdown with active clients;
- exact SSE compatibility framing and payload tests;
- browser smoke for WS, SSE fallback, polling fallback, offline recovery, stale
  callbacks, and single-active-transport ownership.

Run the repository quality gates at every server milestone:

```text
ruff check app tests
PYTHONPATH=app pytest -q
git diff --check
```

### Home Assistant

- capability discovery and legacy `404` behavior;
- WS event parsing and SSE compatibility parsing;
- status/playback/hint semantics;
- sequence-gap refresh;
- WS-to-SSE-to-poll fallback;
- entry unload, reconfigure, cancellation, and retired-generation behavior;
- optional bearer header without secret logging;
- coordinator availability and material-state deduplication.

### Android

- capability and URL conversion for HTTP/HTTPS to WS/WSS;
- WS and SSE event parsing;
- compact playback merge and authoritative status replacement;
- reconnect, backoff, stale callback, and active-server switching;
- token header presence without URL leakage;
- command confirmation while push is healthy;
- polling activation only when both push transports are unavailable;
- service shutdown and network recovery;
- physical-device WebView and system-media-control verification.

### Compatibility and soak matrix

- new server with old browser cache, Home Assistant, and Android;
- old server with new Home Assistant and Android;
- token unset, valid, missing, and rejected for HTTP writes;
- direct HTTP, direct HTTPS, nginx, and Caddy;
- Android background/resume, screen-off, Wi-Fi roam, and server switch;
- Home Assistant reload, reconfigure, server restart, and prolonged outage;
- multiple simultaneous browser, HA, Android, and overlay subscribers;
- slow client and reconnect-storm behavior.

## Rollout and Deprecation Gates

Release the server before companion clients. The server release must retain SSE
so old clients remain functional. New companion clients must remain compatible
with servers that return `404` for `/realtime/capabilities`.

Do not schedule SSE removal during this roadmap. Reconsider it only after:

- supported Home Assistant and Android releases prefer WebSocket;
- browser, native client, proxy, and network soak results are stable;
- reconnect, overflow, and fallback telemetry show no regression;
- companion-client minimum versions and migration paths are documented;
- the value of deleting the thin SSE adapter exceeds its compatibility value.

Because HACS and Android installations may update slowly, retaining SSE over
the shared hub indefinitely is an acceptable outcome.

## Release Impact

The WebSocket preference and shared realtime core are user-visible reliability
and efficiency features, so server and companion PRs should use `feat:` titles.
PR descriptions must include user impact, operator/proxy changes, compatibility,
tests, and `Breaking changes: None`. Evaluate a release highlight when the
server milestone is ready to ship; do not add one during planning alone.

## Milestone Log

| Date | Milestone | Result |
| --- | --- | --- |
| 2026-08-22 | Planning | Cross-repository design recorded; implementation not started. |
| 2026-08-22 | M0 | Added versioned protocol primitives, truthful capability discovery, origin policy, public-route coverage, and focused tests. |
| 2026-08-22 | M1 | Added thread-safe loop handoff, bounded/coalescing subscriptions, transport metrics, and SSE adapters over the shared hub. |
| 2026-08-22 | M2 | Added the read-only UI WebSocket, shared snapshot sampler, protocol/origin enforcement, capability activation, and browser WS-to-SSE-to-poll selection. |
| 2026-08-22 | M3 | Added Home Assistant capability discovery, owned WS/SSE/poll selection, sequence and generation guards, atomic reconfiguration, `local_push` metadata, and lifecycle/fallback tests. |
| 2026-08-22 | M4 | Added Android native WebSocket/SSE selection, adaptive polling fallback, streaming-client ownership, generation-safe server/network switching, retained snapshots, and MockWebServer coverage. |
| 2026-08-22 | M5 | Added the versioned X11 overlay WebSocket, origin/subprotocol enforcement, browser WS-to-SSE selection, stale-stream recovery, telemetry continuity, and route/client tests. |
