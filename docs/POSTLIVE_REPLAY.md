# Post-Live Replay (Still-Processing YouTube Streams)

When a YouTube live stream ends, the video spends minutes to hours in the
`post_live` state while YouTube processes the replay. RelayTV plays these
through a local relay instead of skipping them. This doc covers why the
relay exists, how it works, and how to operate it.

## Why mpv cannot play these directly

Verified empirically on real specimens:

- Only yt-dlp's `tv` player client returns formats at all for `post_live`
  videos (ios/android/web_safari report "This live event has ended").
- Those formats are `http_dash_segments` whose fragment lists carry no
  per-fragment durations, so mpv's `ytdl_hook` cannot build a timeline. It
  falls back to the bare `videoplayback` URL (`live=1&hang=1`), which only
  serves the live edge: playback joins seconds before the end and dies.
  `live_start_index=0`, `--live-from-start`, and hand-built EDLs all fail.
- Formats are adaptive-only (separate audio + video-only); there is no
  muxed format to fall back to.

yt-dlp itself downloads these manifests fine — the gap is specifically
mpv's ability to drive them. So RelayTV runs yt-dlp and hands mpv
something it can trivially play.

## How the relay works

```
yt-dlp -f <video> -o -  ─┐
                         ├─→ ffmpeg -i pipe:V -i pipe:A -c copy -f matroska ─→ spool file
yt-dlp -f <audio> -o -  ─┘                                                        │
                                                                       (tail-follow while growing)
                                                                                  ▼
                                      GET http://127.0.0.1:8787/postlive/<token>.mkv ─→ mpv
                                                                                  │
                                            (mux finalizes: duration + cues written)
                                                                                  ▼
                                       seamless reload from the local file ─→ mpv, seekable
```

On a `post_live` resolve, the player spawns one yt-dlp process per
selected format (yt-dlp cannot merge two formats to stdout, so a
`video+audio` expression is split at the first top-level `+`; an empty
expression means the resolver won with yt-dlp's default selection and
splits to `bv*` + `ba` — never `-f best`, which post_live cannot serve).
Each downloader re-runs the resolver's exact winning argv (same client
args, cookies, JS-runtime flags), and runs in its own writable temp
workdir because the dash downloader stages `--FragN.part` files in its
cwd even when streaming to stdout.

ffmpeg muxes the streams (`-c copy`, no transcode) into a matroska
**spool file** under `STATE_DIR/cache/postlive/<token>.mkv`. The
`GET /postlive/{token}.mkv` route tail-follows the growing file, so
playback starts within seconds while the download runs at network speed
(typically several times realtime).

When the download completes, ffmpeg finalizes the spool with duration and
cues — an ordinary seekable mkv. A player watch thread spots that moment
and, only if mpv is still positively on the relay URL, seamlessly reloads
the local file at the current position: full timeline, seeking, known
duration, and a "seeking is now available" toast. Until then, seeking is
unavailable and an info toast sets the expectation ("Replay is still
processing — playing from the start"). If the seamless replace declines,
progressive playback continues untouched.

Once YouTube finishes processing, later plays of the same video resolve
through the normal VOD path with no relay involved.

## Session lifecycle and hygiene

- **One session at a time** (single-player appliance): a new relay
  session supersedes the previous one, including any kept spool.
- **Single-use tokens**: a consumed, unknown, or superseded token returns
  404. mpv opens the URL directly (`try_ytdl_first` is off), so no yt-dlp
  probe burns the token.
- **Teardown triggers**: route reader disconnect, supersession, and a
  reaper for sessions whose reader never attached (60s grace) or has
  detached. Kill order is ffmpeg first (so downloaders see EPIPE), then
  terminate → 3s → kill. stderr of all three processes is drained into
  bounded ring buffers and the tails are logged on close.
- **Spool retention**: only a *cleanly finished* mux (ffmpeg exit 0 — a
  terminated mux never reports 0, so truncated files are never presented
  as seekable) survives its session, and only the newest one is kept.
  Completed spools expire after 6h, are removed on shutdown, and the
  whole spool dir is swept at startup. Deleting a spool mpv still has
  open is safe (the fd keeps the data alive).
- **Mid-stream death** (a downloader or ffmpeg dies): mpv sees EOF and
  the queue advances — the same behavior as a normal end. A full disk
  fails the mux the same way.

## Operating notes

- **Kill-switch**: `RELAYTV_POSTLIVE_RELAY=0` disables the relay and
  restores the previous behavior (skip with a "processing" toast). This
  is the only knob; there are deliberately no buffer/timeout settings.
- The skip+toast path also remains for relay spawn failures and for the
  hard-failure case where yt-dlp reports "live stream recording is not
  available" (no formats exist — nothing to relay).
- The `/postlive` route needs no auth exemption: the API-token guard
  covers only write methods, and none should be added for this route.
  It is excluded from slow-request logging (a long-lived stream is the
  feature working).
- **Disk**: a long replay spools multiple GB under
  `STATE_DIR/cache/postlive`. Retention rules above bound the total to
  roughly one replay's worth plus the active download.
- Relay playbacks are never cached as resolved streams and never
  prefetched: a session spawns processes and must not start until the
  item actually plays. Resume positions are ignored (replays start from
  0:00); after the seek upgrade, normal seeking applies.
- History `duration_sec` stays null for the progressive phase; after the
  upgrade mpv reports the real duration.

## Troubleshooting

- Grep logs for `postlive_relay_session_created`,
  `post_live_relay_started`, `post_live_relay_upgraded`, and
  `postlive_relay_session_closed` (the close line includes the stderr
  tails of all three processes and whether the spool was kept).
- "Requested format is not available" from a downloader usually means a
  muxed-format expression reached the relay; post_live serves adaptive
  formats only.
- Verify YouTube extraction inside the production container — its pinned
  yt-dlp + deno JS runtime is the supported combination; a stale host
  yt-dlp fails YouTube's challenge/SABR gates and proves nothing.
- Leftover `yt-dlp`/`ffmpeg` processes or `/tmp/postlive-*` workdirs
  after playback ends indicate a teardown bug — report it; every close
  path is expected to leave zero survivors.
