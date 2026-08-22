# RelayTV Third-Party Notices

RelayTV depends on third-party software and assets. This file is the
human-maintained notice overview for the source repository. Release builds
should also include a generated inventory created from the exact build or
runtime environment.

Generate a release-time inventory with:

```bash
./scripts/generate-third-party-licenses.sh
```

The default output is:

```text
dist/licenses/relaytv-THIRD_PARTY_LICENSES.md
```

## Python Runtime Dependencies

RelayTV uses Python packages including, but not limited to:

- `fastapi`
- `python-multipart`
- `uvicorn`
- `yt-dlp`
- `curl-cffi`
- `zeroconf`
- `qrcode`
- `PySide6` when the Qt runtime bundle is installed

The generated inventory records installed package names, versions, and package
metadata license fields where available.

## Container and System Packages

The Docker image installs Debian packages for playback, Qt runtime support,
hardware acceleration, audio, CEC, and diagnostics. Examples include:

- `mpv`
- `libmpv2`
- `cec-utils`
- `ffmpeg`
- `alsa-utils`
- Mesa, VA-API, DRM, Wayland, Vulkan, and Qt runtime libraries
- optional debug/headless packages when build bundles are enabled

The generated inventory records installed Debian packages and versions when run
inside the container or another Debian-based runtime environment.

## Bundled Assets

RelayTV includes project artwork and UI assets under:

- `app/relaytv_app/static/brand/`
- `app/relaytv_app/static/weather/`
- `docs/images/`

See [ASSETS.md](ASSETS.md) for RelayTV mark and bundled asset usage rules.

## Release Inventory

For official release artifacts, include the generated inventory alongside the
artifact or inside the container image. The inventory is more specific than
this overview because it is generated from the exact installed environment.

## Corrections

If a third-party notice is missing or inaccurate, open an issue or pull request
with the package/asset name, upstream URL, and license evidence.
