# SPDX-License-Identifier: GPL-3.0-only
from relaytv_app.integrations import seerr_service
from relaytv_app.integrations.seerr_client import SeerrBinaryResponse, SeerrJsonResponse


class _Config:
    enabled = True
    configured = True
    configuration_error = ""
    server_url = "https://seerr.example"
    api_key = "secret"
    shared_requests_enabled = False
    request_user_id = None
    request_mode = "disabled"


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
            }
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
    }
    assert "private@example.com" not in str(result)


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
        {"/imageproxy/tmdb/w342/poster.jpg": image},
    )

    assert seerr_service.image("w342", "poster.jpg") is image
    assert calls == [("/imageproxy/tmdb/w342/poster.jpg", {"auth": False})]

    for unsafe in ("../secret", "https://attacker.example/a.jpg", "folder/a.jpg"):
        try:
            seerr_service.image("w342", unsafe)
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 400
        else:
            raise AssertionError(f"unsafe image path should fail: {unsafe}")


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
