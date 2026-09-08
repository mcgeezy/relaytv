# Plex Companion discovery — Phase 0 spike

Phase 5 of `PLEX_INTEGRATION_PLAN.md` would let Plex apps cast *to* RelayTV.
Its gate is not code, it is evidence: the protocol lives in the wiki of an
archived project, and Plex's supported-apps page excludes mobile 2025.10 and
higher, so whether current controllers can discover a third-party player is
unknown. This document records what was measured, not what the specification
claims.

The harness is `scripts/plex-companion-probe.py`. Nothing in the app imports
it, and nothing here is evidence of a working receiver.

## Environment

| Host | Role | Notes |
| --- | --- | --- |
| `nuc.lan` / `10.55.55.2` | Living Room RelayTV **and** the Plex Media Server | RelayTV uses `network_mode: host` |
| `raspi.lan` / `10.55.55.77` | Mark's Room RelayTV | no PMS |

PMS `1.43.3.10896-cb3ebc72d`, machine `056f476e…`.

## Recorded 2026-09-08

### GDM works on this network

`scan` sent `M-SEARCH` to `239.0.0.250` on 32410, 32412, 32413 and 32414. The
server answered on **32410 and 32414** with `Content-Type: plex/media-server`,
`Name: nuc-server`, `Port: 32400`, and a `Resource-Identifier` matching the
known machine id. It did not answer on 32412 or 32413.

This matters mainly as a control: multicast leaves and returns on this host, so
a later silence from a controller is a real result rather than a broken network.

### A third-party player is discoverable on the LAN

Running `advertise` on the Pi bound all four GDM ports and **joined the
multicast group successfully on each**. It received `M-SEARCH` from the Living
Room host on 32412 and 32414 and replied. A `scan` from the Living Room host
then found `RelayTV Probe (Pi)` on all four ports, alongside the real server.

The HTTP half answered as well: `GET /resources` returned the `MediaContainer`
`Player` document with `protocolCapabilities="timeline,playback"`. `/player/*`
paths are deliberately answered `404` — inventing responses would teach a
controller a shape this spike has not verified.

So the mechanism works. What remains unproven is the only question that
matters: whether a **real controller** accepts and displays it.

### The GDM path is unavailable when co-located with PMS

On the Living Room host, PMS already holds **all four** GDM ports:

```
32410 -> Plex Media Serv    32413 -> Plex Media Serv
32412 -> Plex Media Serv    32414 -> Plex Media Serv
```

RelayTV runs there with host networking, so a receiver on that box cannot take
a GDM port that is free. Binding anyway with `SO_REUSEPORT` would let the
kernel distribute incoming `M-SEARCH` datagrams between PMS and RelayTV, which
would intermittently make the *server* undiscoverable for every Plex client in
the house. **That was deliberately not tested**, because the failure mode falls
on the household rather than on the spike.

This is a design constraint, not a bug to fix later. A receiver has to either

- register through account resources rather than GDM, so discovery does not
  depend on owning a local port; or
- be supported only on hosts without a co-located PMS; or
- run in its own network namespace, which host networking currently precludes.

The plan's `plex_receiver_enabled` default of false should stay false on a
co-located install even if the feature ships.

## Open — needs a person

Everything above was measured without a controller. These cannot be:

- [ ] **Does current Plex Web show the advertised player?** Record the exact version.
- [ ] **Do the household's Android/iOS apps show it?** Record exact versions; the
      supported-apps page excludes 2025.10+, so a negative result is expected
      and is itself the deliverable.
- [ ] **What does a controller actually send?** Run `listen` and open a cast
      picker. Ports and payloads observed there, not the archived wiki, should
      drive any `/player/*` implementation.
- [ ] **Controller authentication.** A client identifier or source address is
      not an authenticated controller. Nothing should open `/player/*` until
      this is answered, and it must compose with `RELAYTV_API_TOKEN`.

## Running it

```sh
python3 scripts/plex-companion-probe.py scan --wait 3
python3 scripts/plex-companion-probe.py listen --seconds 180
python3 scripts/plex-companion-probe.py advertise --name "RelayTV Probe" --seconds 300
```

Do not run `advertise` or `listen` on a host that also runs PMS — see above.
Output is one JSON line per observation, so a run can be diffed and attached to
this document.
