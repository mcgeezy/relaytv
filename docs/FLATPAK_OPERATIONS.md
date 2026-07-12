# Flatpak Operations

RelayTV ships as a single-file Flatpak bundle attached to each GitHub Release,
alongside the Docker image. The Flatpak runs the same server + Qt shell + mpv
stack directly on the host session — no Docker required.

**Scope (v1):** core playback (Wayland/X11 Qt shell, mpv/libmpv, YouTube via
yt-dlp + deno, Jellyfin/Emby, direct URLs), audio, GPU decode, HDMI-CEC
control, and mDNS discovery. Not included: headless Xvfb/x11vnc remote mode,
the GTK/WebKitGTK X11 notification overlay (the Qt WebEngine overlay is used
instead), and the Chromium idle browser.

## Install

Download the bundle for your architecture from the GitHub Release assets and
install it user-level:

```sh
flatpak remote-add --user --if-not-exists flathub https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user -y ./relaytv-vX.Y.Z-x86_64.flatpak
```

The flathub remote is needed once so the `org.freedesktop.Platform` runtime
dependency can be resolved. Updating means installing the bundle from a newer
release the same way.

Run it:

```sh
flatpak run io.github.mcgeezy.relaytv
```

The web UI comes up on `http://<host>:8787/` by default.

## Permissions

The bundle requests:

| Permission | Why |
| --- | --- |
| `--share=network` | HTTP API/UI on the configured port; zeroconf/mDNS multicast discovery |
| `--socket=wayland`, `--socket=fallback-x11`, `--share=ipc` | Qt shell + mpv on the host display session |
| `--socket=pulseaudio` | Audio output (PulseAudio/PipeWire) |
| `--device=all` | GPU decode (`/dev/dri`), ALSA enumeration (`/dev/snd`), HDMI-CEC (`/dev/cec*`) |
| `--filesystem=/run/udev:ro` | Device metadata for the devices inventory |

`--device=all` is the broad Flatpak device grant; it is required because CEC
device nodes cannot be granted individually. Trim it to `--device=dri` with a
`flatpak override` if you do not use CEC.

## Configuration

The Flatpak analog of the Docker `.env` file is:

```
~/.var/app/io.github.mcgeezy.relaytv/config/relaytv/env
```

Plain `KEY=value` lines, exported into the app at launch. All `RELAYTV_*`
variables documented for the Docker runtime work here too. Example:

```sh
RELAYTV_PORT=8790
RELAYTV_MODE=wayland
```

`RELAYTV_PORT` changes the HTTP port (default 8787); the launcher re-derives
the Qt overlay and idle URLs automatically.

If audio lands on the wrong output (e.g. a USB DAC instead of the TV), pin
the HDMI sink in the web UI's audio-device setting or via the API — with the
device on auto, RelayTV prefers an HDMI ALSA output detected through the
bundled `aplay`, falling back to the session default sink.

## Data locations

Everything persistent lives in the app's private data dir on the host:

```
~/.var/app/io.github.mcgeezy.relaytv/data/    # settings.json, queue, history, assets/
├── thumbs/                                   # thumbnail cache
└── uploads/                                  # uploaded media
```

Back up or reset state by copying or clearing that directory while the app is
stopped. `flatpak uninstall --delete-data io.github.mcgeezy.relaytv` removes
it all.

## yt-dlp updates

The bundle pins yt-dlp at build time and disables the pip self-update path by
default. The runtime's Python ships without pip, so the updater cannot work
inside the sandbox at all: if it runs anyway it fails cleanly with
`No module named pip` and retries on its normal interval. If YouTube playback
breaks between releases, install the bundle from a newer release.

Note for state migrated from a Docker install: the `ytdlp_auto_update_enabled`
setting in `settings.json` overrides the launcher's env default, so a Docker
deployment that had the updater on will keep attempting (and failing) the
update under the Flatpak. Turn the yt-dlp auto-update toggle off in Settings
to silence it.

## Toast overlay mode

Flatpak defaults toast notifications to the browser overlay:

```sh
RELAYTV_QT_NATIVE_TOASTS=0
RELAYTV_QT_NATIVE_TOASTS_TOPLEVEL=0
```

Native Qt toast surfaces remain override-only because both top-level and child
Qt surfaces can disturb fullscreen compositor state or stall embedded libmpv
video on Wayland. Set `RELAYTV_QT_NATIVE_TOASTS=1` only for diagnostics. The
Flatpak does not fall back to mpv OSD notifications.

## Autostart on boot

The flatpak does not install a service. For appliance-style bring-up, create a
systemd user unit ordered after the graphical session:

```ini
# ~/.config/systemd/user/relaytv.service
[Unit]
Description=RelayTV
After=graphical-session.target
PartOf=graphical-session.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
ExecStart=/usr/bin/flatpak run io.github.mcgeezy.relaytv
# flatpak instances live in their own transient scope outside this service's
# cgroup; without an explicit kill, stop/restart leaves the old instance
# running and the unit crash-loops on the busy port.
ExecStop=/usr/bin/flatpak kill io.github.mcgeezy.relaytv
Restart=on-failure
RestartSec=5

[Install]
WantedBy=graphical-session.target
```

```sh
systemctl --user daemon-reload
systemctl --user enable --now relaytv.service
```

## Coexistence with the Docker install

Both runtimes default to port 8787 and will conflict if run simultaneously on
one host. Either stop the Docker container (`docker compose down`) before
running the Flatpak, or move the Flatpak to another port with `RELAYTV_PORT`
in the env file. They keep separate state (Docker: `./data`; Flatpak:
`~/.var/app/.../data`) — settings are not shared.

## Building locally

```sh
flatpak install --user -y flathub org.flatpak.Builder \
  org.freedesktop.Platform//25.08 org.freedesktop.Sdk//25.08
flatpak run org.flatpak.Builder --user --install --force-clean --ccache \
  builddir packaging/flatpak/io.github.mcgeezy.relaytv.yml
```

Add `--disable-rofiles-fuse` if the build fails with a `rofiles-fuse` /
`fusermount` error (host FUSE incompatibility). To produce a distributable
bundle from the build:

```sh
flatpak build-bundle ~/.local/share/flatpak/repo relaytv-x86_64.flatpak \
  io.github.mcgeezy.relaytv
```

CI builds the bundle on every PR touching `packaging/flatpak/**` or `app/**`
(`.github/workflows/flatpak.yml`) and attaches `x86_64` + `aarch64` bundles to
each GitHub Release (`.github/workflows/release-please.yml`).
