# Post-Live Replay Relay Roadmap

Status: **in progress**

Date started: 2026-07-15

Branch: `feat/postlive-replay-relay` (cut from `main` at the v0.6.0
release, `f0791a7`)

Goal: YouTube videos in the `post_live` state — a live stream that has
ended while YouTube still processes the replay, which can take minutes to
hours — play from the start instead of being skipped.

Today RelayTV refuses them. `play_item` sees `live_status == "post_live"`,
fires a toast ("YouTube is processing this live stream. Replay is not
currently available.") and the queue advances past the item
(`player.py:4688-4693`, from PR #33). A viewer who queued a stream that
just ended cannot watch it at all until YouTube finishes processing.

## Why the current skip exists

The skip is not caution; it is a wall, measured on a real `post_live`
specimen during the PR #33 work:

- Only yt-dlp's `tv` player client returns formats. `ios`, `android` and
  `web_safari` answer "This live event has ended".
- Those formats are `http_dash_segments` with fragment lists (`sq=0..N`,
  e.g. 979 fragments). Fragment entries carry only `fragment_count` and
  `url` — **no per-fragment durations**.
- mpv's `ytdl_hook` needs durations to build its EDL timeline. Without
  them it falls back to the bare `videoplayback` base URL
  (`live=1&hang=1`), which serves only the live edge. Playback joins
  seconds from the end and dies. `live_start_index=0`, `--live-from-start`
  and hand-synthesized EDLs were each tried and each failed (fragment
  durations are non-uniform; the first is 0.8s).
- The formats are adaptive-only: audio itag 140 plus video-only itags
  (160/133/134 avc1, 242/278 vp9, 394/395 av01). There is no muxed format
  to fall back to.

The wall is specific to **mpv**. yt-dlp handles these manifests fine — its
`dashsegments` downloader is the normal, working path for post-live
content, and individual fragment URLs are plain-GET fetchable (verified).

## Design

Put yt-dlp where it works and hand mpv something it can trivially play: a
local HTTP stream.

```
yt-dlp -f <video> -o -  ─┐
                         ├─→ ffmpeg -i pipe:V -i pipe:A -c copy -f matroska pipe:1 ─┐
yt-dlp -f <audio> -o -  ─┘                                                          │
                                                                                    ▼
                                        GET http://127.0.0.1:8787/postlive/<token>.mkv
                                                                                    │
                                                                                    ▼
                                                                                   mpv
```

Rationale for each choice:

- **Matroska, `-c copy`**: codec-agnostic (avc1/vp9/av01 video with m4a
  audio all mux without re-encode) and streams progressively. No
  transcode, so CPU cost on the appliance is negligible.
- **Two yt-dlp processes**: yt-dlp cannot merge two formats to stdout —
  `-o -` with `bestvideo+bestaudio` is rejected outright. Single-format
  `-o -` works. ffmpeg does the muxing, reading both children through
  `pass_fds` + `pipe:<fd>` inputs.
- **A plain HTTP URL**: works identically on all three runtimes (Docker
  external mpv, Qt-shell external mpv, Qt-shell in-process libmpv),
  because each simply receives a stream URL. No `ytdl_hook`, no
  per-runtime special-casing.
- **OS pipe backpressure**: when mpv pauses, ffmpeg's write blocks, its
  reads stop, and yt-dlp's writes block. Flow regulates itself with no
  unbounded buffering.

## Working Rules

- The relay is a **fallback-safe addition**. The existing skip+toast stays
  live and reachable: when the kill-switch is off, when session creation
  fails, and for the hard-failure case where yt-dlp reports "live stream
  recording is not available" (`resolver.py:734` raises
  `YouTubePostLiveProcessingError` — no formats exist, so there is nothing
  to relay). The worst outcome of a relay failure is today's behavior.
- **No orphaned processes.** Every session has three teardown triggers
  (client disconnect, supersede, reaper). This is the feature's main
  correctness risk and is verified explicitly in M6.
- **One session at a time.** This is a single-player appliance; creating a
  session closes any existing one.
- Update this file whenever a milestone starts, completes, changes scope,
  or uncovers follow-up work.

## Scope

In scope:

- `ResolvedStreams.ytdlp_args` so the relay can re-run the exact winning
  yt-dlp strategy rather than re-deriving it.
- `postlive_relay.py`: session registry, process pipeline, supervision and
  teardown.
- `GET /postlive/{token}.mkv` streaming route.
- `play_item` / `restart_current` post_live branches: try relay, else skip.
- `RELAYTV_POSTLIVE_RELAY` kill-switch (default enabled).
- Operator docs, env inventory regen, API docs.

Out of scope (v1):

- **Seeking and duration.** mpv receives a progressive stream, so it has
  no timeline: seeks are unavailable and `duration_sec` stays null in
  history. An info toast sets the expectation. Revisit only if the replay
  wait proves long enough that viewers ask for it.
- Caching relayed output to disk for later replay.
- Any non-YouTube provider — no other provider exhibits this manifest
  shape.
- Additional buffer/timeout tuning knobs. Pipe backpressure is
  self-regulating; knobs would be speculation.

## Baseline (measured at start)

- `main` at `f0791a7` (v0.6.0), 375 tests passing, ruff clean.
- Guardrail inventories in play: env inventory
  (`tests/test_env_inventory.py --write`) — `RELAYTV_POSTLIVE_RELAY`
  requires a regen; public route inventory
  (`tests/test_route_inventory.py`, hand-maintained) — the new
  `/postlive/{token}.mkv` route requires an entry.
- Depends on PR #33 (`dc884cd`), already merged into `main`.

## Milestones

### M0 — Branch + roadmap (complete)

Branch cut from `main` at `f0791a7`; this document.

Prerequisite handled: 1,527 lines of unrelated YouTube live-playback work
were sitting uncommitted on `main` and touching the same files. Parked as
a self-contained commit on `feat/youtube-live-playback` (`206ba20`, ruff
clean, 388 tests passing) for separate review, leaving this branch clean.

### M1 — Export the winning yt-dlp argv (complete)

`ytdlp_args: tuple[str, ...] = ()` on `ResolvedStreams`
(`resolver.py:28-51`), populated from `selected_args` at the post_live
return (`resolver.py:794-807`). The 2-tuple `__iter__` contract stays
untouched so existing callers keep unpacking as before. The relay then
re-runs the exact strategy that won — `tv` client args, cookies,
js-runtime flags — instead of re-deriving it and risking a different
outcome.

Tests extend `test_resolver_defers_postlive_youtube_to_mpv_ytdl_hook`:
`ytdlp_args` carries `player_client=tv_simply`; VOD results leave it
empty.

### M2 — `postlive_relay.py` session module (complete)

```python
def split_format_expression(fmt: str) -> tuple[str, str | None]
def create_session(url, ytdl_format, ytdlp_args) -> RelaySession
def get_session(token: str) -> RelaySession | None
def close_session(token: str, *, reason: str) -> None
def relay_url(token: str) -> str
```

- `split_format_expression` splits on the first `+` outside brackets
  (candidates look like `bestvideo[vcodec!*=av01][height<=1080]+bestaudio`).
  No `+` → one yt-dlp process and a single-input ffmpeg.
- Session cap of 1; creating one closes any existing session
  (`reason="superseded"`).
- Teardown on client disconnect, on supersede, and via a reaper thread for
  readerless sessions (daemon-thread pattern per
  `_arm_playback_start_watchdog`, `player.py:4458`). Kill order: ffmpeg,
  then the yt-dlp children; `terminate()` → 3s → `kill()`.
- stderr for all three processes drained on daemon threads into a bounded
  ring buffer. An undrained full stderr pipe would deadlock the pipeline.
- Mid-stream death (ffmpeg or a yt-dlp child exits) needs no new
  detection: mpv sees EOF → `playback_service.natural_end()` → the queue
  advances, which is exactly today's behavior. Exit codes and last stderr
  are logged for diagnosis.
- `ffmpeg` and `yt-dlp` are invoked by bare name from PATH, per the
  `thumb_cache.py:190` precedent.

### M3 — `GET /postlive/{token}.mkv` (complete)

New router `routes/postlive.py`, registered in `routes/__init__.py`.
`StreamingResponse(gen(), media_type="video/x-matroska")`; the generator
reads ffmpeg's stdout in 64 KiB chunks and closes the session in
`finally`. Unknown token → 404.

No auth exemption is needed and none should be added:
`api_auth.write_request_allowed` (`api_auth.py:37`) guards only
POST/PUT/PATCH/DELETE, and reads are never guarded. mpv sends no token, so
this route works by construction rather than by carve-out.

`/postlive` joins the `skip_slow_request_logging` list — a multi-hour
stream would otherwise be logged as a multi-hour slow request.

### M4 — Player integration (complete)

- `play_item` (`player.py:4692-4693`): post_live becomes try-relay-else-skip.
  On success the relay URL is loaded as a plain direct stream (no ytdl
  overrides, no separate audio URL — ffmpeg already muxed it) through the
  normal path, with an info toast: "Replay is still processing — playing
  from the start. Seeking is unavailable." On failure, the existing
  `_notify_post_live_processing` + raise runs unchanged.
- `restart_current` (`player.py:4688-4693`): same try-relay-first;
  current playback is kept on failure, as today.
- Prefetch: post_live stays out of it (`_resolved_playback_source`,
  `player.py:3893-3942`, already clears prefetch for `mpv_ytdl`). A relay
  session spawns processes, so it must not start before the item plays.
- Relay URL built from `127.0.0.1` plus the same `RELAYTV_PORT`/`PORT`
  logic as `routes/__init__.py:_host_urls()`, extracted into one shared
  helper rather than copied a third time. Loopback reaches the server in
  every runtime (Docker: mpv and server share the container netns;
  Flatpak: same sandbox).
- The 45s `RELAYTV_PLAYBACK_START_TIMEOUT_SEC` watchdog needs no change —
  first bytes should flow within seconds, and if it does fire it already
  stops and advances cleanly while the disconnect tears the session down.
  Measure in M6 rather than pre-tuning.
- Queue-advance skip logic (`player.py:4200-4201`) is unchanged: post_live
  only reaches it when the relay declined.

### M5 — Kill-switch + guardrails (complete)

`RELAYTV_POSTLIVE_RELAY`, default enabled — playing these videos is the
point of the feature; operators who hit trouble set it to `0` for the
skip+toast behavior. Env inventory regenerated
(`PYTHONPATH=app python3 tests/test_env_inventory.py --write`); route
inventory updated; `/postlive/{token}.mkv` documented in `API.md` as
internal/loopback.

Implementation notes recorded along the way:

- One defect caught by tests during M4: play_item's generic resolved-stream
  caching stored the relay URL as `_resolved_stream`, which a replay within
  the prefetch TTL would have reused as a dead single-use token (404). Relay
  playbacks now skip that caching entirely.
- Background queue prefetch never spawns a relay: prefetch goes through
  `_resolved_playback_source` (which drops `mpv_ytdl` results), and only
  `play_item` starts a pipeline — sessions exist only for items actually
  playing.
- A resume position is dropped on the relay path (the stream is not
  seekable), so a restart or resume of a relayed replay plays from the start.

### M6 — Live verification on the appliance (in progress)

Pre-deployment checks, run 2026-07-15 with the real module **inside the
production Docker container** (its pinned yt-dlp 2026.07.04 + deno; the
host's stale yt-dlp 2026.03.03 fails YouTube's challenge/SABR gates and is
not representative):

1. **Pipeline on a normal VOD with forced adaptive formats** — done.
   `bestvideo[height<=480][vcodec^=avc1]+bestaudio[ext=m4a]/bestaudio` on a
   139s VOD: yt-dlp picked formats 135+140, ffmpeg muxed, **first matroska
   bytes after 2.03s** (EBML magic verified; 45s watchdog has huge margin),
   3 MB in 2.25s. ffprobe: h264+aac; mpv decoded 60 frames with `A-V: 0.000`
   and clean EOF.
2. **Process hygiene (reader close)** — done: closing the reader tore down
   all three processes (yt-dlp SIGTERM, ffmpeg escalated to kill, audio
   child saw the expected broken pipe); zero survivors in container or on
   the host. Supersede/close/reaper paths are unit-tested; the
   restart-mid-stream case rides on cgroup teardown plus the lifespan
   `close_all`.
3. **Backpressure** — done: on a 2-hour VOD, pausing reads for 60s grew the
   trio's RSS by **0 KB** and reads resumed instantly.
4. **Kill-switch** — unit-tested (`RELAYTV_POSTLIVE_RELAY=0` restores
   skip+toast in play_item and restart_current); live toggle check happens
   with the deployed build.
5. **Deployed end-to-end** — done 2026-07-17, against genuine post_live
   specimens (a just-ended Merz/Macron press conference, caught by polling a
   basket of currently-live streams until one flipped). The first two
   attempts each exposed a real bug the synthetic checks could not:
   - The resolver's winning post_live attempt often carries **no `-f`**
     (yt-dlp's default `bv*+ba/b`), and the relay mapped that empty
     expression to `-f best` — a muxed format post_live never serves.
     Fixed: empty now splits to `bv*` + `ba` (d509bf3).
   - yt-dlp's dash-fragment downloader stages `--FragN.part` files in its
     **cwd even when streaming to stdout**; the children inherited the
     server's read-only `/app` and died with `Permission denied`.
     Progressive-VOD verification never staged fragments, which is why
     pre-deployment checks missed it. Fixed: one temp workdir per child,
     removed on session close (next commit).
   The third attempt played: session `bv*`+`ba` (audio itag 140, 799
   fragments), both downloaders + ffmpeg alive, mpv position advancing
   (32.5 → 47.8 over 15s) with the muxed timeline growing ahead of
   playback. Both failed attempts also proved the failure path: clean
   teardown, stderr tails logged, dead-token replays 404, appliance falls
   back to the idle screen without wedging.
6. **Regression** — 390 tests green, ruff clean.

## Risks

- **ffmpeg startup latency**: matroska needs both input headers before it
  emits output, so first bytes wait on both yt-dlp children connecting.
  Expected to be seconds; the 45s watchdog is the backstop. Measured in M6.
- **Fragment URL expiry (~6h)**: an 82-minute video relays in roughly real
  time, well inside the window. A 6h+ stream could expire mid-relay → EOF
  → queue advance. Accepted for v1.
- **Orphaned processes**: the main correctness risk; three teardown
  triggers plus explicit M6 verification.
- **stderr deadlock**: bounded drain threads, by design in M2.

## Validation Gates

Per milestone: `ruff check app tests` and
`PYTHONPATH=app python3 -m pytest -q tests/`; after inventory changes,
confirm the regenerated diff is exactly the intended delta. Relay tests use
fake `Popen` objects and monkeypatched `resolver.run` — no real
subprocesses, no network, matching the existing resolver/player test
patterns.

## PR Log

- (none yet) — `feat/postlive-replay-relay` → `main`.
