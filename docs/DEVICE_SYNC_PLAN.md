# Device Sync Plan: send the queue to another RelayTV device

Planning artifact for the multi-device feature: send queue items, the whole
queue, or live playback from one RelayTV device to another, with manually
added peers and optional local-network auto-discovery.

Status: proposed, not implemented. Per `docs/README.md` (Rule), plans stay
out of the public docs tree — this file is branch/PR-scoped and should be
folded into PR bodies rather than merged to `main` as a permanent doc.

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

## 8. Phasing

| Phase | Scope | Suggested PR title |
| --- | --- | --- |
| 1 | `device_id`, peer registry, manual add, `/queue/import`, header sheet, copy whole queue | `feat: send the queue to another RelayTV device` |
| 2 | mDNS browse, online probes, adopt-from-nearby | `feat: discover RelayTV devices on the local network` |
| 3 | Per-item send, Move and Handoff modes, receiver overlay toast | `feat: hand off playback between RelayTV devices` |
| 4 | `docs/API.md` peer surface, Home Assistant and companion-app notes | `docs: document the RelayTV device sync API` |

## 9. Guardrails to update

Per `AGENTS.md` quality gates:

- `tests/test_route_inventory.py` — add `/peers*`, `/queue/import`,
  `/queue/handoff` (hand-maintained, no companion doc)
- `tests/test_env_inventory.py --write` — any new `RELAYTV_PEERS_*` or
  `RELAYTV_MDNS_BROWSE_*` variables
- `tests/test_transition_inventory.py` — handoff must write transition state
  only through `playback_service`
- New `tests/test_peers.py` — registry behavior plus the token-redaction
  contract
- A narrowly scoped `docs/DEVICE_SYNC_OPERATIONS.md` operator doc alongside
  the IPTV and Jellyfin runbooks (this plan file is not that doc)
- `docs/release-highlights/<next-version>.md` — handoff warrants a highlight

## 10. Risks

- **mDNS and Docker networking**: fine on `network_mode: host` (the default),
  broken on bridge. Detect and surface "discovery unavailable in this network
  mode" rather than rendering an empty list that looks like a bug.
- **Uploads across devices**: the receiver streams from the sender's HTTP
  server, so playback dies if the sender sleeps. Badge those items in the
  send sheet as "streams from this device".
- **Provider mismatch**: a peer without Jellyfin configured receives an
  unplayable item. The per-item `accepted`/`reason` contract in §6 exists to
  make that visible instead of silent.
- **Token drift**: a peer that later sets `RELAYTV_API_TOKEN` starts
  rejecting writes. Surface `last_error` in the device sheet with a
  re-authenticate action rather than failing opaquely.
