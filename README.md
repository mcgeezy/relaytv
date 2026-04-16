# RelayTV

[![GitHub Repo](https://img.shields.io/badge/GitHub-mcgeezy%2Frelaytv-181717?logo=github)](https://github.com/mcgeezy/relaytv)
[![Home Assistant Integration](https://img.shields.io/badge/Home%20Assistant-relaytv--ha-41BDF5?logo=homeassistant)](https://github.com/mcgeezy/relaytv-ha)
[![Android Companion App](https://img.shields.io/badge/Android-relaytv--android-3DDC84?logo=android)](https://github.com/mcgeezy/relaytv-android)
[![Buy Me A Coffee](https://img.shields.io/badge/Support-Buy%20Me%20a%20Coffee-FFDD00?logo=buymeacoffee&logoColor=000000)](https://buymeacoffee.com/relaytv)

**RelayTV turns a Linux box connected to your TV into a local, automation-friendly playback target.
Send links from your phone, Home Assistant, scripts, or apps and play them now or queue them for later.**

Local-first, no account required, and no cloud dependency for core playback.

<p align="center">
  <img src="docs/images/hero.png" alt="RelayTV screenshots" width="90%">
</p>

**Best for:** Raspberry Pi and mini PC TV boxes, Home Assistant users, homelab and self-hosted setups, and AI-driven automation workflows.

## Why RelayTV?

RelayTV is built for people who want a **local, automation-friendly way to send media to a TV** without relying on closed casting ecosystems, cloud accounts, or app-specific playback flows.

---

## Install

See [docs/INSTALL.md](docs/INSTALL.md) for full installation and runtime notes.

### Quick start with published images

```bash
mkdir -p ~/relaytv
cd ~/relaytv
curl -fsSL https://raw.githubusercontent.com/mcgeezy/relaytv/main/install.sh | bash
```

Then open http://<your-host>:8787/ui

### Local source iteration

```bash
cd /path/to/relaytv
./scripts/install.sh
docker compose up -d --build
./scripts/doctor.sh
```

---

## Common ways people use RelayTV

- **Send links from Android to the TV** and start playback in seconds
- **Queue videos for later** without interrupting what’s already playing
- **Use Home Assistant as the control layer** for a dedicated TV box
- **Browse Jellyfin** with a clean interface built for desktop/mobile control for playback on TV.
- **Trigger playback or on-screen notifications** from automations
- **Build AI agent workflows** that control TV playback through the API

---

## RelayTV Ecosystem

RelayTV is designed as a family of companion projects:

### RelayTV Server
This repository powers the local playback engine, queue manager, browser UI, API, and TV runtime.

### Home Assistant Integration
[relaytv-ha](https://github.com/mcgeezy/relaytv-ha)

Add RelayTV to Home Assistant with entities, services, automations, and side-panel workflows.

### Android Companion App
[relaytv-android](https://github.com/mcgeezy/relaytv-android)

Send links directly to RelayTV from Android and control playback from your phone.

### Coming Soon / In Progress

- **iPhone Companion App** — work in progress
- **Windows version of RelayTV Server** — planned / exploratory

---

## Features

### Playback

- Play now or enqueue media from URLs
- Persistent queue with auto-advance
- Stream resolution for supported providers
- Local playback history and resumable session state
- Designed for reliable always-on living-room use

### Remote Control and Automation

- HTTP API for scripts and automations
- Mobile-friendly browser remote UI
- Home Assistant integration for service calls and entity control
- Share-to-TV workflows through companion apps
- Overlay and toast notification support on the TV

### Platform Support

- Built for Linux hosts connected directly to a TV
- Works well on Intel mini PCs, NUCs, HTPCs, and Raspberry Pi class devices
- Docker-based deployment
- GitHub Container Registry image publishing
- Native runtime optimized for real-world TV usage

### Integrations

- **Home Assistant** for entities, services, automations, and dashboard workflows
- **Jellyfin** for a beautiful library browsing experience
- **Android companion workflows** for fast share-to-TV control
- **Local network automation and scripting** through the HTTP API

### Privacy and Ownership

- Local-first
- No account required
- No tracking
- No cloud dependency for core playback

---

## Jellyfin on RelayTV

RelayTV includes a beautiful Jellyfin client experience, making it easy to browse your library and launch playback directly on the connected display.

### Why it matters

- **Built for the TV screen** with a clean, full-screen interface
- **Fast access to your Jellyfin library** without relying on another playback device
- **Local-first playback** on the same box connected to your television
- **Unified experience** alongside RelayTV queueing, remote control, and automation workflows

Whether you are browsing your own media collection or mixing Jellyfin with shared links and automations, RelayTV helps turn a small Linux box into a polished living-room media endpoint.

---

## Screenshots

### Web UI

<p align="center">
  <img src="docs/images/ui.png" alt="RelayTV Web UI screenshot" width="48%">
  <img src="docs/images/ui2.png" alt="RelayTV idle screen" width="48%">
</p>

### TV Runtime / Idle Screen

<p align="center">
  <img src="docs/images/toast.png" alt="RelayTV TV runtime with overlay" width="48%">
  <img src="docs/images/idle.png" alt="RelayTV idle screen" width="48%">
</p>

### Jellyfin Client Experience

<p align="center">
  <img src="docs/images/jellyfin.png" alt="RelayTV Jellyfin browser view" width="48%">
  <img src="docs/images/jellyfin-item.png" alt="RelayTV Jellyfin item view" width="48%">
</p>

## API Examples

### Play now

Once RelayTV is running, this plays a video immediately:

```bash
curl -X POST http://127.0.0.1:8787/play_now \
  -H "Content-Type: application/json" \
  -d '{"url":"https://youtu.be/dQw4w9WgXcQ"}'
```

### Enqueue

```bash
curl -X POST http://127.0.0.1:8787/enqueue \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/video.mp4"}'
```

### Overlay notification

```bash
curl -X POST http://127.0.0.1:8787/overlay \
  -H "Content-Type: application/json" \
  -d '{"text":"Doorbell", "duration":2.5}'
```

---


## Documentation

Primary docs:

- [docs/INSTALL.md](docs/INSTALL.md)
- [docs/API.md](docs/API.md)
- [docs/JELLYFIN_OPERATIONS.md](docs/JELLYFIN_OPERATIONS.md)
- [docs/NATIVE_RUNTIME_OPERATIONS.md](docs/NATIVE_RUNTIME_OPERATIONS.md)

For the doc map, see [docs/README.md](docs/README.md).

---

## Runtime Notes

- Native Qt is the active desktop runtime
- Wayland and X11 desktop installs default to native Qt
- Rollback is a tagged-baseline redeploy, not a live compatibility mode
- Published images are available through GitHub Container Registry

Published image:

- `ghcr.io/mcgeezy/relaytv:latest`

---

## Roadmap

Planned and in-progress areas include:

- iPhone companion app
- Windows server/runtime support
- continued mobile sharing improvements
- richer TV idle and overlay experiences
- expanded companion app ecosystem
- improved onboarding and release packaging

---

## Support, Star, and Share

If RelayTV is useful to you, please consider:

- starring the repository
- sharing it with others
- supporting development at https://buymeacoffee.com/relaytv

<a href="https://buymeacoffee.com/relaytv" target="_blank" rel="noopener noreferrer">
  <img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=%E2%98%95&slug=relaytv&button_colour=FFDD00&font_colour=000000&font_family=Cookie&outline_colour=000000&coffee_colour=ffffff" alt="Buy Me a Coffee">
</a>

Every bit of support helps move the project forward.
