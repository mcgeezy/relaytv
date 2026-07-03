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

import os

from fastapi import HTTPException

from typing import Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .. import player, state, video_profile
from ..config import runtime_config
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
