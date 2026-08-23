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
    return {
        "enabled": config.enabled,
        "configured": config.configured,
        "reachable": False,
        "server_url": config.server_url,
        "version": "",
        "application_title": "Seerr",
        "media_server_type": "unknown",
        "auth_mode": "shared_api_key" if config.api_key else "none",
        "shared_requests_enabled": config.shared_requests_enabled,
        "writes_allowed": bool(config.configured and config.shared_requests_enabled),
        "request_user_id": config.request_user_id,
    }


def integration_status(*, probe: bool = True) -> dict[str, object]:
    config = SeerrConfig.current()
    out = _config_status(config)
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
        out.update(_probe(config))
        logger.info(
            "seerr_status operation=status host=%s latency_ms=%d result=ok",
            _safe_host(config.server_url),
            int((time.monotonic() - started) * 1000),
        )
        return out
    except SeerrError as exc:
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


def discover(section: str, page: int) -> dict[str, object]:
    path = _DISCOVER_PATHS.get(str(section or "").strip().lower())
    if path is None:
        raise _invalid("Unknown Seerr discovery section")
    page_number = _bounded_int(page, name="page", minimum=1, maximum=500)
    payload = _client().get(path, query={"page": page_number})
    return _normalize_media_page(payload, fallback_page=page_number)


def search(query: str, page: int) -> dict[str, object]:
    text = str(query or "").strip()
    if not text or len(text) > 200:
        raise _invalid("Search query must contain between 1 and 200 characters")
    page_number = _bounded_int(page, name="page", minimum=1, maximum=500)
    payload = _client().get("/search", query={"query": text, "page": page_number})
    return _normalize_media_page(payload, fallback_page=page_number)


def item_detail(media_type: str, media_id: int) -> dict[str, object]:
    kind = _media_type(media_type)
    tmdb_id = _bounded_int(media_id, name="media_id", minimum=1, maximum=2_147_483_647)
    payload = _client().get(f"/{kind}/{tmdb_id}")
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
    return item


def list_requests(
    *,
    take: int,
    skip: int,
    status_filter: str,
) -> dict[str, object]:
    take_number = _bounded_int(take, name="take", minimum=1, maximum=100)
    skip_number = _bounded_int(skip, name="skip", minimum=0, maximum=100_000)
    selected_filter = str(status_filter or "all").strip().lower()
    if selected_filter not in _REQUEST_FILTERS:
        raise _invalid("Unknown Seerr request filter")
    config, client = _client_with_config()
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


def image(size: str, image_path: str) -> SeerrBinaryResponse:
    selected_size = str(size or "").strip().lower()
    selected_path = str(image_path or "").strip().lstrip("/")
    if selected_size not in _IMAGE_SIZES or not _IMAGE_PATH_RE.fullmatch(selected_path):
        raise _invalid("Invalid Seerr image path or size")
    response = _client().get_binary(
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


def _client() -> SeerrClient:
    return _client_with_config()[1]


def _client_with_config() -> tuple[SeerrConfig, SeerrClient]:
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
    return config, SeerrClient(config)


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


def _probe(config: SeerrConfig, *, client: SeerrClient | None = None) -> dict[str, object]:
    active_client = client or SeerrClient(config)
    status = active_client.get("/status", query={"checkUpdateAvailable": False}, auth=False)
    settings = active_client.get("/settings/main")
    if not isinstance(status, dict) or not isinstance(settings, dict):
        raise SeerrError(
            "seerr_invalid_response",
            "Seerr returned an unexpected response shape",
            status_code=502,
        )
    return {
        "reachable": True,
        "version": str(status.get("version") or "").strip(),
        "application_title": str(settings.get("applicationTitle") or "Seerr").strip()
        or "Seerr",
        "media_server_type": _MEDIA_SERVER_TYPES.get(
            _safe_int(settings.get("mediaServerType")), "unknown"
        ),
    }


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
