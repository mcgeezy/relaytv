# Plex integration operations

RelayTV's Plex integration provides account linking, direct server discovery,
server selection, connection testing, and read-only personal-library browsing.
Playback is a later phase.

## Set up Plex

1. Open **Settings → Plex Integration** and enable Plex.
2. Choose **Link Plex account**, then open **Continue on Plex**.
3. Approve RelayTV on Plex's site and return to RelayTV.
4. Choose **Check link**.
5. Select a library server and choose **Apply Plex**.
6. Choose **Test server** to confirm the saved connection and PMS version.
7. Close Settings and choose **Plex** in the RelayTV header to browse Home,
   movie and TV libraries, search, details, seasons, and episodes.

RelayTV uses Plex's Ed25519 device-key and PIN flow. It does not collect a Plex
password. Server discovery prefers direct local connections, with verified
HTTPS connections preferred when a server advertises more than one local
address. Relay connections are excluded in this phase.

Catalog responses expose authenticated encrypted IDs tied to the linked
account and selected server. Artwork is fetched by RelayTV with the saved
server token and returned through a private no-store image route. Plex tokens
and upstream paths are not sent to the browser. Changing the account or
selected server invalidates an in-flight browse result and all older item IDs.

## Credential storage and backup

Plex private state is stored in `/data/plex_auth.json` with mode `0600`. The
file contains the device private key, renewable account token, and selected
server token. Treat backups of this file as secrets. The ordinary
`/data/settings.json` file contains only the enable switch and selected server
machine identifier.

Unlinking removes the linked account and server credentials from RelayTV. It
does not currently revoke the device through Plex's account service. You can
remove an authorization separately from the Plex account's authorized-device
controls.

When cloning a RelayTV data directory for another physical device, remove both
`plex_auth.json` and `device_id` from the clone before startup. Each TV should
create its own device identity and Plex authorization.

## Troubleshooting

- **No servers appear:** confirm the linked Plex account can see a Plex Media
  Server and that Remote Access or a local connection is available. Relays are
  intentionally omitted.
- **Server selection fails:** open Plex once, confirm the server reports
  online, then reload RelayTV settings. RelayTV rejects a connection whose
  reported machine identifier does not match the selected server.
- **Authorization expires:** link the account again. RelayTV renews current
  JWT credentials within 24 hours of their expiry and serializes concurrent
  refresh attempts.
- **A timeout occurs during link or refresh:** retry without deleting the
  private state file. Network failures do not erase credentials.
- **Plex does not appear in the header:** finish all three setup steps: enable
  Plex, link an account, and select a server. The browse entry remains hidden
  until the integration is ready.
- **An item becomes unavailable after changing servers:** reload the Plex
  browser. Item and artwork references are intentionally scoped to the server
  that returned them.

## Exercised contract

The initial contract was exercised on 2026-09-05 against Plex Media Server
`1.43.3.10828-00f62d37d`. The server returned JSON for identity, media-provider
features, two video library sections, account resources, and direct connection
probing. Its account exposed one owned server with local secure connections;
RelayTV selected one and verified the server machine identifier. A live modern
PIN/JWK request returned the documented 30-minute strong-PIN response shape.

The read-only catalog path was also exercised against that PMS: Home returned
a Recently Added Movies row; library paging reported 286 movies; item detail,
search, and a 131,340-byte JPEG artwork proxy response all completed. The
sanitized RelayTV results contained no upstream `/library/` paths.

No Plex controller or media playback behavior is claimed by this phase.
