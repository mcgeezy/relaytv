# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import pytest

from relaytv_app.config import runtime_config
from relaytv_app.integrations import iptv_service
from relaytv_app import player, state


PLAYLIST = """#EXTM3U
#EXTINF:-1 tvg-id="news.example" tvg-name="Example News" tvg-logo="https://img.example/logo.png" group-title="News",Example News
#EXTVLCOPT:http-user-agent=Example Agent
#EXTVLCOPT:http-referrer=https://ref.example/
https://catalog.example/list/streams/news.m3u8
#EXTINF:-1 group-title="Local",Local One
https://stream.example/local.m3u8
"""


@pytest.fixture
def iptv_tmp(monkeypatch, tmp_path):
    monkeypatch.setenv("RELAYTV_IPTV_DB_PATH", str(tmp_path / "iptv.sqlite3"))
    monkeypatch.setenv("RELAYTV_IPTV_ENABLED", "1")
    runtime_config.refresh_from_env()
    iptv_service.reset_store_for_tests()
    yield
    iptv_service.reset_store_for_tests()


def test_parse_m3u_reads_metadata_headers_and_relative_urls(iptv_tmp) -> None:
    relative = PLAYLIST.replace(
        "https://catalog.example/list/streams/news.m3u8", "streams/news.m3u8"
    )
    items = iptv_service.parse_m3u(relative, base_url="https://catalog.example/list/main.m3u")

    assert len(items) == 2
    assert items[0] == {
        "name": "Example News",
        "tvg_id": "news.example",
        "tvg_name": "Example News",
        "logo_url": "https://img.example/logo.png",
        "group_title": "News",
        "user_agent": "Example Agent",
        "referrer": "https://ref.example/",
        "stream_url": "https://catalog.example/list/streams/news.m3u8",
        "upstream_index": 0,
    }


def test_parse_m3u_rejects_hls_manifest_as_catalog(iptv_tmp) -> None:
    manifest = "#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXT-X-MEDIA-SEQUENCE:1\nsegment.ts\n"

    with pytest.raises(ValueError, match="HLS media/master"):
        iptv_service.parse_m3u(manifest, base_url="https://stream.example/live.m3u8")


def test_refresh_preserves_favorite_hidden_and_rank_across_url_rotation(iptv_tmp, monkeypatch) -> None:
    source = iptv_service.create_source(
        name="Test",
        content=PLAYLIST,
        refresh_interval_sec=3600,
    )
    first = iptv_service.refresh_source(str(source["id"]))
    assert first["active"] == 2

    catalog = iptv_service.list_channels(source_id=str(source["id"]))
    news = next(item for item in catalog["items"] if item["tvg_id"] == "news.example")
    iptv_service.update_channel(
        str(source["id"]), str(news["channel_id"]), {"favorite": True, "hidden": True}
    )

    rotated = PLAYLIST.replace("streams/news.m3u8", "streams/news-token-2.m3u8")
    raw_source = iptv_service.store().get_source(str(source["id"]))
    assert raw_source is not None
    monkeypatch.setattr(
        iptv_service,
        "_fetch_source",
        lambda _source: (rotated, "etag-2", "", False),
    )
    second = iptv_service.refresh_source(str(source["id"]))

    assert second["inserted"] == 0
    updated = iptv_service.store().get_channel(
        str(source["id"]), str(news["channel_id"]), redacted=True
    )
    assert updated is not None
    assert updated["favorite"] is True
    assert updated["hidden"] is True


def test_source_and_channel_public_payloads_hide_credentials(iptv_tmp) -> None:
    source = iptv_service.create_source(
        name="Secret",
        location="https://user:pass@example.test/list.m3u?token=secret",
        refresh_interval_sec=3600,
    )
    public = iptv_service.store().get_source(str(source["id"]), redacted=True)

    assert public is not None
    assert public["location_host"] == "example.test"
    assert public["location_configured"] is True
    assert "location" not in public


def test_availability_requires_three_failures_and_recovers(iptv_tmp) -> None:
    source = iptv_service.create_source(name="Test", content=PLAYLIST)
    iptv_service.refresh_source(str(source["id"]))
    item = iptv_service.list_channels(source_id=str(source["id"]))["items"][0]
    channel_id = str(item["channel_id"])

    one = iptv_service.store().mark_channel_check(str(source["id"]), channel_id, available=False)
    two = iptv_service.store().mark_channel_check(str(source["id"]), channel_id, available=False)
    three = iptv_service.store().mark_channel_check(str(source["id"]), channel_id, available=False)
    recovered = iptv_service.store().mark_channel_check(str(source["id"]), channel_id, available=True)

    assert one and one["availability"] == "suspect"
    assert two and two["availability"] == "suspect"
    assert three and three["availability"] == "unavailable"
    assert recovered and recovered["availability"] == "available"
    assert recovered["consecutive_failures"] == 0


def test_directory_is_searchable_and_never_returns_playlist_locations(iptv_tmp) -> None:
    results = iptv_service.directory("news")

    assert [item["id"] for item in results] == ["iptv-org-news"]
    assert all("location" not in item for item in results)


def test_channel_action_uses_playback_service_and_marks_success(iptv_tmp, monkeypatch) -> None:
    source = iptv_service.create_source(name="Test", content=PLAYLIST)
    iptv_service.refresh_source(str(source["id"]))
    item = iptv_service.list_channels(source_id=str(source["id"]))["items"][0]
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        iptv_service.playback_service,
        "play_now",
        lambda media, **kwargs: calls.append({"media": media, **kwargs}) or {"title": media["title"]},
    )

    result = iptv_service.channel_action(
        str(source["id"]), str(item["channel_id"]), "play_now"
    )

    assert result["ok"] is True
    assert calls[0]["use_resolver"] is False
    assert calls[0]["media"]["provider"] == "iptv"
    assert calls[0]["media"]["http_headers"]["User-Agent"] == "Example Agent"


def test_iptv_queue_persistence_keeps_only_opaque_catalog_reference(iptv_tmp) -> None:
    persisted = state._persistable_queue_item(
        {
            "provider": "iptv",
            "title": "Private channel",
            "url": "https://stream.example/customer-secret/live.m3u8?token=secret",
            "http_headers": {"Referer": "https://private.example/customer-secret"},
            "iptv_source_id": "source-1",
            "iptv_channel_id": "channel-1",
        }
    )

    assert persisted is not None
    assert persisted["url"] == "https://iptv.invalid/source-1/channel-1"
    assert persisted["iptv_source_id"] == "source-1"
    assert persisted["iptv_channel_id"] == "channel-1"
    assert "http_headers" not in persisted
    assert "secret" not in str(persisted)


def test_mpv_iptv_header_args_are_supported_and_redacted(iptv_tmp, monkeypatch) -> None:
    monkeypatch.setattr(player.state, "get_settings", lambda: {"volume": 75})
    monkeypatch.setattr(player, "_effective_audio_device", lambda settings=None: "")
    monkeypatch.setattr(player, "_x11_mode_active", lambda selected_mode=None: False)
    monkeypatch.setattr(player, "_x11_overlay_enabled", lambda: False)
    monkeypatch.setattr(player, "_provider_hint_for_stream", lambda *_a, **_k: "iptv")
    monkeypatch.setattr(player, "_should_force_ytdl_off", lambda *_a, **_k: False)
    monkeypatch.setattr(player, "_effective_ytdl_format", lambda *_a, **_k: "")

    args = player._build_mpv_args(
        "https://stream.example/live.m3u8",
        None,
        "x11",
        http_headers={"User-Agent": "Private Agent", "Referer": "https://private.example/"},
    )

    assert "--user-agent=Private Agent" in args
    assert "--referrer=https://private.example/" in args
    rendered = player._redacted_mpv_args(args)
    assert "Private Agent" not in rendered
    assert "private.example" not in rendered


def test_scheduled_checks_are_limited_to_favorites(iptv_tmp, monkeypatch) -> None:
    source = iptv_service.create_source(name="Test", content=PLAYLIST)
    iptv_service.refresh_source(str(source["id"]))
    channels = iptv_service.list_channels(source_id=str(source["id"]))["items"]
    favorite = channels[0]
    iptv_service.update_channel(
        str(source["id"]), str(favorite["channel_id"]), {"favorite": True}
    )
    checked: list[tuple[str, str]] = []
    monkeypatch.setattr(
        iptv_service,
        "check_channel",
        lambda source_id, channel_id: checked.append((source_id, channel_id)) or {},
    )

    iptv_service._check_due_favorites()

    assert checked == [(str(source["id"]), str(favorite["channel_id"]))]


def test_remove_unavailable_updates_source_channel_count(iptv_tmp) -> None:
    source = iptv_service.create_source(name="Test", content=PLAYLIST)
    sid = str(source["id"])
    iptv_service.refresh_source(sid)
    item = iptv_service.list_channels(source_id=sid)["items"][0]
    for _ in range(3):
        iptv_service.store().mark_channel_check(sid, str(item["channel_id"]), available=False)

    assert iptv_service.store().get_source(sid, redacted=True)["channel_count"] == 2
    removed = iptv_service.store().remove_unavailable(source_id=sid)
    assert removed == 1
    assert iptv_service.store().get_source(sid, redacted=True)["channel_count"] == 1


def test_scheduled_checks_skip_disabled_sources(iptv_tmp) -> None:
    source = iptv_service.create_source(name="Test", content=PLAYLIST)
    sid = str(source["id"])
    iptv_service.refresh_source(sid)
    item = iptv_service.list_channels(source_id=sid)["items"][0]
    iptv_service.update_channel(sid, str(item["channel_id"]), {"favorite": True})

    due = iptv_service.store().channels_due_for_check(before=9e18, limit=10)
    assert any(str(c["channel_id"]) == str(item["channel_id"]) for c in due)

    iptv_service.store().update_source(sid, {"enabled": 0})
    assert iptv_service.store().channels_due_for_check(before=9e18, limit=10) == []


def test_update_source_location_switches_pasted_source_to_url(iptv_tmp) -> None:
    source = iptv_service.create_source(name="Pasted", content=PLAYLIST)
    sid = str(source["id"])
    assert iptv_service.store().get_source(sid)["kind"] == "upload"

    iptv_service.update_source(sid, {"location": "https://example.test/list.m3u"})
    raw = iptv_service.store().get_source(sid)
    assert raw["kind"] == "url"
    assert raw["content"] == ""
    assert raw["location"] == "https://example.test/list.m3u"


def test_refresh_keeps_identity_when_duplicate_tvg_appears(iptv_tmp, monkeypatch) -> None:
    source = iptv_service.create_source(name="Test", content=PLAYLIST)
    sid = str(source["id"])
    iptv_service.refresh_source(sid)
    news = next(
        c for c in iptv_service.list_channels(source_id=sid)["items"] if c["tvg_id"] == "news.example"
    )
    original_id = str(news["channel_id"])
    iptv_service.update_channel(sid, original_id, {"favorite": True})

    dup_playlist = PLAYLIST + (
        '#EXTINF:-1 tvg-id="news.example" tvg-name="Example News Duplicate" '
        'group-title="News",Example News Duplicate\n'
        "https://catalog.example/list/streams/news-dup.m3u8\n"
    )
    monkeypatch.setattr(iptv_service, "_fetch_source", lambda _s: (dup_playlist, "e", "", False))
    iptv_service.refresh_source(sid)

    # A duplicate tvg-id appearing must not detach the incumbent's user state.
    updated = iptv_service.store().get_channel(sid, original_id, redacted=True)
    assert updated is not None
    assert updated["active"] is True
    assert updated["favorite"] is True


def test_update_source_location_clears_conditional_validators(iptv_tmp, monkeypatch) -> None:
    source = iptv_service.create_source(name="URL", location="https://old.example/list.m3u")
    sid = str(source["id"])
    monkeypatch.setattr(
        iptv_service,
        "_fetch_source",
        lambda _s: (PLAYLIST, "etag-1", "Mon, 01 Jan 2026 00:00:00 GMT", False),
    )
    iptv_service.refresh_source(sid)
    raw = iptv_service.store().get_source(sid)
    assert raw["etag"] == "etag-1"
    assert raw["last_modified"] == "Mon, 01 Jan 2026 00:00:00 GMT"

    iptv_service.update_source(sid, {"location": "https://new.example/list.m3u"})
    raw2 = iptv_service.store().get_source(sid)
    assert raw2["etag"] == ""
    assert raw2["last_modified"] == ""
    assert raw2["location"] == "https://new.example/list.m3u"
