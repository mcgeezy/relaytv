# SPDX-License-Identifier: GPL-3.0-only
"""Jellyfin product service (docs/ARCHITECTURE.md).

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

import os
import re
import threading
import time

from fastapi import HTTPException

from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .. import playback_service, player, resolver, state, video_profile
from ..config import runtime_config
from ..debug import get_logger
from ..thumb_cache import attach_local_thumbnail
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
        "unpause": "resume",
        "unpauseplayback": "resume",
        "resumeplayback": "resume",
        # The single button on the Jellyfin remote; the client sends one
        # command for both directions and expects the device to decide.
        "playpause": "play_pause",
        "togglepause": "play_pause",
    }
    return aliases.get(raw, raw)


def playback_is_paused() -> bool:
    """Current pause state, for resolving a PlayPause toggle."""
    try:
        if not bool(player.is_playing()):
            return True
        props = player.mpv_get_many(["pause"])
    except Exception:
        return False
    if isinstance(props, dict) and props.get("pause") is not None:
        return bool(props.get("pause"))
    return str(getattr(state, "SESSION_STATE", "") or "").strip().lower() == "paused"


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
        return "jellyfin" in host or "emby" in host
    except Exception:
        return False


def track_type_is_subtitle(raw_type: object) -> bool:
    return str(raw_type or "").strip().lower() in {"sub", "subtitle", "subtitles"}


def effective_playback_mode(settings: dict | None = None) -> str:
    src = settings if isinstance(settings, dict) else (state.get_settings() if hasattr(state, "get_settings") else {})
    val = src.get("jellyfin_playback_mode") if isinstance(src, dict) else None
    if val is None or str(val).strip() == "":
        val = runtime_config.snapshot().raw("RELAYTV_JELLYFIN_PLAYBACK_MODE", "auto")
    return normalize_playback_mode(val)


def native_auto_transcode_guard_active(*, profile: dict | None = None) -> bool:
    # Native composed playback used to blanket-force transcode in auto mode.
    # That was safe but too conservative, especially on healthy Intel/QSV and
    # Intel/VAAPI hosts. Keep an override env, otherwise only force transcode
    # for native runtimes with riskier decode profiles.
    try:
        native_active = bool(player.native_qt_runtime_active())
    except Exception:
        native_active = False
    if not native_active:
        return False

    raw = str(os.getenv("RELAYTV_JELLYFIN_NATIVE_AUTO_TRANSCODE") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False

    vp = profile if isinstance(profile, dict) else {}
    decode_profile = str(vp.get("decode_profile") or "").strip().lower()
    if decode_profile in ("intel_amd64_qsv", "intel_amd64_vaapi", "nvidia_cuda"):
        return False
    if decode_profile in ("software", "arm_safe", "vaapi_generic", "vulkan_generic"):
        return True
    # Unknown native profile stays conservative.
    return True


def target_max_streaming_bitrate(
    *,
    profile: dict | None = None,
    settings: dict | None = None,
) -> int:
    # Allow an explicit override for deployments that need tighter control.
    try:
        raw = int(float(os.getenv("RELAYTV_JELLYFIN_MAX_STREAMING_BITRATE", "0") or "0"))
        if raw > 0:
            return raw
    except Exception:
        pass

    cap = 0
    vp = profile if isinstance(profile, dict) else {}
    if isinstance(settings, dict):
        try:
            qmode = str(settings.get("quality_mode") or "").strip().lower()
            qcap = str(settings.get("quality_cap") or "").strip()
            if qmode == "manual" and qcap and qcap.isdigit():
                cap = int(qcap)
        except Exception:
            cap = 0
    if cap <= 0:
        try:
            cap = int(vp.get("display_cap_height") or 0)
        except Exception:
            cap = 0

    if cap <= 0:
        return 18_000_000
    if cap <= 360:
        return 2_500_000
    if cap <= 480:
        return 4_000_000
    if cap <= 720:
        return 8_000_000
    if cap <= 1080:
        return 18_000_000
    if cap <= 1440:
        return 28_000_000
    return 35_000_000


def auto_prefers_transcode(
    *,
    item_detail: dict | None,
    profile: dict | None,
) -> tuple[bool, str]:
    detail = item_detail if isinstance(item_detail, dict) else {}
    vp = profile if isinstance(profile, dict) else {}
    codec = str(detail.get("video_codec") or "").strip().lower()
    try:
        height = int(detail.get("video_height") or 0)
    except Exception:
        height = 0
    try:
        bit_depth = int(detail.get("video_bit_depth") or 0)
    except Exception:
        bit_depth = 0
    try:
        bitrate = int(detail.get("video_bitrate") or 0)
    except Exception:
        bitrate = 0

    decode_profile = str(vp.get("decode_profile") or "").strip().lower()
    av1_allowed = bool(vp.get("av1_allowed"))
    try:
        display_cap_height = int(vp.get("display_cap_height") or 0)
    except Exception:
        display_cap_height = 0

    if codec in ("av1", "av01") and not av1_allowed:
        return True, "av1_not_allowed"
    if display_cap_height > 0 and height > 0 and height > display_cap_height:
        return True, "exceeds_display_cap"
    if decode_profile in ("software", "arm_safe"):
        if codec in ("hevc", "h265", "av1", "vp9") and height >= 1080:
            return True, "software_decode_high_cost"
        if bit_depth > 8 and codec in ("hevc", "h265", "av1"):
            return True, "software_decode_10bit"
        if bitrate > 25_000_000:
            return True, "software_decode_high_bitrate"
    if codec in ("hevc", "h265") and bit_depth > 8 and decode_profile not in (
        "intel_amd64_qsv",
        "intel_amd64_vaapi",
        "nvidia_cuda",
    ):
        return True, "limited_hevc_10bit_support"
    return False, "direct_ok"


def select_playback_url(
    *,
    item_id: str,
    source_url: str,
    server_url: str,
    api_key: str,
    media_source_id: str = "",
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
    settings: dict | None = None,
) -> dict[str, str]:
    iid = str(item_id or "").strip()
    src = str(source_url or "").strip()
    base = str(server_url or "").strip()
    tok = str(api_key or "").strip()
    mid = str(media_source_id or "").strip()
    aidx = str(audio_stream_index or "").strip()
    sidx = str(subtitle_stream_index or "").strip()
    mode = effective_playback_mode(settings)
    if not iid:
        return {"url": src, "mode": "direct", "reason": "no_item_id", "media_source_id": mid}

    detail = {}
    try:
        detail = jellyfin_receiver.get_item_detail(iid)
    except Exception:
        detail = {}
    profile = {}
    try:
        profile = video_profile.get_profile() or {}
    except Exception:
        profile = {}

    prefer_transcode = False
    reason = "direct_mode"
    if mode == "transcode":
        prefer_transcode = True
        reason = "forced_transcode_mode"
    elif mode == "auto":
        if native_auto_transcode_guard_active(profile=profile):
            prefer_transcode = True
            reason = "native_auto_transcode"
        elif not isinstance(detail, dict) or not detail:
            # Compatibility-first fallback: if detail lookup fails, prefer
            # transcode to avoid repeated direct-play failures on unknown codecs.
            prefer_transcode = True
            reason = "auto_no_detail"
        else:
            prefer_transcode, reason = auto_prefers_transcode(item_detail=detail, profile=profile)

    if not src:
        src = build_item_stream_url(
            iid,
            server_url=base,
            api_key=tok,
            media_source_id=mid,
            audio_stream_index=aidx,
            subtitle_stream_index=sidx,
        )

    if not prefer_transcode:
        return {"url": src, "mode": "direct", "reason": reason, "media_source_id": mid}

    try:
        cap_height = int(profile.get("display_cap_height") or 0)
    except Exception:
        cap_height = 0
    target_bitrate = target_max_streaming_bitrate(profile=profile, settings=settings if isinstance(settings, dict) else None)
    selected = jellyfin_receiver.resolve_playback_url(
        iid,
        prefer_transcode=True,
        media_source_id=mid,
        audio_stream_index=aidx,
        subtitle_stream_index=sidx,
        max_height=(cap_height if cap_height > 0 else None),
        max_streaming_bitrate=target_bitrate,
    )
    t_url = str((selected or {}).get("url") or "").strip()
    t_method = str((selected or {}).get("method") or "").strip()
    out_mid = _first_nonempty_str([str((selected or {}).get("media_source_id") or "").strip(), mid])
    if not t_url:
        t_url = build_item_transcode_url(
            iid,
            server_url=base,
            api_key=tok,
            media_source_id=out_mid,
            audio_stream_index=aidx,
            subtitle_stream_index=sidx,
            max_height=(cap_height if cap_height > 0 else None),
            max_streaming_bitrate=target_bitrate,
        )
        t_method = "fallback_master"
    if t_url:
        return {
            "url": t_url,
            "mode": "transcode",
            "reason": reason if reason != "forced_transcode_mode" else "forced_transcode_mode",
            "media_source_id": out_mid,
            "method": t_method,
        }
    return {"url": src, "mode": "direct", "reason": "transcode_unavailable", "media_source_id": mid}


def first_playable_episode(payload: dict | None) -> dict[str, object]:
    episodes = payload.get("episodes") if isinstance(payload, dict) and isinstance(payload.get("episodes"), list) else []
    for episode in episodes:
        if not isinstance(episode, dict):
            continue
        episode_id = str(episode.get("item_id") or "").strip()
        if not episode_id:
            continue
        episode_type = str(episode.get("type") or "").strip().lower()
        if episode_type in ("", "episode"):
            return episode
    return {}


def resolve_playable_item(item_id: str, *, media_source_id: str = "") -> dict[str, object]:
    iid = str(item_id or "").strip()
    if not iid:
        return {"item_id": "", "detail": {}, "media_source_id": ""}

    try:
        detail = jellyfin_receiver.get_item_detail(iid)
    except Exception:
        detail = {}

    item_type = str(detail.get("type") if isinstance(detail, dict) else "").strip().lower()
    if item_type not in ("series", "season"):
        return {
            "item_id": iid,
            "detail": detail if isinstance(detail, dict) else {},
            "media_source_id": _first_nonempty_str([
                str(media_source_id or "").strip(),
                detail.get("media_source_id") if isinstance(detail, dict) else "",
            ]),
        }

    series_id = ""
    season_id = ""
    season_number = None
    if item_type == "series":
        series_id = iid
    else:
        season_id = iid
        series_id = _first_nonempty_str([
            detail.get("series_id") if isinstance(detail, dict) else "",
            detail.get("SeriesId") if isinstance(detail, dict) else "",
        ])
        try:
            raw_season = detail.get("season_number") if isinstance(detail, dict) else None
            season_number = int(raw_season) if raw_season is not None else None
        except Exception:
            season_number = None

    if not series_id:
        raise HTTPException(status_code=404, detail=f"jellyfin {item_type} is not directly playable")

    episodes_payload = jellyfin_receiver.list_series_episodes(
        series_id,
        season_id=season_id,
        season_number=season_number,
    )
    episode = first_playable_episode(episodes_payload)
    resolved_item_id = str((episode.get("item_id") if isinstance(episode, dict) else "") or "").strip()
    if not resolved_item_id:
        raise HTTPException(status_code=404, detail=f"no playable episode available for jellyfin {item_type}")

    resolved_detail = episode if isinstance(episode, dict) else {}
    if resolved_item_id != iid:
        try:
            fetched = jellyfin_receiver.get_item_detail(resolved_item_id)
        except Exception:
            fetched = {}
        if isinstance(fetched, dict) and fetched:
            resolved_detail = fetched

    return {
        "item_id": resolved_item_id,
        "detail": resolved_detail if isinstance(resolved_detail, dict) else {},
        "media_source_id": _first_nonempty_str([
            resolved_detail.get("media_source_id") if isinstance(resolved_detail, dict) else "",
            episode.get("media_source_id") if isinstance(episode, dict) else "",
            (str(media_source_id or "").strip() if resolved_item_id == iid else ""),
        ]),
    }


def _normalize_lang_pref(raw: str) -> str:
    text = str(raw or "").strip().lower().replace("_", "-")
    if "," in text:
        text = text.split(",", 1)[0].strip()
    return text


def _language_aliases(raw: str) -> set[str]:
    base = _normalize_lang_pref(raw)
    if not base:
        return set()
    aliases: set[str] = {base}
    # Normalize common locale stems.
    if "-" in base:
        aliases.add(base.split("-", 1)[0])
    # Map common ISO-639-2 and alternate spellings to ISO-639-1 stems.
    to_stem = {
        "eng": "en",
        "por": "pt",
        "spa": "es",
        "jpn": "ja",
        "deu": "de",
        "ger": "de",
        "fra": "fr",
        "fre": "fr",
        "ita": "it",
        "rus": "ru",
        "kor": "ko",
        "zho": "zh",
        "chi": "zh",
        "ara": "ar",
        "ces": "cs",
        "cze": "cs",
        "nld": "nl",
        "dut": "nl",
        "pol": "pl",
        "tur": "tr",
        "hun": "hu",
        "dan": "da",
        "fin": "fi",
        "ron": "ro",
        "rum": "ro",
        "swe": "sv",
        "ell": "el",
        "gre": "el",
        "nob": "nb",
    }
    stem = to_stem.get(base, "")
    if stem:
        aliases.add(stem)
    short = base.split("-", 1)[0]
    if short in to_stem:
        aliases.add(to_stem[short])
    # Regional variants used by stream metadata.
    if "pt" in aliases:
        aliases.update({"pt-br", "pt-pt"})
    if "en" in aliases:
        aliases.update({"en-us", "en-gb"})
    if "es" in aliases:
        aliases.update({"es-419", "es-es"})
    return {a for a in aliases if a}


def _language_matches(pref: str, candidate: str) -> bool:
    p_alias = _language_aliases(pref)
    c_alias = _language_aliases(candidate)
    if not p_alias or not c_alias:
        return False
    if p_alias.intersection(c_alias):
        return True
    return False


def preferred_stream_indices(item_id: str) -> tuple[str, str]:
    iid = str(item_id or "").strip()
    if not iid:
        return "", ""
    settings = state.get_settings() if hasattr(state, "get_settings") else {}
    audio_pref = _normalize_lang_pref(str(settings.get("jellyfin_audio_lang") or ""))
    sub_pref = _normalize_lang_pref(str(settings.get("jellyfin_sub_lang") or ""))
    if not audio_pref and not sub_pref:
        return "", ""
    sub_off = sub_pref in {"off", "none", "disabled", "no", "false", "0"}
    try:
        detail = jellyfin_receiver.get_item_detail(iid)
    except Exception:
        detail = {}
    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) else []
    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) else []
    audio_idx = ""
    sub_idx = "-1" if sub_off else ""
    if audio_pref and isinstance(audio_streams, list):
        for stream in audio_streams:
            if not isinstance(stream, dict):
                continue
            if _language_matches(audio_pref, str(stream.get("language") or "")):
                try:
                    audio_idx = str(int(stream.get("index")))
                except Exception:
                    audio_idx = ""
                if audio_idx:
                    break
    if sub_pref and (not sub_off) and isinstance(subtitle_streams, list):
        for stream in subtitle_streams:
            if not isinstance(stream, dict):
                continue
            if _language_matches(sub_pref, str(stream.get("language") or "")):
                try:
                    sub_idx = str(int(stream.get("index")))
                except Exception:
                    sub_idx = ""
                if sub_idx:
                    break
    return audio_idx, sub_idx


def retarget_queue_stream_preferences() -> int:
    """Best-effort: rewrite queued Jellyfin URLs using current language prefs."""
    with state.QUEUE_LOCK:
        snapshot = list(state.QUEUE)
    if not snapshot:
        return 0

    changed = 0
    updated_queue: list[object] = list(snapshot)
    for i, entry in enumerate(snapshot):
        if not isinstance(entry, dict):
            continue
        raw_url = str(entry.get("url") or "").strip()
        if not raw_url:
            continue
        provider = str(entry.get("provider") or "").strip().lower()
        item_id = str(entry.get("jellyfin_item_id") or "").strip()
        if not item_id:
            item_id = extract_item_id_from_url_raw(raw_url)
        if provider != "jellyfin" and not item_id:
            continue
        pref_audio_idx, pref_sub_idx = preferred_stream_indices(item_id) if item_id else ("", "")
        if pref_audio_idx == "" and pref_sub_idx == "":
            continue
        next_url = apply_stream_params(
            raw_url,
            audio_stream_index=pref_audio_idx,
            subtitle_stream_index=pref_sub_idx,
        )
        media_source_id = _first_nonempty_str(
            [
                str(entry.get("jellyfin_media_source_id") or "").strip(),
                extract_media_source_id_from_url(raw_url),
            ]
        )
        next_url = apply_media_source_param(next_url, media_source_id=media_source_id)
        if next_url and next_url != raw_url:
            out_entry = dict(entry)
            out_entry["url"] = next_url
            if item_id:
                out_entry["jellyfin_item_id"] = item_id
            updated_queue[i] = out_entry
            changed += 1

    if changed <= 0:
        return 0

    with state.QUEUE_LOCK:
        state.QUEUE[:] = updated_queue
    try:
        state.persist_queue()
    except Exception:
        pass
    try:
        player.prime_mpv_up_next_from_queue(force=True)
    except Exception:
        pass
    return changed


def _is_generic_playback_title(title: object, url: object) -> bool:
    t = str(title or "").strip()
    if not t:
        return True
    low = t.lower()
    if low in {"stream", "download", "video", "playback", "master", "master.m3u8", "main", "main.m3u8"}:
        return True
    u = str(url or "").strip()
    if u and t == u:
        return True
    return False


def merge_playback_metadata(now: dict, enriched: dict) -> dict:
    out = dict(now)
    provider = str(enriched.get("provider") or "").strip().lower()
    if provider == "jellyfin" and str(out.get("provider") or "").strip().lower() != "jellyfin":
        out["provider"] = "jellyfin"

    if enriched.get("title") and _is_generic_playback_title(out.get("title"), out.get("url")):
        out["title"] = enriched.get("title")

    if enriched.get("channel") and not out.get("channel"):
        out["channel"] = enriched.get("channel")
    if enriched.get("thumbnail") and not out.get("thumbnail"):
        out["thumbnail"] = enriched.get("thumbnail")
    if enriched.get("thumbnail_local") and not out.get("thumbnail_local"):
        out["thumbnail_local"] = enriched.get("thumbnail_local")

    # Keep canonical Jellyfin identifiers from enriched metadata when present.
    if enriched.get("jellyfin_item_id"):
        out["jellyfin_item_id"] = enriched.get("jellyfin_item_id")
    if enriched.get("jellyfin_media_source_id"):
        out["jellyfin_media_source_id"] = enriched.get("jellyfin_media_source_id")
    return out


def enrich_now_stream_metadata(
    now: dict,
    *,
    detail: dict | None = None,
    audio_stream_index: str = "",
    subtitle_stream_index: str = "",
) -> dict:
    out = dict(now or {})
    meta = detail if isinstance(detail, dict) else {}
    audio_streams = meta.get("audio_streams") if isinstance(meta.get("audio_streams"), list) else []
    subtitle_streams = meta.get("subtitle_streams") if isinstance(meta.get("subtitle_streams"), list) else []
    if audio_streams:
        out["audio_streams"] = audio_streams
    if subtitle_streams:
        out["subtitle_streams"] = subtitle_streams

    selected_audio = str(audio_stream_index or out.get("jellyfin_audio_stream_index") or "").strip()
    if not selected_audio:
        selected_audio = extract_audio_stream_index_from_url(str(out.get("url") or ""))
    selected_sub = str(subtitle_stream_index or out.get("jellyfin_subtitle_stream_index") or "").strip()
    if not selected_sub:
        selected_sub = extract_subtitle_stream_index_from_url(str(out.get("url") or ""))

    selected_audio_lang = ""
    if isinstance(audio_streams, list):
        target_idx = None
        try:
            target_idx = int(selected_audio) if selected_audio != "" else None
        except Exception:
            target_idx = None
        if target_idx is not None:
            for row in audio_streams:
                if not isinstance(row, dict):
                    continue
                try:
                    if int(row.get("index")) == target_idx:
                        selected_audio_lang = str(row.get("language") or "").strip()
                        break
                except Exception:
                    continue
        if not selected_audio_lang:
            for row in audio_streams:
                if isinstance(row, dict) and bool(row.get("is_default")) and str(row.get("language") or "").strip():
                    selected_audio_lang = str(row.get("language") or "").strip()
                    break

    selected_sub_lang = "off" if selected_sub == "-1" else ""
    if isinstance(subtitle_streams, list) and selected_sub != "-1":
        target_sub_idx = None
        try:
            target_sub_idx = int(selected_sub) if selected_sub != "" else None
        except Exception:
            target_sub_idx = None
        if target_sub_idx is not None:
            for row in subtitle_streams:
                if not isinstance(row, dict):
                    continue
                try:
                    if int(row.get("index")) == target_sub_idx:
                        selected_sub_lang = str(row.get("language") or "").strip()
                        break
                except Exception:
                    continue
        if not selected_sub_lang:
            for row in subtitle_streams:
                if isinstance(row, dict) and bool(row.get("is_default")) and str(row.get("language") or "").strip():
                    selected_sub_lang = str(row.get("language") or "").strip()
                    break

    if selected_audio != "":
        out["jellyfin_audio_stream_index"] = selected_audio
    if selected_sub != "":
        out["jellyfin_subtitle_stream_index"] = selected_sub

    out["audio_language"] = selected_audio_lang or str(meta.get("audio_language") or out.get("audio_language") or "").strip()
    out["subtitle_language"] = selected_sub_lang or str(meta.get("subtitle_language") or out.get("subtitle_language") or "").strip()
    out["jellyfin_audio_language"] = str(out.get("audio_language") or "").strip()
    out["jellyfin_subtitle_language"] = str(out.get("subtitle_language") or "").strip()
    return out


def try_set_mpv_audio_track(
    *,
    language: str = "",
    display: str = "",
    preferred_stream_index: int | None = None,
) -> bool:
    target_lang = _normalize_lang_pref(str(language or ""))
    target_display = str(display or "").strip().lower()
    target_display_tokens = [tok for tok in re.split(r"[^a-z0-9]+", target_display) if len(tok) >= 3]
    try:
        track_list = player.mpv_get("track-list")
    except Exception:
        track_list = None
    if not isinstance(track_list, list):
        return False

    candidates: list[tuple[int, int]] = []
    for idx, row in enumerate(track_list):
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").strip().lower() != "audio":
            continue
        try:
            tid = int(row.get("id"))
        except Exception:
            continue
        if tid <= 0:
            continue
        src_id = None
        ff_index = None
        try:
            src_id = int(row.get("src-id"))
        except Exception:
            src_id = None
        try:
            ff_index = int(row.get("ff-index"))
        except Exception:
            ff_index = None
        lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
        title = str(row.get("title") or row.get("name") or "").strip().lower()
        score = 0
        if preferred_stream_index is not None:
            if ff_index is not None and ff_index == preferred_stream_index:
                score += 20
            if src_id is not None and (src_id - 1) == preferred_stream_index:
                score += 18
        if target_lang and _language_matches(target_lang, lang):
            score += 6
        if target_display and title:
            if target_display in title or title in target_display:
                score += 4
            elif target_display_tokens:
                token_hits = sum(1 for tok in target_display_tokens if tok in title)
                score += min(3, token_hits)
        if score <= 0:
            continue
        # Stable tie-break by track order.
        candidates.append((score * 1000 - idx, tid))
    if not candidates:
        return False
    candidates.sort(reverse=True)
    selected_tid = int(candidates[0][1])

    try:
        player.mpv_set("aid", selected_tid)
    except Exception:
        return False

    # Confirm selection if possible.
    try:
        updated = player.mpv_get("track-list")
    except Exception:
        updated = None
    if isinstance(updated, list):
        for row in updated:
            if not isinstance(row, dict):
                continue
            if str(row.get("type") or "").strip().lower() != "audio":
                continue
            if not bool(row.get("selected")):
                continue
            src_id = None
            ff_index = None
            try:
                src_id = int(row.get("src-id"))
            except Exception:
                src_id = None
            try:
                ff_index = int(row.get("ff-index"))
            except Exception:
                ff_index = None
            if preferred_stream_index is not None:
                if ff_index is not None and ff_index == preferred_stream_index:
                    return True
                if src_id is not None and (src_id - 1) == preferred_stream_index:
                    return True
            lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
            title = str(row.get("title") or row.get("name") or "").strip().lower()
            if target_lang and _language_matches(target_lang, lang):
                return True
            if target_display and target_display in title:
                return True
    return False


def try_set_mpv_subtitle_track(
    *,
    language: str = "",
    display: str = "",
    preferred_stream_index: int | None = None,
    off: bool = False,
) -> bool:
    if off:
        try:
            player.mpv_set("sid", "no")
            try:
                player.mpv_set("sub-visibility", False)
            except Exception:
                pass
            return True
        except Exception:
            return False

    target_lang = _normalize_lang_pref(str(language or ""))
    target_display = str(display or "").strip().lower()
    target_display_tokens = [tok for tok in re.split(r"[^a-z0-9]+", target_display) if len(tok) >= 3]
    try:
        track_list = player.mpv_get("track-list")
    except Exception:
        track_list = None
    if not isinstance(track_list, list):
        return False

    candidates: list[tuple[int, int]] = []
    for idx, row in enumerate(track_list):
        if not isinstance(row, dict):
            continue
        if not track_type_is_subtitle(row.get("type")):
            continue
        try:
            tid = int(row.get("id"))
        except Exception:
            continue
        if tid <= 0:
            continue
        src_id = None
        ff_index = None
        try:
            src_id = int(row.get("src-id"))
        except Exception:
            src_id = None
        try:
            ff_index = int(row.get("ff-index"))
        except Exception:
            ff_index = None
        lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
        title = str(row.get("title") or row.get("name") or "").strip().lower()
        score = 0
        if preferred_stream_index is not None:
            if ff_index is not None and ff_index == preferred_stream_index:
                score += 20
            if src_id is not None and (src_id - 1) == preferred_stream_index:
                score += 18
        if target_lang and _language_matches(target_lang, lang):
            score += 6
        if target_display and title:
            if target_display in title or title in target_display:
                score += 4
            elif target_display_tokens:
                token_hits = sum(1 for tok in target_display_tokens if tok in title)
                score += min(3, token_hits)
        if score <= 0:
            continue
        candidates.append((score * 1000 - idx, tid))
    if not candidates:
        return False
    candidates.sort(reverse=True)
    selected_tid = int(candidates[0][1])

    try:
        player.mpv_set("sid", selected_tid)
        try:
            player.mpv_set("sub-visibility", True)
        except Exception:
            pass
    except Exception:
        return False

    try:
        updated = player.mpv_get("track-list")
    except Exception:
        updated = None
    if isinstance(updated, list):
        for row in updated:
            if not isinstance(row, dict):
                continue
            if not track_type_is_subtitle(row.get("type")):
                continue
            if not bool(row.get("selected")):
                continue
            src_id = None
            ff_index = None
            try:
                src_id = int(row.get("src-id"))
            except Exception:
                src_id = None
            try:
                ff_index = int(row.get("ff-index"))
            except Exception:
                ff_index = None
            if preferred_stream_index is not None:
                if ff_index is not None and ff_index == preferred_stream_index:
                    return True
                if src_id is not None and (src_id - 1) == preferred_stream_index:
                    return True
            lang = _normalize_lang_pref(str(row.get("lang") or row.get("language") or ""))
            title = str(row.get("title") or row.get("name") or "").strip().lower()
            if target_lang and _language_matches(target_lang, lang):
                return True
            if target_display and target_display in title:
                return True
    return False


def runtime_selected_audio_stream(audio_streams: list[dict[str, object]]) -> tuple[int | None, str]:
    """Resolve selected Jellyfin audio stream index from mpv runtime track data."""
    try:
        track_list = player.mpv_get("track-list")
    except Exception:
        track_list = None
    if not isinstance(track_list, list):
        return None, ""

    selected: dict[str, object] | None = None
    for row in track_list:
        if not isinstance(row, dict):
            continue
        if str(row.get("type") or "").strip().lower() != "audio":
            continue
        if bool(row.get("selected")):
            selected = row
            break
    if not isinstance(selected, dict):
        return None, ""

    selected_lang = _normalize_lang_pref(str(selected.get("lang") or selected.get("language") or ""))
    selected_title = str(selected.get("title") or selected.get("name") or "").strip().lower()
    selected_title_tokens = [tok for tok in re.split(r"[^a-z0-9]+", selected_title) if len(tok) >= 3]

    try:
        ff_index = int(selected.get("ff-index"))
    except Exception:
        ff_index = None
    try:
        src_id = int(selected.get("src-id"))
    except Exception:
        src_id = None

    if ff_index is not None:
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == ff_index:
                    return ff_index, selected_lang
            except Exception:
                continue
    if src_id is not None:
        candidate = int(src_id) - 1
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == candidate:
                    return candidate, selected_lang
            except Exception:
                continue

    best_idx: int | None = None
    best_score = 0
    for row in audio_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except Exception:
            continue
        score = 0
        row_lang = _normalize_lang_pref(str(row.get("language") or ""))
        if selected_lang and _language_matches(selected_lang, row_lang):
            score += 3
        row_display = str(row.get("display") or "").strip().lower()
        if selected_title and row_display:
            if selected_title in row_display or row_display in selected_title:
                score += 2
            elif selected_title_tokens:
                token_hits = sum(1 for tok in selected_title_tokens if tok in row_display)
                score += min(2, token_hits)
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx, selected_lang


def runtime_selected_subtitle_stream(subtitle_streams: list[dict[str, object]]) -> tuple[int | None, str, bool]:
    """Resolve selected Jellyfin subtitle stream index from mpv runtime track data."""
    try:
        props = player.mpv_get_many(["track-list", "sid", "sub-visibility"])
    except Exception:
        props = {}
    track_list = props.get("track-list") if isinstance(props, dict) else None
    sid_raw = props.get("sid") if isinstance(props, dict) else None
    sub_visible = props.get("sub-visibility") if isinstance(props, dict) else None
    sid_text = str(sid_raw or "").strip().lower()
    sid_off = sid_text in {"no", "0", "-1", "false"}
    if sub_visible is False or sid_off:
        return None, "off", True
    if not isinstance(track_list, list):
        return None, "", False

    selected: dict[str, object] | None = None
    for row in track_list:
        if not isinstance(row, dict):
            continue
        if not track_type_is_subtitle(row.get("type")):
            continue
        if bool(row.get("selected")):
            selected = row
            break
    if not isinstance(selected, dict):
        return None, "", False

    selected_lang = _normalize_lang_pref(str(selected.get("lang") or selected.get("language") or ""))
    selected_title = str(selected.get("title") or selected.get("name") or "").strip().lower()
    selected_title_tokens = [tok for tok in re.split(r"[^a-z0-9]+", selected_title) if len(tok) >= 3]

    try:
        ff_index = int(selected.get("ff-index"))
    except Exception:
        ff_index = None
    try:
        src_id = int(selected.get("src-id"))
    except Exception:
        src_id = None

    if ff_index is not None:
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == ff_index:
                    return ff_index, selected_lang, False
            except Exception:
                continue
    if src_id is not None:
        candidate = int(src_id) - 1
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == candidate:
                    return candidate, selected_lang, False
            except Exception:
                continue

    best_idx: int | None = None
    best_score = 0
    for row in subtitle_streams:
        if not isinstance(row, dict):
            continue
        try:
            idx = int(row.get("index"))
        except Exception:
            continue
        score = 0
        row_lang = _normalize_lang_pref(str(row.get("language") or ""))
        if selected_lang and _language_matches(selected_lang, row_lang):
            score += 3
        row_display = str(row.get("display") or "").strip().lower()
        if selected_title and row_display:
            if selected_title in row_display or row_display in selected_title:
                score += 2
            elif selected_title_tokens:
                token_hits = sum(1 for tok in selected_title_tokens if tok in row_display)
                score += min(2, token_hits)
        if score > best_score:
            best_score = score
            best_idx = idx
    return best_idx, selected_lang, False
    return None, selected_lang


def emit_progress_hint() -> None:
    """Trigger an immediate best-effort progress push without blocking request paths."""
    def _run() -> None:
        try:
            jellyfin_receiver.send_progress_once()
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="relaytv-jellyfin-progress-hint").start()
    except Exception:
        pass


def emit_playback_start_hint() -> None:
    """Announce playback start, then the first progress tick, off the request path.

    Ordering matters: Jellyfin builds the session's now-playing item from the
    start report, so a progress post that beats it describes an item the server
    does not think is playing yet.
    """
    def _run() -> None:
        try:
            payload = progress_snapshot()
        except Exception:
            payload = None
        if isinstance(payload, dict) and payload:
            try:
                jellyfin_receiver.send_playback_start_once(payload)
            except Exception:
                pass
        try:
            jellyfin_receiver.send_progress_once()
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="relaytv-jellyfin-start-hint").start()
    except Exception:
        pass


def _require_current_jellyfin_item() -> tuple[dict, str]:
    """Return (now_playing, item_id) for the active Jellyfin item or raise 409/502."""
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not isinstance(now, dict) or not now:
        raise HTTPException(status_code=409, detail="no active now_playing item")
    provider = str(now.get("provider") or "").strip().lower()
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        item_id = extract_item_id_from_url_raw(str(now.get("url") or ""))
    if provider != "jellyfin" and not item_id:
        raise HTTPException(status_code=409, detail="now_playing is not a jellyfin item")
    if not item_id:
        raise HTTPException(status_code=409, detail="missing jellyfin item_id for current playback")
    return now, item_id


def _capture_switch_position(now: dict) -> tuple[float | None, bool, str | None]:
    """Return (start_pos, was_paused, pause_reason) for an in-place restart."""
    was_playing = bool(player.is_playing())
    try:
        props = player.mpv_get_many(["time-pos", "pause"]) if was_playing else {}
    except Exception:
        props = {}
    start_pos: float | None = None
    if isinstance(props, dict):
        try:
            raw_pos = props.get("time-pos")
            if raw_pos is not None:
                start_pos = float(raw_pos)
        except Exception:
            start_pos = None
    if start_pos is None:
        try:
            raw_resume = now.get("resume_pos")
            if raw_resume is not None:
                start_pos = float(raw_resume)
        except Exception:
            start_pos = None
    was_paused = bool((props or {}).get("pause")) or str(getattr(state, "SESSION_STATE", "") or "").strip().lower() == "paused"
    pause_reason = state.get_pause_reason() if hasattr(state, "get_pause_reason") else None
    return start_pos, was_paused, pause_reason


def _restart_with_stream_params(
    *,
    now: dict,
    detail: dict,
    item_id: str,
    server_url: str,
    media_source_id: str,
    audio_stream_index: str,
    subtitle_stream_index: str,
    start_pos: float | None,
    mode: str,
) -> dict:
    """Restart current playback with rewritten stream params; returns now_playing."""
    try:
        settings_snapshot = state.get_settings()
    except Exception:
        settings_snapshot = {}
    auth_token = access_token()

    source_url = build_item_stream_url(
        item_id,
        server_url=server_url,
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    selected_stream = select_playback_url(
        item_id=item_id,
        source_url=source_url,
        server_url=server_url,
        api_key=auth_token,
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
    )
    source_url = normalize_source_url(
        str(selected_stream.get("url") or source_url),
        server_url=server_url,
        api_key=auth_token,
    )
    source_url = apply_stream_params(
        source_url,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    media_source_id = _first_nonempty_str(
        [
            str(selected_stream.get("media_source_id") or "").strip(),
            extract_media_source_id_from_url(source_url),
            media_source_id,
        ]
    )
    if not source_url:
        raise HTTPException(status_code=502, detail="unable to build jellyfin stream url")

    play_payload = {
        "url": source_url,
        "provider": "jellyfin",
        "title": str(now.get("title") or "").strip() or f"Jellyfin item {item_id}",
        **({"channel": now.get("channel")} if now.get("channel") else {}),
        **({"thumbnail": now.get("thumbnail")} if now.get("thumbnail") else {}),
        **({"thumbnail_local": now.get("thumbnail_local")} if now.get("thumbnail_local") else {}),
        "jellyfin_item_id": item_id,
        **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
    }

    playback_service.suppress_auto_next(2.0)
    switched = playback_service.play_now(
        play_payload,
        use_resolver=False,
        cec=False,
        clear_queue=False,
        mode=mode,
        start_pos=start_pos,
    )
    now_out = switched if isinstance(switched, dict) else dict(play_payload)
    now_out["jellyfin_item_id"] = item_id
    if media_source_id:
        now_out["jellyfin_media_source_id"] = media_source_id
    now_out["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
    now_out["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
    now_out = enrich_now_stream_metadata(
        now_out,
        detail=detail,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
    )
    return now_out


def _finish_switch(now_out: dict, *, was_paused: bool, pause_reason: str | None) -> None:
    playback_service.update_now_playing(now_out)
    if was_paused:
        try:
            player.mpv_set("pause", True)
        except Exception:
            pass
        playback_service.mark_paused(True, reason=pause_reason)
    emit_progress_hint()


def switch_audio_track(requested_index: object, *, server_status: dict) -> dict:
    """Switch the audio track of the current Jellyfin item.

    Prefers an in-place mpv track change; falls back to restarting playback
    with a rewritten stream URL at the captured position. Raises HTTPException
    with the same statuses the route historically returned.
    """
    now, item_id = _require_current_jellyfin_item()

    try:
        detail = jellyfin_receiver.get_item_detail(item_id)
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")

    audio_streams = detail.get("audio_streams") if isinstance(detail, dict) and isinstance(detail.get("audio_streams"), list) else []
    try:
        requested_idx = int(requested_index)
    except Exception:
        raise HTTPException(status_code=400, detail="audio index must be an integer")
    if requested_idx < 0:
        raise HTTPException(status_code=400, detail="audio index must be non-negative")
    requested_audio_language = ""
    requested_audio_display = ""
    if audio_streams:
        valid = False
        for row in audio_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == requested_idx:
                    valid = True
                    requested_audio_language = str(row.get("language") or "").strip()
                    requested_audio_display = str(row.get("display") or "").strip()
                    break
            except Exception:
                continue
        if not valid:
            raise HTTPException(status_code=400, detail="requested audio stream index is unavailable")
    preferred_audio_lang = _normalize_lang_pref(requested_audio_language)
    queue_retargeted = 0
    if preferred_audio_lang:
        try:
            state.update_settings({"jellyfin_audio_lang": preferred_audio_lang})
        except Exception:
            pass
        try:
            runtime_config.set_value("RELAYTV_JELLYFIN_AUDIO_LANG", preferred_audio_lang)
        except Exception:
            pass
        try:
            queue_retargeted = int(retarget_queue_stream_preferences())
        except Exception:
            queue_retargeted = 0

    start_pos, was_paused, pause_reason = _capture_switch_position(now)

    media_source_id = _first_nonempty_str(
        [
            str(now.get("jellyfin_media_source_id") or "").strip(),
            extract_media_source_id_from_url(str(now.get("url") or "")),
            str(detail.get("media_source_id") if isinstance(detail, dict) else "").strip(),
        ]
    )
    subtitle_stream_index = _first_nonempty_str(
        [
            str(now.get("jellyfin_subtitle_stream_index") or "").strip(),
            extract_subtitle_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    audio_stream_index = str(requested_idx)

    if try_set_mpv_audio_track(language=requested_audio_language, display=requested_audio_display):
        now_out = enrich_now_stream_metadata(
            dict(now),
            detail=detail if isinstance(detail, dict) else {},
            audio_stream_index=audio_stream_index,
            subtitle_stream_index=subtitle_stream_index,
        )
        playback_service.update_now_playing(now_out)
        emit_progress_hint()
        return {
            "ok": True,
            "method": "mpv_runtime_aid",
            "item_id": item_id,
            "current_audio_stream_index": requested_idx,
            "current_audio_language": str(now_out.get("jellyfin_audio_language") or now_out.get("audio_language") or "").strip(),
            "queued_items_retargeted": queue_retargeted,
            "now_playing": now_out,
        }

    now_out = _restart_with_stream_params(
        now=now,
        detail=detail if isinstance(detail, dict) else {},
        item_id=item_id,
        server_url=str(server_status.get("server_url") or ""),
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        start_pos=start_pos,
        mode="jellyfin_audio_switch",
    )
    try_set_mpv_audio_track(language=requested_audio_language, display=requested_audio_display)
    _finish_switch(now_out, was_paused=was_paused, pause_reason=pause_reason)
    return {
        "ok": True,
        "item_id": item_id,
        "current_audio_stream_index": requested_idx,
        "current_audio_language": str(now_out.get("jellyfin_audio_language") or now_out.get("audio_language") or "").strip(),
        "queued_items_retargeted": queue_retargeted,
        "now_playing": now_out,
    }


def switch_subtitle_track(requested_index: object, *, server_status: dict) -> dict:
    """Switch (or turn off) the subtitle track of the current Jellyfin item.

    Same in-place-first strategy and HTTP error statuses as
    ``switch_audio_track``; index -1 turns subtitles off.
    """
    now, item_id = _require_current_jellyfin_item()

    try:
        detail = jellyfin_receiver.get_item_detail(item_id)
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=502, detail="failed to fetch jellyfin item detail")

    subtitle_streams = detail.get("subtitle_streams") if isinstance(detail, dict) and isinstance(detail.get("subtitle_streams"), list) else []
    try:
        requested_idx = int(requested_index)
    except Exception:
        raise HTTPException(status_code=400, detail="subtitle index must be an integer")
    if requested_idx < -1:
        raise HTTPException(status_code=400, detail="subtitle index must be -1 or non-negative")
    requested_subtitle_language = ""
    requested_subtitle_display = ""
    if requested_idx >= 0 and subtitle_streams:
        valid = False
        for row in subtitle_streams:
            if not isinstance(row, dict):
                continue
            try:
                if int(row.get("index")) == requested_idx:
                    valid = True
                    requested_subtitle_language = str(row.get("language") or "").strip()
                    requested_subtitle_display = str(row.get("display") or "").strip()
                    break
            except Exception:
                continue
        if not valid:
            raise HTTPException(status_code=400, detail="requested subtitle stream index is unavailable")
    preferred_subtitle_lang = "off" if requested_idx < 0 else _normalize_lang_pref(requested_subtitle_language)
    queue_retargeted = 0
    try:
        state.update_settings({"jellyfin_sub_lang": preferred_subtitle_lang})
    except Exception:
        pass
    try:
        runtime_config.set_value("RELAYTV_JELLYFIN_SUB_LANG", preferred_subtitle_lang)
    except Exception:
        pass
    try:
        queue_retargeted = int(retarget_queue_stream_preferences())
    except Exception:
        queue_retargeted = 0

    start_pos, was_paused, pause_reason = _capture_switch_position(now)

    media_source_id = _first_nonempty_str(
        [
            str(now.get("jellyfin_media_source_id") or "").strip(),
            extract_media_source_id_from_url(str(now.get("url") or "")),
            str(detail.get("media_source_id") if isinstance(detail, dict) else "").strip(),
        ]
    )
    audio_stream_index = _first_nonempty_str(
        [
            str(now.get("jellyfin_audio_stream_index") or "").strip(),
            extract_audio_stream_index_from_url(str(now.get("url") or "")),
        ]
    )
    subtitle_stream_index = "-1" if requested_idx < 0 else str(requested_idx)

    if try_set_mpv_subtitle_track(
        language=requested_subtitle_language,
        display=requested_subtitle_display,
        preferred_stream_index=(requested_idx if requested_idx >= 0 else None),
        off=(requested_idx < 0),
    ):
        now_out = enrich_now_stream_metadata(
            dict(now),
            detail=detail if isinstance(detail, dict) else {},
            audio_stream_index=audio_stream_index,
            subtitle_stream_index=subtitle_stream_index,
        )
        playback_service.update_now_playing(now_out)
        emit_progress_hint()
        return {
            "ok": True,
            "method": "mpv_runtime_sid",
            "item_id": item_id,
            "current_subtitle_stream_index": requested_idx,
            "current_subtitle_language": str(now_out.get("jellyfin_subtitle_language") or now_out.get("subtitle_language") or "").strip(),
            "current_subtitle_off": requested_idx < 0,
            "queued_items_retargeted": queue_retargeted,
            "now_playing": now_out,
        }

    now_out = _restart_with_stream_params(
        now=now,
        detail=detail if isinstance(detail, dict) else {},
        item_id=item_id,
        server_url=str(server_status.get("server_url") or ""),
        media_source_id=media_source_id,
        audio_stream_index=audio_stream_index,
        subtitle_stream_index=subtitle_stream_index,
        start_pos=start_pos,
        mode="jellyfin_subtitle_switch",
    )
    try_set_mpv_subtitle_track(
        language=requested_subtitle_language,
        display=requested_subtitle_display,
        preferred_stream_index=(requested_idx if requested_idx >= 0 else None),
        off=(requested_idx < 0),
    )
    _finish_switch(now_out, was_paused=was_paused, pause_reason=pause_reason)
    return {
        "ok": True,
        "item_id": item_id,
        "current_subtitle_stream_index": requested_idx,
        "current_subtitle_language": str(now_out.get("jellyfin_subtitle_language") or now_out.get("subtitle_language") or "").strip(),
        "current_subtitle_off": requested_idx < 0,
        "queued_items_retargeted": queue_retargeted,
        "now_playing": now_out,
    }


def complete_ratio() -> float:
    try:
        ratio = float(os.getenv("RELAYTV_JELLYFIN_COMPLETE_RATIO", "0.98"))
    except Exception:
        ratio = 0.98
    return min(0.999, max(0.0, ratio))


def complete_remaining_sec() -> float:
    try:
        sec = float(os.getenv("RELAYTV_JELLYFIN_COMPLETE_REMAINING_SEC", "0"))
    except Exception:
        sec = 0.0
    return max(0.0, sec)


def snap_position_ticks(pos_ticks: int, run_ticks: int | None = None) -> int:
    pos = max(0, int(pos_ticks or 0))
    try:
        run = int(run_ticks) if run_ticks is not None else None
    except Exception:
        run = None
    if run is None or run <= 0:
        return pos
    if pos >= run:
        return run
    if pos >= int(run * complete_ratio()):
        return run
    remain_sec = complete_remaining_sec()
    if remain_sec > 0.0:
        remain_ticks = int(remain_sec * 10_000_000)
        if (run - pos) <= max(0, remain_ticks):
            return run
    return pos


def played_percentage(pos_ticks: int, run_ticks: int | None = None) -> float | None:
    try:
        run = int(run_ticks) if run_ticks is not None else None
    except Exception:
        run = None
    if run is None or run <= 0:
        return None
    try:
        pct = (float(max(0, int(pos_ticks or 0))) / float(run)) * 100.0
    except Exception:
        return None
    if pct < 0.0:
        pct = 0.0
    if pct > 100.0:
        pct = 100.0
    return round(pct, 3)


def stopped_snapshot_from_now(
    now: dict | None,
    position_sec: float | None = None,
    duration_sec: float | None = None,
) -> dict | None:
    if not isinstance(now, dict):
        return None
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        return None
    pos_f = None
    if position_sec is not None:
        try:
            pos_f = float(position_sec)
        except Exception:
            pos_f = None
    if pos_f is None:
        try:
            rp = now.get("resume_pos")
            pos_f = float(rp) if rp is not None else None
        except Exception:
            pos_f = None
    if pos_f is None:
        try:
            pos_f = float(getattr(state, "SESSION_POSITION", 0.0) or 0.0)
        except Exception:
            pos_f = 0.0
    dur_f = None
    if duration_sec is not None:
        try:
            dur_f = float(duration_sec)
        except Exception:
            dur_f = None
    if dur_f is None:
        try:
            d = now.get("duration")
            dur_f = float(d) if d is not None else None
        except Exception:
            dur_f = None
    payload = {
        "ItemId": item_id,
        "IsPaused": False,
    }
    pos_ticks = max(0, int((pos_f or 0.0) * 10_000_000))
    if dur_f is not None and dur_f >= 0:
        run_ticks = int(dur_f * 10_000_000)
        payload["RunTimeTicks"] = run_ticks
        pos_ticks = snap_position_ticks(pos_ticks, run_ticks)
        played_pct = played_percentage(pos_ticks, run_ticks)
        if played_pct is not None:
            payload["PlayedPercentage"] = played_pct
    payload["PositionTicks"] = pos_ticks
    play_session_id = str(now.get("jellyfin_play_session_id") or "").strip()
    if play_session_id:
        payload["PlaySessionId"] = play_session_id
    media_source_id = _first_nonempty_str(
        [
            now.get("jellyfin_media_source_id"),
            extract_media_source_id_from_url(str(now.get("url") or "")),
        ]
    )
    if media_source_id:
        payload["MediaSourceId"] = media_source_id
    return payload


def stopped_snapshot(position_sec: float | None = None, duration_sec: float | None = None) -> dict | None:
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    return stopped_snapshot_from_now(now, position_sec, duration_sec)


def emit_stopped_payload(payload: dict | None) -> None:
    if not isinstance(payload, dict) or not payload:
        return

    def _run() -> None:
        try:
            jellyfin_receiver.send_progress_payload_once(payload)
        except Exception:
            pass
        try:
            jellyfin_receiver.send_playback_stopped_once(payload)
        except Exception:
            pass

    try:
        threading.Thread(target=_run, daemon=True, name="relaytv-jellyfin-stopped-hint").start()
    except Exception:
        pass


def emit_stopped_hint(position_sec: float | None = None, duration_sec: float | None = None) -> None:
    try:
        player.remember_recent_jellyfin_stop(state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None)
    except Exception:
        pass
    emit_stopped_payload(stopped_snapshot(position_sec, duration_sec))


def play_method(now: dict | None) -> str:
    """Report how the stream is being served, in Jellyfin's PlayMethod terms.

    RelayTV always pulls a server-produced stream URL rather than the original
    file, so a direct stream is ``DirectStream``, never ``DirectPlay``.
    """
    mode = str((now or {}).get("jellyfin_stream_mode") or "").strip().lower()
    return "Transcode" if mode == "transcode" else "DirectStream"


def progress_snapshot() -> dict | None:
    now = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
    if not now:
        return None
    item_id = str(now.get("jellyfin_item_id") or "").strip()
    if not item_id:
        return None
    is_playing = bool(player.is_playing())
    props = player.mpv_get_many(["pause", "time-pos", "duration", "mute", "volume"]) if is_playing else {}

    pos = props.get("time-pos") if isinstance(props, dict) else None
    dur = props.get("duration") if isinstance(props, dict) else None
    muted = props.get("mute") if isinstance(props, dict) else None
    volume = props.get("volume") if isinstance(props, dict) else None

    try:
        if pos is not None:
            pos_f = float(pos)
        elif now.get("resume_pos") is not None:
            pos_f = float(now.get("resume_pos"))
        else:
            pos_f = float(state.SESSION_POSITION or 0.0)
    except Exception:
        pos_f = 0.0
    try:
        if dur is not None:
            dur_f = float(dur)
        elif now.get("duration") is not None:
            dur_f = float(now.get("duration"))
        else:
            dur_f = None
    except Exception:
        dur_f = None

    pos_ticks = max(0, int(pos_f * 10_000_000))
    payload = {
        "ItemId": item_id,
        "IsPaused": bool(props.get("pause")) if is_playing and isinstance(props, dict) else (not is_playing),
        # Without CanSeek the remote renders a read-only progress bar: the
        # scrubber cannot be dragged and Jellyfin will not send Seek at all.
        "CanSeek": True,
        "PlayMethod": play_method(now),
    }
    play_session_id = str(now.get("jellyfin_play_session_id") or "").strip()
    if play_session_id:
        payload["PlaySessionId"] = play_session_id
    media_source_id = _first_nonempty_str(
        [
            now.get("jellyfin_media_source_id"),
            extract_media_source_id_from_url(str(now.get("url") or "")),
        ]
    )
    if media_source_id:
        payload["MediaSourceId"] = media_source_id
    if dur_f is not None and dur_f >= 0:
        run_ticks = int(dur_f * 10_000_000)
        payload["RunTimeTicks"] = run_ticks
        pos_ticks = snap_position_ticks(pos_ticks, run_ticks)
        played_pct = played_percentage(pos_ticks, run_ticks)
        if played_pct is not None:
            payload["PlayedPercentage"] = played_pct
    payload["PositionTicks"] = pos_ticks
    if muted is not None:
        payload["IsMuted"] = bool(muted)
    if volume is not None:
        try:
            payload["VolumeLevel"] = int(float(volume))
        except Exception:
            pass
    return payload


# Command ingress dedupe state (play debounce, command-id dedupe, UI action
# dedupe). Reset on connect/disconnect via reset_command_state().
_JELLYFIN_PLAY_DEBOUNCE_LOCK = threading.Lock()
_JELLYFIN_LAST_PLAY: dict[str, object] = {"ts": 0.0, "url": "", "item_id": "", "start_pos": None}
_JELLYFIN_COMMAND_DEDUPE_LOCK = threading.Lock()
_JELLYFIN_RECENT_COMMAND_IDS: dict[str, float] = {}
_JELLYFIN_UI_ACTION_DEDUPE_LOCK = threading.Lock()
_JELLYFIN_LAST_UI_ACTION: dict[str, object] = {"ts": 0.0, "command": "", "item_id": "", "resume_pos": None}


def _extract_api_key_from_url(url: str) -> str:
    try:
        q = dict(parse_qsl(urlsplit(str(url or "").strip()).query, keep_blank_values=True))
        return str(q.get("api_key") or q.get("ApiKey") or "").strip()
    except Exception:
        return ""


def smart_item_from_url(url: str, *, start_pos: float | None = None, lightweight: bool = False) -> dict:
    """
    Build a playback item for smart/jellyfin paths.
    If the URL looks like Jellyfin media, enrich title/thumbnail/resume from Jellyfin APIs.
    """
    shared = str(url or "").strip()
    item_id = jellyfin_receiver.extract_item_id_from_url(shared)
    st = jellyfin_receiver.status()
    if item_id and (st.get("server_url") or looks_like_media_url(shared)):
        origin = url_origin(shared)
        server_url = origin or str(st.get("server_url") or "")
        link_api_key = _extract_api_key_from_url(shared)
        token = link_api_key or access_token()
        pref_audio_idx, pref_sub_idx = preferred_stream_indices(item_id)
        normalized_url = normalize_source_url(shared, server_url=server_url, api_key=token)
        normalized_url = apply_stream_params(
            normalized_url,
            audio_stream_index=pref_audio_idx,
            subtitle_stream_index=pref_sub_idx,
        )
        # Share links commonly use /Items/<id>/Download - convert to stream endpoint for consistency.
        low_path = (urlsplit(shared).path or "").lower()
        if "/items/" in low_path and "/download" in low_path:
            normalized_url = build_item_stream_url(
                item_id,
                server_url=server_url,
                api_key=token,
                audio_stream_index=pref_audio_idx,
                subtitle_stream_index=pref_sub_idx,
            )
        media_source_id = extract_media_source_id_from_url(normalized_url)
        try:
            settings_snapshot = state.get_settings()
        except Exception:
            settings_snapshot = {}
        selected = select_playback_url(
            item_id=item_id,
            source_url=normalized_url,
            server_url=server_url,
            api_key=token,
            media_source_id=media_source_id,
            audio_stream_index=pref_audio_idx,
            subtitle_stream_index=pref_sub_idx,
            settings=settings_snapshot,
        )
        normalized_url = normalize_source_url(
            str(selected.get("url") or normalized_url),
            server_url=server_url,
            api_key=token,
        )
        media_source_id = _first_nonempty_str(
            [
                str(selected.get("media_source_id") or "").strip(),
                extract_media_source_id_from_url(normalized_url),
                media_source_id,
            ]
        )
        item: dict[str, object] = {
            "url": normalized_url,
            "provider": "jellyfin",
            "jellyfin_item_id": item_id,
            **({"jellyfin_media_source_id": media_source_id} if media_source_id else {}),
            "jellyfin_stream_mode": str(selected.get("mode") or "direct"),
            "jellyfin_stream_reason": str(selected.get("reason") or ""),
        }
        meta = jellyfin_receiver.get_item_metadata(item_id, token_override=token, server_url_override=server_url)
        if isinstance(meta, dict):
            title = str(meta.get("title") or "").strip()
            channel = str(meta.get("channel") or "").strip()
            thumb = str(meta.get("thumbnail") or "").strip()
            if title:
                item["title"] = title
            if channel:
                item["channel"] = channel
            if thumb:
                item["thumbnail"] = thumb
            if start_pos is None:
                try:
                    rp = meta.get("resume_pos")
                    if rp is not None:
                        item["resume_pos"] = float(rp)
                except Exception:
                    pass
        if start_pos is not None:
            try:
                item["resume_pos"] = float(start_pos)
            except Exception:
                pass
        return attach_local_thumbnail(item)
    # Non-Jellyfin path: existing resolver behavior.
    try:
        return resolver.make_item(shared, lightweight=lightweight)
    except TypeError:
        # Compatibility for tests/patches that mock make_item(url) without kwargs.
        return resolver.make_item(shared)


def reset_command_state() -> None:
    with _JELLYFIN_PLAY_DEBOUNCE_LOCK:
        _JELLYFIN_LAST_PLAY.update({"ts": 0.0, "url": "", "item_id": "", "start_pos": None})
    with _JELLYFIN_COMMAND_DEDUPE_LOCK:
        _JELLYFIN_RECENT_COMMAND_IDS.clear()
    with _JELLYFIN_UI_ACTION_DEDUPE_LOCK:
        _JELLYFIN_LAST_UI_ACTION.update({"ts": 0.0, "command": "", "item_id": "", "resume_pos": None})


def is_duplicate_command(command_id: str) -> bool:
    cid = str(command_id or "").strip()
    if not cid:
        return False
    ttl = max(1.0, float(os.getenv("RELAYTV_JELLYFIN_COMMAND_ID_TTL_SEC", "30")))
    now_ts = time.time()
    with _JELLYFIN_COMMAND_DEDUPE_LOCK:
        # prune expired ids
        expired = [k for k, ts in _JELLYFIN_RECENT_COMMAND_IDS.items() if (now_ts - ts) > ttl]
        for k in expired:
            _JELLYFIN_RECENT_COMMAND_IDS.pop(k, None)
        if cid in _JELLYFIN_RECENT_COMMAND_IDS:
            _JELLYFIN_RECENT_COMMAND_IDS[cid] = now_ts
            return True
        _JELLYFIN_RECENT_COMMAND_IDS[cid] = now_ts
        return False


def should_suppress_duplicate_play(url: str, item_id: str, start_pos: float | None) -> bool:
    window_sec = max(0.0, float(os.getenv("RELAYTV_JELLYFIN_PLAY_DEBOUNCE_SEC", "1.5")))
    if window_sec <= 0:
        return False
    now_ts = time.time()
    with _JELLYFIN_PLAY_DEBOUNCE_LOCK:
        last_ts = float(_JELLYFIN_LAST_PLAY.get("ts") or 0.0)
        if now_ts - last_ts > window_sec:
            _JELLYFIN_LAST_PLAY.update({"ts": now_ts, "url": url, "item_id": item_id, "start_pos": start_pos})
            return False
        same_url = str(_JELLYFIN_LAST_PLAY.get("url") or "") == str(url or "")
        same_item = str(_JELLYFIN_LAST_PLAY.get("item_id") or "") == str(item_id or "")
        last_start = _JELLYFIN_LAST_PLAY.get("start_pos")
        try:
            delta = abs(float(last_start) - float(start_pos)) if (last_start is not None and start_pos is not None) else 0.0
        except Exception:
            delta = 0.0
        same_start = (last_start is None and start_pos is None) or (delta < 1.0)
        suppressed = same_url and (same_item or (not item_id)) and same_start
        _JELLYFIN_LAST_PLAY.update({"ts": now_ts, "url": url, "item_id": item_id, "start_pos": start_pos})
        return suppressed


def should_suppress_duplicate_ui_action(command: str, item_id: str, resume_pos: float | None) -> bool:
    window_sec = max(0.0, float(os.getenv("RELAYTV_JELLYFIN_UI_ACTION_DEDUPE_SEC", "1.5")))
    if window_sec <= 0:
        return False
    now_ts = time.time()
    norm_cmd = str(command or "").strip().lower()
    norm_item_id = canonical_item_id(item_id)
    with _JELLYFIN_UI_ACTION_DEDUPE_LOCK:
        last_ts = float(_JELLYFIN_LAST_UI_ACTION.get("ts") or 0.0)
        if now_ts - last_ts > window_sec:
            _JELLYFIN_LAST_UI_ACTION.update(
                {"ts": now_ts, "command": norm_cmd, "item_id": norm_item_id, "resume_pos": resume_pos}
            )
            return False
        same_cmd = str(_JELLYFIN_LAST_UI_ACTION.get("command") or "") == norm_cmd
        same_item = str(_JELLYFIN_LAST_UI_ACTION.get("item_id") or "") == norm_item_id
        last_resume = _JELLYFIN_LAST_UI_ACTION.get("resume_pos")
        try:
            delta = abs(float(last_resume) - float(resume_pos)) if (last_resume is not None and resume_pos is not None) else 0.0
        except Exception:
            delta = 0.0
        same_resume = (last_resume is None and resume_pos is None) or (delta < 1.0)
        suppressed = same_cmd and same_item and same_resume
        _JELLYFIN_LAST_UI_ACTION.update(
            {"ts": now_ts, "command": norm_cmd, "item_id": norm_item_id, "resume_pos": resume_pos}
        )
        return suppressed


def _playlist_entry_display_fields(entry: dict, iid: str, *, api_key: str, server_url: str) -> dict[str, str]:
    """
    Return display metadata (title/channel/thumbnail) for a playlist queue
    entry. Playlist payloads (series play-all, Jellyfin app casts) often carry
    bare item ids, so fall back to the catalog like the single-item path does.
    """
    title = str(entry.get("title") or "").strip()
    channel = ""
    thumbnail = ""
    if not title:
        try:
            meta = jellyfin_receiver.get_item_metadata(iid, token_override=api_key, server_url_override=server_url)
        except Exception:
            meta = None
        if isinstance(meta, dict):
            title = str(meta.get("title") or "").strip()
            channel = str(meta.get("channel") or "").strip()
            thumbnail = str(meta.get("thumbnail") or "").strip()
    out = {"title": title or f"Jellyfin item {iid}"}
    if channel:
        out["channel"] = channel
    if thumbnail:
        out["thumbnail"] = thumbnail
    return out


def handle_command(req: CommandReqLike, *, controls: dict, ui: dict):
    """Normalized Jellyfin command ingress (v1: Play/Stop/Pause/Resume/Seek/Next).

    ``controls`` maps stop/pause/resume/seek/next/previous/set_volume/mute
    to route-side playback control callables; ``ui`` provides toast,
    notification_display_sec, queue_event, and jellyfin_event callbacks.
    Route-facing side effects stay behind these seams so the service never
    imports the routes package.
    """
    st = jellyfin_receiver.status()
    if not bool(st.get("enabled")):
        raise HTTPException(status_code=503, detail="jellyfin integration disabled")

    action = normalize_action(req.action, req.payload)
    jellyfin_receiver.mark_command(action)
    jellyfin_receiver.mark_heartbeat()
    command_id = extract_command_id(req)
    if is_duplicate_command(command_id):
        return {"ok": True, "action": action or "unknown", "suppressed_duplicate_command": True}

    try:
        if action == "play":
            source_url = (req.url or "").strip()
            playlist_items = extract_playlist_items(req.payload)
            item_ids = [it.get("id", "") for it in playlist_items if isinstance(it, dict)]
            play_mode = extract_play_mode(req.payload)
            if not source_url:
                source_url = extract_play_url(req.payload)
            item_id = extract_item_id(req.payload)
            if not item_id and item_ids:
                item_id = item_ids[0]
            requested_item_id = str(item_id or "").strip()
            media_source_id = extract_media_source_id(req.payload)
            explicit_audio_idx = extract_audio_stream_index(req.payload)
            explicit_sub_idx = extract_subtitle_stream_index(req.payload)
            try:
                settings_snapshot = state.get_settings()
            except Exception:
                settings_snapshot = {}
            auth_token = access_token()
            pref_audio_idx = ""
            pref_sub_idx = ""
            resolved_detail: dict[str, object] = {}
            if item_id:
                resolved_item = resolve_playable_item(item_id, media_source_id=media_source_id)
                item_id = str(resolved_item.get("item_id") or item_id).strip()
                resolved_detail = resolved_item.get("detail") if isinstance(resolved_item.get("detail"), dict) else {}
                media_source_id = _first_nonempty_str([
                    resolved_item.get("media_source_id") if isinstance(resolved_item, dict) else "",
                    media_source_id,
                ])
                if requested_item_id and item_id and item_id != requested_item_id:
                    if item_ids and item_ids[0] == requested_item_id:
                        item_ids[0] = item_id
                    if playlist_items and isinstance(playlist_items[0], dict) and str(playlist_items[0].get("id") or "").strip() == requested_item_id:
                        playlist_items[0] = {
                            **playlist_items[0],
                            "id": item_id,
                            "media_source_id": media_source_id or str(playlist_items[0].get("media_source_id") or "").strip(),
                        }
                pref_audio_idx, pref_sub_idx = preferred_stream_indices(item_id)
                if not media_source_id:
                    detail = resolved_detail if isinstance(resolved_detail, dict) else {}
                    if not detail:
                        try:
                            detail = jellyfin_receiver.get_item_detail(item_id)
                        except Exception:
                            detail = {}
                    media_source_id = _first_nonempty_str(
                        [
                            detail.get("media_source_id") if isinstance(detail, dict) else "",
                            detail.get("MediaSourceId") if isinstance(detail, dict) else "",
                        ]
                    )
            audio_stream_index = explicit_audio_idx or pref_audio_idx
            subtitle_stream_index = explicit_sub_idx or pref_sub_idx
            if not source_url and item_id:
                source_url = build_item_stream_url(
                    item_id,
                    server_url=str(st.get("server_url") or ""),
                    api_key=auth_token,
                    media_source_id=media_source_id,
                    audio_stream_index=audio_stream_index,
                    subtitle_stream_index=subtitle_stream_index,
                )
            source_url = normalize_source_url(
                source_url,
                server_url=str(st.get("server_url") or ""),
                api_key=auth_token,
            )
            source_url = apply_stream_params(
                source_url,
                audio_stream_index=audio_stream_index,
                subtitle_stream_index=subtitle_stream_index,
            )
            source_url = apply_media_source_param(source_url, media_source_id=media_source_id)
            if not media_source_id:
                media_source_id = extract_media_source_id_from_url(source_url)
            selected_stream: dict[str, str] = {"mode": "direct", "reason": "", "media_source_id": media_source_id}
            if item_id:
                selected_stream = select_playback_url(
                    item_id=item_id,
                    source_url=source_url,
                    server_url=str(st.get("server_url") or ""),
                    api_key=auth_token,
                    media_source_id=media_source_id,
                    audio_stream_index=audio_stream_index,
                    subtitle_stream_index=subtitle_stream_index,
                    settings=settings_snapshot,
                )
                source_url = normalize_source_url(
                    str(selected_stream.get("url") or source_url),
                    server_url=str(st.get("server_url") or ""),
                    api_key=auth_token,
                )
                source_url = apply_stream_params(
                    source_url,
                    audio_stream_index=audio_stream_index,
                    subtitle_stream_index=subtitle_stream_index,
                )
                media_source_id = _first_nonempty_str(
                    [
                        str(selected_stream.get("media_source_id") or "").strip(),
                        extract_media_source_id_from_url(source_url),
                        media_source_id,
                    ]
                )
            if not source_url:
                raise HTTPException(status_code=400, detail="play command requires url")
            start_sec = extract_start_seconds(req)
            try:
                suppress_recent_stop = bool(
                    getattr(player, "recent_jellyfin_stop_matches", lambda **_: False)(
                        item_id=item_id,
                        source_url=source_url,
                        media_source_id=media_source_id,
                    )
                )
            except Exception:
                suppress_recent_stop = False
            if suppress_recent_stop and (start_sec is None or float(start_sec) <= 1.0):
                return {
                    "ok": True,
                    "action": "play",
                    "suppressed_recent_stop_replay": True,
                    "now_playing": state.NOW_PLAYING,
                }
            if should_suppress_duplicate_play(source_url, item_id, start_sec):
                return {"ok": True, "action": "play", "suppressed_duplicate": True, "now_playing": state.NOW_PLAYING}
            # If a play command explicitly asks to queue and we are already playing,
            # add items to queue without interrupting current playback.
            if play_mode in ("playnext", "playlast") and player.is_playing():
                try:
                    settings_snapshot = state.get_settings()
                except Exception:
                    settings_snapshot = {}
                queued = []
                existing_item_media: dict[str, set[str]] = {}
                existing_urls: set[str] = set()
                def _remember(iid_raw: object, url_raw: object, mid_raw: object = "") -> None:
                    iid = canonical_item_id(iid_raw)
                    if not iid:
                        iid = extract_item_id_from_url(str(url_raw or ""))
                    mid = canonical_media_source_id(mid_raw)
                    if not mid:
                        mid = canonical_media_source_id(
                            extract_media_source_id_from_url(str(url_raw or ""))
                        )
                    if iid:
                        mids = existing_item_media.setdefault(iid, set())
                        mids.add(mid)
                    qurl_existing = canonical_url_key(url_raw)
                    if qurl_existing:
                        existing_urls.add(qurl_existing)

                _remember(
                    (state.NOW_PLAYING or {}).get("jellyfin_item_id"),
                    (state.NOW_PLAYING or {}).get("url"),
                    (state.NOW_PLAYING or {}).get("jellyfin_media_source_id"),
                )
                for q in list(state.QUEUE):
                    if isinstance(q, dict):
                        _remember(q.get("jellyfin_item_id"), q.get("url"), q.get("jellyfin_media_source_id"))
                    else:
                        _remember("", q, "")

                def _seen(iid_raw: str, qurl_raw: str, mid_raw: str = "") -> bool:
                    iid = canonical_item_id(iid_raw)
                    if not iid:
                        iid = extract_item_id_from_url(qurl_raw)
                    mid = canonical_media_source_id(mid_raw)
                    if not mid:
                        mid = canonical_media_source_id(extract_media_source_id_from_url(qurl_raw))

                    if iid:
                        seen_mids = existing_item_media.get(iid)
                        if seen_mids:
                            # Allow different known media-source variants of the same item.
                            # Unknown/blank media source is treated as duplicate-safe and blocks additional variants.
                            if not mid:
                                return True
                            if mid in seen_mids or "" in seen_mids:
                                return True

                    qurl = canonical_url_key(qurl_raw)
                    if qurl and qurl in existing_urls:
                        return True
                    return False

                source_for_queue = source_url
                if source_for_queue:
                    selected_queue = select_playback_url(
                        item_id=item_id,
                        source_url=source_for_queue,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=media_source_id,
                        audio_stream_index=audio_stream_index,
                        subtitle_stream_index=subtitle_stream_index,
                        settings=settings_snapshot,
                    )
                    source_for_queue = normalize_source_url(
                        str(selected_queue.get("url") or source_for_queue),
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                    )
                    q_item = smart_item_from_url(source_for_queue)
                    q_title = str(q_item.get("title") or "") if isinstance(q_item, dict) else ""
                    q_channel = str(q_item.get("channel") or "") if isinstance(q_item, dict) else ""
                    source_media_source_id = _first_nonempty_str(
                        [
                            media_source_id,
                            q_item.get("jellyfin_media_source_id") if isinstance(q_item, dict) else "",
                            extract_media_source_id_from_url(source_for_queue),
                        ]
                    )
                    if not _seen(item_id, source_for_queue, source_media_source_id):
                        queued.append(
                            {
                                "url": source_for_queue,
                                "title": q_title or (f"Jellyfin item {item_id}" if item_id else "Jellyfin item"),
                                "provider": "jellyfin",
                                **({"channel": q_channel} if q_channel else {}),
                                **({"thumbnail": q_item.get("thumbnail")} if isinstance(q_item, dict) and q_item.get("thumbnail") else {}),
                                **({"jellyfin_item_id": item_id} if item_id else {}),
                                **({"jellyfin_media_source_id": source_media_source_id} if source_media_source_id else {}),
                                "jellyfin_stream_mode": str(selected_queue.get("mode") or ""),
                                "jellyfin_stream_reason": str(selected_queue.get("reason") or ""),
                            }
                        )
                        _remember(item_id, source_for_queue, source_media_source_id)
                # Prefer rich playlist items when available.
                for entry in playlist_items:
                    iid = str(entry.get("id") or "").strip()
                    if not iid:
                        continue
                    q_audio_idx, q_sub_idx = preferred_stream_indices(iid)
                    if explicit_audio_idx:
                        q_audio_idx = explicit_audio_idx
                    if explicit_sub_idx:
                        q_sub_idx = explicit_sub_idx
                    qurl = build_item_stream_url(
                        iid,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=str(entry.get("media_source_id") or "").strip(),
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                    )
                    if not qurl:
                        continue
                    selected_q = select_playback_url(
                        item_id=iid,
                        source_url=qurl,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=str(entry.get("media_source_id") or "").strip(),
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                        settings=settings_snapshot,
                    )
                    qurl = normalize_source_url(
                        str(selected_q.get("url") or qurl),
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                    )
                    if qurl == source_for_queue:
                        continue
                    q_media_source_id = str(entry.get("media_source_id") or "").strip()
                    if _seen(iid, qurl, q_media_source_id):
                        continue
                    q_display = _playlist_entry_display_fields(
                        entry, iid, api_key=auth_token, server_url=str(st.get("server_url") or "")
                    )
                    queued.append(
                        {
                            "url": qurl,
                            **q_display,
                            "provider": "jellyfin",
                            "jellyfin_item_id": iid,
                            **({"jellyfin_media_source_id": q_media_source_id} if q_media_source_id else {}),
                            "jellyfin_stream_mode": str(selected_q.get("mode") or ""),
                            "jellyfin_stream_reason": str(selected_q.get("reason") or ""),
                        }
                    )
                    _remember(iid, qurl, q_media_source_id)
                if queued:
                    with state.QUEUE_LOCK:
                        if play_mode == "playnext":
                            state.QUEUE[:0] = queued
                        else:
                            state.QUEUE.extend(queued)
                        qlen = len(state.QUEUE)
                        queue_snapshot = list(state.QUEUE)
                    try:
                        state.persist_queue()
                    except Exception:
                        pass
                    try:
                        player.prime_mpv_up_next_from_queue(force=True)
                    except Exception:
                        pass
                    try:
                        lead = queued[0] if isinstance(queued[0], dict) else {}
                        lead_title = str((lead or {}).get("title") or "").strip()
                        queued_count = len(queued)
                        if queued_count > 1:
                            qtext = f"Queued {queued_count} items"
                        else:
                            qtext = f"Queued next: {lead_title or 'item'}"
                        ui["toast"](
                            text=qtext,
                            duration=ui["notification_display_sec"](),
                            level="info",
                            icon="share",
                            image_url=(lead.get("thumbnail") if isinstance(lead, dict) else None),
                        )
                    except Exception:
                        pass
                    emit_progress_hint()
                    ui["queue_event"]("jellyfin_queue", queue=queue_snapshot, queue_length=qlen, source="jellyfin")
                    ui["jellyfin_event"](
                        "queue_only",
                        refresh_active_tab=True,
                        refresh_status=True,
                        reason=play_mode,
                    )
                    return {"ok": True, "action": "queue_only", "queue_mode": play_mode, "queued": len(queued), "queue_length": qlen}
            stopped_payload = None
            if play_mode == "playnow":
                cur = state.NOW_PLAYING if isinstance(state.NOW_PLAYING, dict) else None
                if isinstance(cur, dict) and bool(player.is_playing()):
                    cur_copy = dict(cur)
                    cur_item_id = canonical_item_id(cur_copy.get("jellyfin_item_id"))
                    cur_url_key = canonical_url_key(cur_copy.get("url"))
                    next_item_id = canonical_item_id(item_id)
                    next_url_key = canonical_url_key(source_url)
                    replacing = False
                    if cur_item_id and next_item_id:
                        replacing = cur_item_id != next_item_id
                    elif cur_url_key and next_url_key:
                        replacing = cur_url_key != next_url_key
                    if replacing:
                        pos = None
                        dur = None
                        try:
                            with player.MPV_LOCK:
                                pos = player.mpv_get("time-pos")
                                dur = player.mpv_get("duration")
                        except Exception:
                            pos = None
                            dur = None
                        stopped_payload = stopped_snapshot_from_now(cur_copy, pos, dur)
            clear_queue_for_play = play_mode == "playnow"
            playback_service.suppress_auto_next(2.0)
            play_item_payload = smart_item_from_url(source_url, start_pos=start_sec)
            play_target = play_item_payload if isinstance(play_item_payload, dict) else source_url
            now = playback_service.play_now(
                play_target,
                use_resolver=bool(req.use_ytdlp),
                cec=False,
                clear_queue=clear_queue_for_play,
                mode="jellyfin_play",
                start_pos=start_sec,
            )
            # Preserve Jellyfin identifiers for progress/session reporting.
            if isinstance(now, dict):
                now = dict(now)
                if isinstance(play_item_payload, dict):
                    now = merge_playback_metadata(now, play_item_payload)
                if item_id:
                    now["jellyfin_item_id"] = item_id
                    if media_source_id:
                        now["jellyfin_media_source_id"] = media_source_id
                    now["jellyfin_stream_mode"] = str(selected_stream.get("mode") or "direct")
                    now["jellyfin_stream_reason"] = str(selected_stream.get("reason") or "")
                    play_session_id = _first_nonempty_str([
                        (req.payload or {}).get("play_session_id") if isinstance(req.payload, dict) else "",
                        (req.payload or {}).get("PlaySessionId") if isinstance(req.payload, dict) else "",
                    ])
                    if play_session_id:
                        now["jellyfin_play_session_id"] = play_session_id
                    try:
                        detail = jellyfin_receiver.get_item_detail(item_id)
                    except Exception:
                        detail = {}
                    now = enrich_now_stream_metadata(
                        now,
                        detail=detail if isinstance(detail, dict) else {},
                        audio_stream_index=audio_stream_index,
                        subtitle_stream_index=subtitle_stream_index,
                    )
                playback_service.update_now_playing(now)
            # Playlist-style play command support: enqueue remaining ItemIds.
            if item_ids and len(item_ids) > 1:
                extra_items = playlist_items[1:] if len(playlist_items) > 1 else [{"id": iid, "title": "", "media_source_id": ""} for iid in item_ids[1:]]
                queued = []
                seen_item_media: dict[str, set[str]] = {}
                seen_urls: set[str] = set()

                def _remember_seen(iid_raw: object, url_raw: object, mid_raw: object = "") -> None:
                    iid = canonical_item_id(iid_raw)
                    if not iid:
                        iid = extract_item_id_from_url(str(url_raw or ""))
                    mid = canonical_media_source_id(mid_raw)
                    if not mid:
                        mid = canonical_media_source_id(extract_media_source_id_from_url(str(url_raw or "")))
                    if iid:
                        seen_item_media.setdefault(iid, set()).add(mid)
                    key = canonical_url_key(url_raw)
                    if key:
                        seen_urls.add(key)

                def _seen(iid_raw: str, url_raw: str, mid_raw: str = "") -> bool:
                    iid = canonical_item_id(iid_raw)
                    if not iid:
                        iid = extract_item_id_from_url(url_raw)
                    mid = canonical_media_source_id(mid_raw)
                    if not mid:
                        mid = canonical_media_source_id(extract_media_source_id_from_url(url_raw))
                    if iid:
                        mids = seen_item_media.get(iid)
                        if mids:
                            if not mid:
                                return True
                            if mid in mids or "" in mids:
                                return True
                    key = canonical_url_key(url_raw)
                    return bool(key and key in seen_urls)

                _remember_seen(now.get("jellyfin_item_id") if isinstance(now, dict) else "", now.get("url") if isinstance(now, dict) else "", now.get("jellyfin_media_source_id") if isinstance(now, dict) else "")
                for existing in list(state.QUEUE):
                    if isinstance(existing, dict):
                        _remember_seen(existing.get("jellyfin_item_id"), existing.get("url"), existing.get("jellyfin_media_source_id"))
                    else:
                        _remember_seen("", existing, "")

                for entry in extra_items:
                    iid = str(entry.get("id") or "").strip()
                    if not iid:
                        continue
                    q_audio_idx, q_sub_idx = preferred_stream_indices(iid)
                    if explicit_audio_idx:
                        q_audio_idx = explicit_audio_idx
                    if explicit_sub_idx:
                        q_sub_idx = explicit_sub_idx
                    qurl = build_item_stream_url(
                        iid,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=str(entry.get("media_source_id") or "").strip(),
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                    )
                    if not qurl:
                        continue
                    q_media_source_id = str(entry.get("media_source_id") or "").strip()
                    selected_q = select_playback_url(
                        item_id=iid,
                        source_url=qurl,
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                        media_source_id=q_media_source_id,
                        audio_stream_index=q_audio_idx,
                        subtitle_stream_index=q_sub_idx,
                        settings=settings_snapshot if isinstance(settings_snapshot, dict) else {},
                    )
                    qurl = normalize_source_url(
                        str(selected_q.get("url") or qurl),
                        server_url=str(st.get("server_url") or ""),
                        api_key=auth_token,
                    )
                    q_media_source_id = _first_nonempty_str(
                        [
                            str(selected_q.get("media_source_id") or "").strip(),
                            extract_media_source_id_from_url(qurl),
                            q_media_source_id,
                        ]
                    )
                    if _seen(iid, qurl, q_media_source_id):
                        continue
                    q_display = _playlist_entry_display_fields(
                        entry, iid, api_key=auth_token, server_url=str(st.get("server_url") or "")
                    )
                    queued.append(
                        {
                            "url": qurl,
                            **q_display,
                            "provider": "jellyfin",
                            "jellyfin_item_id": iid,
                            **({"jellyfin_media_source_id": q_media_source_id} if q_media_source_id else {}),
                            "jellyfin_stream_mode": str(selected_q.get("mode") or ""),
                            "jellyfin_stream_reason": str(selected_q.get("reason") or ""),
                        }
                    )
                    _remember_seen(iid, qurl, q_media_source_id)
                if queued:
                    with state.QUEUE_LOCK:
                        state.QUEUE.extend(queued)
                        queue_snapshot = list(state.QUEUE)
                    try:
                        state.persist_queue()
                    except Exception:
                        pass
                    try:
                        player.prime_mpv_up_next_from_queue(force=True)
                    except Exception:
                        pass
                    ui["queue_event"]("jellyfin_playlist", queue=queue_snapshot, queue_length=len(queue_snapshot), source="jellyfin")
            if isinstance(stopped_payload, dict) and stopped_payload:
                emit_stopped_payload(stopped_payload)
            emit_playback_start_hint()
            ui["jellyfin_event"]("play", refresh_active_tab=True, refresh_status=True, reason=play_mode or "play")
            return {"ok": True, "action": "play", "now_playing": now}

        if action == "stop":
            res = controls["stop"]()
            return {"ok": True, "action": "stop", "result": res}

        if action == "pause":
            out = {"ok": True, "action": "pause", "result": controls["pause"]()}
            emit_progress_hint()
            return out

        if action in ("resume", "unpause"):
            out = {"ok": True, "action": "resume", "result": controls["resume"]()}
            emit_progress_hint()
            return out

        if action == "play_pause":
            paused = playback_is_paused()
            resolved = "resume" if paused else "pause"
            out = {
                "ok": True,
                "action": resolved,
                "toggled_from": "play_pause",
                "result": controls["resume"]() if paused else controls["pause"](),
            }
            emit_progress_hint()
            return out

        if action == "seek":
            sec = extract_seek_seconds(req)
            if sec is None:
                raise HTTPException(status_code=400, detail="seek command requires start_pos or payload.position")
            out = {"ok": True, "action": "seek", "result": controls["seek"](float(sec))}
            emit_progress_hint()
            return out

        if action == "next":
            out = {"ok": True, "action": "next", "result": controls["next"]()}
            emit_progress_hint()
            return out

        if action == "previous":
            out = {"ok": True, "action": "previous", "result": controls["previous"]()}
            emit_progress_hint()
            return out

        if action == "set_volume":
            vol = extract_volume(req)
            if vol is None:
                raise HTTPException(status_code=400, detail="set_volume requires payload.VolumeLevel or payload.volume")
            out = {"ok": True, "action": "set_volume", "result": controls["set_volume"](vol)}
            emit_progress_hint()
            return out

        if action == "mute":
            out = {"ok": True, "action": "mute", "result": controls["mute"](True)}
            emit_progress_hint()
            return out

        if action == "unmute":
            out = {"ok": True, "action": "unmute", "result": controls["mute"](False)}
            emit_progress_hint()
            return out

        raise HTTPException(status_code=400, detail=f"unsupported jellyfin action: {req.action}")
    except HTTPException:
        raise
    except Exception as e:
        jellyfin_receiver.mark_error(str(e))
        raise HTTPException(status_code=500, detail=f"jellyfin command failed: {e}")


jellyfin_receiver.register_progress_provider(progress_snapshot)
