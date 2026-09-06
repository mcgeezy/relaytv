# SPDX-License-Identifier: GPL-3.0-only
import base64
import pytest

from relaytv_app.integrations import plex_auth, plex_service
from relaytv_app.integrations.plex_client import PlexBinaryResponse, PlexError


class _CatalogClient:
    def __init__(self) -> None:
        self.calls = []

    def get(self, path, *, query=None, auth=True):
        self.calls.append((path, query, auth))
        if path == "/hubs":
            return {
                "MediaContainer": {
                    "Hub": [
                        {
                            "hubIdentifier": "home.recent",
                            "title": "Recently Added",
                            "more": True,
                            "Metadata": [
                                {
                                    "key": "/library/metadata/10",
                                    "ratingKey": "10",
                                    "type": "movie",
                                    "title": "A Movie",
                                    "year": 2026,
                                    "duration": 7200000,
                                    "viewOffset": 1800000,
                                    "thumb": "/library/metadata/10/thumb/1",
                                    "art": "/library/metadata/10/art/1",
                                },
                                {
                                    "key": "/library/metadata/music",
                                    "type": "album",
                                    "title": "Not video",
                                },
                            ],
                        }
                    ]
                }
            }
        if path == "/library/sections":
            return {
                "MediaContainer": {
                    "Directory": [
                        {"key": "2", "type": "movie", "title": "Movies"},
                        {"key": "3", "type": "show", "title": "Shows"},
                        {"key": "4", "type": "artist", "title": "Music"},
                    ]
                }
            }
        if path == "/library/sections/2/all":
            assert query["X-Plex-Container-Start"] == 0
            assert query["X-Plex-Container-Size"] == 1
            assert query["sort"] == "titleSort:asc"
            return {
                "MediaContainer": {
                    "offset": 0,
                    "totalSize": 2,
                    "Metadata": [
                        {
                            "key": "/library/metadata/10",
                            "type": "movie",
                            "title": "A Movie",
                            "year": 2026,
                            "thumb": "/library/metadata/10/thumb/1",
                        }
                    ],
                }
            }
        if path == "/library/metadata/10":
            return {
                "MediaContainer": {
                    "Metadata": [
                        {
                            "key": "/library/metadata/10",
                            "type": "movie",
                            "title": "A Movie",
                            "summary": "A safe summary.",
                            "Genre": [{"tag": "Drama"}],
                            "audienceRating": 8.2,
                        }
                    ]
                }
            }
        if path == "/library/metadata/20/children":
            return {
                "MediaContainer": {
                    "offset": 0,
                    "totalSize": 1,
                    "Metadata": [
                        {
                            "key": "/library/metadata/21",
                            "type": "season",
                            "title": "Season 1",
                            "parentTitle": "A Show",
                            "index": 1,
                        }
                    ],
                }
            }
        raise AssertionError((path, query, auth))

    def get_bytes(self, path, *, query=None, auth=True):
        self.calls.append((path, query, auth))
        assert path == "/library/metadata/10/thumb/1"
        assert query["width"] == 1000
        return PlexBinaryResponse(body=b"jpeg-data", content_type="image/jpeg")


class _Auth:
    def __init__(self) -> None:
        self.client = _CatalogClient()
        self.current = True
        self.session = plex_auth.PlexServerSession(
            client=self.client,
            account_id="account-7",
            machine_id="server-1",
            generation=3,
            reference_key=b"r" * 32,
        )

    def selected_server_session(self):
        return self.session

    def assert_server_session_current(self, session):
        assert session is self.session
        if not self.current:
            raise plex_auth.PlexAuthError(
                "plex_credentials_changed",
                "Plex account or server changed while the request was in progress",
                status_code=409,
            )


@pytest.fixture
def service():
    auth = _Auth()
    return plex_service.PlexCatalogService(auth), auth


def test_home_normalizes_video_and_keeps_upstream_paths_private(service) -> None:
    catalog, _auth = service

    result = catalog.home(limit=10)

    assert result["server"] == "server-1"
    assert result["rows"][0]["title"] == "Recently Added"
    assert len(result["rows"][0]["items"]) == 1
    item = result["rows"][0]["items"][0]
    assert item["title"] == "A Movie"
    assert item["progress"] == 25.0
    assert item["poster_url"].startswith("/plex/artwork/")
    assert "ratingKey" not in repr(result)
    assert "/library/metadata/10" not in repr(result)
    encoded = str(item["id"])
    decoded = base64.urlsafe_b64decode(encoded + ("=" * (-len(encoded) % 4)))
    assert b"/library/metadata/10" not in decoded


def test_library_detail_children_and_artwork_use_scoped_references(service) -> None:
    catalog, _auth = service
    libraries = catalog.libraries()["libraries"]
    assert [(item["title"], item["type"]) for item in libraries] == [
        ("Movies", "movie"),
        ("Shows", "show"),
    ]

    page = catalog.library_items(
        libraries[0]["id"],
        start=0,
        limit=1,
        sort="title",
    )
    assert page["count"] == 2
    assert page["next_start"] == 1
    detail = catalog.item_detail(page["items"][0]["id"])
    assert detail["item"]["summary"] == "A safe summary."
    assert detail["item"]["genres"] == ["Drama"]

    show_id = catalog._reference(
        _auth.session,
        "item",
        "/library/metadata/20",
        "show",
    )
    children = catalog.children(show_id, start=0, limit=10)
    assert children["items"][0]["type"] == "season"
    assert children["items"][0]["children_available"] is True

    artwork_id = page["items"][0]["poster_url"].removeprefix("/plex/artwork/")
    artwork = catalog.artwork(artwork_id)
    assert artwork.body == b"jpeg-data"
    assert artwork.content_type == "image/jpeg"


def test_reference_tampering_and_account_switch_are_rejected(service) -> None:
    catalog, auth = service
    library_id = catalog.libraries()["libraries"][0]["id"]

    with pytest.raises(PlexError) as tampered:
        catalog.library_items(
            f"{'A' if library_id[0] != 'A' else 'B'}{library_id[1:]}",
            start=0,
            limit=10,
            sort="title",
        )
    assert tampered.value.code == "plex_invalid_reference"

    auth.session = plex_auth.PlexServerSession(
        client=auth.client,
        account_id="account-8",
        machine_id="server-1",
        generation=4,
        reference_key=b"r" * 32,
    )
    with pytest.raises(PlexError) as switched:
        catalog.library_items(
            library_id,
            start=0,
            limit=10,
            sort="title",
        )
    assert switched.value.code == "plex_invalid_reference"


def test_catalog_rejects_results_if_settings_change_during_fetch(service) -> None:
    catalog, auth = service
    original_get = auth.client.get

    def changing_get(path, *, query=None, auth=True):
        result = original_get(path, query=query, auth=auth)
        auth_manager.current = False
        return result

    auth_manager = auth
    auth.client.get = changing_get

    with pytest.raises(PlexError) as exc_info:
        catalog.home()
    assert exc_info.value.code == "plex_credentials_changed"
