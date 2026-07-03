# SPDX-License-Identifier: GPL-3.0-only
"""Jellyfin product service (docs/ARCHITECTURE_PHASE_4_ROADMAP.md).

Product-level Jellyfin behavior extracted from the routes package: payload
parsing and normalization, stream URL construction, direct/transcode policy,
playable item resolution, track preference handling, and stopped/progress
payload creation. `jellyfin_receiver` stays the transport/session/catalog
adapter; the routes modules keep HTTP guards, request models, ack shaping,
and UI events.

This module must never import the routes package. Playback transitions go
through `playback_service`; mpv control goes through `player`.
"""
from __future__ import annotations

from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..debug import get_logger
from . import jellyfin_receiver

logger = get_logger("jellyfin")


class CommandReqLike(Protocol):
    """Duck-typed view of the routes JellyfinCommandReq model."""

    action: str | None
    url: str | None
    start_pos: float | None
    payload: dict | None


def _first_nonempty_str(values: list[object]) -> str:
    for v in values:
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def access_token() -> str:
    """Prefer authenticated login-session token; fall back to configured API key."""
    return _first_nonempty_str([jellyfin_receiver.session_token(), jellyfin_receiver.api_key()])


def extract_play_url(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    # Common direct fields.
    url = _first_nonempty_str(
        [
            payload.get("url"),
            payload.get("stream_url"),
            payload.get("playback_url"),
            payload.get("direct_stream_url"),
            payload.get("transcoding_url"),
        ]
    )
    if url:
        return url

    # Common nested item/media-source fields.
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    media_sources = payload.get("MediaSources")
    if not isinstance(media_sources, list):
        media_sources = item.get("MediaSources") if isinstance(item, dict) else None
    first_media = media_sources[0] if isinstance(media_sources, list) and media_sources and isinstance(media_sources[0], dict) else {}
    url = _first_nonempty_str(
        [
            first_media.get("DirectStreamUrl"),
            first_media.get("TranscodingUrl"),
        ]
    )
    if url:
        return url

    # Fallback for command payload wrappers.
    play_cmd = payload.get("playCommand") if isinstance(payload.get("playCommand"), dict) else {}
    return _first_nonempty_str([play_cmd.get("url"), play_cmd.get("stream_url"), play_cmd.get("playback_url")])


def extract_item_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = _first_nonempty_str([payload.get("item_id"), payload.get("itemId"), payload.get("ItemId"), payload.get("id"), payload.get("Id")])
    if direct:
        return direct
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    return _first_nonempty_str([item.get("id"), item.get("Id"), item.get("item_id"), item.get("itemId")])


def canonical_item_id(raw: str | None) -> str:
    """Normalize Jellyfin ids for dedupe across hyphenated/non-hyphenated forms."""
    s = str(raw or "").strip().lower()
    if not s:
        return ""
    return s.replace("-", "")


def canonical_media_source_id(raw: str | None) -> str:
    return str(raw or "").strip().lower()


def extract_item_id_from_url(raw_url: str | None) -> str:
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        segs = [seg for seg in (parts.path or "").split("/") if seg]
        for idx, seg in enumerate(segs):
            low = seg.lower()
            if low in ("videos", "items") and idx + 1 < len(segs):
                return canonical_item_id(segs[idx + 1])
    except Exception:
        return ""
    return ""


def canonical_url_key(raw_url: str | None) -> str:
    """
    Build a stable Jellyfin media key from url for dedupe.
    Prefer canonical item id from /Videos/<id>/ or /Items/<id>/ path and
    include mediaSourceId when present so multi-version items do not collapse.
    """
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        iid = extract_item_id_from_url(u)
        if iid:
            q = dict(parse_qsl(parts.query, keep_blank_values=True))
            mid = canonical_media_source_id(
                _first_nonempty_str([q.get("mediaSourceId"), q.get("MediaSourceId"), q.get("mediasourceid")])
            )
            if mid:
                return f"{iid}::{mid}"
            return iid
        return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, "", ""))
    except Exception:
        return u


def extract_media_source_id(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    direct = _first_nonempty_str(
        [
            payload.get("media_source_id"),
            payload.get("mediaSourceId"),
            payload.get("MediaSourceId"),
        ]
    )
    if direct:
        return direct
    item = payload.get("item") if isinstance(payload.get("item"), dict) else {}
    media_sources = payload.get("MediaSources")
    if not isinstance(media_sources, list):
        media_sources = item.get("MediaSources") if isinstance(item, dict) else None
    first_media = media_sources[0] if isinstance(media_sources, list) and media_sources and isinstance(media_sources[0], dict) else {}
    return _first_nonempty_str(
        [
            first_media.get("Id"),
            first_media.get("id"),
            first_media.get("MediaSourceId"),
            first_media.get("mediaSourceId"),
        ]
    )


def extract_item_ids(payload: dict | None) -> list[str]:
    if not isinstance(payload, dict):
        return []
    raw = payload.get("ItemIds")
    if not isinstance(raw, list):
        raw = payload.get("item_ids")
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for it in raw:
        s = str(it or "").strip()
        if s:
            out.append(s)
    return out


def extract_playlist_items(payload: dict | None) -> list[dict[str, str]]:
    """
    Return playlist entries from common Jellyfin payload shapes.
    Output entries use keys: id, title, media_source_id.
    """
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, str]] = []

    def _append_entry(item_id: str, title: str = "", media_source_id: str = "") -> None:
        iid = str(item_id or "").strip()
        if not iid:
            return
        out.append(
            {
                "id": iid,
                "title": str(title or "").strip(),
                "media_source_id": str(media_source_id or "").strip(),
            }
        )

    # Rich Items list (e.g., [{"Id":"...","Name":"...","MediaSourceId":"..."}]).
    for key in ("Items", "items", "PlaylistItems", "playlist_items"):
        raw_items = payload.get(key)
        if isinstance(raw_items, list):
            for it in raw_items:
                if not isinstance(it, dict):
                    continue
                item_id = _first_nonempty_str([it.get("Id"), it.get("id"), it.get("ItemId"), it.get("itemId")])
                title = _first_nonempty_str([it.get("Name"), it.get("name"), it.get("Title"), it.get("title")])
                media_source_id = _first_nonempty_str(
                    [it.get("MediaSourceId"), it.get("mediaSourceId"), it.get("MediaSourceID"), it.get("media_source_id")]
                )
                _append_entry(item_id, title, media_source_id)
            if out:
                return out

    # Fallback to ItemIds list.
    for iid in extract_item_ids(payload):
        _append_entry(iid)
    return out


def extract_play_mode(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    mode = _first_nonempty_str(
        [
            payload.get("PlayCommand"),
            payload.get("play_command"),
            payload.get("PlayMode"),
            payload.get("play_mode"),
            payload.get("commandMode"),
            payload.get("mode"),
        ]
    ).lower()
    aliases = {
        "playnext": "playnext",
        "next": "playnext",
        "playlast": "playlast",
        "enqueue": "playlast",
        "playnow": "playnow",
        "replaceall": "playnow",
    }
    return aliases.get(mode, mode)


def normalize_action(action: str | None, payload: dict | None) -> str:
    raw = (action or "").strip().lower()
    if not raw and isinstance(payload, dict):
        raw = _first_nonempty_str(
            [
                payload.get("action"),
                payload.get("Action"),
                payload.get("command"),
                payload.get("Command"),
                payload.get("name"),
                payload.get("Name"),
            ]
        ).lower()
    aliases = {
        "playnow": "play",
        "playnext": "next",
        "nexttrack": "next",
        "previoustrack": "previous",
        "setvolume": "set_volume",
        "muteaudio": "mute",
        "unmuteaudio": "unmute",
        "pauseplayback": "pause",
        "resumeback": "resume",
        "unpauseplayback": "resume",
        "resumeplayback": "resume",
    }
    return aliases.get(raw, raw)


def ticks_to_seconds(value: object) -> float | None:
    try:
        v = float(value)
    except Exception:
        return None
    # Jellyfin ticks are 10,000,000 per second.
    if abs(v) > 1_000_000:
        return v / 10_000_000.0
    return v


def extract_seek_seconds(req: CommandReqLike) -> float | None:
    if req.start_pos is not None:
        return float(req.start_pos)
    payload = req.payload if isinstance(req.payload, dict) else {}
    for key in ("position", "seek", "PositionMs", "position_ms"):
        if key in payload:
            sec = ticks_to_seconds(payload.get(key))
            if sec is not None:
                # PositionMs style values should be converted from ms when small.
                if key.lower().endswith("ms"):
                    return sec / 1000.0
                return sec
    for key in ("PositionTicks", "position_ticks", "SeekPositionTicks", "seek_position_ticks"):
        if key in payload:
            sec = ticks_to_seconds(payload.get(key))
            if sec is not None:
                return sec
    return None


def extract_start_seconds(req: CommandReqLike) -> float | None:
    if req.start_pos is not None:
        return float(req.start_pos)
    payload = req.payload if isinstance(req.payload, dict) else {}
    for key in ("StartPositionTicks", "start_position_ticks", "position", "PositionTicks"):
        if key in payload:
            sec = ticks_to_seconds(payload.get(key))
            if sec is not None:
                return sec
    return None


def extract_command_id(req: CommandReqLike) -> str:
    payload = req.payload if isinstance(req.payload, dict) else {}
    return _first_nonempty_str(
        [
            payload.get("CommandId"),
            payload.get("commandId"),
            payload.get("MessageId"),
            payload.get("messageId"),
            payload.get("EventId"),
            payload.get("eventId"),
        ]
    )


def extract_volume(req: CommandReqLike) -> float | None:
    payload = req.payload if isinstance(req.payload, dict) else {}
    for key in ("volume", "Volume", "volume_level", "VolumeLevel"):
        if key in payload:
            try:
                v = float(payload.get(key))
                return max(0.0, min(200.0, v))
            except Exception:
                continue
    return None


def normalize_source_url(raw_url: str, *, server_url: str, api_key: str) -> str:
    u = (raw_url or "").strip()
    if not u:
        return ""
    if u.startswith("http://") or u.startswith("https://"):
        return u
    base = (server_url or "").strip().rstrip("/")
    if not base:
        return u
    path = u if u.startswith("/") else f"/{u}"
    abs_url = f"{base}{path}"
    token = (api_key or "").strip()
    if not token:
        return abs_url
    try:
        parts = urlsplit(abs_url)
        q = dict(parse_qsl(parts.query, keep_blank_values=True))
        if "api_key" not in q and "ApiKey" not in q:
            q["api_key"] = token
            return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), parts.fragment))
        return abs_url
    except Exception:
        return abs_url


def build_item_stream_url(
    item_id: str,
    *,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
) -> str:
    iid = (item_id or "").strip()
    base = (server_url or "").strip().rstrip("/")
    if not iid or not base:
        return ""
    params = {"static": "true"}
    if media_source_id:
        params["mediaSourceId"] = media_source_id
    if audio_stream_index != "":
        params["audioStreamIndex"] = str(audio_stream_index)
    if subtitle_stream_index != "":
        params["subtitleStreamIndex"] = str(subtitle_stream_index)
    if api_key:
        params["api_key"] = api_key
    return f"{base}/Videos/{iid}/stream?{urlencode(params)}"


def build_item_transcode_url(
    item_id: str,
    *,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
    max_height: int | None = None,
    max_streaming_bitrate: int | None = None,
) -> str:
    iid = (item_id or "").strip()
    base = (server_url or "").strip().rstrip("/")
    if not iid or not base:
        return ""
    params: dict[str, str] = {
        "VideoCodec": "h264",
        "AudioCodec": "aac,mp3,ac3,eac3,opus",
        "SegmentContainer": "ts",
        "BreakOnNonKeyFrames": "True",
    }
    if api_key:
        params["api_key"] = api_key
    if media_source_id:
        params["MediaSourceId"] = media_source_id
    if audio_stream_index != "":
        params["AudioStreamIndex"] = str(audio_stream_index)
    if subtitle_stream_index != "":
        params["SubtitleStreamIndex"] = str(subtitle_stream_index)
    if max_height is not None:
        try:
            h = int(max_height)
            if h > 0:
                params["MaxHeight"] = str(h)
        except Exception:
            pass
    if max_streaming_bitrate is not None:
        try:
            bps = int(max_streaming_bitrate)
            if bps > 0:
                params["MaxStreamingBitrate"] = str(bps)
                params["VideoBitrate"] = str(bps)
        except Exception:
            pass
    return f"{base}/Videos/{iid}/master.m3u8?{urlencode(params)}"


def normalize_playback_mode(raw: object) -> str:
    s = str(raw or "").strip().lower()
    if s in ("direct", "transcode", "auto"):
        return s
    return "auto"


def extract_audio_stream_index(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("AudioStreamIndex", "audioStreamIndex", "audio_stream_index"):
        if key in payload:
            try:
                return str(int(payload.get(key)))
            except Exception:
                continue
    return ""


def extract_subtitle_stream_index(payload: dict | None) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("SubtitleStreamIndex", "subtitleStreamIndex", "subtitle_stream_index"):
        if key in payload:
            try:
                return str(int(payload.get(key)))
            except Exception:
                continue
    return ""


def apply_stream_params(url: str, *, audio_stream_index: str = "", subtitle_stream_index: str = "") -> str:
    u = str(url or "").strip()
    if not u:
        return ""
    if audio_stream_index == "" and subtitle_stream_index == "":
        return u
    try:
        p = urlsplit(u)
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        # Normalize key case by endpoint type:
        #  - direct stream endpoints use lower camel-case keys
        #  - master transcode endpoints commonly use PascalCase keys
        path = str(p.path or "").strip().lower()
        is_master = path.endswith("/master.m3u8")
        audio_key = "AudioStreamIndex" if is_master else "audioStreamIndex"
        sub_key = "SubtitleStreamIndex" if is_master else "subtitleStreamIndex"
        for k in ("audioStreamIndex", "AudioStreamIndex", "audiostreamindex"):
            q.pop(k, None)
        for k in ("subtitleStreamIndex", "SubtitleStreamIndex", "subtitlestreamindex"):
            q.pop(k, None)
        if audio_stream_index != "":
            q[audio_key] = str(audio_stream_index)
        if subtitle_stream_index != "":
            q[sub_key] = str(subtitle_stream_index)
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    except Exception:
        return u


def apply_media_source_param(url: str, *, media_source_id: str = "") -> str:
    u = str(url or "").strip()
    mid = str(media_source_id or "").strip()
    if not u or not mid:
        return u
    try:
        p = urlsplit(u)
        q = dict(parse_qsl(p.query, keep_blank_values=True))
        if "mediaSourceId" not in q and "MediaSourceId" not in q:
            q["mediaSourceId"] = mid
        return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))
    except Exception:
        return u


def extract_media_source_id_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        return _first_nonempty_str(
            [
                q.get("mediaSourceId"),
                q.get("MediaSourceId"),
                q.get("mediasourceid"),
            ]
        )
    except Exception:
        return ""


def extract_audio_stream_index_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        for key in ("audioStreamIndex", "AudioStreamIndex", "audiostreamindex"):
            if key in q:
                return str(int(str(q.get(key) or "").strip()))
    except Exception:
        return ""
    return ""


def extract_subtitle_stream_index_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        for key in ("subtitleStreamIndex", "SubtitleStreamIndex", "subtitlestreamindex"):
            if key in q:
                return str(int(str(q.get(key) or "").strip()))
    except Exception:
        return ""
    return ""


def extract_item_id_from_url_raw(raw_url: str | None) -> str:
    u = str(raw_url or "").strip()
    if not u:
        return ""
    try:
        parts = urlsplit(u)
        segs = [seg for seg in (parts.path or "").split("/") if seg]
        for idx, seg in enumerate(segs):
            low = seg.lower()
            if low in ("videos", "items") and idx + 1 < len(segs):
                return str(segs[idx + 1] or "").strip()
    except Exception:
        return ""
    return ""


def url_origin(url: str) -> str:
    try:
        p = urlsplit(str(url or "").strip())
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"
    except Exception:
        return ""
    return ""


def looks_like_media_url(url: str) -> bool:
    try:
        p = urlsplit(str(url or "").strip())
        path = (p.path or "").lower()
        if "/items/" in path or "/videos/" in path:
            return True
        host = (p.netloc or "").lower()
        return "jellyfin" in host
    except Exception:
        return False


def track_type_is_subtitle(raw_type: object) -> bool:
    return str(raw_type or "").strip().lower() in {"sub", "subtitle", "subtitles"}
