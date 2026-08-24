# SPDX-License-Identifier: GPL-3.0-only
"""RelayTV-facing Seerr product behavior and sanitized integration status."""
from __future__ import annotations

import re
import time
import urllib.parse

from ..debug import get_logger
from .seerr_client import (
    SeerrBinaryResponse,
    SeerrClient,
    SeerrConfig,
    SeerrError,
)
from . import jellyfin_service, seerr_sessions

logger = get_logger("seerr")

_MEDIA_SERVER_TYPES = {
    1: "plex",
    2: "jellyfin",
    3: "emby",
    4: "not_configured",
}

_MEDIA_STATUSES = {
    1: "unknown",
    2: "pending",
    3: "processing",
    4: "partially_available",
    5: "available",
    6: "blocklisted",
    7: "deleted",
}
_REQUEST_STATUSES = {
    1: "pending",
    2: "approved",
    3: "declined",
    4: "failed",
    5: "completed",
}
_DISCOVER_PATHS = {
    "trending": "/discover/trending",
    "movies": "/discover/movies",
    "tv": "/discover/tv",
}
_REQUEST_FILTERS = {
    "all",
    "approved",
    "available",
    "pending",
    "processing",
    "unavailable",
    "failed",
    "deleted",
    "completed",
}
_IMAGE_SIZES = {"w185", "w342", "w500", "w780", "original"}
_IMAGE_PATH_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,255}$")


def _config_status(config: SeerrConfig) -> dict[str, object]:
    request_mode = _request_mode(config)
    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "reachable": False,
        "server_url": config.server_url,
        "version": "",
        "application_title": "Seerr",
        "media_server_type": "unknown",
        "auth_mode": (
            "caller_session"
            if request_mode == "caller_session"
            else ("shared_api_key" if config.api_key else "none")
        ),
        "request_mode": request_mode,
        "shared_requests_enabled": config.shared_requests_enabled,
        "writes_allowed": bool(config.configured and request_mode == "shared_admin"),
        "request_user_id": config.request_user_id,
    }


def integration_status(
    *, probe: bool = True, session_id: str | None = None
) -> dict[str, object]:
    config = SeerrConfig.current()
    out = _config_status(config)
    caller = seerr_sessions.status(session_id)
    out["caller_connected"] = bool(caller.get("connected"))
    out["writes_allowed"] = bool(
        config.configured
        and (
            out["request_mode"] == "shared_admin"
            or (out["request_mode"] == "caller_session" and caller.get("connected"))
        )
    )
    if caller.get("identity"):
        out["caller_identity"] = caller["identity"]
    if config.configuration_error:
        out["error"] = {
            "code": "seerr_invalid_configuration",
            "message": config.configuration_error,
        }
        return out
    if not config.enabled or not config.configured or not probe:
        return out
    started = time.monotonic()
    try:
        if _request_mode(config) == "caller_session":
            session = seerr_sessions.resolve(session_id)
            client = SeerrClient(config, session_cookie=session.cookie) if session else None
            out.update(_probe(config, client=client, public_only=session is None))
        else:
            out.update(_probe(config))
        logger.info(
            "seerr_status operation=status host=%s latency_ms=%d result=ok",
            _safe_host(config.server_url),
            int((time.monotonic() - started) * 1000),
        )
        return out
    except SeerrError as exc:
        if exc.code == "seerr_session_expired":
            seerr_sessions.retire(session_id)
            out["caller_connected"] = False
            out["writes_allowed"] = False
            out.pop("caller_identity", None)
        out["error"] = {"code": exc.code, "message": exc.message}
        logger.warning(
            "seerr_status operation=status host=%s latency_ms=%d result=%s",
            _safe_host(config.server_url),
            int((time.monotonic() - started) * 1000),
            exc.code,
        )
        return out


def test_connection() -> dict[str, object]:
    config = SeerrConfig.current()
    if not config.enabled:
        raise SeerrError("seerr_disabled", "Seerr integration is disabled", status_code=503)
    if config.configuration_error:
        raise SeerrError(
            "seerr_invalid_configuration",
            config.configuration_error,
            status_code=400,
        )
    if not config.configured:
        raise SeerrError(
            "seerr_not_configured",
            "Seerr server URL and API key are required",
            status_code=503,
        )
    status = _config_status(config)
    client = SeerrClient(config)
    if _request_mode(config) == "caller_session":
        status.update(_probe(config, client=client, public_only=True))
        status["ok"] = True
        return status
    status.update(_probe(config, client=client))
    identity = client.get("/auth/me")
    if not isinstance(identity, dict):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected identity response",
            status_code=502,
        )
    status["identity"] = {
        "id": _safe_int(identity.get("id")),
        "display_name": str(identity.get("displayName") or "").strip(),
        "username": str(identity.get("username") or "").strip(),
    }
    status["ok"] = True
    return status


def discover(section: str, page: int, *, session_id: str | None = None) -> dict[str, object]:
    path = _DISCOVER_PATHS.get(str(section or "").strip().lower())
    if path is None:
        raise _invalid("Unknown Seerr discovery section")
    page_number = _bounded_int(page, name="page", minimum=1, maximum=500)
    payload = _client(session_id=session_id).get(path, query={"page": page_number})
    return _normalize_media_page(payload, fallback_page=page_number)


def search(query: str, page: int, *, session_id: str | None = None) -> dict[str, object]:
    text = str(query or "").strip()
    if not text or len(text) > 200:
        raise _invalid("Search query must contain between 1 and 200 characters")
    page_number = _bounded_int(page, name="page", minimum=1, maximum=500)
    payload = _client(session_id=session_id).get(
        "/search", query={"query": text, "page": page_number}
    )
    return _normalize_media_page(payload, fallback_page=page_number)


def item_detail(
    media_type: str, media_id: int, *, session_id: str | None = None
) -> dict[str, object]:
    kind = _media_type(media_type)
    tmdb_id = _bounded_int(media_id, name="media_id", minimum=1, maximum=2_147_483_647)
    payload = _client(session_id=session_id).get(f"/{kind}/{tmdb_id}")
    if not isinstance(payload, dict):
        raise _invalid_upstream()
    item = _normalize_media(payload, fallback_type=kind)
    item.update(
        {
            "runtime_minutes": _runtime(payload, kind),
            "genres": _normalize_genres(payload.get("genres")),
            "tagline": _text(payload.get("tagline"), limit=300),
            "seasons": _normalize_seasons(payload.get("seasons")) if kind == "tv" else [],
        }
    )
    validated = _validated_playback(payload, media_type=kind, tmdb_id=tmdb_id)
    if validated:
        item["playback_available"] = True
        item["playback"] = {
            "provider": "jellyfin",
            "media_type": kind,
            "media_id": tmdb_id,
        }
    return item


def list_requests(
    *,
    take: int,
    skip: int,
    status_filter: str,
    session_id: str | None = None,
) -> dict[str, object]:
    take_number = _bounded_int(take, name="take", minimum=1, maximum=100)
    skip_number = _bounded_int(skip, name="skip", minimum=0, maximum=100_000)
    selected_filter = str(status_filter or "all").strip().lower()
    if selected_filter not in _REQUEST_FILTERS:
        raise _invalid("Unknown Seerr request filter")
    config, client = _client_with_config(session_id=session_id)
    query: dict[str, object] = {
        "take": take_number,
        "skip": skip_number,
        "filter": selected_filter,
        "sort": "added",
        "sortDirection": "desc",
    }
    if config.shared_requests_enabled and config.request_user_id is not None:
        query["requestedBy"] = config.request_user_id
    payload = client.get("/request", query=query)
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _invalid_upstream()
    page_info = payload.get("pageInfo") if isinstance(payload.get("pageInfo"), dict) else {}
    return {
        "page": _safe_int(page_info.get("page")) or (skip_number // take_number) + 1,
        "total_pages": _safe_int(page_info.get("pages")),
        "total_results": _safe_int(page_info.get("results")),
        "take": take_number,
        "skip": skip_number,
        "results": [
            normalized
            for raw in payload["results"]
            if (normalized := _normalize_request(raw)) is not None
        ],
    }


def list_users() -> list[dict[str, object]]:
    payload = _client().get(
        "/user",
        query={"take": 100, "skip": 0, "sort": "displayname", "sortDirection": "asc"},
    )
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _invalid_upstream()
    users = []
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            continue
        user_id = _safe_int(raw.get("id"))
        if user_id <= 0:
            continue
        users.append(
            {
                "id": user_id,
                "display_name": _text(raw.get("displayName"), limit=150),
                "username": _text(raw.get("username"), limit=150),
            }
        )
    return users


def create_request(
    *,
    media_type: str,
    media_id: int,
    seasons: list[int] | str | None,
    is_4k: bool,
    session_id: str | None = None,
) -> dict[str, object]:
    kind = _media_type(media_type)
    tmdb_id = _bounded_int(media_id, name="media_id", minimum=1, maximum=2_147_483_647)
    config, client = _client_with_config(session_id=session_id)
    request_mode = _request_mode(config)
    if request_mode == "disabled":
        raise SeerrError(
            "seerr_requests_disabled",
            "Seerr requests are disabled",
            status_code=403,
        )
    request_body: dict[str, object] = {
        "mediaType": kind,
        "mediaId": tmdb_id,
        "is4k": bool(is_4k),
    }
    if kind == "tv":
        request_body["seasons"] = _normalize_requested_seasons(seasons)
    elif seasons not in (None, [], ""):
        raise _invalid("Seasons can only be selected for TV requests")
    if request_mode == "shared_admin" and config.request_user_id is not None:
        request_body["userId"] = config.request_user_id
    response = client.request_json_response("POST", "/request", body=request_body)
    if response.status == 202:
        return {
            "created": False,
            "reason": "no_requestable_seasons",
            "media_type": kind,
            "media_id": tmdb_id,
        }
    if not isinstance(response.data, dict):
        raise _invalid_upstream()
    media = response.data.get("media") if isinstance(response.data.get("media"), dict) else {}
    return {
        "created": True,
        "request": {
            "request_id": _safe_int(response.data.get("id")),
            "status": _REQUEST_STATUSES.get(
                _safe_int(response.data.get("status")), "unknown"
            ),
            "media_type": _media_type_or_none(media.get("mediaType")) or kind,
            "media_id": _safe_int(media.get("tmdbId")) or tmdb_id,
            "media_status": _MEDIA_STATUSES.get(
                _safe_int(media.get("status")), "unknown"
            ),
            "is_4k": bool(response.data.get("is4k")),
        },
    }


def playback_action(
    *,
    media_type: str,
    media_id: int,
    command: str,
    session_id: str | None = None,
) -> dict[str, object]:
    kind = _media_type(media_type)
    tmdb_id = _bounded_int(media_id, name="media_id", minimum=1, maximum=2_147_483_647)
    selected = str(command or "").strip().lower()
    if selected not in {"play_now", "play_next", "play_last"}:
        raise _invalid("Playback command must be play_now, play_next, or play_last")
    config, client = _client_with_config(session_id=session_id)
    payload = client.get(f"/{kind}/{tmdb_id}")
    if not isinstance(payload, dict):
        raise _invalid_upstream()
    if not _operation_current(config, session_id):
        raise SeerrError(
            "seerr_playback_unavailable",
            "The Seerr configuration changed; try again",
            status_code=409,
        )
    validated = _validated_playback(payload, media_type=kind, tmdb_id=tmdb_id)
    if not validated:
        raise SeerrError(
            "seerr_playback_unavailable",
            "This Seerr item is not validated on RelayTV's active media server",
            status_code=409,
        )
    try:
        result = jellyfin_service.dispatch_external_item(
            validated,
            command=selected,
            guard=lambda: _operation_current(config, session_id),
        )
    except Exception:
        logger.warning(
            "seerr_playback_failed operation=playback host=%s media_type=%s result=dispatch_failed",
            _safe_host(config.server_url),
            kind,
        )
        raise SeerrError(
            "seerr_playback_failed",
            "RelayTV could not start Jellyfin playback",
            status_code=502,
        ) from None
    if not bool(result.get("ok")):
        raise SeerrError(
            "seerr_playback_unavailable",
            "The validated Jellyfin playback target changed; try again",
            status_code=409,
            upstream_status=None,
        )
    return {
        "ok": True,
        "media_type": kind,
        "media_id": tmdb_id,
        "command": selected,
        "queued": str(result.get("action") or "").strip() == "queue_only",
        "suppressed": bool(
            result.get("suppressed_duplicate")
            or result.get("suppressed_duplicate_ui_action")
        ),
    }


def image(size: str, image_path: str) -> SeerrBinaryResponse:
    selected_size = str(size or "").strip().lower()
    selected_path = str(image_path or "").strip().lstrip("/")
    if selected_size not in _IMAGE_SIZES or not _IMAGE_PATH_RE.fullmatch(selected_path):
        raise _invalid("Invalid Seerr image path or size")
    config = _base_config()
    response = SeerrClient(config).get_binary(
        f"/imageproxy/tmdb/{selected_size}/{urllib.parse.quote(selected_path, safe='')}",
        auth=False,
    )
    if not response.content_type.lower().startswith("image/"):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected image response",
            status_code=502,
        )
    return response


def _client(*, session_id: str | None = None) -> SeerrClient:
    return _client_with_config(session_id=session_id)[1]


def _base_config() -> SeerrConfig:
    config = SeerrConfig.current()
    if not config.enabled:
        raise SeerrError("seerr_disabled", "Seerr integration is disabled", status_code=503)
    if config.configuration_error:
        raise SeerrError(
            "seerr_invalid_configuration",
            config.configuration_error,
            status_code=400,
        )
    if not config.configured:
        requirement = (
            "Seerr server URL is required"
            if _request_mode(config) == "caller_session"
            else "Seerr server URL and API key are required"
        )
        raise SeerrError(
            "seerr_not_configured",
            requirement,
            status_code=503,
        )
    return config


def _client_with_config(
    *, session_id: str | None = None
) -> tuple[SeerrConfig, SeerrClient]:
    config = _base_config()
    if _request_mode(config) == "caller_session":
        session = seerr_sessions.resolve(session_id)
        if session is None:
            raise SeerrError(
                "seerr_session_required",
                "Connect your Seerr account to continue",
                status_code=401,
            )
        return config, SeerrClient(config, session_cookie=session.cookie)
    return config, SeerrClient(config)


def _request_mode(config: SeerrConfig) -> str:
    request_mode = str(getattr(config, "request_mode", "") or "").strip()
    if request_mode in {"disabled", "shared_admin", "caller_session"}:
        return request_mode
    return "shared_admin" if config.shared_requests_enabled else "disabled"


def _operation_current(config: SeerrConfig, session_id: str | None) -> bool:
    if SeerrConfig.current() != config:
        return False
    if _request_mode(config) != "caller_session":
        return True
    return seerr_sessions.resolve(session_id) is not None


def _normalize_media_page(payload: object, *, fallback_page: int) -> dict[str, object]:
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        raise _invalid_upstream()
    results = []
    for raw in payload["results"]:
        if not isinstance(raw, dict):
            continue
        kind = _media_type_or_none(raw.get("mediaType"))
        if kind is None:
            continue
        results.append(_normalize_media(raw, fallback_type=kind))
    return {
        "page": _safe_int(payload.get("page")) or fallback_page,
        "total_pages": _safe_int(payload.get("totalPages")),
        "total_results": _safe_int(payload.get("totalResults")),
        "results": results,
    }


def _normalize_media(raw: dict, *, fallback_type: str) -> dict[str, object]:
    kind = _media_type_or_none(raw.get("mediaType")) or fallback_type
    title = raw.get("title") if kind == "movie" else raw.get("name")
    original_title = raw.get("originalTitle") if kind == "movie" else raw.get("originalName")
    date = raw.get("releaseDate") if kind == "movie" else raw.get("firstAirDate")
    media_info = raw.get("mediaInfo") if isinstance(raw.get("mediaInfo"), dict) else {}
    request_summary = _latest_request(media_info.get("requests"))
    return {
        "media_type": kind,
        "media_id": _safe_int(raw.get("id")) or _safe_int(media_info.get("tmdbId")),
        "title": _text(title, limit=300),
        "original_title": _text(original_title, limit=300),
        "date": _text(date, limit=40),
        "year": _year(date),
        "overview": _text(raw.get("overview"), limit=600),
        "poster_url": _image_url(raw.get("posterPath"), "w342"),
        "backdrop_url": _image_url(raw.get("backdropPath"), "w780"),
        "rating": _safe_float(raw.get("voteAverage")),
        "media_status": _MEDIA_STATUSES.get(_safe_int(media_info.get("status")), "unknown"),
        "request": request_summary,
        "playback_available": False,
    }


def _validated_playback(
    payload: dict[str, object], *, media_type: str, tmdb_id: int
) -> dict[str, object]:
    media_info = payload.get("mediaInfo")
    if not isinstance(media_info, dict):
        return {}
    item_id = str(
        media_info.get("jellyfinMediaId")
        or media_info.get("JellyfinMediaId")
        or ""
    ).strip()
    if not item_id:
        return {}
    try:
        return jellyfin_service.validate_external_item(
            item_id,
            media_type=media_type,
            tmdb_id=tmdb_id,
        )
    except Exception:
        return {}


def _normalize_request(raw: object) -> dict[str, object] | None:
    if not isinstance(raw, dict):
        return None
    media = raw.get("media") if isinstance(raw.get("media"), dict) else {}
    kind = _media_type_or_none(media.get("mediaType"))
    if kind is None:
        return None
    return {
        "request_id": _safe_int(raw.get("id")),
        "status": _REQUEST_STATUSES.get(_safe_int(raw.get("status")), "unknown"),
        "media_type": kind,
        "media_id": _safe_int(media.get("tmdbId")),
        "media_status": _MEDIA_STATUSES.get(_safe_int(media.get("status")), "unknown"),
        "is_4k": bool(raw.get("is4k")),
        "created_at": _text(raw.get("createdAt"), limit=40),
        "updated_at": _text(raw.get("updatedAt"), limit=40),
    }


def _latest_request(value: object) -> dict[str, object] | None:
    if not isinstance(value, list):
        return None
    requests = [item for item in value if isinstance(item, dict)]
    if not requests:
        return None
    raw = max(requests, key=lambda item: _safe_int(item.get("id")))
    return {
        "request_id": _safe_int(raw.get("id")),
        "status": _REQUEST_STATUSES.get(_safe_int(raw.get("status")), "unknown"),
        "is_4k": bool(raw.get("is4k")),
    }


def _normalize_genres(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    return [
        {"id": _safe_int(item.get("id")), "name": _text(item.get("name"), limit=100)}
        for item in value
        if isinstance(item, dict) and _text(item.get("name"), limit=100)
    ]


def _normalize_seasons(value: object) -> list[dict[str, object]]:
    if not isinstance(value, list):
        return []
    seasons = []
    for item in value:
        if not isinstance(item, dict):
            continue
        number = _safe_int(item.get("seasonNumber"))
        if number < 0:
            continue
        seasons.append(
            {
                "season_number": number,
                "name": _text(item.get("name"), limit=150),
                "episode_count": max(0, _safe_int(item.get("episodeCount"))),
                "air_date": _text(item.get("airDate"), limit=40),
                "poster_url": _image_url(item.get("posterPath"), "w342"),
            }
        )
    return seasons


def _runtime(payload: dict, kind: str) -> int:
    if kind == "movie":
        return max(0, _safe_int(payload.get("runtime")))
    values = payload.get("episodeRunTime")
    if not isinstance(values, list):
        return 0
    return next((number for value in values if (number := _safe_int(value)) > 0), 0)


def _normalize_requested_seasons(value: list[int] | str | None) -> list[int] | str:
    if value is None or value == "all":
        return "all"
    if not isinstance(value, list) or not value or len(value) > 100:
        raise _invalid("Select between 1 and 100 TV seasons, or all seasons")
    seasons = []
    for raw in value:
        number = _bounded_int(raw, name="season", minimum=0, maximum=10_000)
        if number not in seasons:
            seasons.append(number)
    return seasons


def _image_url(value: object, size: str) -> str:
    path = str(value or "").strip().lstrip("/")
    if not _IMAGE_PATH_RE.fullmatch(path):
        return ""
    return f"/seerr/image/{size}/{urllib.parse.quote(path, safe='')}"


def _media_type(value: object) -> str:
    kind = _media_type_or_none(value)
    if kind is None:
        raise _invalid("Media type must be movie or tv")
    return kind


def _media_type_or_none(value: object) -> str | None:
    kind = str(value or "").strip().lower()
    return kind if kind in {"movie", "tv"} else None


def _bounded_int(value: object, *, name: str, minimum: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise _invalid(f"{name} must be an integer") from None
    if number < minimum or number > maximum:
        raise _invalid(f"{name} must be between {minimum} and {maximum}")
    return number


def _year(value: object) -> int | None:
    text = str(value or "").strip()
    if len(text) >= 4 and text[:4].isdigit():
        year = int(text[:4])
        if 1800 <= year <= 3000:
            return year
    return None


def _text(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def _safe_float(value: object) -> float:
    try:
        return round(float(value), 1)
    except (TypeError, ValueError):
        return 0.0


def _invalid(message: str) -> SeerrError:
    return SeerrError("seerr_invalid_request", message, status_code=400)


def _invalid_upstream() -> SeerrError:
    return SeerrError(
        "seerr_invalid_response",
        "Seerr returned an unexpected response shape",
        status_code=502,
    )


def _probe(
    config: SeerrConfig,
    *,
    client: SeerrClient | None = None,
    public_only: bool = False,
) -> dict[str, object]:
    active_client = client or SeerrClient(config)
    status = active_client.get("/status", query={"checkUpdateAvailable": False}, auth=False)
    if not isinstance(status, dict):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected response shape",
            status_code=502,
        )
    out = {
        "reachable": True,
        "version": str(status.get("version") or "").strip(),
    }
    if public_only:
        return out
    settings = active_client.get("/settings/main")
    if not isinstance(settings, dict):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected response shape",
            status_code=502,
        )
    out.update(
        {
            "application_title": str(settings.get("applicationTitle") or "Seerr").strip()
            or "Seerr",
            "media_server_type": _MEDIA_SERVER_TYPES.get(
                _safe_int(settings.get("mediaServerType")), "unknown"
            ),
        }
    )
    return out


def _safe_int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_host(url: str) -> str:
    from urllib.parse import urlsplit

    try:
        return str(urlsplit(url).hostname or "")
    except ValueError:
        return ""
