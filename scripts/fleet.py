#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Report the state of several RelayTV devices at once.

Built for multi-device work: one command shows what each device is playing, its
queue, its saved peers, and whether mDNS discovery is running — the things worth
watching during a soak test or when a transfer between devices misbehaves.

Read-only. Every request is a GET; nothing here changes device state.

Devices are named on the command line, or in ``RELAYTV_FLEET``:

    scripts/fleet.py report living=http://10.0.0.5:8787 bedroom=http://10.0.0.6:8787
    RELAYTV_FLEET="living=http://10.0.0.5:8787,bedroom=http://10.0.0.6:8787" scripts/fleet.py
    scripts/fleet.py watch --interval=60 http://10.0.0.5:8787 http://10.0.0.6:8787

A bare URL is accepted and named after its host. ``--token`` sends a bearer
token; reads are unguarded on a stock device, so it is rarely needed.

Exit status is 1 when any device could not be reached, so it can gate a script.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 8.0


def parse_devices(argv: list[str]) -> list[tuple[str, str]]:
    """Turn ``name=url`` / bare-url arguments (or RELAYTV_FLEET) into pairs."""
    raw: list[str] = [a for a in argv if not a.startswith("-")]
    if not raw:
        raw = [p for p in (os.getenv("RELAYTV_FLEET") or "").split(",") if p.strip()]
    if not raw:
        raw = ["http://127.0.0.1:8787"]

    devices: list[tuple[str, str]] = []
    for entry in raw:
        text = entry.strip()
        if not text:
            continue
        name, _, url = text.partition("=")
        if not url:
            url, name = name, ""
        if "://" not in url:
            url = f"http://{url}"
        url = url.rstrip("/")
        if not name:
            host = urlsplit(url).hostname or url
            port = urlsplit(url).port
            name = host if port in (None, 8787) else f"{host}:{port}"
        devices.append((name, url))
    return devices


def option(argv: list[str], name: str, fallback: str = "") -> str:
    prefix = f"--{name}="
    for arg in argv:
        if arg.startswith(prefix):
            return arg[len(prefix):]
    return fallback


def get(base: str, path: str, *, token: str = "", timeout: float = DEFAULT_TIMEOUT) -> tuple[int, dict]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(f"{base}{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(2_000_000).decode("utf-8", "replace")
        return 200, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        return exc.code, {}
    except Exception as exc:
        return 0, {"error": f"{type(exc).__name__}: {exc}"}


def _seconds(value: object) -> str:
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return "-"
    return f"{total // 60}:{total % 60:02d}"


def _titles(items: list[dict], width: int = 30) -> list[str]:
    return [str((item or {}).get("title") or "?")[:width] for item in items]


def report_device(name: str, base: str, *, token: str = "") -> bool:
    code, state = get(base, "/playback/state", token=token)
    if code != 200:
        detail = state.get("error") or f"HTTP {code}"
        print(f"  [{name}] UNREACHABLE at {base} — {detail}")
        return False

    _, queue = get(base, "/queue", token=token)
    _, peers = get(base, "/peers", token=token)
    now = (queue.get("now_playing") or {}).get("title") or "-"
    items = list(queue.get("queue") or [])

    playing = "playing" if state.get("playing") else ("paused" if state.get("paused") else "-")
    position = _seconds(state.get("position"))
    duration = _seconds(state.get("duration"))
    print(f"  [{name}] {base}")
    print(f"       session={state.get('state')} {playing} {position}/{duration}")
    print(f"       now_playing={str(now)[:60]!r}")
    print(f"       queue({len(items)}) {_titles(items)}")

    if "peers" in peers:
        saved = [f"{p['name']}{' *token' if p.get('has_token') else ''}"
                 f"{' !' + p['last_error'] if p.get('last_error') else ''}"
                 for p in peers.get("peers", [])]
        nearby = [c.get("device_name") or "?" for c in peers.get("discovered", [])]
        discovery = peers.get("discovery") or {}
        active = discovery.get("active")
        state_word = "running" if active else ("off" if discovery.get("enabled") is False else "not running")
        print(f"       peers({len(saved)}) {saved}  nearby({len(nearby)}) {nearby}")
        print(f"       discovery={state_word} found={discovery.get('found')}"
              f"{' error=' + str(discovery['last_error']) if discovery.get('last_error') else ''}")
    else:
        # A device without the peer endpoints is running a build from before
        # multi-device support; everything above still applies.
        print("       peers=unsupported on this build")
    return True


def report(devices: list[tuple[str, str]], *, token: str = "", label: str = "") -> bool:
    stamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"--- {stamp}{' ' + label if label else ''}")
    ok = True
    for name, base in devices:
        ok = report_device(name, base, token=token) and ok
    # Flush per cycle: stdout is block-buffered when redirected, so a soak log
    # would otherwise stay empty until the buffer fills, and lose everything
    # buffered if the watch is killed.
    sys.stdout.flush()
    return ok


def main() -> int:
    argv = sys.argv[1:]
    if {"-h", "--help", "help"} & set(argv):
        print(__doc__)
        return 0

    command = "report"
    if argv and not argv[0].startswith("-") and "=" not in argv[0] and "://" not in argv[0]:
        command = argv[0]
        argv = argv[1:]

    token = option(argv, "token", os.getenv("RELAYTV_API_TOKEN", ""))
    devices = parse_devices(argv)
    if command == "report":
        return 0 if report(devices, token=token) else 1
    if command == "watch":
        interval = max(5.0, float(option(argv, "interval", "60")))
        print(f"watching {len(devices)} device(s) every {interval:.0f}s — Ctrl-C to stop")
        try:
            while True:
                report(devices, token=token)
                time.sleep(interval)
        except KeyboardInterrupt:
            return 0
    print(f"unknown command {command!r}; try report or watch", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
