# Seerr Operations

RelayTV's Seerr integration provides discovery, search, request status, safe
movie/series requests, and validated playback of already-available Jellyfin
items. Seerr remains responsible for request policy and Radarr/Sonarr work;
Jellyfin/Emby and RelayTV remain responsible for playback.

The supported target is Seerr `3.1.0` or newer. Development and initial
compatibility checks use Seerr `3.4.1`. Caller-specific sign-in requires Seerr
to use Jellyfin as its media server because it uses Jellyfin Quick Connect.
Shared API-key browsing and requests do not have that restriction.

## Configure the Integration

Open RelayTV's web UI, then choose **Settings → Seerr Integration**:

1. Enable the integration.
2. Enter the Seerr base URL, such as `http://seerr.lan:5055`. A trailing
   `/api/v1` is accepted and removed automatically.
3. Choose exactly one request identity mode.
4. Enter the API key when the selected mode requires it.
5. Apply the settings, then use **Test connection**.

Do not put credentials in the server URL. The Seerr API key is write-only in
RelayTV settings: it is stored server-side, never returned by `/settings`, and
never placed in browser URLs or assets. Leaving the key field empty preserves
the stored value; use the explicit clear control to remove it.

Operator environment defaults are also supported:

```text
RELAYTV_SEERR_ENABLED
RELAYTV_SEERR_SERVER_URL
RELAYTV_SEERR_API_KEY
RELAYTV_SEERR_REQUEST_MODE
RELAYTV_SEERR_REQUEST_USER_ID
```

`RELAYTV_SEERR_SHARED_REQUESTS_ENABLED` remains a compatibility input for an
older boolean setting. Prefer `RELAYTV_SEERR_REQUEST_MODE` for new installs.

## Choose the Request Identity

### Disabled

Browsing uses the shared API key, but request actions are unavailable. This is
the safest first-soak mode and the default. The Seerr URL and API key are both
required.

### Shared administrator

Browsing and requests use Seerr's global API key. That key is an administrator
identity, so requests can inherit administrator behavior and may auto-approve
even when an attributed user's normal policy would not.

An operator may select a sanitized Seerr user for attribution. Attribution
changes who the request is recorded for; it does not enforce that user's
quota, permissions, or approval policy. The browser cannot choose an arbitrary
user ID.

### Caller-specific

Each browser connects through Jellyfin Quick Connect. RelayTV then uses that
caller's Seerr session for discovery, visibility, quotas, permissions, and
requests. The global API key is neither required nor used as a fallback in
this mode.

Approve the displayed code in Jellyfin, then return to RelayTV to complete the
connection. The upstream session cookie stays only in RelayTV memory. The
browser receives an opaque `HttpOnly`, `SameSite=Strict` cookie; it cannot read
the upstream cookie. Sessions expire after 12 hours and are retired by logout,
Seerr origin/mode changes, upstream rejection, or RelayTV restart.

## Playback Bridge

Seerr is not a playback provider. RelayTV offers **Play**, **Play next**, or
**Add to queue** only when all of the following are true:

- Seerr reports a Jellyfin media ID for the item.
- RelayTV's active Jellyfin/Emby integration resolves that ID.
- The resolved item type exactly matches movie or series.
- Its TMDB provider ID exactly matches the Seerr TMDB ID.

RelayTV repeats this validation when the action is submitted. A title match is
never sufficient, and the browser never receives or submits the internal
Jellyfin item ID or stream URL. If the media servers differ, metadata is
missing, or either configuration changes during validation, the item stays
request-only.

## Safe Verification

After configuration, start with read-only checks:

```bash
curl -fsS http://RELAYTV_HOST:8787/integrations/seerr/status
curl -fsS 'http://RELAYTV_HOST:8787/seerr/discover?section=trending&page=1'
```

Confirm that `reachable` is true, the version and media-server type are
expected, and no API key or cookie appears in the output or logs. In the UI,
check trending, movies, series, search, images, detail, and request listing at
phone and desktop widths.

Treat request creation as a real external side effect. In shared-administrator
mode, deliberately choose a disposable title because Seerr may auto-approve
and send it to Radarr or Sonarr. For caller-specific acceptance, connect two
non-admin users and verify visibility, quota/approval behavior, session
separation, logout, and reconnect after a RelayTV restart.

For playback acceptance, choose media already available on the same configured
Jellyfin/Emby server. Verify Play and queue behavior, progress reporting, and
that a mismatched or missing TMDB provider ID leaves the action unavailable.

## Troubleshooting

- **Status is disabled or not configured:** enable Seerr and provide a valid
  HTTP(S) base URL. Shared browsing also needs the global API key.
- **Test returns `seerr_auth_failed`:** replace the API key, or in
  caller-specific mode reconnect the browser session.
- **Quick Connect never completes:** confirm Seerr uses Jellyfin, approve the
  displayed code before its 10-minute expiry, and ensure RelayTV can reach the
  same Seerr origin the browser is using.
- **Caller session disappears:** sessions are intentionally memory-only and
  expire after 12 hours or any RelayTV restart/origin/mode change.
- **Requests are unavailable:** check `request_mode` and `writes_allowed` in
  the status response. Disabled mode is read-only; caller mode requires a live
  browser session.
- **A shared request has unexpected approval behavior:** the global API key is
  an administrator identity. Attribution does not apply the selected user's
  policy; use caller-specific mode for that.
- **Playback returns `409` or no Play action appears:** verify the same media
  exists on RelayTV's active Jellyfin/Emby server and has the same type and
  TMDB provider ID. RelayTV intentionally does not guess by title.
- **Upstream `502`/`504`:** verify Seerr reachability and reverse-proxy routing.
  RelayTV does not expose arbitrary upstream HTML or headers in the response.

## Rollback

Set the request mode to **Disabled** to stop writes while retaining shared-key
browsing, or disable the Seerr integration entirely. Existing Jellyfin/Emby
browse, cast-target, queue, and playback paths are independent and remain the
fallback. Changing the Seerr origin or request mode retires all caller
sessions; disabling the integration adds no background worker or persistent
session state to clean up.
