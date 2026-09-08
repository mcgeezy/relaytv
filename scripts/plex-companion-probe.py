#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""Phase 0 Companion discovery probe. Unshipped: nothing in the app imports it.

The Companion protocol lives in the wiki of an archived project, and Plex's
supported-apps page excludes mobile 2025.10 and higher, so what current
controllers actually do is unknown. This harness answers that by observation
rather than by implementing the spec and hoping.

It does three separable things, in the order worth doing them:

  scan       Send GDM discovery and report what answers. Proves multicast
             leaves and returns in this host's networking, so a later silence
             means something.
  listen     Bind the GDM ports and log every datagram verbatim. Run it, then
             open the cast picker on a phone: whatever the controller sends
             shows up here, including ports and payloads no document names.
  advertise  Answer discovery as a player and serve the HTTP side, so a
             controller has something to find. The only mode that asserts a
             protocol shape, and it is built from what the first two observed.

Nothing here is evidence of a working receiver. The deliverable is the
supported/unsupported matrix in docs/PLEX_COMPANION_DISCOVERY.md, recorded with
exact controller versions.
"""
from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# Plex GDM uses this group. Which port carries which role is part of what the
# spike establishes, so every known port is watched rather than assuming one.
GDM_GROUP = "239.0.0.250"
GDM_PORTS = (32410, 32412, 32413, 32414)
DEFAULT_HTTP_PORT = 32500
PRODUCT = "RelayTV"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _log(event: str, **fields: object) -> None:
    """One JSON line per observation, so a run can be diffed and attached."""
    payload: dict[str, object] = {"ts": _now(), "event": event}
    payload.update(fields)
    print(json.dumps(payload, sort_keys=True, default=str), flush=True)


def _decode(data: bytes) -> str:
    return data.decode("utf-8", "replace").replace("\r\n", "\\r\\n")


def _join_group(sock: socket.socket, port: int) -> bool:
    """Join the GDM group, reporting failure instead of dying silently.

    This host has already logged `No buffer space available` joining multicast
    groups on some interfaces, so a failed join is a plausible reason for a
    controller never finding the player. It has to be visible in the record.
    """
    try:
        request = struct.pack("=4sl", socket.inet_aton(GDM_GROUP), socket.INADDR_ANY)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, request)
        return True
    except OSError as exc:
        _log("gdm_join_failed", port=port, error=str(exc))
        return False


def _bind(port: int) -> socket.socket | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except (AttributeError, OSError):
        pass
    try:
        sock.bind(("", port))
    except OSError as exc:
        _log("gdm_bind_failed", port=port, error=str(exc))
        sock.close()
        return None
    joined = _join_group(sock, port)
    _log("gdm_bound", port=port, joined_group=joined)
    return sock


# --- scan -------------------------------------------------------------------


def cmd_scan(args: argparse.Namespace) -> int:
    """Ask the network who is out there, on each GDM port."""
    found: list[dict[str, object]] = []
    for port in GDM_PORTS:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
        sock.settimeout(0.5)
        probe = b"M-SEARCH * HTTP/1.1\r\n\r\n"
        try:
            sock.sendto(probe, (GDM_GROUP, port))
        except OSError as exc:
            _log("scan_send_failed", port=port, error=str(exc))
            sock.close()
            continue
        deadline = time.monotonic() + args.wait
        while time.monotonic() < deadline:
            try:
                data, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError as exc:
                _log("scan_recv_failed", port=port, error=str(exc))
                break
            entry = {"port": port, "peer": f"{addr[0]}:{addr[1]}", "body": _decode(data)}
            found.append(entry)
            _log("scan_response", **entry)
        sock.close()
    _log("scan_complete", responses=len(found))
    if not found:
        _log(
            "scan_note",
            detail=(
                "No GDM responses. Before concluding controllers cannot discover "
                "a player, confirm the host can reach the group at all — a Plex "
                "server on this network should answer on one of these ports."
            ),
        )
    return 0


# --- listen -----------------------------------------------------------------


def _listen_loop(sock: socket.socket, port: int, stop: threading.Event) -> None:
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError as exc:
            if not stop.is_set():
                _log("listen_error", port=port, error=str(exc))
            return
        _log(
            "gdm_datagram",
            port=port,
            peer=f"{addr[0]}:{addr[1]}",
            bytes=len(data),
            body=_decode(data),
        )


def cmd_listen(args: argparse.Namespace) -> int:
    """Log every GDM datagram. Open the cast picker while this runs."""
    stop = threading.Event()
    socks = [s for s in (_bind(port) for port in GDM_PORTS) if s is not None]
    if not socks:
        _log("listen_aborted", reason="no_ports_bound")
        return 1
    threads = [
        threading.Thread(
            target=_listen_loop,
            args=(sock, sock.getsockname()[1], stop),
            daemon=True,
        )
        for sock in socks
    ]
    for thread in threads:
        thread.start()
    _log("listen_ready", ports=[s.getsockname()[1] for s in socks], seconds=args.seconds)
    try:
        stop.wait(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for sock in socks:
            sock.close()
    _log("listen_complete")
    return 0


# --- advertise --------------------------------------------------------------


class _ResourcesHandler(BaseHTTPRequestHandler):
    """The HTTP half a controller fetches once GDM points it here."""

    identity: dict[str, str] = {}

    def log_message(self, fmt: str, *fmt_args: object) -> None:  # noqa: A003
        _log("http_request_log", detail=fmt % fmt_args)

    def do_GET(self) -> None:  # noqa: N802
        _log(
            "http_request",
            path=self.path,
            peer=self.client_address[0],
            headers={k.lower(): v for k, v in self.headers.items()},
        )
        if self.path.split("?", 1)[0] != "/resources":
            # Everything else is recorded but not answered: inventing responses
            # would teach the controller a shape this spike has not verified.
            self.send_response(404)
            self.end_headers()
            return
        body = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<MediaContainer>\n"
            "  <Player "
            f'title="{self.identity["name"]}" '
            f'machineIdentifier="{self.identity["machine"]}" '
            f'product="{PRODUCT}" '
            f'version="{self.identity["version"]}" '
            'platform="Linux" '
            'protocolVersion="1" '
            'protocolCapabilities="timeline,playback" '
            "/>\n"
            "</MediaContainer>\n"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml;charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _advertise_loop(
    sock: socket.socket,
    port: int,
    reply: bytes,
    stop: threading.Event,
) -> None:
    sock.settimeout(0.5)
    while not stop.is_set():
        try:
            data, addr = sock.recvfrom(8192)
        except socket.timeout:
            continue
        except OSError:
            return
        text = _decode(data)
        _log("gdm_datagram", port=port, peer=f"{addr[0]}:{addr[1]}", body=text)
        if not text.upper().startswith("M-SEARCH"):
            continue
        try:
            sock.sendto(reply, addr)
            _log("gdm_reply_sent", port=port, peer=f"{addr[0]}:{addr[1]}")
        except OSError as exc:
            _log("gdm_reply_failed", port=port, peer=f"{addr[0]}:{addr[1]}", error=str(exc))


def cmd_advertise(args: argparse.Namespace) -> int:
    """Answer discovery as a player and serve /resources over HTTP."""
    machine = args.machine_id or uuid.uuid4().hex
    identity = {"name": args.name, "machine": machine, "version": args.version}
    _ResourcesHandler.identity = identity

    reply = (
        "HTTP/1.0 200 OK\r\n"
        f"Name: {args.name}\r\n"
        f"Port: {args.http_port}\r\n"
        f"Product: {PRODUCT}\r\n"
        f"Version: {args.version}\r\n"
        f"Resource-Identifier: {machine}\r\n"
        "Protocol: plex\r\n"
        "Protocol-Version: 1\r\n"
        "Protocol-Capabilities: timeline,playback\r\n"
        "Device-Class: pc\r\n"
        "Updated-At: 0\r\n"
        "\r\n"
    ).encode("utf-8")

    stop = threading.Event()
    socks = [s for s in (_bind(port) for port in GDM_PORTS) if s is not None]
    if not socks:
        _log("advertise_aborted", reason="no_ports_bound")
        return 1
    for sock in socks:
        threading.Thread(
            target=_advertise_loop,
            args=(sock, sock.getsockname()[1], reply, stop),
            daemon=True,
        ).start()

    try:
        server = ThreadingHTTPServer(("0.0.0.0", args.http_port), _ResourcesHandler)
    except OSError as exc:
        _log("advertise_aborted", reason="http_bind_failed", error=str(exc))
        stop.set()
        for sock in socks:
            sock.close()
        return 1
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _log(
        "advertise_ready",
        name=args.name,
        machine_id=machine,
        http_port=args.http_port,
        gdm_ports=[s.getsockname()[1] for s in socks],
        seconds=args.seconds,
    )
    try:
        stop.wait(args.seconds)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.shutdown()
        for sock in socks:
            sock.close()
    _log("advertise_complete")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="ask the network who answers GDM")
    scan.add_argument("--wait", type=float, default=3.0, help="seconds per port")
    scan.set_defaults(func=cmd_scan)

    listen = sub.add_parser("listen", help="log every GDM datagram")
    listen.add_argument("--seconds", type=float, default=120.0)
    listen.set_defaults(func=cmd_listen)

    advertise = sub.add_parser("advertise", help="answer discovery as a player")
    advertise.add_argument("--name", default="RelayTV Probe")
    advertise.add_argument("--machine-id", default="")
    advertise.add_argument("--version", default="0.0-probe")
    advertise.add_argument("--http-port", type=int, default=DEFAULT_HTTP_PORT)
    advertise.add_argument("--seconds", type=float, default=300.0)
    advertise.set_defaults(func=cmd_advertise)

    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    sys.exit(main())
