# SPDX-License-Identifier: GPL-3.0-only
"""Plex catalog normalization and opaque public references."""
from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import os
import re
import threading
import time
from typing import Any
import urllib.parse
import uuid

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

from .. import config, playback_service, state, video_profile
from ..debug import get_logger
from . import plex_auth
from .plex_client import PlexBinaryResponse, PlexError, PlexStreamResponse


logger = get_logger("plex_service")

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
_TRANSCODE_LOCK = threading.RLock()


@dataclass
class _TranscodeEntry:
    """One minted transcode reference and the PMS session it will start."""

    session: plex_auth.PlexServerSession
    session_id: str
    created_at: float
    opened: bool = False


_TRANSCODE_SESSIONS: dict[str, _TranscodeEntry] = {}
# How long a minted transcode reference may sit unopened before it is dropped.
# Playback opens the stream within seconds of resolving it; anything still
# unopened after this was abandoned by a play that never started.
TRANSCODE_UNOPENED_TTL_SEC = 300.0


def _sweep_unopened_transcodes_locked(now: float) -> list[str]:
    """Drop references that were minted for a play that never opened them.

    Caller holds ``_TRANSCODE_LOCK``. An entry is only reclaimed when
    ``opened`` is still False, so a stream that has been running for hours is
    never swept out from under itself. Nothing is asked of PMS: an unopened
    reference never reached the start path, so there is no session to stop.
    """
    stale = [
        stream_id
        for stream_id, entry in _TRANSCODE_SESSIONS.items()
        if not entry.opened and (now - entry.created_at) > TRANSCODE_UNOPENED_TTL_SEC
    ]
    for stream_id in stale:
        _TRANSCODE_SESSIONS.pop(stream_id, None)
    return stale
PLEX_PLAYBACK_MODES = {"auto", "direct", "transcode"}
TRANSCODE_START_PATH = "/video/:/transcode/universal/start.mkv"
TRANSCODE_STOP_PATH = "/video/:/transcode/universal/stop"
TRANSCODE_DECISION_PATH = "/video/:/transcode/universal/decision"


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
    # Traversal cannot be reached through a minted reference, which is
    # authenticated and only ever carries paths the server itself returned.
    # It is refused anyway so this stays a whole-path validator rather than one
    # that happens to be safe because of who calls it. Unquote first: the check
    # must see the same segments the server will.
    if any(
        segment == ".."
        for segment in urllib.parse.unquote(parsed.path).replace("\\", "/").split("/")
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
        records = self._records(container, "Metadata")
        items = [
            item for raw in records if (item := self._item(session, raw)) is not None
        ]
        total = max(len(items), _integer(container.get("totalSize"), len(items)))
        offset = max(0, _integer(container.get("offset"), start))
        # Advance by what the server returned, not by what survived filtering.
        # Any record dropped by _item would otherwise shorten the stride and
        # make the next page overlap this one, repeating rows in the UI.
        next_start = offset + len(records)
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
        next_start = offset + len(records)
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

    def durable_item(
        self,
        item_id: str,
        *,
        version_id: str = "",
        audio_id: str = "",
        subtitle_id: str = "",
    ) -> dict[str, object]:
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
        selected_part_id = str(version_id or "").strip()
        selected_part_path = ""
        if selected_part_id:
            selected_reference = self._resolve(
                session,
                selected_part_id,
                expected_kind="part",
            )
            selected_part_path = selected_reference["path"]
        selected_part, _selected_media = self._select_direct_part(
            raw,
            part_path=selected_part_path,
        )
        selected_audio_id = str(audio_id or "").strip()
        selected_subtitle_id = str(subtitle_id or "").strip()
        if selected_audio_id:
            self._selected_stream_id(
                session,
                selected_audio_id,
                selected_part,
                stream_type=2,
            )
        if selected_subtitle_id:
            self._selected_stream_id(
                session,
                selected_subtitle_id,
                selected_part,
                stream_type=3,
            )
        configured_mode = str(
            state.get_settings().get("plex_playback_mode") or "auto"
        ).strip().lower()
        if configured_mode == "direct" and (
            selected_audio_id or selected_subtitle_id
        ):
            raise PlexError(
                "plex_track_requires_transcode",
                "Plex track selection requires Automatic or Always transcode playback",
                status_code=400,
            )
        durable = {
            "url": "https://plex.invalid/item",
            "provider": "plex",
            "title": str(item.get("title") or "Plex item"),
            "plex_item_id": item_id,
            "plex_server_machine_id": session.machine_id,
            "type": str(item.get("type") or ""),
            **({"plex_part_id": selected_part_id} if selected_part_id else {}),
            **({"plex_audio_id": selected_audio_id} if selected_audio_id else {}),
            **(
                {"plex_subtitle_id": selected_subtitle_id}
                if selected_subtitle_id
                else {}
            ),
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

    def resolve_playback_item(
        self,
        item: dict[str, object],
        *,
        start_pos: float | None = None,
    ) -> dict[str, object]:
        item_id = str(item.get("plex_item_id") or "").strip()
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        raw = self._fetch_item_record(session, reference["path"])
        selected_part_path = ""
        selected_part_id = str(item.get("plex_part_id") or "").strip()
        if selected_part_id:
            selected_part_path = self._resolve(
                session,
                selected_part_id,
                expected_kind="part",
            )["path"]
        part, media, media_index, part_index = self._select_playback_part(
            raw,
            part_path=selected_part_path,
        )
        part_path = _safe_upstream_path(part.get("key"))
        if not part_path or not part_path.startswith("/library/parts/"):
            raise PlexError(
                "plex_media_unavailable",
                "Plex did not return a playable media part",
                status_code=502,
            )
        selected_audio = ""
        selected_subtitle = ""
        if item.get("plex_audio_id"):
            selected_audio = self._selected_stream_id(
                session,
                str(item["plex_audio_id"]),
                part,
                stream_type=2,
            )
        if item.get("plex_subtitle_id"):
            selected_subtitle = self._selected_stream_id(
                session,
                str(item["plex_subtitle_id"]),
                part,
                stream_type=3,
            )
        settings = state.get_settings()
        configured_mode = str(settings.get("plex_playback_mode") or "auto").strip().lower()
        playback_mode = configured_mode if configured_mode in PLEX_PLAYBACK_MODES else "auto"
        max_bitrate = _integer(settings.get("plex_max_bitrate"))
        if max_bitrate not in {4000, 8000, 12000, 20000}:
            max_bitrate = 0
        if playback_mode == "direct" and (selected_audio or selected_subtitle):
            raise PlexError(
                "plex_track_requires_transcode",
                "Plex track selection requires Automatic or Always transcode playback",
                status_code=400,
            )
        stream_mode = "direct"
        stream_path = part_path
        stream_kind = "stream"
        if playback_mode != "direct":
            session_id = str(uuid.uuid4())
            profile_requires_transcode = (
                playback_mode == "auto"
                and self._profile_requires_transcode(media, part)
            )
            decision_query = self._transcode_query(
                reference["path"],
                media_index=media_index,
                part_index=part_index,
                session_id=session_id,
                start_pos=start_pos,
                direct_play=(
                    playback_mode == "auto"
                    and not selected_audio
                    and not selected_subtitle
                    and not profile_requires_transcode
                ),
                media=media,
                max_bitrate=max_bitrate,
                audio_stream_id=selected_audio,
                subtitle_stream_id=selected_subtitle,
            )
            decision = self._playback_decision(session, decision_query)
            if decision != "direct":
                stream_mode = decision
                stream_kind = "transcode_stream"
                stream_path = f"{TRANSCODE_START_PATH}?{urllib.parse.urlencode(decision_query)}"
        stream_id = self._reference(session, stream_kind, stream_path, "media")
        self.auth_manager.assert_server_session_current(session)
        if stream_kind == "transcode_stream":
            with _TRANSCODE_LOCK:
                dropped = _sweep_unopened_transcodes_locked(time.time())
                _TRANSCODE_SESSIONS[stream_id] = _TranscodeEntry(
                    session=session,
                    session_id=session_id,
                    created_at=time.time(),
                )
            if dropped:
                logger.info("plex_transcode_refs_swept count=%d", len(dropped))
        return {
            **item,
            "url": f"http://127.0.0.1:{config.server_port()}/plex/stream/{stream_id}",
            "provider": "plex",
            "plex_server_machine_id": session.machine_id,
            "plex_stream_mode": stream_mode,
            "plex_container": str(media.get("container") or part.get("container") or ""),
            "plex_video_codec": str(media.get("videoCodec") or ""),
            "plex_audio_codec": str(media.get("audioCodec") or ""),
        }

    def media_stream(self, stream_id: str, *, range_header: str) -> PlexStreamResponse:
        session = self.auth_manager.selected_server_session()
        transcode = False
        try:
            reference = self._resolve(session, stream_id, expected_kind="stream")
        except PlexError as direct_error:
            try:
                reference = self._resolve(
                    session,
                    stream_id,
                    expected_kind="transcode_stream",
                )
                transcode = True
            except PlexError:
                raise direct_error from None
        path = str(reference["path"])
        if not transcode and not path.startswith("/library/parts/"):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex stream reference is no longer valid",
                status_code=404,
            )
        if transcode:
            parsed = urllib.parse.urlsplit(path)
            query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            session_id = str(query.get("session") or "")
            if (
                parsed.path != TRANSCODE_START_PATH
                or not re.fullmatch(r"[0-9a-fA-F-]{36}", session_id)
            ):
                raise PlexError(
                    "plex_invalid_reference",
                    "The Plex stream reference is no longer valid",
                    status_code=404,
                )
            with _TRANSCODE_LOCK:
                registered = _TRANSCODE_SESSIONS.get(stream_id)
                registered_session = registered.session if registered is not None else None
                if (
                    registered is None
                    or registered_session is None
                    or registered.session_id != session_id
                    or registered_session.account_id != session.account_id
                    or registered_session.machine_id != session.machine_id
                    or registered_session.generation != session.generation
                    or registered_session.reference_key != session.reference_key
                ):
                    raise PlexError(
                        "plex_stream_expired",
                        "The Plex transcoded stream has expired",
                        status_code=404,
                    )
                # Mark before the request goes out: from here the reference is
                # in use and the sweep must leave it alone however long the
                # stream runs.
                registered.opened = True
            try:
                stream = session.client.open_stream(
                    parsed.path,
                    query=query,
                    on_close=lambda: stop_transcode_stream(stream_id),
                    allow_incomplete_read=True,
                )
            except Exception:
                stop_transcode_stream(stream_id)
                raise
        else:
            stream = session.client.open_stream(path, range_header=range_header)
        try:
            self.auth_manager.assert_server_session_current(session)
        except Exception:
            stream.close()
            raise
        return stream

    @staticmethod
    def _stop_transcode(
        session: plex_auth.PlexServerSession,
        session_id: str,
    ) -> None:
        try:
            session.client.request_no_content(
                "GET",
                TRANSCODE_STOP_PATH,
                query={"session": session_id},
            )
        except Exception:
            pass

    @staticmethod
    def _profile_value(value: object) -> str:
        candidate = str(value or "").strip().lower()
        return candidate if re.fullmatch(r"[a-z0-9_.-]+", candidate) else ""

    @staticmethod
    def _profile_requires_transcode(
        media: dict[str, Any],
        part: dict[str, Any],
    ) -> bool:
        try:
            profile = dict(video_profile.get_profile() or {})
        except Exception:
            profile = {}
        video_stream = next(
            (
                stream
                for stream in (
                    part.get("Stream")
                    if isinstance(part.get("Stream"), list)
                    else []
                )
                if isinstance(stream, dict)
                and _integer(stream.get("streamType")) == 1
            ),
            {},
        )
        codec = str(
            media.get("videoCodec") or video_stream.get("codec") or ""
        ).strip().lower()
        height = _integer(media.get("height") or video_stream.get("height"))
        bit_depth = _integer(video_stream.get("bitDepth") or media.get("bitDepth"))
        bitrate = _integer(media.get("bitrate") or video_stream.get("bitrate"))
        decode_profile = str(profile.get("decode_profile") or "").strip().lower()
        display_cap_height = _integer(profile.get("display_cap_height"))

        if codec in {"av1", "av01"} and not bool(profile.get("av1_allowed")):
            return True
        if display_cap_height > 0 and height > display_cap_height:
            return True
        if decode_profile in {"software", "arm_safe"}:
            if codec in {"hevc", "h265", "av1", "vp9"} and height >= 1080:
                return True
            if bit_depth > 8 and codec in {"hevc", "h265", "av1"}:
                return True
            if bitrate > 25_000:
                return True
        if (
            codec in {"hevc", "h265"}
            and bit_depth > 8
            and decode_profile
            not in {"intel_amd64_qsv", "intel_amd64_vaapi", "nvidia_cuda"}
        ):
            return True
        return False

    def _transcode_query(
        self,
        item_path: str,
        *,
        media_index: int,
        part_index: int,
        session_id: str,
        start_pos: float | None,
        direct_play: bool,
        media: dict[str, Any],
        max_bitrate: int,
        audio_stream_id: str,
        subtitle_stream_id: str,
    ) -> dict[str, object]:
        query: dict[str, object] = {
            "path": item_path,
            "mediaIndex": media_index,
            "partIndex": part_index,
            "protocol": "http",
            "fastSeek": 1,
            "directPlay": 1 if direct_play else 0,
            "directStream": 1 if direct_play else 0,
            "directStreamAudio": 1 if direct_play else 0,
            "subtitles": "burn" if subtitle_stream_id else "none",
            "location": "lan",
            "session": session_id,
            "hasMDE": 1,
            "copyts": 1,
            "mediaBufferSize": 20971,
            "X-Plex-Client-Profile-Name": "Chrome",
        }
        try:
            offset = max(0.0, float(start_pos)) if start_pos is not None else 0.0
        except (TypeError, ValueError):
            offset = 0.0
        if offset:
            query["offset"] = round(offset, 3)
        if max_bitrate:
            query["maxVideoBitrate"] = max_bitrate
        if audio_stream_id:
            query["audioStreamID"] = audio_stream_id
        if subtitle_stream_id:
            query["subtitleStreamID"] = subtitle_stream_id
            query["advancedSubtitles"] = "text"
        if direct_play:
            container = self._profile_value(media.get("container"))
            video_codec = self._profile_value(media.get("videoCodec"))
            audio_codec = self._profile_value(media.get("audioCodec"))
            if container and (video_codec or audio_codec):
                profile = (
                    "add-direct-play-profile(type=videoProfile"
                    f"&container={container}"
                    f"&videoCodec={video_codec or '*'}"
                    f"&audioCodec={audio_codec or '*'}"
                    "&subtitleCodec=*)"
                )
                if max_bitrate:
                    profile += (
                        "+add-limitation(scope=videoCodec&scopeName=*"
                        "&type=upperBound&name=video.bitrate"
                        f"&value={max_bitrate}&isRequired=true&replace=true)"
                    )
                query["X-Plex-Client-Profile-Extra"] = profile
        return query

    def _playback_decision(
        self,
        session: plex_auth.PlexServerSession,
        query: dict[str, object],
    ) -> str:
        payload = session.client.get(TRANSCODE_DECISION_PATH, query=query)
        container = self._container(payload)
        direct_code = _integer(container.get("directPlayDecisionCode"), -1)
        records = self._records(container, "Metadata") or self._records(
            container,
            "Video",
        )
        media = self._records(records[0], "Media") if records else []
        parts = self._records(media[0], "Part") if media else []
        part_decision = re.sub(
            r"[^a-z]",
            "",
            str(parts[0].get("decision") or "").lower() if parts else "",
        )
        if bool(_integer(query.get("directPlay"))) and (
            direct_code == 1000 or part_decision == "directplay"
        ):
            return "direct"
        if not parts or part_decision not in {
            "copy",
            "transcode",
        }:
            raise PlexError(
                "plex_media_incompatible",
                "Plex could not prepare this item for playback",
                status_code=502,
            )
        stream_decisions = {
            str(stream.get("decision") or "").lower()
            for stream in self._records(parts[0], "Stream")
            if _integer(stream.get("streamType")) in {1, 2}
        }
        return "remux" if stream_decisions and stream_decisions <= {"copy"} else "transcode"

    def action(
        self,
        item_id: str,
        command: str,
        *,
        version_id: str = "",
        audio_id: str = "",
        subtitle_id: str = "",
    ) -> dict[str, object]:
        action = str(command or "play_now").strip().lower()
        if action not in {"play_now", "play_next", "play_last", "resume"}:
            raise PlexError(
                "plex_action_unsupported",
                "Choose a supported Plex playback action",
                status_code=400,
            )
        item = self.durable_item(
            item_id,
            version_id=version_id,
            audio_id=audio_id,
            subtitle_id=subtitle_id,
        )
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
    def _select_direct_part(
        raw: dict[str, Any],
        *,
        part_path: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        part, media, _media_index, _part_index = (
            PlexCatalogService._select_playback_part(raw, part_path=part_path)
        )
        return part, media

    @staticmethod
    def _select_playback_part(
        raw: dict[str, Any],
        *,
        part_path: str = "",
    ) -> tuple[dict[str, Any], dict[str, Any], int, int]:
        requested_path = _safe_upstream_path(part_path) if part_path else ""
        media_values = raw.get("Media") if isinstance(raw.get("Media"), list) else []
        for media_index, media in enumerate(media_values):
            if not isinstance(media, dict):
                continue
            parts = media.get("Part") if isinstance(media.get("Part"), list) else []
            for part_index, part in enumerate(parts):
                if not isinstance(part, dict):
                    continue
                if part.get("accessible") is False or part.get("exists") is False:
                    continue
                candidate_path = _safe_upstream_path(part.get("key"))
                if candidate_path and (not requested_path or candidate_path == requested_path):
                    return part, media, media_index, part_index
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
                    "versions": self._media_versions(session, raw),
                }
            )
        return item

    def _media_versions(
        self,
        session: plex_auth.PlexServerSession,
        raw: dict[str, Any],
    ) -> list[dict[str, object]]:
        versions: list[dict[str, object]] = []
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
                part_path = _safe_upstream_path(part.get("key"))
                if not part_path or not part_path.startswith("/library/parts/"):
                    continue
                resolution = str(media.get("videoResolution") or "").strip().upper()
                container = str(media.get("container") or part.get("container") or "").strip()
                video_codec = str(media.get("videoCodec") or "").strip()
                audio_codec = str(media.get("audioCodec") or "").strip()
                label_parts = [value for value in (resolution, container.upper()) if value]
                if video_codec:
                    label_parts.append(video_codec.upper())
                versions.append(
                    {
                        "id": self._reference(session, "part", part_path, "media"),
                        "label": " · ".join(label_parts) or f"Version {len(versions) + 1}",
                        "container": container,
                        "video_codec": video_codec,
                        "audio_codec": audio_codec,
                        "width": _integer(media.get("width")) or None,
                        "height": _integer(media.get("height")) or None,
                        "size": _integer(part.get("size")) or None,
                        "audio_tracks": self._track_options(
                            session,
                            part,
                            stream_type=2,
                        ),
                        "subtitle_tracks": self._track_options(
                            session,
                            part,
                            stream_type=3,
                        ),
                    }
                )
        return versions

    def _track_options(
        self,
        session: plex_auth.PlexServerSession,
        part: dict[str, Any],
        *,
        stream_type: int,
    ) -> list[dict[str, object]]:
        values: list[dict[str, object]] = []
        streams = part.get("Stream") if isinstance(part.get("Stream"), list) else []
        kind = "audio_track" if stream_type == 2 else "subtitle_track"
        for stream in streams:
            if not isinstance(stream, dict) or _integer(stream.get("streamType")) != stream_type:
                continue
            stream_id = str(stream.get("id") or "").strip()
            if re.fullmatch(r"\d+", stream_id) is None:
                continue
            language = str(
                stream.get("language") or stream.get("languageCode") or ""
            ).strip()
            codec = str(stream.get("codec") or "").strip()
            label = str(
                stream.get("extendedDisplayTitle")
                or stream.get("displayTitle")
                or stream.get("title")
                or language
                or codec.upper()
                or ("Audio track" if stream_type == 2 else "Subtitle track")
            ).strip()
            values.append(
                {
                    "id": self._reference(
                        session,
                        kind,
                        f"/library/streams/{stream_id}",
                        "audio" if stream_type == 2 else "subtitle",
                    ),
                    "label": label,
                    "language": language,
                    "language_code": str(stream.get("languageCode") or "").strip(),
                    "codec": codec,
                    "channels": _integer(stream.get("channels")) or None,
                    "default": _integer(stream.get("default")) == 1,
                    "forced": _integer(stream.get("forced")) == 1,
                }
            )
        return values

    def _selected_stream_id(
        self,
        session: plex_auth.PlexServerSession,
        value: str,
        part: dict[str, Any],
        *,
        stream_type: int,
    ) -> str:
        kind = "audio_track" if stream_type == 2 else "subtitle_track"
        reference = self._resolve(session, value, expected_kind=kind)
        match = re.fullmatch(r"/library/streams/(\d+)", reference["path"])
        selected_id = match.group(1) if match else ""
        streams = part.get("Stream") if isinstance(part.get("Stream"), list) else []
        if selected_id and any(
            isinstance(stream, dict)
            and _integer(stream.get("streamType")) == stream_type
            and str(stream.get("id") or "").strip() == selected_id
            for stream in streams
        ):
            return selected_id
        raise PlexError(
            "plex_track_unavailable",
            "The selected Plex media track is no longer available",
            status_code=409,
        )

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


def _stream_id(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme or parsed.netloc:
        if parsed.path.startswith("/plex/stream/"):
            return parsed.path.rsplit("/", 1)[-1]
        return ""
    return raw.rsplit("/", 1)[-1]


def stop_transcode_stream(value: object) -> bool:
    stream_id = _stream_id(value)
    if not stream_id:
        return False
    with _TRANSCODE_LOCK:
        entry = _TRANSCODE_SESSIONS.pop(stream_id, None)
    if entry is None:
        return False
    PlexCatalogService._stop_transcode(entry.session, entry.session_id)
    return True


def stop_transcode_for_now(now: dict[str, object] | None) -> bool:
    if not isinstance(now, dict) or str(now.get("provider") or "").lower() != "plex":
        return False
    return stop_transcode_stream(now.get("stream") or now.get("url"))


def stop_all_transcodes() -> int:
    with _TRANSCODE_LOCK:
        entries = list(_TRANSCODE_SESSIONS.values())
        _TRANSCODE_SESSIONS.clear()
    for entry in entries:
        PlexCatalogService._stop_transcode(entry.session, entry.session_id)
    return len(entries)


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
            if state_name == "stopped" and last_state == "stopped":
                # Already reported stopped and nothing has played since, so
                # this adds nothing. Being idempotent here lets every path that
                # can end an item report it without coordinating with the rest.
                should_send = False
            else:
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
