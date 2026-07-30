# Device Sync Plan: send the queue to another RelayTV device

Planning artifact for the multi-device feature: send queue items, the whole
queue, or live playback from one RelayTV device to another, with manually
added peers and optional local-network auto-discovery.

**Status: Phases 1–3 shipped** (see [Milestones](#8-milestones)). Each phase folds
its user-facing surface into the existing docs as it lands — `API.md` for the
endpoint contract, `ARCHITECTURE.md` for module ownership — and this file
tracks milestone completion and any deviation from the original design. Per
`docs/README.md` (Rule), the plan itself stays out of the permanent public doc
set: retire it once Phase 4 has moved the operator-facing material into a
runbook.

---

## 1. What already exists

Grounding facts from the current tree, because they decide most of the design:

- `app/relaytv_app/discovery_mdns.py` **advertises** `_relaytv._tcp.local.`
  (register only, no browsing). Half of auto-discovery is already built,
  including the optional-`zeroconf` degradation and the async start path.
- `docker-compose.yml` runs `network_mode: host`, so mDNS browsing works in
  the default deployment.
- `routes/queue.py:history_requeue` establishes the pattern this feature
  needs: **queue by reference and let the server rebuild the playable item**,
  because stored/public payloads are display-safe and not replayable.
- `public_media.public_media_item()` already strips `_resolved_stream`,
  `_resolved_audio`, tokens, cookies, and signed query params. That is the
  wire-safe serializer for peer transfer, at no extra cost.
- There is no device identity today. `device_name` is a display string used
  by mDNS and the Jellyfin receiver; nothing is stable or unique per install.

## 2. Core design decision: send by reference, not by resolution

A queue item is one of four things and each travels differently:

| Item type | Portable? | Approach |
| --- | --- | --- |
| Public URL (YouTube, direct media) | Yes | Send `url`; receiver re-resolves with its own yt-dlp, cookies, and quality policy |
| Jellyfin/Emby | Only if the peer has a server configured | Send the share/item URL, never the resolved stream; receiver re-resolves through its own token |
| Upload (`/media/uploads/...`) | Only while the sender stays up | Rewrite to the sender's absolute LAN URL; receiver streams from the sender |
| IPTV channel | Usually | Send stream URL plus channel name as a plain URL item |

**Rule:** the wire payload is a display-safe item (`public_media_item`) plus
title/thumbnail/resume hints. The receiver calls
`jellyfin_service.smart_item_from_url(url)` and overlays the hints, exactly
as `/history/requeue` does today.

Never ship `_resolved_*` fields or the sender's Jellyfin token: they are
sender-scoped, often short-lived, and leak a credential across a trust
boundary for no benefit.

## 3. Device identity (prerequisite)

Add a stable `device_id`: UUID4 generated once and persisted to
`data/device_id` — deliberately not `settings.json`, so it survives a
settings reset and is not user-editable. Needed for:

- filtering this device out of its own discovery results
- de-duplicating a manually added peer against the same box found via mDNS
- loop prevention on import

Extend `discovery_mdns._props()` with `id`, `name`, and `version`. TXT record
additions are backward compatible; peers running older builds simply lack
`id` and fall back to `host:port` identity.

## 4. Peer registry

New `app/relaytv_app/peers.py` (registry + persistence) and
`app/relaytv_app/routes/peers.py` (endpoint registration), following the
service/routes split the architecture doc pins.

JSON-backed at `data/peers.json`, not SQLite: this is 2–10 rows, not the
tens of thousands of channel rows that justified `integrations/iptv_store.py`.
Reuse `state.py`'s atomic-write helper shape and `chmod 0600` the file the
way `iptv_store` does.

Record shape:

```json
{
  "id": "…",
  "name": "Bedroom TV",
  "base_url": "http://192.168.1.42:8080",
  "source": "manual | mdns",
  "token": "…",
  "added_at": 0,
  "last_seen_at": 0,
  "last_ok_at": 0,
  "last_error": ""
}
```

### Secret handling

A peer's `RELAYTV_API_TOKEN` is a *remote* device's secret, so storing it
does not violate the env-only constraint in `AGENTS.md`, which governs this
device's own token. Treat it with the same discipline anyway:

- stored only in `peers.json` (mode `0600`), never in `settings.json`
- never returned by any endpoint — expose `has_token: true` instead
- never logged, including on connection-test failures

Pin all three with assertions in a new `tests/test_peers.py`, mirroring
`tests/test_api_auth.py`.

## 5. Discovery

Add a `browse()` path to `discovery_mdns.py` using `ServiceBrowser` plus a
listener maintaining a TTL'd cache of seen services. Surface results through
`/peers` as unsaved candidates.

Discovered peers are **suggestions, never auto-added**. The user taps to
adopt. This is both the safer default and the better UX: a device list that
grows on its own reads as broken.

Keep `zeroconf` import failure non-fatal, matching the existing advertise
path, and keep browse behind the same `RELAYTV_MDNS_ENABLED` respect plus a
dedicated toggle.

## 6. Receive endpoints

```
POST /queue/import
  { items: [...], mode: "append" | "replace",
    from: { device_id, name, base_url } }
```

Bulk rather than N calls to `/enqueue`: one request, one persist, one SSE
`queue` event, one on-TV toast. Import loops per item through
`playback_service.queue_item` so playback-transition ownership stays where
`docs/TRANSITION_INVENTORY.md` requires.

```
POST /queue/handoff
  { now_playing: {...}, resume_pos, items: [...],
    from: { device_id, name, base_url } }
```

Per-item results come back as `accepted: false, reason: "…"` (for example
`jellyfin_not_configured`) so the sender can report partial success honestly
instead of claiming everything landed.

**Loop guard:** record `from.device_id` on imported items and never
auto-forward an imported item to another peer.

## 7. UX

### Entry points, two levels

- **Queue level** — a cast-style button in the "Up Next" card header. This is
  the primary flow and the one to build first.
- **Item level** — the queue tile currently carries only `✕`
  (`static/ui/app.js`, `qDelBtn`). Add a `⋯` opening a small menu with
  "Send to…". One indirection keeps the tile clean; a device picker per tile
  does not scale.

### The device sheet

Bottom sheet on mobile, popover on desktop, matching the existing
`hdrMenuPanel` pattern and glass system.

```
┌─────────────────────────────┐
│ Send queue to               │
│  12 items · 2h 14m          │
├─────────────────────────────┤
│ ● Bedroom TV      online    │
│ ● Kitchen Pi      online    │
│ ○ Garage          offline   │
├─────────────────────────────┤
│ Found nearby                │
│ + Office TV       add       │
├─────────────────────────────┤
│ + Add device manually       │
└─────────────────────────────┘
```

Online status is a cheap parallel `GET /health` probe on sheet open with a
~1.5s timeout. Offline peers stay listed but disabled — a device that
disappears from the list looks like data loss.

### Mode selection

Tapping a device performs **Copy**, the safe and common case. A segmented
control at the top of the sheet switches mode, so no device row carries three
buttons:

- **Copy** — the sender keeps its queue. Default.
- **Move** — the sender's queue clears only after confirmed receipt
  (two-phase: clear on HTTP 200 only).
- **Handoff** — "Continue on Bedroom TV": sends now-playing plus resume
  position plus the queue, the receiver starts playing, the sender stops.
  This is the flow with the most product value — walking from the couch to
  the bedroom.

### Feedback

- Sender toast: `Sent 12 items to Bedroom TV`. Partial failure shows
  `Sent 10 of 12 · 2 failed` with tap-to-expand detail.
- Receiver: reuse `_push_queue_added_toast_async` for the on-TV overlay —
  `Living Room sent 12 items`.
- Manual add form: base URL (`http://192.168.1.42:8080`) plus optional token,
  with a **Test connection** button that hits `/health` and displays the
  peer's real `device_name` before saving. Never save an unverified peer
  silently.

### Settings

A "Devices" section: discovery toggle, saved peer list (rename, re-token,
remove), and this device's own name and discoverability toggle.

## 8. Milestones

| Phase | Scope | Status |
| --- | --- | --- |
| 1 | `device_id`, peer registry, manual add, `/queue/import`, queue-header sheet, copy whole queue | **done** |
| 2 | mDNS browse, adopt-from-nearby | **done** |
| 3 | Per-item send, Move and Handoff modes | **done** |
| 4 | Operator runbook, Home Assistant and companion-app notes | not started |

Phases 1–3 complete the functional feature. Phase 4 is documentation and
companion-surface work: a `docs/DEVICE_SYNC_OPERATIONS.md` runbook, the Home
Assistant and Android companion notes, and a release highlight for handoff.

Phase 1 also pulled in work originally scheduled later: per-peer online probes
(planned for Phase 2) landed with the sheet, because a device list without
status is not usable, and the peer API surface went into `docs/API.md`
immediately rather than waiting for Phase 4.

### Phase 1 as shipped

- `device_identity.py` — `device_id` persisted to `/data/device_id` (mode
  0600), pinnable with `RELAYTV_DEVICE_ID`; owns the display name, LAN address,
  and identity payload. `discovery_mdns` now delegates its name/address lookups
  here and advertises `id`, `name`, and `app` TXT records for Phase 2.
- `peers.py` — JSON registry at `/data/peers.json` (mode 0600), address
  normalization, identity probes, and queue wire serialization.
- `routes/peers.py` — `GET /peers/identity`, `GET|POST /peers`,
  `PATCH|DELETE /peers/{id}`, `POST /peers/probe`,
  `POST /peers/{id}/probe`, `POST /peers/{id}/send`.
- `routes/queue.py` — `POST /queue/import` with per-item results, replace mode,
  the loop guard, and the receiver toast.
- `static/ui/peers.{js,css}` — Send pill in the queue header plus the sheet:
  device rows with live status, tap to send, manual add with Test connection,
  remove.
- `tests/test_peers.py` — 14 cases: registry CRUD, token redaction on disk and
  over the API, self/duplicate rejection, wire serialization, import
  rebuilding, and send summaries.
- `scripts/peers-ui-smoke.js` — browser smoke needing two devices:

  ```sh
  node scripts/peers-ui-smoke.js --base=http://<sender> --peer=http://<receiver>
  ```

### Phase 2 as shipped

- `discovery_mdns.py` — `ServiceBrowser` on the RelayTV service type. Callbacks
  only enqueue names; a worker thread resolves them, and re-resolves known
  services every `RELAYTV_MDNS_BROWSE_REFRESH_SEC` (default 60s). Entries also
  age out after `RELAYTV_MDNS_BROWSE_TTL_SEC` (default 300s) so a device that
  vanishes without announcing goodbye still disappears.
  `RELAYTV_MDNS_BROWSE_ENABLED` opts out without disabling advertising.
- `peers.discovered_candidates()` — visible devices minus anything already
  saved (matched by device id or address). Candidates are never auto-added.
- `GET /peers` — now carries `discovered` and a `discovery` state block;
  `GET /discovery/status` reports the same state under `mdns.browse`.
- `static/ui/peers.js` — a "Found nearby" group with one-tap Add. The group
  stays visible while discovery is running so an empty list reads as "nothing
  found yet", and the note distinguishes discovery being off, unavailable on
  this network (bridged containers get no multicast), or simply quiet. Adopting
  a device that requires a token falls through to the manual form with the
  address prefilled, since one tap cannot supply a token.
- `scripts/peers-ui-smoke.js` — covers adopt-from-nearby when the run's devices
  can actually see each other, and asserts the discovery state is explained to
  the user when they cannot.

### Phase 3 as shipped

- `playback_service.handoff_snapshot()` — read-only capture of what is playing
  plus its position. IPTV sessions are excluded: their stream URLs are
  re-resolved from a local catalog the other device does not have.
- `POST /peers/{id}/handoff` — sends the snapshot and queue, then stops local
  playback and clears the local queue. Ordering is the whole point: the local
  stop happens only after the peer confirms it took over.
- `POST /queue/handoff` — the receiving side. Takes over playback first and
  imports the queue second, so a device that cannot start playing leaves the
  sender holding both its playback and its items. What was playing on the
  receiver is preserved to the front of its queue rather than discarded.
- `mode: "move"` on `POST /peers/{id}/send` — two-phase: import on the peer,
  then drop locally. The local drop delegates to the existing queue routes
  rather than writing `state.QUEUE`, keeping the writer set pinned by
  `tests/test_transition_inventory.py` unchanged.
- UI — a Copy/Move/Handoff selector at the top of the sheet, and a `⋯` on each
  queue tile that opens the sheet scoped to that single item. Handoff is offered
  only for the whole session and only while something is playing; the sheet
  re-evaluates that on every status push, so playback stopping while the sheet
  is open withdraws the option instead of leaving a button that 409s.
- Nine more cases in `tests/test_peers.py` and smoke coverage for the mode
  selector, Move emptying the sender, and per-item send.

### Deviations from the original design

- **Probe endpoint.** The plan used `GET /health` for reachability, but that
  payload is intentionally `{"ok": true}` with no identity. Added
  `GET /peers/identity` instead: anonymous, read-only, and enough to refuse
  this device's own address and de-duplicate a peer found twice.
- **Peer-hosted uploads.** Upload URLs are shaped `/media/uploads/<id>/<file>`
  regardless of which device hosts them, so the receiver resolved a peer's file
  against its own upload store and rejected it as expired. Found while sending
  between two live servers. The sender now declares `provider: "upload"`, the
  receiver builds a plain remote-media item marked `peer_hosted`, and
  `upload_store.annotate_item` leaves those items alone.
- **IPTV items.** `public_media_item` withholds IPTV URLs entirely, so those
  items have nothing portable to send. Rather than dropping them silently they
  are reported in the send response's `rejected` list.
- **Title hints.** Queue-time item building defers metadata lookups and falls
  back to a URL-derived placeholder, so a peer's real title would have lost to
  a stub. Text hints from the sender now outrank a lightweight local title;
  artwork and duration still only fill gaps, since preferring a peer's
  thumbnail would tie our artwork to that device staying reachable.
- **Modes.** Phase 1 shipped `append`/`replace` server-side with a copy-only
  sheet; Phase 3 added `move` and handoff behind the mode selector.
- **Discovery freshness.** The plan assumed a TTL'd cache fed by zeroconf
  callbacks. In practice a device that keeps advertising does not necessarily
  produce another callback before the TTL expires, so peers dropped off the list
  while sitting right there — caught by watching two live servers across a
  shortened TTL. Known services are now re-resolved on an interval, which also
  removes devices that disappeared without a goodbye packet (verified: a killed
  device leaves the list within about three seconds).
- **Bridged-network detection.** Rather than trying to detect the network mode,
  the UI reports what it knows: `discovery.active` is false when browsing is not
  running, and the sheet says host networking is required for mDNS.
- **Handoff ordering on the receiver.** The plan had the receiver import the
  queue and then start playing. Driving it against a device that could not start
  playback showed why that is wrong: the queue landed and then playback failed,
  leaving the items duplicated on a device that was not taking over. Playback is
  now taken over first, and the import only happens once it succeeds.
- **Handoff availability is live, not sampled.** Evaluating "is something
  playing" once at sheet-open time left Handoff on offer after playback ended.
  It is re-evaluated on every status push, and app.js notifies the sheet.
- **One toast per handoff.** The receiver's queue import shares a code path with
  `/queue/import`, which raises an on-TV toast. A handoff raised two
  notifications for one action, so the import core took an `announce` flag.

## 9. Guardrails

Per `AGENTS.md` quality gates:

- `tests/test_route_inventory.py` — `/peers*` and `/queue/import` are pinned
  (done); add `/queue/handoff` in Phase 3
- `tests/test_env_inventory.py --write` — regenerated for `RELAYTV_DEVICE_ID`
  and `RELAYTV_PEERS_FILE` (done); rerun for any `RELAYTV_MDNS_BROWSE_*`
- `tests/test_transition_inventory.py` — regenerated for the queue writer in
  `routes/queue.py` (done); in Phase 3, handoff must write transition state
  only through `playback_service`
- `tests/test_peers.py` — registry behavior plus the token-redaction contract
  (done)
- Phase 4: a narrowly scoped `docs/DEVICE_SYNC_OPERATIONS.md` operator doc
  alongside the IPTV and Jellyfin runbooks (this plan file is not that doc)
- `docs/release-highlights/<next-version>.md` — handoff warrants a highlight
  when Phase 3 lands

## 10. Risks

- **mDNS and Docker networking**: fine on `network_mode: host` (the default),
  broken on bridge. Handled in Phase 2 — the sheet distinguishes "no devices
  found yet" from "discovery is unavailable on this network" and names host
  networking as the requirement, instead of rendering an empty list that looks
  like a bug.
- **Uploads across devices**: the receiver streams from the sender's HTTP
  server, so playback dies if the sender sleeps. Badge those items in the
  send sheet as "streams from this device".
- **Provider mismatch**: a peer without Jellyfin configured receives an
  unplayable item. The per-item `accepted`/`reason` contract in §6 exists to
  make that visible instead of silent.
- **Token drift**: a peer that later sets `RELAYTV_API_TOKEN` starts
  rejecting writes. Surface `last_error` in the device sheet with a
  re-authenticate action rather than failing opaquely.
