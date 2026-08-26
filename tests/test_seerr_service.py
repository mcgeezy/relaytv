# SPDX-License-Identifier: GPL-3.0-only
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import pytest

from relaytv_app.integrations import seerr_service, seerr_sessions
from relaytv_app.integrations.seerr_client import (
    SeerrBinaryResponse,
    SeerrConfig,
    SeerrError,
    SeerrJsonResponse,
)


class _Config:
    enabled = True
    configured = True
    configuration_error = ""
    server_url = "https://seerr.example"
    api_key = "secret"
    shared_requests_enabled = False
    request_user_id = None
    request_mode = "disabled"


@pytest.fixture(autouse=True)
def _clear_request_metadata_cache():
    seerr_service._clear_request_metadata_cache()
    yield
    seerr_service._clear_request_metadata_cache()


def _install_client(monkeypatch, responses: dict[str, object], *, config=None):
    calls = []
    selected_config = config or _Config()

    class _Client:
        def __init__(self, current_config):
            assert current_config is selected_config

        def get(self, path, **kwargs):
            calls.append((path, kwargs))
            return responses[path]

        def get_binary(self, path, **kwargs):
            calls.append((path, kwargs))
            return responses[path]

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: selected_config)
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)
    return calls


def test_discover_normalizes_cards_and_omits_people(monkeypatch) -> None:
    calls = _install_client(
        monkeypatch,
        {
            "/discover/trending": {
                "page": 2,
                "totalPages": 9,
                "totalResults": 170,
                "results": [
                    {
                        "id": 11,
                        "mediaType": "movie",
                        "title": "Example",
                        "originalTitle": "Original Example",
                        "releaseDate": "2025-07-02",
                        "overview": "Story",
                        "posterPath": "/poster.jpg",
                        "backdropPath": "/backdrop.jpg",
                        "voteAverage": 7.86,
                        "mediaInfo": {
                            "status": 5,
                            "requests": [{"id": 8, "status": 2, "is4k": False}],
                        },
                    },
                    {"id": 12, "mediaType": "person", "name": "Not a media card"},
                ],
            }
        },
    )

    result = seerr_service.discover("trending", 2)

    assert calls == [("/discover/trending", {"query": {"page": 2}})]
    assert result["page"] == 2
    assert result["total_pages"] == 9
    assert result["results"] == [
        {
            "media_type": "movie",
            "media_id": 11,
            "title": "Example",
            "original_title": "Original Example",
            "date": "2025-07-02",
            "year": 2025,
            "overview": "Story",
            "poster_url": "/seerr/image/w342/poster.jpg",
            "backdrop_url": "/seerr/image/w780/backdrop.jpg",
            "rating": 7.9,
            "media_status": "available",
            "request": {"request_id": 8, "status": "approved", "is_4k": False},
            "playback_available": False,
        }
    ]


def test_search_bounds_query_and_maps_unknown_future_status(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        {
            "/search": {
                "results": [
                    {
                        "id": 22,
                        "mediaType": "tv",
                        "name": "Series",
                        "mediaInfo": {"status": 999},
                    }
                ]
            }
        },
    )

    assert seerr_service.search(" Series ", 1)["results"][0]["media_status"] == "unknown"

    try:
        seerr_service.search(" ", 1)
    except Exception as exc:
        assert getattr(exc, "status_code", None) == 400
    else:
        raise AssertionError("blank search should fail")


def test_tv_detail_is_a_small_stable_product_model(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        {
            "/tv/44": {
                "id": 44,
                "name": "Series",
                "originalName": "Original Series",
                "firstAirDate": "2024-01-12",
                "episodeRunTime": [47],
                "genres": [{"id": 18, "name": "Drama"}],
                "seasons": [
                    {
                        "seasonNumber": 1,
                        "name": "Season 1",
                        "episodeCount": 8,
                        "airDate": "2024-01-12",
                        "posterPath": "/season.jpg",
                        "episodes": [{"name": "Must not escape"}],
                    }
                ],
                "credits": {"cast": [{"email": "must-not-escape@example.com"}]},
            }
        },
    )

    result = seerr_service.item_detail("tv", 44)

    assert result["runtime_minutes"] == 47
    assert result["genres"] == [{"id": 18, "name": "Drama"}]
    assert result["seasons"] == [
        {
            "season_number": 1,
            "name": "Season 1",
            "episode_count": 8,
            "air_date": "2024-01-12",
            "poster_url": "/seerr/image/w342/season.jpg",
        }
    ]
    assert "must-not-escape" not in str(result)


def test_detail_exposes_playback_only_after_exact_jellyfin_validation(monkeypatch) -> None:
    calls = []
    _install_client(
        monkeypatch,
        {
            "/movie/329865": {
                "id": 329865,
                "mediaType": "movie",
                "title": "Arrival",
                "mediaInfo": {
                    "status": 5,
                    "jellyfinMediaId": "jellyfin-item-1",
                },
            }
        },
    )
    monkeypatch.setattr(
        seerr_service.jellyfin_service,
        "validate_external_item",
        lambda item_id, **kwargs: calls.append((item_id, kwargs))
        or {
            "item_id": item_id,
            "media_type": kwargs["media_type"],
            "tmdb_id": kwargs["tmdb_id"],
            "generation": 7,
        },
    )

    result = seerr_service.item_detail("movie", 329865)

    assert result["playback_available"] is True
    assert result["playback"] == {
        "provider": "jellyfin",
        "media_type": "movie",
        "media_id": 329865,
    }
    assert "jellyfin-item-1" not in str(result)
    assert calls == [
        ("jellyfin-item-1", {"media_type": "movie", "tmdb_id": 329865})
    ]


def test_detail_keeps_mismatched_jellyfin_item_request_only(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        {
            "/movie/329865": {
                "id": 329865,
                "mediaType": "movie",
                "title": "Arrival",
                "mediaInfo": {"status": 5, "jellyfinMediaId": "wrong-item"},
            }
        },
    )
    monkeypatch.setattr(
        seerr_service.jellyfin_service, "validate_external_item", lambda *args, **kwargs: {}
    )

    result = seerr_service.item_detail("movie", 329865)

    assert result["playback_available"] is False
    assert "playback" not in result


def test_request_list_uses_only_configured_shared_attribution(monkeypatch) -> None:
    class _SharedConfig(_Config):
        shared_requests_enabled = True
        request_user_id = 17

    calls = _install_client(
        monkeypatch,
        {
            "/request": {
                "pageInfo": {"page": 1, "pages": 2, "results": 30},
                "results": [
                    {
                        "id": 91,
                        "status": 1,
                        "is4k": True,
                        "createdAt": "2026-08-23T10:00:00Z",
                        "media": {
                            "mediaType": "movie",
                            "tmdbId": 55,
                            "status": 3,
                            "requests": [{"requestedBy": {"email": "private@example.com"}}],
                        },
                        "requestedBy": {"email": "private@example.com"},
                    }
                ],
            },
            "/movie/55": {
                "id": 55,
                "mediaType": "movie",
                "title": "Request Title",
                "originalTitle": "Original Request Title",
                "releaseDate": "2025-03-14",
                "posterPath": "/request-poster.jpg",
                "backdropPath": "/request-backdrop.jpg",
                "voteAverage": 8.24,
                "credits": {"cast": [{"email": "private@example.com"}]},
            },
        },
        config=_SharedConfig(),
    )

    result = seerr_service.list_requests(take=20, skip=0, status_filter="pending")

    assert calls[0][1]["query"]["requestedBy"] == 17
    assert result["results"][0] == {
        "request_id": 91,
        "status": "pending",
        "media_type": "movie",
        "media_id": 55,
        "media_status": "processing",
        "is_4k": True,
        "created_at": "2026-08-23T10:00:00Z",
        "updated_at": "",
        "title": "Request Title",
        "original_title": "Original Request Title",
        "date": "2025-03-14",
        "year": 2025,
        "poster_url": "/seerr/image/w342/request-poster.jpg",
        "backdrop_url": "/seerr/image/w780/request-backdrop.jpg",
        "rating": 8.2,
    }
    assert [call[0] for call in calls] == ["/request", "/movie/55"]
    assert "private@example.com" not in str(result)


def test_request_metadata_is_deduplicated_and_cached(monkeypatch) -> None:
    calls = _install_client(
        monkeypatch,
        {
            "/request": {
                "pageInfo": {"page": 1, "pages": 1, "results": 2},
                "results": [
                    {
                        "id": request_id,
                        "status": 1,
                        "media": {"mediaType": "tv", "tmdbId": 44, "status": 2},
                    }
                    for request_id in (1, 2)
                ],
            },
            "/tv/44": {
                "id": 44,
                "name": "Cached Series",
                "firstAirDate": "2024-01-12",
                "posterPath": "/series.jpg",
            },
        },
    )

    first = seerr_service.list_requests(take=20, skip=0, status_filter="all")
    second = seerr_service.list_requests(take=20, skip=0, status_filter="all")

    assert [item["title"] for item in first["results"]] == [
        "Cached Series",
        "Cached Series",
    ]
    assert [item["poster_url"] for item in second["results"]] == [
        "/seerr/image/w342/series.jpg",
        "/seerr/image/w342/series.jpg",
    ]
    assert [path for path, _kwargs in calls].count("/tv/44") == 1


def test_request_metadata_failure_keeps_other_cards_usable(monkeypatch) -> None:
    config = _Config()

    class _Client:
        def __init__(self, current_config):
            assert current_config is config

        def get(self, path, **kwargs):
            if path == "/request":
                return {
                    "pageInfo": {"page": 1, "pages": 1, "results": 2},
                    "results": [
                        {
                            "id": 1,
                            "status": 1,
                            "media": {"mediaType": "movie", "tmdbId": 11, "status": 2},
                        },
                        {
                            "id": 2,
                            "status": 1,
                            "media": {"mediaType": "movie", "tmdbId": 22, "status": 2},
                        },
                    ],
                }
            if path == "/movie/11":
                return {"id": 11, "title": "Recognizable Movie"}
            raise SeerrError(
                "seerr_timeout", "Seerr timed out", status_code=504
            )

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: config)
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    result = seerr_service.list_requests(take=20, skip=0, status_filter="all")

    assert result["results"][0]["title"] == "Recognizable Movie"
    assert "title" not in result["results"][1]
    assert [item["media_id"] for item in result["results"]] == [11, 22]


def test_image_uses_only_allowlisted_tmdb_proxy_path(monkeypatch) -> None:
    image = SeerrBinaryResponse(
        content=b"image",
        content_type="image/jpeg",
        cache_control="public, max-age=7200",
        etag='"abc"',
        last_modified="",
    )
    calls = _install_client(
        monkeypatch,
        {"/imageproxy/tmdb/t/p/w342/poster.jpg": image},
    )

    assert seerr_service.image("w342", "poster.jpg") is image
    assert calls == [("/imageproxy/tmdb/t/p/w342/poster.jpg", {"auth": False})]

    for unsafe in ("../secret", "https://attacker.example/a.jpg", "folder/a.jpg"):
        try:
            seerr_service.image("w342", unsafe)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
        else:
            raise AssertionError(f"unsafe image path should fail: {unsafe}")


@pytest.mark.parametrize(
    "content_type",
    ["image/svg+xml", "image/gif", "text/html", "application/octet-stream", ""],
)
def test_image_rejects_active_or_unexpected_content_types(
    monkeypatch, content_type: str
) -> None:
    image = SeerrBinaryResponse(
        content=b"untrusted",
        content_type=content_type,
        cache_control="public, max-age=7200",
        etag="",
        last_modified="",
    )
    _install_client(
        monkeypatch,
        {"/imageproxy/tmdb/t/p/w342/poster.jpg": image},
    )

    with pytest.raises(SeerrError) as exc_info:
        seerr_service.image("w342", "poster.jpg")

    assert exc_info.value.code == "seerr_invalid_response"
    assert exc_info.value.status_code == 502


@pytest.mark.parametrize(
    ("content_type", "expected_content_type"),
    [
        ("image/jpeg", "image/jpeg"),
        ("image/jpg", "image/jpeg"),
        ("image/png", "image/png"),
        ("image/webp", "image/webp"),
    ],
)
def test_image_accepts_only_allowlisted_raster_content_types(
    monkeypatch, content_type: str, expected_content_type: str
) -> None:
    image = SeerrBinaryResponse(
        content=b"raster",
        content_type=content_type,
        cache_control="public, max-age=7200",
        etag="",
        last_modified="",
    )
    _install_client(
        monkeypatch,
        {"/imageproxy/tmdb/t/p/w342/poster.jpg": image},
    )

    result = seerr_service.image("w342", "poster.jpg")

    assert result.content == image.content
    assert result.content_type == expected_content_type
    assert result.cache_control == image.cache_control


def test_image_requests_seerr_root_proxy_with_tmdb_asset_prefix(monkeypatch) -> None:
    expected_path = "/imageproxy/tmdb/t/p/w342/poster.jpg"
    requests: list[tuple[str, str | None, str | None]] = []

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            requests.append(
                (
                    self.path,
                    self.headers.get("X-Api-Key"),
                    self.headers.get("Accept"),
                )
            )
            if self.path != expected_path:
                self.send_error(404)
                return
            body = b"jpeg"
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "public, max-age=60")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        host, port = server.server_address
        config = SeerrConfig(
            enabled=True,
            server_url=f"http://{host}:{port}",
            api_key="server-secret",
            shared_requests_enabled=False,
            request_user_id=None,
        )
        monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: config)

        image = seerr_service.image("w342", "poster.jpg")
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)

    assert image.content == b"jpeg"
    assert image.content_type == "image/jpeg"
    assert requests == [(expected_path, None, "image/*")]


def test_user_selector_records_are_sanitized(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        {
            "/user": {
                "results": [
                    {
                        "id": 7,
                        "displayName": "Living Room",
                        "username": "mark",
                        "email": "private@example.com",
                        "permissions": 2,
                        "jellyfinToken": "never-return-this",
                    }
                ]
            }
        },
    )

    result = seerr_service.list_users()

    assert result == [{"id": 7, "display_name": "Living Room", "username": "mark"}]
    assert "private@example.com" not in str(result)
    assert "never-return-this" not in str(result)


def test_shared_admin_request_uses_only_configured_attribution(monkeypatch) -> None:
    calls = []

    class _SharedConfig(_Config):
        shared_requests_enabled = True
        request_mode = "shared_admin"
        request_user_id = 17

    config = _SharedConfig()

    class _Client:
        def __init__(self, current_config):
            assert current_config is config

        def request_json_response(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return SeerrJsonResponse(
                data={
                    "id": 91,
                    "status": 2,
                    "is4k": False,
                    "media": {"mediaType": "tv", "tmdbId": 44, "status": 2},
                },
                status=201,
            )

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: config)
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    result = seerr_service.create_request(
        media_type="tv", media_id=44, seasons=[1, 2, 2], is_4k=False
    )

    assert calls == [
        (
            "POST",
            "/request",
            {
                "body": {
                    "mediaType": "tv",
                    "mediaId": 44,
                    "is4k": False,
                    "seasons": [1, 2],
                    "userId": 17,
                }
            },
        )
    ]
    assert result == {
        "created": True,
        "request": {
            "request_id": 91,
            "status": "approved",
            "media_type": "tv",
            "media_id": 44,
            "media_status": "pending",
            "is_4k": False,
        },
    }


def test_request_creation_is_rejected_when_policy_is_disabled(monkeypatch) -> None:
    _install_client(monkeypatch, {})

    try:
        seerr_service.create_request(
            media_type="movie", media_id=11, seasons=None, is_4k=False
        )
    except Exception as exc:
        assert getattr(exc, "code", "") == "seerr_requests_disabled"
        assert getattr(exc, "status_code", None) == 403
    else:
        raise AssertionError("disabled request policy should reject writes")


def test_no_requestable_seasons_is_a_successful_semantic_result(monkeypatch) -> None:
    class _SharedConfig(_Config):
        shared_requests_enabled = True
        request_mode = "shared_admin"

    config = _SharedConfig()

    class _Client:
        def __init__(self, current_config):
            assert current_config is config

        def request_json_response(self, method, path, **kwargs):
            return SeerrJsonResponse(data={"status": 202}, status=202)

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: config)
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    result = seerr_service.create_request(
        media_type="tv", media_id=44, seasons="all", is_4k=False
    )

    assert result == {
        "created": False,
        "reason": "no_requestable_seasons",
        "media_type": "tv",
        "media_id": 44,
    }


def test_caller_request_uses_session_cookie_without_admin_attribution(monkeypatch) -> None:
    calls = []

    class _CallerConfig(_Config):
        api_key = "administrator-secret"
        request_mode = "caller_session"
        request_user_id = 17

    config = _CallerConfig()
    session = seerr_sessions.CallerSession(
        cookie="connect.sid=caller-secret",
        server_url=config.server_url,
        identity={"id": 9, "display_name": "Caller", "username": "caller"},
        expires_at=999999,
    )

    class _Client:
        def __init__(self, current_config, **kwargs):
            calls.append(("client", kwargs))

        def request_json_response(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            return SeerrJsonResponse(
                data={
                    "id": 92,
                    "status": 1,
                    "media": {"mediaType": "movie", "tmdbId": 11, "status": 2},
                },
                status=201,
            )

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: config)
    monkeypatch.setattr(seerr_service.seerr_sessions, "resolve", lambda session_id: session)
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    result = seerr_service.create_request(
        media_type="movie",
        media_id=11,
        seasons=None,
        is_4k=False,
        session_id="opaque",
    )

    assert calls[0] == ("client", {"session_cookie": "connect.sid=caller-secret"})
    assert calls[1][2]["body"] == {
        "mediaType": "movie",
        "mediaId": 11,
        "is4k": False,
    }
    assert result["created"] is True


def test_caller_mode_without_session_never_falls_back_to_admin_key(monkeypatch) -> None:
    class _CallerConfig(_Config):
        api_key = "administrator-secret"
        request_mode = "caller_session"

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: _CallerConfig())
    monkeypatch.setattr(
        seerr_service.seerr_sessions, "resolve", lambda session_id: None
    )
    monkeypatch.setattr(
        seerr_service,
        "SeerrClient",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("administrator client must not be constructed")
        ),
    )

    with pytest.raises(SeerrError) as exc_info:
        seerr_service.discover("trending", 1, session_id="missing")

    assert exc_info.value.code == "seerr_session_required"
    assert exc_info.value.status_code == 401


def test_expired_caller_status_retires_session_and_disables_writes(monkeypatch) -> None:
    class _CallerConfig(_Config):
        api_key = ""
        request_mode = "caller_session"

    config = _CallerConfig()
    session = seerr_sessions.CallerSession(
        cookie="connect.sid=expired",
        server_url=config.server_url,
        identity={"id": 9, "display_name": "Caller", "username": "caller"},
        expires_at=999999,
    )
    retired = []

    class _Client:
        def __init__(self, current_config, **kwargs):
            assert current_config is config
            assert kwargs == {"session_cookie": "connect.sid=expired"}

        def get(self, path, **kwargs):
            if path == "/status":
                return {"version": "3.4.1"}
            raise SeerrError(
                "seerr_session_expired",
                "Your Seerr session has expired; connect again",
                status_code=401,
            )

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: config)
    monkeypatch.setattr(
        seerr_service.seerr_sessions,
        "status",
        lambda session_id: {"connected": True, "identity": session.identity},
    )
    monkeypatch.setattr(
        seerr_service.seerr_sessions, "resolve", lambda session_id: session
    )
    monkeypatch.setattr(
        seerr_service.seerr_sessions,
        "retire",
        lambda session_id: retired.append(session_id),
    )
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    result = seerr_service.integration_status(session_id="opaque")

    assert result["caller_connected"] is False
    assert result["writes_allowed"] is False
    assert "caller_identity" not in result
    assert result["error"]["code"] == "seerr_session_expired"
    assert retired == ["opaque"]


def test_seerr_playback_revalidates_and_uses_jellyfin_command_sink(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        {
            "/movie/329865": {
                "id": 329865,
                "mediaType": "movie",
                "mediaInfo": {"jellyfinMediaId": "jellyfin-item-1"},
            }
        },
    )
    validated = {
        "item_id": "jellyfin-item-1",
        "media_type": "movie",
        "tmdb_id": 329865,
        "generation": 7,
    }
    dispatched = []
    monkeypatch.setattr(
        seerr_service.jellyfin_service,
        "validate_external_item",
        lambda *args, **kwargs: dict(validated),
    )
    monkeypatch.setattr(
        seerr_service.jellyfin_service,
        "dispatch_external_item",
        lambda identity, **kwargs: dispatched.append((identity, kwargs))
        or {"ok": True, "action": "queue_only", "private_url": "must-not-escape"},
    )

    result = seerr_service.playback_action(
        media_type="movie",
        media_id=329865,
        command="play_last",
    )

    assert result == {
        "ok": True,
        "media_type": "movie",
        "media_id": 329865,
        "command": "play_last",
        "queued": True,
        "suppressed": False,
    }
    assert "jellyfin-item-1" not in str(result)
    assert "must-not-escape" not in str(result)
    assert len(dispatched) == 1
    assert dispatched[0][0] == validated
    assert dispatched[0][1]["command"] == "play_last"
    assert callable(dispatched[0][1]["guard"])
    assert dispatched[0][1]["guard"]() is True


def test_seerr_playback_rejects_unvalidated_item_without_dispatch(monkeypatch) -> None:
    _install_client(
        monkeypatch,
        {
            "/movie/329865": {
                "id": 329865,
                "mediaType": "movie",
                "mediaInfo": {"jellyfinMediaId": "wrong-item"},
            }
        },
    )
    dispatched = []
    monkeypatch.setattr(
        seerr_service.jellyfin_service, "validate_external_item", lambda *args, **kwargs: {}
    )
    monkeypatch.setattr(
        seerr_service.jellyfin_service,
        "dispatch_external_item",
        lambda *args, **kwargs: dispatched.append((args, kwargs)),
    )

    with pytest.raises(SeerrError) as exc_info:
        seerr_service.playback_action(
            media_type="movie", media_id=329865, command="play_now"
        )

    assert exc_info.value.code == "seerr_playback_unavailable"
    assert exc_info.value.status_code == 409
    assert dispatched == []


def test_seerr_playback_discards_result_after_configuration_change(monkeypatch) -> None:
    old_config = _Config()
    new_config = _Config()
    new_config.server_url = "https://replacement.example"
    configs = iter((old_config, new_config))
    validated = []

    class _Client:
        def __init__(self, config):
            assert config is old_config

        def get(self, path, **kwargs):
            return {
                "id": 329865,
                "mediaType": "movie",
                "mediaInfo": {"jellyfinMediaId": "jellyfin-item-1"},
            }

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: next(configs))
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)
    monkeypatch.setattr(
        seerr_service.jellyfin_service,
        "validate_external_item",
        lambda *args, **kwargs: validated.append((args, kwargs)),
    )

    with pytest.raises(SeerrError) as exc_info:
        seerr_service.playback_action(
            media_type="movie", media_id=329865, command="play_now"
        )

    assert exc_info.value.code == "seerr_playback_unavailable"
    assert validated == []
