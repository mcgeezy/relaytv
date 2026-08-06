# SPDX-License-Identifier: GPL-3.0-only
"""Peer RelayTV devices: registry, reachability probes, and queue transfer.

This module owns the multi-device product behavior — which peers exist, how
they are validated, and what a queue item looks like on the wire. Route
modules stay transport-only, matching the service/routes split in
``docs/ARCHITECTURE.md``.

Two contracts matter here:

* **Send by reference.** Peers exchange display-safe items (see
  ``public_media.public_media_item``) and the receiver rebuilds a playable
  item from ``url`` with its own provider configuration. Resolved stream URLs
  and the sender's provider tokens are sender-scoped, frequently short-lived,
  and would cross a trust boundary for no benefit, so they never ship.
* **Remote tokens are secrets.** A peer's ``RELAYTV_API_TOKEN`` is stored so
  that writes to a token-protected device keep working, but it is never
  returned by an endpoint, never logged, and lives only in the 0600 peer file
  (never ``settings.json``).
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit, urlunsplit

from . import device_identity, discovery_mdns, public_media, state, upload_store
from .config import env_str
from .debug import get_logger


PEERS_FILE = "peers.json"
SCHEMA_VERSION = 1

PROBE_TIMEOUT_SEC = 3.0
# Generous: a receiver that is slow to accept an import must not be reported as
# a failure, because the import may well have landed. Live testing hit exactly
# that at 10s (sender gave up at 10.0s, receiver committed at 11.0s).
SEND_TIMEOUT_SEC = 30.0

_LOCK = threading.RLock()

logger = get_logger("peers")


class PeerError(Exception):
    """Peer registry or transport failure with an operator-facing message."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = int(status_code)


# =========================
# Persistence
# =========================


def peers_path() -> str:
    configured = env_str("RELAYTV_PEERS_FILE", "")
    return configured or state._state_path(PEERS_FILE)


def _load_payload() -> dict:
    path = peers_path()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except FileNotFoundError:
        return {"version": SCHEMA_VERSION, "peers": []}
    except Exception as exc:
        logger.warning("peers_read_failed path=%s error=%s", path, exc)
        return {"version": SCHEMA_VERSION, "peers": []}
    if not isinstance(payload, dict):
        return {"version": SCHEMA_VERSION, "peers": []}
    peers = payload.get("peers")
    if not isinstance(peers, list):
        peers = []
    return {"version": SCHEMA_VERSION, "peers": [p for p in peers if isinstance(p, dict)]}


def _save_payload(peers: list[dict]) -> None:
    path = peers_path()
    payload = {"version": SCHEMA_VERSION, "peers": peers, "saved_at": time.time()}
    tmp = None
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.{uuid.uuid4().hex[:8]}.tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
        tmp = None
    except Exception as exc:
        logger.warning("peers_write_failed path=%s error=%s", path, exc)
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


# =========================
# Records
# =========================


def normalize_base_url(value: object) -> str:
    """Validate a peer base URL and strip anything unsafe to store or replay."""
    raw = str(value or "").strip()
    if not raw:
        raise PeerError("device address is required")
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parsed = urlsplit(raw)
    except Exception:
        raise PeerError("device address is not a valid URL")
    scheme = (parsed.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise PeerError("device address must use http or https")
    host = parsed.hostname or ""
    if not host:
        raise PeerError("device address is missing a host")
    # Credentials embedded in the URL would be persisted and replayed on every
    # send; peers authenticate with a bearer token instead.
    if parsed.username or parsed.password:
        raise PeerError("device address must not contain credentials")
    try:
        # urlsplit defers port parsing until the attribute is read, so an
        # operator's typo ("tv.local:8O87") or an out-of-range number raises
        # here rather than above. Without this it escapes as a 500 instead of
        # the actionable message every other bad address gets.
        port = parsed.port
    except ValueError:
        raise PeerError("device address has an invalid port")
    netloc = f"[{host}]" if ":" in host else host
    if port is not None:
        netloc = f"{netloc}:{port}"
    path = (parsed.path or "").rstrip("/")
    return urlunsplit((scheme, netloc, path, "", ""))


def _clean_name(value: object, *, fallback: str = "") -> str:
    name = str(value or "").strip()
    if len(name) > 80:
        name = name[:80].strip()
    return name or fallback


def public_peer(record: dict) -> dict:
    """Serialize a peer for API responses, replacing the token with a flag."""
    return {
        "id": str(record.get("id") or ""),
        "device_id": str(record.get("device_id") or ""),
        "name": str(record.get("name") or "RelayTV"),
        "base_url": str(record.get("base_url") or ""),
        "source": str(record.get("source") or "manual"),
        "has_token": bool(str(record.get("token") or "").strip()),
        "added_at": float(record.get("added_at") or 0.0),
        "last_seen_at": float(record.get("last_seen_at") or 0.0),
        "last_ok_at": float(record.get("last_ok_at") or 0.0),
        "last_error": str(record.get("last_error") or ""),
        "version": str(record.get("version") or ""),
    }


def list_records() -> list[dict]:
    with _LOCK:
        return [dict(record) for record in _load_payload()["peers"]]


def list_peers() -> list[dict]:
    """Public (token-redacted) peer list, newest registration last."""
    records = sorted(list_records(), key=lambda r: float(r.get("added_at") or 0.0))
    return [public_peer(record) for record in records]


def discovered_candidates() -> list[dict]:
    """Devices seen on the network that are not saved yet.

    Discovery only ever suggests. Adopting a candidate is an explicit action,
    both because a device list that grows on its own reads as broken and
    because saving a peer may involve entering that device's API token.
    """
    saved = list_records()
    saved_ids = {str(r.get("device_id") or "") for r in saved if r.get("device_id")}
    saved_urls = {str(r.get("base_url") or "") for r in saved if r.get("base_url")}
    out: list[dict] = []
    for record in discovery_mdns.discovered():
        device_id = str(record.get("device_id") or "")
        base_url = str(record.get("base_url") or "")
        if device_id and device_id in saved_ids:
            continue
        if base_url and base_url in saved_urls:
            continue
        out.append(
            {
                "device_id": device_id,
                "device_name": str(record.get("device_name") or "RelayTV"),
                "base_url": base_url,
                "version": str(record.get("version") or ""),
                "source": "mdns",
                "last_seen_at": float(record.get("last_seen_at") or 0.0),
            }
        )
    return out


def get_record(peer_id: str) -> dict | None:
    wanted = str(peer_id or "").strip()
    for record in list_records():
        if str(record.get("id") or "") == wanted:
            return record
    return None


def require_record(peer_id: str) -> dict:
    record = get_record(peer_id)
    if record is None:
        raise PeerError("unknown device", status_code=404)
    return record


def _matches_existing(record: dict, *, device_id: str, base_url: str) -> bool:
    if device_id and str(record.get("device_id") or "") == device_id:
        return True
    return bool(base_url) and str(record.get("base_url") or "") == base_url


def add_peer(
    *,
    base_url: object,
    name: object = "",
    token: object = "",
    source: str = "manual",
    verify: bool = True,
) -> dict:
    """Register a peer, verifying identity first so bad entries never persist."""
    normalized = normalize_base_url(base_url)
    identity: dict[str, str] = {}
    if verify:
        identity = probe_identity(normalized, token=str(token or ""))
    remote_device_id = str(identity.get("device_id") or "")
    if remote_device_id and remote_device_id == device_identity.device_id():
        raise PeerError("that address is this device")

    now = time.time()
    with _LOCK:
        peers = _load_payload()["peers"]
        for record in peers:
            if _matches_existing(record, device_id=remote_device_id, base_url=normalized):
                raise PeerError("that device is already added", status_code=409)
        record = {
            "id": f"p_{uuid.uuid4().hex[:16]}",
            "device_id": remote_device_id,
            "name": _clean_name(name, fallback=_clean_name(identity.get("device_name"), fallback="RelayTV")),
            "base_url": normalized,
            "source": str(source or "manual"),
            "token": str(token or "").strip(),
            "version": str(identity.get("version") or ""),
            "added_at": now,
            "last_seen_at": now if identity else 0.0,
            "last_ok_at": now if identity else 0.0,
            "last_error": "",
        }
        peers.append(record)
        _save_payload(peers)
    return public_peer(record)


def update_peer(
    peer_id: str,
    *,
    name: object = None,
    base_url: object = None,
    token: object = None,
) -> dict:
    with _LOCK:
        peers = _load_payload()["peers"]
        target = None
        for record in peers:
            if str(record.get("id") or "") == str(peer_id or "").strip():
                target = record
                break
        if target is None:
            raise PeerError("unknown device", status_code=404)
        if name is not None:
            target["name"] = _clean_name(name, fallback=str(target.get("name") or "RelayTV"))
        if base_url is not None:
            target["base_url"] = normalize_base_url(base_url)
        if token is not None:
            # An empty string is a deliberate "clear the token" instruction.
            target["token"] = str(token or "").strip()
        _save_payload(peers)
        return public_peer(target)


def remove_peer(peer_id: str) -> bool:
    with _LOCK:
        peers = _load_payload()["peers"]
        kept = [r for r in peers if str(r.get("id") or "") != str(peer_id or "").strip()]
        if len(kept) == len(peers):
            return False
        _save_payload(kept)
    return True


def _record_contact(
    peer_id: str,
    *,
    ok: bool,
    error: str = "",
    identity: dict[str, str] | None = None,
) -> None:
    """Fold the outcome of a peer request into its stored record."""
    with _LOCK:
        peers = _load_payload()["peers"]
        changed = False
        for record in peers:
            if str(record.get("id") or "") != str(peer_id or "").strip():
                continue
            now = time.time()
            record["last_seen_at"] = now
            if ok:
                record["last_ok_at"] = now
                record["last_error"] = ""
            else:
                record["last_error"] = str(error or "unreachable")
            if identity:
                if identity.get("device_id"):
                    record["device_id"] = str(identity["device_id"])
                if identity.get("version"):
                    record["version"] = str(identity["version"])
            changed = True
            break
        if changed:
            _save_payload(peers)


# =========================
# Transport
# =========================


def _request(
    base_url: str,
    path: str,
    *,
    token: str = "",
    payload: dict | None = None,
    timeout: float = PROBE_TIMEOUT_SEC,
) -> dict:
    url = f"{base_url}{path}"
    data = None
    headers = {"Accept": "application/json", "User-Agent": "RelayTV peer"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if str(token or "").strip():
        headers["Authorization"] = f"Bearer {str(token).strip()}"
    request = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            body = response.read(1_000_000)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise PeerError("device requires an API token", status_code=502)
        if exc.code == 404:
            raise PeerError("device does not support this request", status_code=502)
        raise PeerError(f"device returned HTTP {exc.code}", status_code=502)
    except urllib.error.URLError as exc:
        # Never surface the reason verbatim: it can echo back a URL that
        # contains a token when an operator mistypes one into the address.
        raise PeerError("device is unreachable", status_code=502) from exc
    except Exception as exc:
        raise PeerError("device request failed", status_code=502) from exc
    try:
        parsed = json.loads(body.decode("utf-8"))
    except Exception:
        raise PeerError("device returned an unexpected response", status_code=502)
    return parsed if isinstance(parsed, dict) else {}


def probe_identity(base_url: str, *, token: str = "") -> dict[str, str]:
    """Fetch a peer's identity, proving it is a reachable RelayTV device."""
    normalized = normalize_base_url(base_url)
    payload = _request(normalized, "/peers/identity", token=token)
    device_id_value = str(payload.get("device_id") or "").strip()
    if not device_id_value:
        raise PeerError("that address is not a RelayTV device", status_code=502)
    return {
        "device_id": device_id_value,
        "device_name": str(payload.get("device_name") or "RelayTV").strip() or "RelayTV",
        "version": str(payload.get("version") or "").strip(),
        "base_url": normalized,
    }


def probe_peer(peer_id: str) -> dict:
    """Probe a saved peer and fold the result into its stored record."""
    record = require_record(peer_id)
    try:
        identity = probe_identity(str(record.get("base_url") or ""), token=str(record.get("token") or ""))
    except PeerError as exc:
        _record_contact(peer_id, ok=False, error=exc.message)
        return {"online": False, "error": exc.message, "peer": public_peer(require_record(peer_id))}
    _record_contact(peer_id, ok=True, identity=identity)
    return {
        "online": True,
        "error": "",
        "device_id": identity["device_id"],
        "device_name": identity["device_name"],
        "peer": public_peer(require_record(peer_id)),
    }


# =========================
# Queue transfer
# =========================


def _absolute_media_url(url: str) -> str:
    """Expand a device-relative media URL so the peer can stream it from here."""
    text = str(url or "").strip()
    if not text.startswith("/"):
        return text
    return f"{device_identity.local_base_url()}{text}"


def _item_label(item: object) -> str:
    if not isinstance(item, dict):
        return str(item or "")
    return str(item.get("title") or item.get("name") or item.get("url") or "item")


def wire_item(item: object) -> dict | None:
    """Serialize one queue item for transfer, or None when it cannot travel."""
    public = public_media.public_media_item(upload_store.annotate_item(item))
    if not isinstance(public, dict):
        return None
    url = _absolute_media_url(public.get("url"))
    if not url:
        return None
    entry: dict[str, object] = {"url": url}
    for key in ("title", "channel", "provider", "duration", "mime_type", "size_bytes"):
        value = public.get(key)
        if value not in (None, "", 0):
            entry[key] = value
    thumbnail = _absolute_media_url(public.get("thumbnail") or public.get("thumbnail_local") or "")
    if thumbnail:
        entry["thumbnail"] = thumbnail
    return entry


def wire_entries(items: list[object] | None) -> tuple[list[dict], list[dict], list[object]]:
    """Split items into wire entries, reported skips, and each entry's source.

    IPTV items are the expected skip: their stream URLs may carry credentials
    anywhere in the path, so ``public_media_item`` withholds the URL entirely
    and there is nothing portable left to send. Report them instead of
    dropping them silently.

    The third list pairs each entry with the local queue item it came from, so
    a caller giving up ownership can act on what the peer actually took rather
    than on what it offered.
    """
    entries: list[dict] = []
    skipped: list[dict] = []
    sources: list[object] = []
    for item in list(items or []):
        entry = wire_item(item)
        if entry is not None:
            entries.append(entry)
            sources.append(item)
            continue
        provider = str((item or {}).get("provider") or "").strip().lower() if isinstance(item, dict) else ""
        skipped.append(
            {
                "title": _item_label(item),
                "reason": "iptv_channels_stay_on_this_device" if provider == "iptv" else "item_has_no_shareable_url",
            }
        )
    return entries, skipped, sources


def wire_items(items: list[object] | None) -> tuple[list[dict], list[dict]]:
    """Wire entries and skips, without the source pairing."""
    entries, skipped, _sources = wire_entries(items)
    return entries, skipped


def accepted_sources(
    entries: list[dict],
    sources: list[object],
    response: dict,
) -> list[object]:
    """Which local items the peer actually took, from its per-item results.

    Anything the receiver rejected stays behind: it never landed there, so
    dropping it here would destroy it. When a peer reports no per-item results
    at all, its summary count is the only evidence available — and if that does
    not account for everything sent, nothing is claimed, because deleting on a
    guess is the one outcome this transfer must never produce.
    """
    results = response.get("results") if isinstance(response.get("results"), list) else []
    if results:
        rejected_urls = {
            str(entry.get("url") or "")
            for entry in results
            if isinstance(entry, dict) and not entry.get("accepted")
        }
        return [
            source
            for entry, source in zip(entries, sources)
            if str(entry.get("url") or "") not in rejected_urls
        ]
    if int(response.get("accepted") or 0) >= len(entries):
        return list(sources)
    logger.warning(
        "peer_send_unverified sent=%d accepted=%s results=0 keeping_local_items",
        len(entries),
        response.get("accepted"),
    )
    return []


def sender_payload() -> dict[str, str]:
    """Identify this device to the receiver (used for the toast and loop guard)."""
    return {
        "device_id": device_identity.device_id(),
        "name": device_identity.device_name(),
        "base_url": device_identity.local_base_url(),
    }


def _post_to_peer(peer_id: str, record: dict, path: str, payload: dict) -> dict:
    """POST to a peer, recording the outcome on its registry entry."""
    try:
        response = _request(
            str(record.get("base_url") or ""),
            path,
            token=str(record.get("token") or ""),
            payload=payload,
            timeout=SEND_TIMEOUT_SEC,
        )
    except PeerError as exc:
        _record_contact(peer_id, ok=False, error=exc.message)
        raise
    _record_contact(peer_id, ok=True)
    return response


def send_queue(peer_id: str, *, items: list[object], mode: str = "append") -> tuple[dict, list[object]]:
    """Send queue items to a peer and normalize its import response.

    Returns the response alongside the local items the peer accepted, so a
    caller giving up ownership drops only what actually landed there.
    """
    record = require_record(peer_id)
    entries, skipped, sources = wire_entries(items)
    if not entries:
        if skipped:
            raise PeerError("none of these items can be sent to another device")
        raise PeerError("nothing in the queue to send")
    normalized_mode = str(mode or "append").strip().lower()
    if normalized_mode not in ("append", "replace"):
        raise PeerError("mode must be append or replace")

    payload = {"items": entries, "mode": normalized_mode, "from": sender_payload()}
    response = _post_to_peer(peer_id, record, "/queue/import", payload)

    results = response.get("results") if isinstance(response.get("results"), list) else []
    accepted = int(response.get("accepted") or 0)
    rejected = list(skipped) + [
        {
            "title": str(entry.get("title") or entry.get("url") or ""),
            "reason": str(entry.get("reason") or "rejected"),
        }
        for entry in results
        if isinstance(entry, dict) and not entry.get("accepted")
    ]
    logger.info(
        "peer_send peer=%s mode=%s sent=%d accepted=%d rejected=%d",
        peer_id,
        normalized_mode,
        len(entries),
        accepted,
        len(rejected),
    )
    return (
        {
            "status": "sent",
            "peer": public_peer(require_record(peer_id)),
            "mode": normalized_mode,
            "sent": len(entries),
            "accepted": accepted,
            "rejected": rejected,
            "queue_length": int(response.get("queue_length") or 0),
        },
        accepted_sources(entries, sources, response),
    )


def handoff_playback(
    peer_id: str, *, snapshot: dict, items: list[object]
) -> tuple[dict, list[object]]:
    """Ask a peer to continue the current playback, then report what it did.

    The caller stops local playback only after this returns successfully: a
    handoff that failed in transit must leave the user watching what they were
    already watching. As with ``send_queue``, the accepted local items come
    back so the caller drops only what landed.
    """
    record = require_record(peer_id)
    now_playing = wire_item(snapshot.get("item"))
    if now_playing is None:
        raise PeerError("what is playing cannot be sent to another device")
    entries, skipped, sources = wire_entries(items)

    position = snapshot.get("position")
    try:
        resume_pos = float(position) if position is not None else None
    except Exception:
        resume_pos = None

    payload = {
        "now_playing": now_playing,
        "resume_pos": resume_pos,
        "items": entries,
        "from": sender_payload(),
    }
    response = _post_to_peer(peer_id, record, "/queue/handoff", payload)

    results = response.get("results") if isinstance(response.get("results"), list) else []
    rejected = list(skipped) + [
        {
            "title": str(entry.get("title") or entry.get("url") or ""),
            "reason": str(entry.get("reason") or "rejected"),
        }
        for entry in results
        if isinstance(entry, dict) and not entry.get("accepted")
    ]
    playing = bool(response.get("playing"))
    logger.info(
        "peer_handoff peer=%s resume_pos=%s queue_sent=%d playing=%s",
        peer_id,
        "none" if resume_pos is None else f"{resume_pos:.1f}",
        len(entries),
        playing,
    )
    return (
        {
            "status": "handed_off",
            "peer": public_peer(require_record(peer_id)),
            "playing": playing,
            "resume_pos": resume_pos,
            "sent": len(entries),
            "accepted": int(response.get("accepted") or 0),
            "rejected": rejected,
            "queue_length": int(response.get("queue_length") or 0),
        },
        accepted_sources(entries, sources, response),
    )
