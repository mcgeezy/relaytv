# Emby Integration Roadmap

Status: **in progress**

Date started: 2026-07-05

Branch: `feat/emby-server-support` (cut from `main` at the v0.5.0 release,
`e81a74e`)

Goal: the existing Jellyfin integration also accepts Emby servers. The
settings modal reads "Jellyfin / Emby", takes either server's URL, detects
which product it is, and every "Jellyfin" label in the UI (settings section
title, apply/open buttons, browse shell title, provider name and icon in
now-playing/queue) switches to "Emby" when an Emby server is configured.

Why this is a small change: Emby is the direct ancestor of Jellyfin and the
wire protocols are near-identical. The receiver already sends the shared
header scheme (`X-Emby-Token`, `Authorization: MediaBrowser Token="..."`,
`X-Emby-Authorization`), the auth body `{"Username","Pw"}` works on both,
and Emby registers every API route both bare and under `/emby`, so the
receiver's bare paths (`/Users/AuthenticateByName`, `/Users/{uid}/Items`,
`/Shows/*`, `/Sessions/Playing/Progress|Stopped`,
`/Items/{id}/Images/Primary`, `/Videos/{id}/stream?...&api_key=`) work
unchanged. The one reliable discriminator is the unauthenticated
`GET /System/Info/Public` endpoint: Jellyfin reports
`ProductName: "Jellyfin Server"`, modern Emby reports `"Emby Server"`
(Emby 3.5.3 omits the field entirely).

## Working Rules

- Keep all internal `jellyfin_*` names: settings keys, `RELAYTV_JELLYFIN_*`
  env vars, `/jellyfin/*` and `/integrations/jellyfin/*` routes, and the
  `provider="jellyfin"` item tag. Emby support is a runtime mode of the
  existing integration, not a parallel integration.
- One discriminator only: settings key `jellyfin_server_type`
  (`"jellyfin" | "emby"`, default `"jellyfin"`), mirrored to the settings
  bus as `RELAYTV_JELLYFIN_SERVER_TYPE`. Server-type detection owns this
  key; operators normally never set it by hand.
- Detection must never block or fail a connect: probe failure keeps the
  current value and records the error in receiver status.
- Update this file whenever a milestone starts, completes, changes scope,
  or uncovers follow-up work.

## Scope

In scope:

- `jellyfin_server_type` settings/config plumbing (state defaults,
  allowlist, sanitizer, settings bus, `/settings` route model).
- `detect_server_type()` probe in the receiver, wired into `connect()`
  with a background retry, plus `server_type` / detection fields in the
  receiver status payload.
- Provider semantics: `looks_like_media_url()` accepts `emby` hosts; the
  provider display name resolves to "Emby" when the configured server is
  Emby.
- UI relabeling driven by the detected type, including a neutral
  "Jellyfin / Emby" state before a server URL is configured, and an
  original (non-trademark) Emby icon at `/pwa/emby.svg`.
- Operator docs (`JELLYFIN_OPERATIONS.md` Emby section) and guardrail
  inventory updates.
- Live verification against a real Emby server plus a Jellyfin regression
  pass (M5).

Out of scope:

- Renaming any settings key, env var, route, or provider string.
- The deprecated server-plugin command ingress (410 Gone) — the supported
  product flow is RelayTV-UI browse/play plus server-side progress
  reporting, which is wire-compatible with Emby.
- Reverse proxies that rewrite API paths (documented limitation);
  `https://host/emby` base URLs work because Emby serves routes both bare
  and prefixed.
- Emby-only features (Emby Connect, premiere features) and Jellyfin-only
  features (SyncPlay).

## Baseline (measured at start)

- `main` at `e81a74e` (v0.5.0), 341 tests passing, ruff clean.
- Guardrail inventories in play: env inventory
  (`tests/test_env_inventory.py --write`), public route inventory
  (`tests/test_route_inventory.py`, hand-maintained), jellyfin route
  function inventory (`tests/test_jellyfin_inventory.py --write`,
  expected: no diff from this work).

## Milestones

### M1 — Settings/config plumbing (complete)

`jellyfin_server_type` normalizer, default, allowlist and sanitizer in
`state.py`; `RELAYTV_JELLYFIN_SERVER_TYPE` in `SETTINGS_BUS_VARS`;
optional field + bus mirror in `routes/settings.py` (not part of the
reconnect trigger set). Tests in `test_settings_config_sync.py` and
`test_settings_routes.py`; env inventory regenerated.

### M2 — Detection + provider semantics (complete)

`detect_server_type(server_url)` probes `GET /System/Info/Public`
(3s, single attempt): `ProductName` containing "jellyfin" → jellyfin,
containing "emby" → emby, valid payload with `Version`/`Id` but no
`ProductName` → emby (Emby 3.5.3), failure → keep current value.
`connect()` probes after URL validation; the worker retries once at the
first successful auth/register if detection hasn't succeeded yet.
`_persist_server_type()` writes on change only (settings + bus +
status). Status payload gains `server_type`, `server_product_name`,
`last_detect_ts/ok/error`. `looks_like_media_url()` accepts `emby`
hosts; `_provider_display_name()` shows "Emby" for jellyfin-provider
items when the configured server type is emby.

### M3 — UI relabeling + icon (complete)

Settings section title, apply button, open button, browse shell title,
validation strings, and provider icon rebrand from
`jellyfin_server_type` (neutral "Jellyfin / Emby" while no server URL is
set). Branding refreshes on settings load, on the status poll, and
immediately after a successful Apply. New `/pwa/emby.svg` asset route
(original glyph, not the Emby trademark); route inventory updated.

### M4 — Docs + guardrail sweep (complete)

`JELLYFIN_OPERATIONS.md` Emby section (supported flow, detection
semantics, path-prefix note, `api_key` stream compatibility,
probe-failure behavior); `docs/README.md` mention; inventory generators
re-run; full gates.

### M5 — Live verification (pending — needs a running Emby server)

1. Enter the Emby URL + credentials in settings → section relabels to
   "Emby Integration" without a page reload; `/integrations/jellyfin/status`
   shows `server_type: "emby"`.
2. Auth both ways: username/password (`/Users/AuthenticateByName`) and
   API key (`X-Emby-Token`).
3. Browse home/movies/TV and series → season → episode; posters render.
4. Play a movie and an episode (direct and transcode modes); audio and
   subtitle track selection.
5. Progress and Stopped visible on the Emby dashboard; now-playing/queue
   show provider "Emby" with the Emby icon.
6. Regression: point back at the Jellyfin server → relabels back to
   "Jellyfin"; full browse/play/progress flow re-verified;
   `PYTHONPATH=app pytest -q` green.

## Validation Gates

Per milestone: `ruff check app tests` and `PYTHONPATH=app pytest -q`;
after inventory changes, confirm the regenerated docs diff is exactly the
intended delta.

## PR Log

- (pending) `feat/emby-server-support` → `main`: Emby server support.
