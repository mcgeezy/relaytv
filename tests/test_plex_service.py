# SPDX-License-Identifier: GPL-3.0-only
import base64
import pytest

from relaytv_app.integrations import plex_auth, plex_service
from relaytv_app.integrations.plex_client import (
    PlexBinaryResponse,
    PlexError,
    PlexStreamResponse,
)


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
                            "duration": 7200000,
                            "viewOffset": 1800000,
                            "thumb": "/library/metadata/10/thumb/1",
                            "Media": [
                                {
                                    "container": "mp4",
                                    "videoCodec": "h264",
                                    "audioCodec": "aac",
                                    "Part": [
                                        {
                                            "key": "/library/parts/10/file.mp4",
                                            "accessible": True,
                                            "exists": True,
                                            "Stream": [
                                                {
                                                    "id": "101",
                                                    "streamType": 1,
                                                    "codec": "h264",
                                                },
                                                {
                                                    "id": "201",
                                                    "streamType": 2,
                                                    "codec": "aac",
                                                    "language": "English",
                                                    "languageCode": "eng",
                                                    "extendedDisplayTitle": "English (AAC Stereo)",
                                                    "channels": 2,
                                                    "default": True,
                                                },
                                                {
                                                    "id": "202",
                                                    "streamType": 2,
                                                    "codec": "ac3",
                                                    "language": "Spanish",
                                                    "languageCode": "spa",
                                                    "extendedDisplayTitle": "Spanish (AC3 5.1)",
                                                    "channels": 6,
                                                },
                                                {
                                                    "id": "301",
                                                    "streamType": 3,
                                                    "codec": "srt",
                                                    "language": "English",
                                                    "languageCode": "eng",
                                                    "extendedDisplayTitle": "English (SRT)",
                                                },
                                            ],
                                        }
                                    ],
                                },
                                {
                                    "container": "mp4",
                                    "videoResolution": "720",
                                    "videoCodec": "h264",
                                    "audioCodec": "aac",
                                    "Part": [
                                        {
                                            "key": "/library/parts/10/alternate.mp4",
                                            "accessible": True,
                                            "exists": True,
                                            "Stream": [
                                                {
                                                    "id": "401",
                                                    "streamType": 2,
                                                    "codec": "aac",
                                                    "language": "English",
                                                    "languageCode": "eng",
                                                }
                                            ],
                                        }
                                    ],
                                },
                            ],
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
        if path == plex_service.TRANSCODE_DECISION_PATH:
            if query.get("directPlay") == 1 and not query.get("maxVideoBitrate"):
                return {"MediaContainer": {"directPlayDecisionCode": 1000}}
            return {
                "MediaContainer": {
                    "directPlayDecisionCode": 3000,
                    "Metadata": [
                        {
                            "Media": [
                                {
                                    "Part": [
                                        {
                                            "decision": "transcode",
                                            "Stream": [
                                                {
                                                    "streamType": 1,
                                                    "decision": "transcode",
                                                },
                                                {
                                                    "streamType": 2,
                                                    "decision": "transcode",
                                                },
                                            ],
                                        }
                                    ]
                                }
                            ]
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

    def open_stream(
        self,
        path,
        *,
        range_header="",
        query=None,
        on_close=None,
        allow_incomplete_read=False,
    ):
        self.calls.append((path, range_header, query, True))

        class _Response:
            def __init__(self):
                self.chunks = [b"media", b""]
                self.closed = False

            def read(self, _size):
                return self.chunks.pop(0)

            def close(self):
                self.closed = True

        return PlexStreamResponse(
            status_code=206,
            headers={"Content-Type": "video/mp4", "Content-Range": "bytes 0-4/9"},
            _response=_Response(),
            _on_close=on_close,
            _allow_incomplete_read=allow_incomplete_read,
        )

    def request_no_content(self, method, path, *, query=None, auth=True):
        self.calls.append((method, path, query, auth))


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
    assert [version["label"] for version in detail["item"]["versions"]] == [
        "MP4 · H264",
        "720 · MP4 · H264",
    ]
    assert "/library/parts/" not in repr(detail)

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


def test_playback_reference_resolves_to_private_range_stream(service) -> None:
    catalog, auth = service
    item_id = catalog.home()["rows"][0]["items"][0]["id"]

    durable = catalog.durable_item(item_id)
    resolved = catalog.resolve_playback_item(durable)

    assert durable["url"] == "https://plex.invalid/item"
    assert durable["resume_pos"] == 1800.0
    assert resolved["url"].startswith("http://127.0.0.1:8787/plex/stream/")
    assert resolved["plex_stream_mode"] == "direct"
    assert resolved["plex_container"] == "mp4"
    assert "/library/parts/" not in repr(resolved)
    assert "token" not in repr(resolved).lower()

    stream_id = resolved["url"].rsplit("/", 1)[-1]
    stream = catalog.media_stream(stream_id, range_header="bytes=0-4")
    assert b"".join(stream.iter_bytes()) == b"media"
    assert auth.client.calls[-1] == (
        "/library/parts/10/file.mp4",
        "bytes=0-4",
        None,
        True,
    )


def test_forced_transcode_reuses_decision_session_and_cleans_up(
    service,
    monkeypatch,
) -> None:
    catalog, auth = service
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "transcode"},
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]

    resolved = catalog.resolve_playback_item(
        catalog.durable_item(item_id),
        start_pos=61.25,
    )

    assert resolved["plex_stream_mode"] == "transcode"
    assert "/video/:/transcode/" not in resolved["url"]
    stream_id = resolved["url"].rsplit("/", 1)[-1]
    reference = catalog._resolve(
        auth.session,
        stream_id,
        expected_kind="transcode_stream",
    )
    parsed = plex_service.urllib.parse.urlsplit(reference["path"])
    start_query = dict(plex_service.urllib.parse.parse_qsl(parsed.query))
    decision_call = next(
        call for call in auth.client.calls if call[0] == plex_service.TRANSCODE_DECISION_PATH
    )
    assert parsed.path == plex_service.TRANSCODE_START_PATH
    assert start_query["session"] == decision_call[1]["session"]
    assert start_query["offset"] == "61.25"
    assert start_query["directPlay"] == "0"

    stream = catalog.media_stream(stream_id, range_header="")
    assert stream._allow_incomplete_read is True
    assert b"".join(stream.iter_bytes()) == b"media"
    assert auth.client.calls[-1] == (
        "GET",
        plex_service.TRANSCODE_STOP_PATH,
        {"session": start_query["session"]},
        True,
    )


def test_forced_transcode_ignores_downstream_byte_range(service, monkeypatch) -> None:
    catalog, _auth = service
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "transcode"},
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    resolved = catalog.resolve_playback_item(catalog.durable_item(item_id))

    stream = catalog.media_stream(
        resolved["url"].rsplit("/", 1)[-1],
        range_header="bytes=0-4",
    )
    assert b"".join(stream.iter_bytes()) == b"media"
    start_call = next(
        call for call in _auth.client.calls if call[0] == plex_service.TRANSCODE_START_PATH
    )
    assert start_call[1] == ""


def test_transcode_stream_accepts_fresh_equivalent_server_session(
    service,
    monkeypatch,
) -> None:
    catalog, auth = service
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "transcode"},
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    resolved = catalog.resolve_playback_item(catalog.durable_item(item_id))
    original = auth.session
    fresh_client = _CatalogClient()

    def fresh_session():
        return plex_auth.PlexServerSession(
            client=fresh_client,
            account_id=original.account_id,
            machine_id=original.machine_id,
            generation=original.generation,
            reference_key=original.reference_key,
        )

    monkeypatch.setattr(auth, "selected_server_session", fresh_session)
    monkeypatch.setattr(auth, "assert_server_session_current", lambda _session: None)

    stream = catalog.media_stream(
        resolved["url"].rsplit("/", 1)[-1],
        range_header="",
    )

    assert b"".join(stream.iter_bytes()) == b"media"


def test_automatic_quality_cap_uses_media_decision_transcode(service, monkeypatch) -> None:
    catalog, auth = service
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "auto", "plex_max_bitrate": 4000},
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]

    resolved = catalog.resolve_playback_item(catalog.durable_item(item_id))

    assert resolved["plex_stream_mode"] == "transcode"
    decision = next(
        call for call in auth.client.calls if call[0] == plex_service.TRANSCODE_DECISION_PATH
    )
    assert decision[1]["directPlay"] == 1
    assert decision[1]["maxVideoBitrate"] == 4000
    assert "name=video.bitrate&value=4000" in decision[1][
        "X-Plex-Client-Profile-Extra"
    ]
    assert plex_service.stop_transcode_stream(resolved["url"]) is True


def test_automatic_mode_transcodes_media_outside_runtime_profile(
    service,
    monkeypatch,
) -> None:
    catalog, auth = service
    original_get = auth.client.get

    def av1_media(path, *, query=None, auth=True):
        payload = original_get(path, query=query, auth=auth)
        if path == "/library/metadata/10":
            media = payload["MediaContainer"]["Metadata"][0]["Media"][0]
            media.update({"videoCodec": "av1", "height": 2160})
        return payload

    auth.client.get = av1_media
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "auto"},
    )
    monkeypatch.setattr(
        plex_service.video_profile,
        "get_profile",
        lambda: {
            "decode_profile": "intel_amd64_qsv",
            "av1_allowed": False,
            "display_cap_height": 2160,
        },
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]

    resolved = catalog.resolve_playback_item(catalog.durable_item(item_id))

    assert resolved["plex_stream_mode"] == "transcode"
    decision = next(
        call for call in auth.client.calls if call[0] == plex_service.TRANSCODE_DECISION_PATH
    )
    assert decision[1]["directPlay"] == 0
    assert plex_service.stop_transcode_stream(resolved["url"]) is True


def test_media_decision_preserves_copy_only_remux_result(service) -> None:
    catalog, auth = service
    original_get = auth.client.get

    def remux_decision(path, *, query=None, auth=True):
        if path == plex_service.TRANSCODE_DECISION_PATH:
            return {
                "MediaContainer": {
                    "directPlayDecisionCode": 3000,
                    "Metadata": [
                        {
                            "Media": [
                                {
                                    "Part": [
                                        {
                                            "decision": "copy",
                                            "Stream": [
                                                {
                                                    "streamType": 1,
                                                    "decision": "copy",
                                                },
                                                {
                                                    "streamType": 2,
                                                    "decision": "copy",
                                                },
                                            ],
                                        }
                                    ]
                                }
                            ]
                        }
                    ],
                }
            }
        return original_get(path, query=query, auth=auth)

    auth.client.get = remux_decision
    item_id = catalog.home()["rows"][0]["items"][0]["id"]

    resolved = catalog.resolve_playback_item(catalog.durable_item(item_id))

    assert resolved["plex_stream_mode"] == "remux"
    assert plex_service.stop_transcode_stream(resolved["url"]) is True


def test_media_decision_accepts_directplay_part_without_top_level_code(service) -> None:
    catalog, auth = service
    auth.client.get = lambda path, *, query=None, auth=True: {
        "MediaContainer": {
            "Metadata": [{"Media": [{"Part": [{"decision": "directplay"}]}]}]
        }
    }

    assert catalog._playback_decision(
        auth.session,
        {"directPlay": 1},
    ) == "direct"


def test_selected_media_version_is_kept_separate_and_revalidated(service) -> None:
    catalog, _auth = service
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    detail = catalog.item_detail(item_id)["item"]
    second_version = detail["versions"][1]

    durable = catalog.durable_item(item_id, version_id=second_version["id"])
    resolved = catalog.resolve_playback_item(durable)

    assert durable["plex_part_id"] == second_version["id"]
    assert resolved["plex_container"] == "mp4"
    stream_id = resolved["url"].rsplit("/", 1)[-1]
    reference = catalog._resolve(_auth.session, stream_id, expected_kind="stream")
    assert reference["path"] == "/library/parts/10/alternate.mp4"


def test_track_choices_are_opaque_and_drive_plex_conversion(service, monkeypatch) -> None:
    catalog, auth = service
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "auto"},
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    detail = catalog.item_detail(item_id)["item"]
    version = detail["versions"][0]
    audio = version["audio_tracks"][1]
    subtitle = version["subtitle_tracks"][0]

    durable = catalog.durable_item(
        item_id,
        version_id=version["id"],
        audio_id=audio["id"],
        subtitle_id=subtitle["id"],
    )
    resolved = catalog.resolve_playback_item(durable)

    assert audio == {
        "id": audio["id"],
        "label": "Spanish (AC3 5.1)",
        "language": "Spanish",
        "language_code": "spa",
        "codec": "ac3",
        "channels": 6,
        "default": False,
        "forced": False,
    }
    assert durable["plex_audio_id"] == audio["id"]
    assert durable["plex_subtitle_id"] == subtitle["id"]
    assert resolved["plex_stream_mode"] == "transcode"
    decision = next(
        call for call in auth.client.calls if call[0] == plex_service.TRANSCODE_DECISION_PATH
    )
    assert decision[1]["directPlay"] == 0
    assert decision[1]["audioStreamID"] == "202"
    assert decision[1]["subtitleStreamID"] == "301"
    assert decision[1]["subtitles"] == "burn"
    assert decision[1]["advancedSubtitles"] == "text"
    assert "/library/streams/" not in repr(detail) + repr(durable) + repr(resolved)
    for opaque_id in (audio["id"], subtitle["id"]):
        decoded = base64.urlsafe_b64decode(
            opaque_id + ("=" * (-len(opaque_id) % 4))
        )
        assert b"/library/streams/" not in decoded
    assert plex_service.stop_transcode_stream(resolved["url"]) is True


def test_track_choice_is_revalidated_for_selected_media_version(service) -> None:
    catalog, _auth = service
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    versions = catalog.item_detail(item_id)["item"]["versions"]

    with pytest.raises(PlexError) as exc_info:
        catalog.durable_item(
            item_id,
            version_id=versions[1]["id"],
            audio_id=versions[0]["audio_tracks"][0]["id"],
        )

    assert exc_info.value.code == "plex_track_unavailable"
    assert exc_info.value.status_code == 409


def test_direct_play_mode_rejects_explicit_track_selection(service, monkeypatch) -> None:
    catalog, _auth = service
    monkeypatch.setattr(
        plex_service.state,
        "get_settings",
        lambda: {"plex_playback_mode": "direct"},
    )
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    version = catalog.item_detail(item_id)["item"]["versions"][0]
    with pytest.raises(PlexError) as exc_info:
        catalog.durable_item(
            item_id,
            audio_id=version["audio_tracks"][1]["id"],
        )

    assert exc_info.value.code == "plex_track_requires_transcode"
    assert exc_info.value.status_code == 400


def test_queued_track_is_rechecked_after_switching_to_direct_mode(
    service,
    monkeypatch,
) -> None:
    catalog, _auth = service
    settings = {"plex_playback_mode": "auto"}
    monkeypatch.setattr(plex_service.state, "get_settings", lambda: settings)
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    version = catalog.item_detail(item_id)["item"]["versions"][0]
    durable = catalog.durable_item(
        item_id,
        subtitle_id=version["subtitle_tracks"][0]["id"],
    )
    settings["plex_playback_mode"] = "direct"

    with pytest.raises(PlexError) as exc_info:
        catalog.resolve_playback_item(durable)

    assert exc_info.value.code == "plex_track_requires_transcode"


def test_playback_actions_use_durable_items_and_explicit_resume(service, monkeypatch) -> None:
    catalog, _auth = service
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    calls = []
    monkeypatch.setattr(
        plex_service.playback_service,
        "queue_item_next",
        lambda item: calls.append(("next", item)) or (2, [item]),
    )
    monkeypatch.setattr(
        plex_service.playback_service,
        "play_now",
        lambda item, **kwargs: calls.append(("play", item, kwargs)) or item,
    )

    queued = catalog.action(item_id, "play_next")
    resumed = catalog.action(item_id, "resume")

    assert queued["queue_length"] == 2
    assert calls[0][1]["url"] == "https://plex.invalid/item"
    assert calls[1][2]["start_pos"] == 1800.0
    assert calls[1][2]["use_resolver"] is False
    assert resumed["action"] == "resume"


def test_timeline_reports_milliseconds_from_encrypted_item_reference(service) -> None:
    catalog, auth = service
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    now = catalog.durable_item(item_id)

    assert catalog.report_timeline(
        now,
        playback_state="paused",
        position_sec=12.345,
        duration_sec=7200.0,
    ) is True

    method, path, query, authenticated = auth.client.calls[-1]
    assert (method, path, authenticated) == ("POST", "/:/timeline", True)
    assert query == {
        "ratingKey": "10",
        "key": "/library/metadata/10",
        "identifier": "com.plexapp.plugins.library",
        "state": "paused",
        "time": 12345,
        "duration": 7200000,
    }


def test_newer_timeline_state_retires_a_queued_progress_hint(monkeypatch) -> None:
    pending = []
    sent = []

    class _Thread:
        def __init__(self, *, target, **_kwargs):
            pending.append(target)

        def start(self):
            pass

    monkeypatch.setattr(plex_service.threading, "Thread", _Thread)
    monkeypatch.setattr(
        plex_service.catalog_service,
        "report_timeline",
        lambda _now, **kwargs: sent.append(kwargs["playback_state"]),
    )
    with plex_service._TIMELINE_LOCK:
        plex_service._TIMELINE_LAST.clear()
    now = {
        "provider": "plex",
        "plex_item_id": "opaque-item",
        "history_id": "playback-1",
        "resume_pos": 10.0,
    }

    assert plex_service.emit_timeline_hint(now, playback_state="playing") is True
    assert plex_service.emit_timeline_hint(now, playback_state="stopped") is True
    pending[0]()
    pending[1]()

    assert sent == ["stopped"]


def test_direct_play_rejects_items_without_an_accessible_part(service) -> None:
    catalog, auth = service
    item_id = catalog.home()["rows"][0]["items"][0]["id"]
    original_get = auth.client.get

    def without_media(path, *, query=None, auth=True):
        payload = original_get(path, query=query, auth=auth)
        if path == "/library/metadata/10":
            payload["MediaContainer"]["Metadata"][0].pop("Media")
        return payload

    auth.client.get = without_media
    with pytest.raises(PlexError) as exc_info:
        catalog.resolve_playback_item(catalog.durable_item(item_id))
    assert exc_info.value.code == "plex_media_unavailable"


# --- upstream path validation ----------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/library/parts/1/file.mkv",
        "/library/metadata/1?includeGuids=1",
        "/movies/Some..Title/x.mkv",
    ],
)
def test_upstream_paths_the_server_returns_are_accepted(path) -> None:
    assert plex_service._safe_upstream_path(path) == path


@pytest.mark.parametrize(
    "path",
    [
        "/library/../../etc/passwd",
        "/lib/%2e%2e/x",
        "/a/..%2Fb",
        "//evil.example/x",
        "http://evil.example/x",
        "/x?X-Plex-Token=secret",
    ],
)
def test_upstream_paths_that_leave_the_intended_shape_are_refused(path) -> None:
    """Not reachable through a minted reference, refused regardless.

    Keeping this a whole-path validator means it stays correct if a future
    caller ever hands it a path from somewhere less trusted.
    """
    assert plex_service._safe_upstream_path(path) == ""


# --- transcode reference lifetime -------------------------------------------


class _FakeSession:
    account_id = "a"
    machine_id = "m"
    generation = 1
    reference_key = b"k" * 32


def _entry(created_at: float, *, opened: bool) -> plex_service._TranscodeEntry:
    return plex_service._TranscodeEntry(
        session=_FakeSession(),
        session_id="s",
        created_at=created_at,
        opened=opened,
    )


@pytest.fixture
def transcode_registry():
    with plex_service._TRANSCODE_LOCK:
        original = dict(plex_service._TRANSCODE_SESSIONS)
        plex_service._TRANSCODE_SESSIONS.clear()
    yield plex_service._TRANSCODE_SESSIONS
    with plex_service._TRANSCODE_LOCK:
        plex_service._TRANSCODE_SESSIONS.clear()
        plex_service._TRANSCODE_SESSIONS.update(original)


def test_a_reference_that_was_never_opened_is_reclaimed(transcode_registry) -> None:
    """A play that resolves an item and then fails leaves the entry behind.

    Without a sweep it holds a server session until the process exits.
    """
    now = 10_000.0
    transcode_registry["abandoned"] = _entry(
        now - plex_service.TRANSCODE_UNOPENED_TTL_SEC - 1, opened=False
    )

    with plex_service._TRANSCODE_LOCK:
        dropped = plex_service._sweep_unopened_transcodes_locked(now)

    assert dropped == ["abandoned"]
    assert "abandoned" not in transcode_registry


def test_a_long_running_stream_is_never_swept(transcode_registry) -> None:
    """An opened reference belongs to a live stream, however long it runs."""
    now = 10_000.0
    transcode_registry["playing"] = _entry(now - 60 * 60 * 6, opened=True)

    with plex_service._TRANSCODE_LOCK:
        dropped = plex_service._sweep_unopened_transcodes_locked(now)

    assert dropped == []
    assert "playing" in transcode_registry


def test_a_reference_still_within_its_grace_period_survives(transcode_registry) -> None:
    now = 10_000.0
    transcode_registry["starting"] = _entry(now - 1, opened=False)

    with plex_service._TRANSCODE_LOCK:
        dropped = plex_service._sweep_unopened_transcodes_locked(now)

    assert dropped == []
    assert "starting" in transcode_registry
