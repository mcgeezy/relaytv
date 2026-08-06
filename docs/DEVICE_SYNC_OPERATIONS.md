# Device Sync Operations

Sending a queue — or the running playback — from one RelayTV device to another
on the same network. Covers adding devices, discovery requirements, credential
handling, what can and cannot travel between devices, and troubleshooting.

API reference: the "Peer devices and queue transfer" section of `API.md`.

## What it does

Each RelayTV device can hold a small list of other RelayTV devices. From the
Web UI's queue header, **Send** opens a device sheet with two modes. Both send
the same thing; they differ only in what happens on *this* device:

| Mode | Effect |
| --- | --- |
| **Send** | The other device plays what is playing here, at the same position, and takes the selected queue items. This device stops and gives those items up. Default. |
| **Copy** | Identical, except nothing here changes: both devices end up playing. |

Under the device list, **What to send** lists what is playing plus every queue
item, all selected. Tap a row's circle to leave it out. Live TV channels are
listed but cannot be selected — their stream URLs can carry credentials, so they
never leave the device.

Leaving the now-playing row out turns either mode into a plain queue transfer:
**Send** moves the selected items off this device, **Copy** duplicates them.
That is also what happens when nothing is playing here.

A `⋯` on each queue tile opens the same sheet with only that item selected, so
one thing can be sent without deselecting everything else by hand.

Nothing is sent automatically. Every transfer is an explicit action, and the
receiving device never forwards what it was sent.

## Adding a device

### Found nearby (mDNS)

Devices that can see each other appear under **Found nearby** with an **Add**
button. Discovery only suggests: a device is saved when you add it.

Requirements:

- mDNS enabled on both devices (`RELAYTV_MDNS_ENABLED=1`, the default)
- browsing enabled on the device doing the looking
  (`RELAYTV_MDNS_BROWSE_ENABLED=1`, the default)
- **host networking.** Multicast does not reach a bridged container, so
  discovery cannot work there. `docker-compose.yml` uses `network_mode: host`,
  which is what the installer deploys.

When browsing is not running, the sheet says so rather than showing an empty
list. Check the state directly:

```bash
curl -s http://<host>:8787/discovery/status | jq '.mdns.browse'
```

```json
{ "enabled": true, "active": true, "ttl_sec": 300, "found": 1, "last_error": null }
```

`active: false` with `enabled: true` means browsing could not start — usually a
bridged container, or a build without the optional `zeroconf` dependency
(`last_error` names it).

Devices are matched by their `device_id`, so a device found over mDNS and the
same device added by address are recognized as one and never listed twice.

### By address

**Add device manually** takes an address such as `http://192.168.1.42:8787`.
**Test connection** asks the other device who it is and shows the name it
reports before anything is saved; a bad address is never stored. Adding this
device's own address is refused, as is a device that is already on the list.

Addresses with embedded credentials (`http://user:pass@host`) are rejected —
peers authenticate with a token, not a URL.

## Devices that require an API token

If the receiving device sets `RELAYTV_API_TOKEN`, transfers to it are writes and
need that token. Enter it in the **API token** field when adding the device.
Reads are never guarded, so **Test connection** and the online dot keep working
without one; only the send fails, with `device requires an API token`.

A one-tap **Add** from Found nearby cannot supply a token, so if the device
needs one the sheet drops through to the manual form with the address already
filled in.

How the token is handled:

- stored only in the peer file (`/data/peers.json`, mode `0600`)
- never written to `settings.json`
- never returned by any endpoint — peer payloads carry `has_token: true`
- never logged, including on failed connection tests

This is the *other* device's secret. This device's own `RELAYTV_API_TOKEN`
remains env-only and is never persisted (see `ARCHITECTURE.md`, API Trust
Boundary).

## What travels between devices

Items are sent **by reference**. The sender ships a display-safe URL plus a few
display hints, and the receiving device re-resolves that URL with its own
provider configuration, cookies, and quality policy. Resolved stream URLs and
provider tokens are specific to the sending device and frequently expire, so
they never cross.

| Item | Result |
| --- | --- |
| Public URL (YouTube, direct media) | Travels. Re-resolved on arrival. |
| Jellyfin/Emby | Travels if the receiving device has a server configured. Otherwise reported as rejected. |
| Uploaded media | Travels, and the receiving device streams the file from the sender over HTTP. It plays only while the sending device is reachable. |
| IPTV channel | Does not travel. Reported as rejected. |

IPTV is excluded on purpose: those stream URLs can carry credentials anywhere in
the path, and they are re-resolved from a catalog the other device does not
have. The same applies to a live channel that is playing — it cannot be sent,
and the sheet shows the row greyed out rather than failing after the fact.

Per-item outcomes come back with the response, so a partial transfer is
reported rather than silently trimmed. The UI shows
`Copied 10 of 12 … 2 skipped (reason)`.

## Ordering guarantees

Every destructive step waits for confirmation from the other device:

- **Send** drops the local items only after the peer confirms the import, and
  stops local playback only after the peer reports it took over. A transfer that
  fails in transit loses nothing and leaves you watching what you were watching.
- **Send** gives up only what the other device actually accepted. Anything it
  rejected — an unconfigured provider, say — and anything that could not travel
  at all stays here. The items are matched by identity, not by their position
  in the queue, so a transfer that takes a while cannot delete something that
  moved into a sent item's slot in the meantime.
- **Copy** changes nothing locally at all, so there is nothing to undo.
- On the receiving side, a handoff takes over playback *before* importing the
  queue. A device that cannot start playing does not end up holding the items
  either.
- A handoff preserves whatever the receiving device was playing to the front of
  its queue, so it is never destroyed.
- After a **Send** the device returns to its idle screen with nothing to resume.
  It does not keep the item it gave away, because resuming it would play the
  same thing in two rooms unasked — which is what **Copy** is for when you do
  want it.
- A **Send** of part of the queue leaves the unselected items here, and this
  device advances into them rather than going idle.

## Verifying a pair of devices

```bash
# Who am I talking to?
curl -s http://<peer>:8787/peers/identity

# Saved devices, nearby candidates, and discovery state
curl -s http://<host>:8787/peers | jq '{peers, discovered, discovery}'

# Add and send from the command line
curl -s -X POST http://<host>:8787/peers \
  -H 'Content-Type: application/json' \
  -d '{"base_url":"http://<peer>:8787","name":"Bedroom TV"}'

curl -s -X POST http://<host>:8787/peers/<peer_id>/send \
  -H 'Content-Type: application/json' -d '{"mode":"append"}'

# Just queue positions 0 and 2, giving them up locally
curl -s -X POST http://<host>:8787/peers/<peer_id>/send \
  -H 'Content-Type: application/json' -d '{"mode":"move","indexes":[0,2]}'

# Copy: the peer plays what this device is playing, and this device carries on
curl -s -X POST http://<host>:8787/peers/<peer_id>/handoff \
  -H 'Content-Type: application/json' -d '{"keep_local":true}'
```

`scripts/fleet.py` reports several devices at once — what each is playing, its
queue, saved peers, and whether discovery is running. It is read-only:

```bash
scripts/fleet.py report living=http://<host-a>:8787 bedroom=http://<host-b>:8787

# Or set the fleet once and omit the arguments
export RELAYTV_FLEET="living=http://<host-a>:8787,bedroom=http://<host-b>:8787"
scripts/fleet.py

# Poll during a soak; output is flushed per cycle so it tails cleanly
scripts/fleet.py watch --interval=60 | tee soak.log
```

It exits non-zero when a device could not be reached, so it can gate a script.

A browser smoke covering the whole sheet needs two devices:

```bash
node scripts/peers-ui-smoke.js \
  --base=http://<sender>:8787 \
  --peer=http://<receiver>:8787 \
  --screenshots=/tmp/relaytv-shots
```

## Troubleshooting

**A device never appears under Found nearby.** Check
`/discovery/status` on the device doing the looking: `browse.active` must be
true. If it is false, the container is probably not on host networking. If it
is true and `found` is 0, check that the other device advertises
(`mdns.active: true` there) and that both are on the same subnet — mDNS does not
cross routed segments.

**A device disappeared from the list.** Entries expire
`RELAYTV_MDNS_BROWSE_TTL_SEC` (default 300s) after they were last confirmed,
and known devices are re-confirmed every `RELAYTV_MDNS_BROWSE_REFRESH_SEC`
(default 60s). A device that is powered off drops out within a few seconds;
one that is still advertising should never age out.

**Sends fail with `device requires an API token`.** The receiving device has
`RELAYTV_API_TOKEN` set. Edit the saved device and add the token.

**Sends fail with `device is unreachable`.** The address or port is wrong, the
other device is down, or a firewall is in the way. The saved device keeps the
last error, which the sheet shows under its name.

**A send is slow, or reports a failure.** Accepting an import is fast, but a
handoff also has to start playback on the receiving device, which takes several
seconds (~6–12s observed on a Pi over YouTube). The sender waits up to 30s. If
it does give up, the import may still have landed on the receiver — check the
receiving device's queue before retrying, and use `POST /queue/dedupe` there if
a retry duplicated items.

**An uploaded item plays on the sender but not the receiver.** The receiver
streams that file from the sender. It fails if the sending device is asleep,
off, or on a different network than when the transfer happened.

**A Jellyfin item arrives but will not play.** The receiving device needs its
own Jellyfin/Emby configuration; see `JELLYFIN_OPERATIONS.md`.

## Companion apps and automation

The peer endpoints are ordinary HTTP and usable from Home Assistant, scripts,
and companion apps. `POST /peers/{id}/send` and `POST /peers/{id}/handoff` need
nothing but a saved device id, which makes "move what is playing to the bedroom"
a one-call automation. Every alias and payload shape is documented in `API.md`
and pinned by `tests/test_route_inventory.py`.
