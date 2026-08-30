# Changelog

## [0.10.2](https://github.com/mcgeezy/relaytv/compare/v0.10.1...v0.10.2) (2026-08-30)


### Documentation

* align README with redesigned website ([0e7bb3e](https://github.com/mcgeezy/relaytv/commit/0e7bb3eddc404928d93ecd9979a5d86868f6124f))

## [0.10.1](https://github.com/mcgeezy/relaytv/compare/v0.10.0...v0.10.1) (2026-08-30)


### Bug Fixes

* bound thumbnail and metadata caches ([#76](https://github.com/mcgeezy/relaytv/issues/76)) ([16aa93e](https://github.com/mcgeezy/relaytv/commit/16aa93e21aeae0bccfe45e6bb1eefcb3d9e643dc))
* canonicalize URL host parsing ([#70](https://github.com/mcgeezy/relaytv/issues/70)) ([1f46673](https://github.com/mcgeezy/relaytv/commit/1f46673ace1b577563c064753237bac6bbdea737))
* close post-audit concurrency gaps ([#85](https://github.com/mcgeezy/relaytv/issues/85)) ([fc4f669](https://github.com/mcgeezy/relaytv/commit/fc4f6697823d78e1dba426abc50237e307311041))
* configure shared Jellyfin cast identity ([e4b847c](https://github.com/mcgeezy/relaytv/commit/e4b847cb84ea7ccfaf2b27fe0888716d6311e5f3))
* contain upload identifiers ([#71](https://github.com/mcgeezy/relaytv/issues/71)) ([b159fee](https://github.com/mcgeezy/relaytv/commit/b159feed9c9efa086221c52f91a2d1c1b4e53923))
* enforce latest playback intent ([#74](https://github.com/mcgeezy/relaytv/issues/74)) ([4880bc6](https://github.com/mcgeezy/relaytv/commit/4880bc68c7d67b65ea39a6036b1a5070548f342e))
* fall back after stalled queue handoff ([#66](https://github.com/mcgeezy/relaytv/issues/66)) ([1569aab](https://github.com/mcgeezy/relaytv/commit/1569aab68a9a4b8509202890e419ca51cbdb7a10))
* order durable state publication ([#73](https://github.com/mcgeezy/relaytv/issues/73)) ([415fbe2](https://github.com/mcgeezy/relaytv/commit/415fbe21b178f65702c20093d038c07467559454))
* own background service generations ([#72](https://github.com/mcgeezy/relaytv/issues/72)) ([51ac707](https://github.com/mcgeezy/relaytv/commit/51ac707fad5f6ba927410830aeab8100915b02a8))
* protect active media work ([#81](https://github.com/mcgeezy/relaytv/issues/81)) ([b31e568](https://github.com/mcgeezy/relaytv/commit/b31e56840d0551f40bdd0e0c6866e4661ba7b918))
* reject retired service results ([#80](https://github.com/mcgeezy/relaytv/issues/80)) ([fc89141](https://github.com/mcgeezy/relaytv/commit/fc89141b0940ac9085aab58a9de37575050ff0eb))
* reject stale playback telemetry ([#83](https://github.com/mcgeezy/relaytv/issues/83)) ([6ad0932](https://github.com/mcgeezy/relaytv/commit/6ad093246ad4fcb3c9aaea14e643e97df618edeb))
* remove unauthenticated control side effects from GET ([#68](https://github.com/mcgeezy/relaytv/issues/68)) ([47d7e60](https://github.com/mcgeezy/relaytv/commit/47d7e60367fa0c5015c20aa506c5efa9fd891e7e))
* report control and snapshot failures honestly ([#69](https://github.com/mcgeezy/relaytv/issues/69)) ([29c6aaf](https://github.com/mcgeezy/relaytv/commit/29c6aaf68bee6f24040fba3c9111f911027538ad))


### Performance Improvements

* keep upload ingestion off the event loop ([#75](https://github.com/mcgeezy/relaytv/issues/75)) ([a129324](https://github.com/mcgeezy/relaytv/commit/a12932463cf19655a36e8daafe89b5dfe50a0aa4))


### Documentation

* align release highlight with patch version ([#84](https://github.com/mcgeezy/relaytv/issues/84)) ([2e6978a](https://github.com/mcgeezy/relaytv/commit/2e6978a90d35778829f3f9e2c17de8586a543c54))
* plan functionality audit remediation ([#67](https://github.com/mcgeezy/relaytv/issues/67)) ([1db3afa](https://github.com/mcgeezy/relaytv/commit/1db3afaf16827f2c6ffa76428e1e8233a71f7150))
* record testing gates, discipline, and device workflow in AGENTS.md ([#78](https://github.com/mcgeezy/relaytv/issues/78)) ([176c150](https://github.com/mcgeezy/relaytv/commit/176c150e408364e30b8bab8bb63f5e2d976e3dbb))

## [0.10.0](https://github.com/mcgeezy/relaytv/compare/v0.9.2...v0.10.0) (2026-08-26)


### Features

* add native Seerr discovery and requests ([#65](https://github.com/mcgeezy/relaytv/issues/65)) ([f91a90e](https://github.com/mcgeezy/relaytv/commit/f91a90eebddfc899baf9f85d4a4714eb0d0d844e))
* prefer realtime WebSocket connectivity ([#63](https://github.com/mcgeezy/relaytv/issues/63)) ([c59305f](https://github.com/mcgeezy/relaytv/commit/c59305f74f95c7044e3ce31c620ba5f685718268))

## [0.9.2](https://github.com/mcgeezy/relaytv/compare/v0.9.1...v0.9.2) (2026-08-22)


### Bug Fixes

* retry Rumble with browser impersonation ([#62](https://github.com/mcgeezy/relaytv/issues/62)) ([a9fbc83](https://github.com/mcgeezy/relaytv/commit/a9fbc8355d673493f58eb9140df38217eba17e81))


### Documentation

* record the control socket and the missing MPV_* knobs ([#60](https://github.com/mcgeezy/relaytv/issues/60)) ([db4d1c3](https://github.com/mcgeezy/relaytv/commit/db4d1c3f236877b09af1cf195e42da0f738d3635))

## [0.9.1](https://github.com/mcgeezy/relaytv/compare/v0.9.0...v0.9.1) (2026-08-20)


### Bug Fixes

* keep yt-dlp current so playback stops dying on stale extractors ([#57](https://github.com/mcgeezy/relaytv/issues/57)) ([a0e1f2c](https://github.com/mcgeezy/relaytv/commit/a0e1f2c129b5b38dc8120725245ba49c0eda7575))


### Documentation

* correct the yt-dlp update defaults left stale by 0.9.1 ([#59](https://github.com/mcgeezy/relaytv/issues/59)) ([9454ff2](https://github.com/mcgeezy/relaytv/commit/9454ff2517bf371bbc98c9cfed778d200ae9de43))

## [0.9.0](https://github.com/mcgeezy/relaytv/compare/v0.8.0...v0.9.0) (2026-08-08)


### Features

* appear as a Jellyfin cast target ([#54](https://github.com/mcgeezy/relaytv/issues/54)) ([b30839a](https://github.com/mcgeezy/relaytv/commit/b30839a6337a385a36f138c9bbaab02547ff3fe1))
* send playback and the queue to another RelayTV device ([#52](https://github.com/mcgeezy/relaytv/issues/52)) ([606827a](https://github.com/mcgeezy/relaytv/commit/606827a705a5a35223a363448234bc5e362fe91e))


### Bug Fixes

* stop a retired Jellyfin session from driving the player ([#55](https://github.com/mcgeezy/relaytv/issues/55)) ([4620719](https://github.com/mcgeezy/relaytv/commit/462071999c93b57c3aaee65bc0f2cc84cadfa1fb))
* stop the Add URL modal submitting twice ([#56](https://github.com/mcgeezy/relaytv/issues/56)) ([fe21eee](https://github.com/mcgeezy/relaytv/commit/fe21eee8f415e6b3db594a85129b3965e148e872))

## [0.8.0](https://github.com/mcgeezy/relaytv/compare/v0.7.3...v0.8.0) (2026-07-22)


### Features

* add optional IPTV live channels — browse and play live TV alongside the rest of RelayTV, off by default and enabled in Settings ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))
* build a curated My Channels home from added channels, with favorites pinned to the top and kept separate from the full catalog ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))
* discover channels from custom M3U playlists (URL or pasted) and a built-in free-provider directory (iptv-org and Free-TV), adding the ones you want with a single tap ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))
* manage IPTV sources — add, refresh, enable, disable, and remove owner-only playlists stored locally in /data/iptv.sqlite3 ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))
* play a live channel now, play next, or add it to the queue, with per-channel availability checks ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))
* present IPTV in the RelayTV glass design system across My Channels, Discover, and Sources on phone and desktop ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))


### Bug Fixes

* keep live IPTV playback active across short buffering and telemetry gaps instead of ending the session ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))
* show a stable LIVE status for live channels instead of flashing rolling HLS segment positions and durations ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))


### Documentation

* add IPTV operator guidance and introduce live channels in the README with a My Channels phone mockup ([4516bae](https://github.com/mcgeezy/relaytv/commit/4516baee9bae55bba94a6d53d1d85729ef5c0a74))

## [0.7.3](https://github.com/mcgeezy/relaytv/compare/v0.7.2...v0.7.3) (2026-07-20)


### Bug Fixes

* mount stable Xauthority for Raspberry Pi Wayland installs ([#47](https://github.com/mcgeezy/relaytv/issues/47)) ([4a4e9e9](https://github.com/mcgeezy/relaytv/commit/4a4e9e913c8d2f73453ce27721edf7188a8b4b39))

## [0.7.2](https://github.com/mcgeezy/relaytv/compare/v0.7.1...v0.7.2) (2026-07-20)


### Bug Fixes

* resolve rotating display credentials at runtime ([c288fd3](https://github.com/mcgeezy/relaytv/commit/c288fd31756a9f68be11c7cc2d0f3925cfb6b2d5))

## [0.7.1](https://github.com/mcgeezy/relaytv/compare/v0.7.0...v0.7.1) (2026-07-20)


### Bug Fixes

* harden public playback data for Home Assistant ([#43](https://github.com/mcgeezy/relaytv/issues/43)) ([b477e72](https://github.com/mcgeezy/relaytv/commit/b477e7234f6c3ee52384527a0c0db7d7a2cc1058))

## [0.7.0](https://github.com/mcgeezy/relaytv/compare/v0.6.1...v0.7.0) (2026-07-19)


### Features

* add a visual product overview above generated GitHub Release notes ([3681b20](https://github.com/mcgeezy/relaytv/commit/3681b20fecccc173bc2a5463e36a5a31d8adbf28))
* modernize RelayTV branding and the built-in idle-screen banner ([3681b20](https://github.com/mcgeezy/relaytv/commit/3681b20fecccc173bc2a5463e36a5a31d8adbf28))
* modernize the Jellyfin browsing UI ([#40](https://github.com/mcgeezy/relaytv/issues/40)) ([296044a](https://github.com/mcgeezy/relaytv/commit/296044abc6ef0e7eeffe8454ab09c22eb40185dc))
* redesign the README as a product landing page with current phone and TV visuals ([3681b20](https://github.com/mcgeezy/relaytv/commit/3681b20fecccc173bc2a5463e36a5a31d8adbf28))
* redesign the remote, queue, and now-playing UI as a neon-glass control system with light mode ([d6eb8cf](https://github.com/mcgeezy/relaytv/commit/d6eb8cf12098c408e04c8b9c4988a71f0a7086b9))


### Bug Fixes

* cache-bust UI assets so deploys reach already-installed clients ([d6eb8cf](https://github.com/mcgeezy/relaytv/commit/d6eb8cf12098c408e04c8b9c4988a71f0a7086b9))
* harden the remote UI against silent connection loss on mobile ([d6eb8cf](https://github.com/mcgeezy/relaytv/commit/d6eb8cf12098c408e04c8b9c4988a71f0a7086b9))
* install and validate Docker dependencies on supported Linux hosts ([37fa7f1](https://github.com/mcgeezy/relaytv/commit/37fa7f18e6ac74ac2bee415a03a70bd4fa4f56e3))
* preserve operator environment settings across installer reruns ([37fa7f1](https://github.com/mcgeezy/relaytv/commit/37fa7f18e6ac74ac2bee415a03a70bd4fa4f56e3))
* prevent Compose from creating missing system bind paths ([37fa7f1](https://github.com/mcgeezy/relaytv/commit/37fa7f18e6ac74ac2bee415a03a70bd4fa4f56e3))
* protect user-managed Compose overrides during host detection ([37fa7f1](https://github.com/mcgeezy/relaytv/commit/37fa7f18e6ac74ac2bee415a03a70bd4fa4f56e3))
* publish a cache-safe README banner so GitHub renders the current brand asset ([3681b20](https://github.com/mcgeezy/relaytv/commit/3681b20fecccc173bc2a5463e36a5a31d8adbf28))
* stop the now-playing card flashing to idle every few seconds ([d6eb8cf](https://github.com/mcgeezy/relaytv/commit/d6eb8cf12098c408e04c8b9c4988a71f0a7086b9))


### Documentation

* add a reproducible Playwright workflow for refreshing README imagery ([3681b20](https://github.com/mcgeezy/relaytv/commit/3681b20fecccc173bc2a5463e36a5a31d8adbf28))
* document the native Linux support and Docker installation contract ([37fa7f1](https://github.com/mcgeezy/relaytv/commit/37fa7f18e6ac74ac2bee415a03a70bd4fa4f56e3))

## [0.6.1](https://github.com/mcgeezy/relaytv/compare/v0.6.0...v0.6.1) (2026-07-18)


### Bug Fixes

* a just-ended YouTube stream now plays from the start while YouTube is still processing the replay, instead of being skipped with a "processing" notice — playback begins within seconds, and once the background download completes, seeking and the full timeline switch on mid-playback ([0a6664e](https://github.com/mcgeezy/relaytv/commit/0a6664e50d0527a248369390f264e70dec5c0644))
* still-processing replays restart and auto-resume correctly, never reuse stale stream links, and fall back to the previous skip-with-notice behavior if the relay is disabled (RELAYTV_POSTLIVE_RELAY=0) or unavailable ([0a6664e](https://github.com/mcgeezy/relaytv/commit/0a6664e50d0527a248369390f264e70dec5c0644))
* wait for YouTube media URL availability ([a84b015](https://github.com/mcgeezy/relaytv/commit/a84b015e6f9fca04a4175a53fdb2db4588bf3d8a))


### Documentation

* planning-era architecture and roadmap documents consolidated into permanent ARCHITECTURE.md and POSTLIVE_REPLAY.md operator docs ([0a6664e](https://github.com/mcgeezy/relaytv/commit/0a6664e50d0527a248369390f264e70dec5c0644))

## [0.6.0](https://github.com/mcgeezy/relaytv/compare/v0.5.0...v0.6.0) (2026-07-12)


### Features

* support Emby servers with auto-detected branding, live configuration, capability-aware dashboards, and runtime-gated session restore ([8487d3d](https://github.com/mcgeezy/relaytv/commit/8487d3d1e374153c37efafc3fb856793daa54f85))


### Bug Fixes

* a just-ended YouTube stream now shows "YouTube is processing this live stream" and moves on to the next video instead of playing its final seconds and stopping — the replay plays normally once YouTube finishes processing it ([dc884cd](https://github.com/mcgeezy/relaytv/commit/dc884cd41fe409fac5cc5f388ebd0516bc0631e7))
* live YouTube streams keep your cookies and bot-challenge settings when handed to mpv, so a stream that resolves no longer hits a bot check the moment playback starts ([dc884cd](https://github.com/mcgeezy/relaytv/commit/dc884cd41fe409fac5cc5f388ebd0516bc0631e7))

## [0.5.0](https://github.com/mcgeezy/relaytv/compare/v0.4.0...v0.5.0) (2026-07-05)


### Features

* a machine-checked operations test matrix — every supported host profile (x11, wayland, headless, Raspberry Pi, arm64, external mpv) now has pinned runtime decisions and an operator validation checklist ([#26](https://github.com/mcgeezy/relaytv/issues/26)) ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* a top-to-bottom architecture overhaul — modular routes, a runtime config service, a playback transition service, a dedicated Jellyfin service, and an optional API token for locked-down networks ([#21](https://github.com/mcgeezy/relaytv/issues/21), [#22](https://github.com/mcgeezy/relaytv/issues/22), [#23](https://github.com/mcgeezy/relaytv/issues/23), [#24](https://github.com/mcgeezy/relaytv/issues/24), [#25](https://github.com/mcgeezy/relaytv/issues/25)) ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* deno ships in the image as yt-dlp's JavaScript challenge runtime, so YouTube playback keeps working as YouTube changes things ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* yt-dlp now keeps itself up to date — flip the new "Keep yt-dlp up to date" toggle in Settings and RelayTV checks daily in the background ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))


### Bug Fixes

* bot-checked YouTube videos are skipped with a friendly heads-up toast instead of being retried forever ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* closing a video glides back to the idle screen instead of flashing your desktop for a few seconds ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* device passthrough is now generated from what your host actually has, so the container starts cleanly on any box ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* live and just-ended YouTube streams now play instead of hanging on a black screen, and any stream that can't start shows a "Can't start stream" toast within 45 seconds instead of leaving you staring at nothing ([7f7d722](https://github.com/mcgeezy/relaytv/commit/7f7d722a621345345dc3b00897dd0ece30ec286d))
* Raspberry Pi 5 installs no longer trip over hardware decode nodes that only exist on the Pi 4 ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* your TV and RelayTV finally talk — turning the TV off or switching inputs pauses playback, and coming back resumes right where you left off (CEC) ([945656d](https://github.com/mcgeezy/relaytv/commit/945656d5e934840bed35604958daa5928c8196fa))
* YouTube bot checks now explain themselves everywhere — the warning toast also appears on direct plays and settings changes, and your current video keeps playing instead of dropping to a black screen ([7f7d722](https://github.com/mcgeezy/relaytv/commit/7f7d722a621345345dc3b00897dd0ece30ec286d))


### Documentation

* close out architecture review and slim agent instructions ([#30](https://github.com/mcgeezy/relaytv/issues/30)) ([fb72af5](https://github.com/mcgeezy/relaytv/commit/fb72af5925e802544dccd2c5e43e95339c652e0a))

## [0.4.0](https://github.com/mcgeezy/relaytv/compare/v0.3.1...v0.4.0) (2026-06-30)


### Features

* modernize settings modal controls ([#20](https://github.com/mcgeezy/relaytv/issues/20)) ([0bc277b](https://github.com/mcgeezy/relaytv/commit/0bc277b2312a8dc88b00b5fb8ec39eb93506be15))


### Bug Fixes

* harden runtime profile and cec defaults ([#17](https://github.com/mcgeezy/relaytv/issues/17)) ([0d86d51](https://github.com/mcgeezy/relaytv/commit/0d86d51680c21086de0aa9a3805e2b23b588af35))
* keep tv cursor persistently hidden ([#19](https://github.com/mcgeezy/relaytv/issues/19)) ([812092a](https://github.com/mcgeezy/relaytv/commit/812092a63302222f7d92d155d2f0d4bca1621e08))

## [0.3.1](https://github.com/mcgeezy/relaytv/compare/v0.3.0...v0.3.1) (2026-06-29)


### Bug Fixes

* address stale codex review findings ([#15](https://github.com/mcgeezy/relaytv/issues/15)) ([4092843](https://github.com/mcgeezy/relaytv/commit/4092843608e748b72a720876979239baa2f7a556))
* keep close from replaying interrupted streams ([#12](https://github.com/mcgeezy/relaytv/issues/12)) ([9bbec4c](https://github.com/mcgeezy/relaytv/commit/9bbec4cdfcf983142f5d829e0bc955ecb13bb1f2))
* persist interrupted close state across reloads ([#14](https://github.com/mcgeezy/relaytv/issues/14)) ([c2a2e79](https://github.com/mcgeezy/relaytv/commit/c2a2e79989a49329ec315b0c1fa0ffaf483542ea))
* prevent closed sessions from priming queued playback ([#16](https://github.com/mcgeezy/relaytv/issues/16)) ([e3eba35](https://github.com/mcgeezy/relaytv/commit/e3eba35bc394c4b386b86ed9625fc32e9831b03d))

## [0.3.0](https://github.com/mcgeezy/relaytv/compare/v0.2.0...v0.3.0) (2026-06-28)


### Features

* start resumed playback at saved position ([#8](https://github.com/mcgeezy/relaytv/issues/8)) ([50a7e2e](https://github.com/mcgeezy/relaytv/commit/50a7e2e45166b475dd21263c0d9a9de064671c72))


### Bug Fixes

* keep idle toasts available without dashboard ([#10](https://github.com/mcgeezy/relaytv/issues/10)) ([985b6cc](https://github.com/mcgeezy/relaytv/commit/985b6ccfa7af32b9819010966923605fe1a72dd4))

## [0.2.0](https://github.com/mcgeezy/relaytv/compare/v0.1.0...v0.2.0) (2026-06-28)


### Features

* add release-aware About version status ([#4](https://github.com/mcgeezy/relaytv/issues/4)) ([85c1d7b](https://github.com/mcgeezy/relaytv/commit/85c1d7beb316ce56576a310079fd1711a508cfc3))


### Bug Fixes

* preserve playback position in UI polling ([#7](https://github.com/mcgeezy/relaytv/issues/7)) ([4f12a0a](https://github.com/mcgeezy/relaytv/commit/4f12a0a643895fa3d24a1423bbad0c175788177d))

## Changelog

Release notes are maintained by Release Please.
