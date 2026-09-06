# SPDX-License-Identifier: GPL-3.0-only
"""Plex catalog normalization and opaque public references."""
from __future__ import annotations

import base64
import json
import os
import re
import threading
import time
from typing import Any
import urllib.parse

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

from .. import config, playback_service
from . import plex_auth
from .plex_client import PlexBinaryResponse, PlexError, PlexStreamResponse


REFERENCE_VERSION = 1
REFERENCE_AAD = b"relaytv-plex-reference-v1"
MAX_REFERENCE_LENGTH = 4096
VIDEO_TYPES = {"movie", "show", "season", "episode"}
SECTION_TYPES = {"movie", "show"}
SORTS = {
    "title": "titleSort:asc",
    "added": "addedAt:desc",
    "year": "year:desc",
    "rating": "rating:desc",
}
TIMELINE_STATES = {"buffering", "playing", "paused", "stopped"}
TIMELINE_INTERVAL_SEC = 10.0
TIMELINE_SEEK_DELTA_SEC = 30.0
_TIMELINE_LOCK = threading.RLock()
_TIMELINE_SEND_LOCK = threading.Lock()
_TIMELINE_LAST: dict[tuple[str, str], tuple[float, str, float, int]] = {}
_TIMELINE_SEQUENCE = 0


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_upstream_path(value: object) -> str:
    path = str(value or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return ""
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return ""
    if any(
        key.strip().lower() in {"access_token", "auth_token", "token", "x-plex-token"}
        for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    ):
        return ""
    return path


class PlexCatalogService:
    def __init__(self, auth_manager: plex_auth.PlexAuthManager | None = None) -> None:
        self.auth_manager = auth_manager or plex_auth.auth_manager

    def home(self, *, limit: int = 20) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        payload = session.client.get(
            "/hubs",
            query={
                "includeGuids": 1,
                "includeCollections": 0,
                "includeExternalMedia": 0,
            },
        )
        container = self._container(payload)
        rows: list[dict[str, object]] = []
        for hub in self._records(container, "Hub"):
            items = [
                item
                for raw in self._records(hub, "Metadata")
                if (item := self._item(session, raw)) is not None
            ][:limit]
            if not items:
                continue
            rows.append(
                {
                    "id": str(hub.get("hubIdentifier") or hub.get("key") or ""),
                    "title": str(hub.get("title") or "Plex"),
                    "items": items,
                    "more": bool(hub.get("more")),
                }
            )
        self.auth_manager.assert_server_session_current(session)
        return {"rows": rows, "server": session.machine_id}

    def libraries(self) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        payload = session.client.get("/library/sections")
        container = self._container(payload)
        libraries: list[dict[str, object]] = []
        for section in self._records(container, "Directory"):
            section_type = str(section.get("type") or "").strip().lower()
            section_key = str(section.get("key") or "").strip()
            if section_type not in SECTION_TYPES or not section_key:
                continue
            path = f"/library/sections/{urllib.parse.quote(section_key, safe='')}/all"
            libraries.append(
                {
                    "id": self._reference(session, "section", path, section_type),
                    "title": str(section.get("title") or "Plex Library"),
                    "type": section_type,
                    "updated_at": _integer(section.get("updatedAt")) or None,
                }
            )
        self.auth_manager.assert_server_session_current(session)
        return {"libraries": libraries, "server": session.machine_id}

    def library_items(
        self,
        library_id: str,
        *,
        start: int,
        limit: int,
        sort: str,
    ) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, library_id, expected_kind="section")
        sort_name = str(sort or "title").strip().lower()
        if sort_name not in SORTS:
            raise PlexError(
                "plex_invalid_sort",
                "Choose a supported Plex library sort",
                status_code=400,
            )
        payload = session.client.get(
            reference["path"],
            query={
                "sort": SORTS[sort_name],
                "includeGuids": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": limit,
            },
        )
        container = self._container(payload)
        items = [
            item
            for raw in self._records(container, "Metadata")
            if (item := self._item(session, raw)) is not None
        ]
        total = max(len(items), _integer(container.get("totalSize"), len(items)))
        offset = max(0, _integer(container.get("offset"), start))
        next_start = offset + len(items)
        self.auth_manager.assert_server_session_current(session)
        return {
            "library_id": library_id,
            "library_type": reference["media_type"],
            "sort": sort_name,
            "items": items,
            "count": total,
            "start": offset,
            "limit": limit,
            "next_start": next_start if next_start < total else None,
        }

    def search(self, query: str, *, limit: int) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        payload = session.client.get(
            "/hubs/search",
            query={"query": query, "limit": limit, "includeGuids": 1},
        )
        container = self._container(payload)
        items: list[dict[str, object]] = []
        seen: set[str] = set()
        for hub in self._records(container, "Hub"):
            for raw in self._records(hub, "Metadata"):
                identity = f"{raw.get('type')}:{_safe_upstream_path(raw.get('key'))}"
                if identity in seen:
                    continue
                item = self._item(session, raw)
                if item is None:
                    continue
                seen.add(identity)
                items.append(item)
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        self.auth_manager.assert_server_session_current(session)
        return {"query": query, "items": items, "count": len(items)}

    def item_detail(self, item_id: str) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        payload = session.client.get(
            reference["path"],
            query={"includeGuids": 1, "includeExtras": 0},
        )
        container = self._container(payload)
        records = self._records(container, "Metadata")
        item = self._item(session, records[0], detail=True) if records else None
        if item is None:
            raise PlexError(
                "plex_not_found",
                "The requested Plex item was not found",
                status_code=404,
            )
        self.auth_manager.assert_server_session_current(session)
        return {"item": item}

    def children(
        self,
        item_id: str,
        *,
        start: int,
        limit: int,
    ) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        if reference["media_type"] not in {"show", "season"}:
            raise PlexError(
                "plex_children_unavailable",
                "This Plex item does not contain browsable episodes or seasons",
                status_code=400,
            )
        base_path = str(reference["path"]).partition("?")[0].rstrip("/")
        if re.fullmatch(r"/library/metadata/[^/?]+", base_path) is None:
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is no longer valid",
                status_code=404,
            )
        payload = session.client.get(
            f"{base_path}/children",
            query={
                "includeGuids": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": limit,
            },
        )
        container = self._container(payload)
        records = self._records(container, "Metadata") + self._records(
            container, "Directory"
        )
        items = [
            item
            for raw in records
            if (item := self._item(session, raw)) is not None
        ]
        total = max(len(items), _integer(container.get("totalSize"), len(items)))
        offset = max(0, _integer(container.get("offset"), start))
        next_start = offset + len(items)
        self.auth_manager.assert_server_session_current(session)
        return {
            "parent_id": item_id,
            "items": items,
            "count": total,
            "start": offset,
            "limit": limit,
            "next_start": next_start if next_start < total else None,
        }

    def artwork(self, asset_id: str) -> PlexBinaryResponse:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, asset_id, expected_kind="asset")
        result = session.client.get_bytes(
            reference["path"],
            query={"width": 1000, "height": 1500, "minSize": 1, "upscale": 0},
        )
        self.auth_manager.assert_server_session_current(session)
        return result

    def durable_item(self, item_id: str) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        raw = self._fetch_item_record(session, reference["path"])
        item = self._item(session, raw, detail=True)
        if item is None:
            raise PlexError(
                "plex_not_found",
                "The requested Plex item was not found",
                status_code=404,
            )
        if str(item.get("type") or "") not in {"movie", "episode"}:
            raise PlexError(
                "plex_item_not_playable",
                "Choose a Plex movie or episode to play",
                status_code=400,
            )
        durable = {
            "url": "https://plex.invalid/item",
            "provider": "plex",
            "title": str(item.get("title") or "Plex item"),
            "plex_item_id": item_id,
            "plex_server_machine_id": session.machine_id,
            "type": str(item.get("type") or ""),
            **({"thumbnail": item["poster_url"]} if item.get("poster_url") else {}),
        }
        view_offset = max(0, _integer(item.get("view_offset_ms")))
        if view_offset:
            durable["resume_pos"] = view_offset / 1000.0
        duration = max(0, _integer(item.get("duration_ms")))
        if duration:
            durable["duration_sec"] = duration / 1000.0
        self.auth_manager.assert_server_session_current(session)
        return durable

    def resolve_playback_item(self, item: dict[str, object]) -> dict[str, object]:
        item_id = str(item.get("plex_item_id") or "").strip()
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        raw = self._fetch_item_record(session, reference["path"])
        part, media = self._select_direct_part(raw)
        part_path = _safe_upstream_path(part.get("key"))
        if not part_path or not part_path.startswith("/library/parts/"):
            raise PlexError(
                "plex_media_unavailable",
                "Plex did not return a playable media part",
                status_code=502,
            )
        stream_id = self._reference(session, "stream", part_path, "media")
        self.auth_manager.assert_server_session_current(session)
        return {
            **item,
            "url": f"http://127.0.0.1:{config.server_port()}/plex/stream/{stream_id}",
            "provider": "plex",
            "plex_server_machine_id": session.machine_id,
            "plex_stream_mode": "direct",
            "plex_container": str(media.get("container") or part.get("container") or ""),
            "plex_video_codec": str(media.get("videoCodec") or ""),
            "plex_audio_codec": str(media.get("audioCodec") or ""),
        }

    def media_stream(self, stream_id: str, *, range_header: str) -> PlexStreamResponse:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, stream_id, expected_kind="stream")
        if not str(reference["path"]).startswith("/library/parts/"):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex stream reference is no longer valid",
                status_code=404,
            )
        stream = session.client.open_stream(
            reference["path"],
            range_header=range_header,
        )
        self.auth_manager.assert_server_session_current(session)
        return stream

    def action(self, item_id: str, command: str) -> dict[str, object]:
        action = str(command or "play_now").strip().lower()
        if action not in {"play_now", "play_next", "play_last", "resume"}:
            raise PlexError(
                "plex_action_unsupported",
                "Choose a supported Plex playback action",
                status_code=400,
            )
        item = self.durable_item(item_id)
        if action == "play_next":
            queue_length, _snapshot = playback_service.queue_item_next(item)
            return {"ok": True, "action": action, "queue_length": queue_length, "item": item}
        if action == "play_last":
            queue_length, _snapshot = playback_service.queue_item(item)
            return {"ok": True, "action": action, "queue_length": queue_length, "item": item}
        start_pos = None
        if action == "resume":
            resume_pos = item.get("resume_pos")
            start_pos = float(resume_pos) if resume_pos is not None else None
        now = playback_service.play_now(
            item,
            use_resolver=False,
            cec=False,
            clear_queue=True,
            mode="plex_play",
            start_pos=start_pos,
        )
        return {"ok": True, "action": action, "now_playing": now}

    def report_timeline(
        self,
        now: dict[str, object],
        *,
        playback_state: str,
        position_sec: float | None = None,
        duration_sec: float | None = None,
    ) -> bool:
        state_name = str(playback_state or "").strip().lower()
        if state_name not in TIMELINE_STATES:
            raise ValueError("Unsupported Plex timeline state")
        if str(now.get("provider") or "").strip().lower() != "plex":
            return False
        item_id = str(now.get("plex_item_id") or "").strip()
        if not item_id:
            return False
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        path = str(reference["path"]).partition("?")[0]
        match = re.fullmatch(r"/library/metadata/([^/]+)", path)
        if match is None:
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is no longer valid",
                status_code=404,
            )
        position = _number(position_sec if position_sec is not None else now.get("resume_pos"))
        duration = _number(duration_sec if duration_sec is not None else now.get("duration_sec"))
        time_ms = max(0, int((position or 0.0) * 1000.0))
        duration_ms = max(0, int((duration or 0.0) * 1000.0))
        self.auth_manager.assert_server_session_current(session)
        session.client.request_no_content(
            "POST",
            "/:/timeline",
            query={
                "ratingKey": match.group(1),
                "key": path,
                "identifier": "com.plexapp.plugins.library",
                "state": state_name,
                "time": time_ms,
                "duration": duration_ms,
            },
        )
        self.auth_manager.assert_server_session_current(session)
        return True

    def _fetch_item_record(
        self,
        session: plex_auth.PlexServerSession,
        path: str,
    ) -> dict[str, Any]:
        payload = session.client.get(
            path,
            query={"includeGuids": 1, "includeExtras": 0},
        )
        container = self._container(payload)
        records = self._records(container, "Metadata")
        if not records:
            raise PlexError(
                "plex_not_found",
                "The requested Plex item was not found",
                status_code=404,
            )
        return records[0]

    @staticmethod
    def _select_direct_part(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        media_values = raw.get("Media") if isinstance(raw.get("Media"), list) else []
        for media in media_values:
            if not isinstance(media, dict):
                continue
            parts = media.get("Part") if isinstance(media.get("Part"), list) else []
            for part in parts:
                if not isinstance(part, dict):
                    continue
                if part.get("accessible") is False or part.get("exists") is False:
                    continue
                if _safe_upstream_path(part.get("key")):
                    return part, media
        raise PlexError(
            "plex_media_unavailable",
            "Plex did not return a playable media part",
            status_code=502,
        )

    @staticmethod
    def _container(payload: object) -> dict[str, Any]:
        container = payload.get("MediaContainer") if isinstance(payload, dict) else None
        if not isinstance(container, dict):
            raise PlexError(
                "plex_invalid_response",
                "Plex returned an unexpected catalog response",
                status_code=502,
            )
        return container

    @staticmethod
    def _records(container: object, field: str) -> list[dict[str, Any]]:
        records = container.get(field) if isinstance(container, dict) else None
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    def _item(
        self,
        session: plex_auth.PlexServerSession,
        raw: dict[str, Any],
        *,
        detail: bool = False,
    ) -> dict[str, object] | None:
        media_type = str(raw.get("type") or "").strip().lower()
        path = _safe_upstream_path(raw.get("key"))
        if media_type not in VIDEO_TYPES or not path:
            return None
        duration = max(0, _integer(raw.get("duration")))
        view_offset = max(0, _integer(raw.get("viewOffset")))
        progress = round(min(100.0, (view_offset / duration) * 100.0), 1) if duration else 0.0
        subtitle_parts: list[str] = []
        if media_type == "episode":
            if raw.get("grandparentTitle"):
                subtitle_parts.append(str(raw["grandparentTitle"]))
            season = _integer(raw.get("parentIndex"), -1)
            episode = _integer(raw.get("index"), -1)
            if season >= 0 and episode >= 0:
                subtitle_parts.append(f"S{season:02d} E{episode:02d}")
        elif media_type == "season" and raw.get("parentTitle"):
            subtitle_parts.append(str(raw["parentTitle"]))
        elif raw.get("year"):
            subtitle_parts.append(str(raw["year"]))

        item: dict[str, object] = {
            "id": self._reference(session, "item", path, media_type),
            "type": media_type,
            "title": str(raw.get("title") or "Untitled"),
            "subtitle": " · ".join(subtitle_parts),
            "year": _integer(raw.get("year")) or None,
            "duration_ms": duration or None,
            "view_offset_ms": view_offset,
            "progress": progress,
            "watched": _integer(raw.get("viewCount")) > 0,
            "index": _integer(raw.get("index")) or None,
            "parent_index": _integer(raw.get("parentIndex")) or None,
            "leaf_count": _integer(raw.get("leafCount")) or None,
            "viewed_leaf_count": _integer(raw.get("viewedLeafCount")),
            "poster_url": self._asset_url(session, raw.get("thumb")),
            "backdrop_url": self._asset_url(session, raw.get("art")),
            "children_available": media_type in {"show", "season"},
        }
        if detail:
            genres = [
                str(value.get("tag") or "").strip()
                for value in (raw.get("Genre") or [])
                if isinstance(value, dict) and str(value.get("tag") or "").strip()
            ]
            item.update(
                {
                    "summary": str(raw.get("summary") or ""),
                    "tagline": str(raw.get("tagline") or ""),
                    "content_rating": str(raw.get("contentRating") or ""),
                    "audience_rating": _number(raw.get("audienceRating")),
                    "studio": str(raw.get("studio") or ""),
                    "originally_available_at": str(raw.get("originallyAvailableAt") or ""),
                    "genres": genres,
                }
            )
        return item

    def _asset_url(
        self,
        session: plex_auth.PlexServerSession,
        value: object,
    ) -> str:
        path = _safe_upstream_path(value)
        if not path:
            return ""
        asset_id = self._reference(session, "asset", path, "image")
        return f"/plex/artwork/{asset_id}"

    @staticmethod
    def _reference(
        session: plex_auth.PlexServerSession,
        kind: str,
        path: str,
        media_type: str,
    ) -> str:
        document = {
            "v": REFERENCE_VERSION,
            "a": session.account_id,
            "m": session.machine_id,
            "k": kind,
            "p": path,
            "t": media_type,
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        encrypted = AESGCMSIV(session.reference_key).encrypt(
            nonce,
            raw,
            REFERENCE_AAD,
        )
        return _b64encode(nonce + encrypted)

    @staticmethod
    def _resolve(
        session: plex_auth.PlexServerSession,
        value: str,
        *,
        expected_kind: str,
    ) -> dict[str, str]:
        reference = str(value or "").strip()
        if not reference or len(reference) > MAX_REFERENCE_LENGTH:
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is invalid",
                status_code=404,
            )
        try:
            encrypted = _b64decode(reference)
            nonce, ciphertext = encrypted[:12], encrypted[12:]
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError
            raw = AESGCMSIV(session.reference_key).decrypt(
                nonce,
                ciphertext,
                REFERENCE_AAD,
            )
            document = json.loads(raw.decode("utf-8"))
        except (InvalidTag, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is invalid",
                status_code=404,
            ) from None
        if not isinstance(document, dict):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is invalid",
                status_code=404,
            )
        path = _safe_upstream_path(document.get("p"))
        if (
            document.get("v") != REFERENCE_VERSION
            or str(document.get("a") or "") != session.account_id
            or str(document.get("m") or "") != session.machine_id
            or str(document.get("k") or "") != expected_kind
            or not path
        ):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is no longer valid",
                status_code=404,
            )
        return {
            "path": path,
            "media_type": str(document.get("t") or ""),
        }


catalog_service = PlexCatalogService()


def emit_timeline_hint(
    now: dict[str, object] | None,
    *,
    playback_state: str,
    position_sec: float | None = None,
    duration_sec: float | None = None,
) -> bool:
    """Schedule one ordered, throttled Plex watch-state report."""
    if not isinstance(now, dict):
        return False
    item_id = str(now.get("plex_item_id") or "").strip()
    if str(now.get("provider") or "").strip().lower() != "plex" or not item_id:
        return False
    state_name = str(playback_state or "").strip().lower()
    if state_name not in TIMELINE_STATES:
        return False
    position = _number(position_sec if position_sec is not None else now.get("resume_pos"))
    duration = _number(duration_sec if duration_sec is not None else now.get("duration_sec"))
    pos = max(0.0, position or 0.0)
    identity = (item_id, str(now.get("history_id") or ""))
    now_ts = time.monotonic()

    global _TIMELINE_SEQUENCE
    with _TIMELINE_LOCK:
        previous = _TIMELINE_LAST.get(identity)
        should_send = previous is None
        if previous is not None:
            last_ts, last_state, last_pos, _last_sequence = previous
            should_send = (
                state_name != last_state
                or state_name == "stopped"
                or (now_ts - last_ts) >= TIMELINE_INTERVAL_SEC
                or abs(pos - last_pos) >= TIMELINE_SEEK_DELTA_SEC
            )
        if not should_send:
            return False
        _TIMELINE_SEQUENCE += 1
        sequence = _TIMELINE_SEQUENCE
        _TIMELINE_LAST[identity] = (now_ts, state_name, pos, sequence)
        if len(_TIMELINE_LAST) > 256:
            oldest = min(_TIMELINE_LAST, key=lambda key: _TIMELINE_LAST[key][0])
            _TIMELINE_LAST.pop(oldest, None)
        snapshot = dict(now)

    def _run() -> None:
        with _TIMELINE_SEND_LOCK:
            with _TIMELINE_LOCK:
                latest = _TIMELINE_LAST.get(identity)
                if latest is None or latest[3] != sequence:
                    return
            try:
                catalog_service.report_timeline(
                    snapshot,
                    playback_state=state_name,
                    position_sec=pos,
                    duration_sec=duration,
                )
            except (PlexError, ValueError):
                return

    try:
        threading.Thread(
            target=_run,
            daemon=True,
            name="relaytv-plex-timeline",
        ).start()
    except Exception:
        return False
    return True
