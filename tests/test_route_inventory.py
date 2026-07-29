# SPDX-License-Identifier: GPL-3.0-only
from fastapi.routing import APIRoute

from relaytv_app.main import create_app


EXPECTED_ROUTES = {
    ("GET", "/", "root"),
    ("POST", "/api/queue/add", "enqueue"),
    ("GET", "/app/info", "app_info"),
    ("GET", "/assets/banner.png", "relaytv_banner_png_asset"),
    ("GET", "/assets/banner.svg", "relaytv_banner_svg_asset"),
    ("GET", "/assets/logo.svg", "relaytv_logo_svg_asset"),
    ("POST", "/auth/check", "auth_check"),
    ("POST", "/clear", "clear"),
    ("POST", "/close", "close"),
    ("GET", "/devices", "get_devices"),
    ("GET", "/discovery/status", "discovery_status"),
    ("POST", "/enqueue", "enqueue"),
    ("GET", "/favicon.ico", "favicon_ico"),
    ("GET", "/health", "health"),
    ("GET", "/history", "history"),
    ("POST", "/history/clear", "history_clear"),
    ("POST", "/history/play", "history_play"),
    ("POST", "/history/requeue", "history_requeue"),
    ("GET", "/idle", "idle_page"),
    ("GET", "/idle/weather", "get_idle_weather"),
    ("POST", "/ingest/media", "ingest_media"),
    ("POST", "/ingest/media/enqueue", "ingest_media_enqueue"),
    ("POST", "/ingest/media/play", "ingest_media_play"),
    ("GET", "/integrations/iptv/status", "iptv_status"),
    ("POST", "/integrations/jellyfin/catalog/cache_clear", "jellyfin_catalog_cache_clear"),
    ("POST", "/integrations/jellyfin/command", "jellyfin_integration_command"),
    ("POST", "/integrations/jellyfin/connect", "jellyfin_integration_connect"),
    ("POST", "/integrations/jellyfin/disconnect", "jellyfin_integration_disconnect"),
    ("POST", "/integrations/jellyfin/heartbeat", "jellyfin_integration_heartbeat"),
    ("GET", "/integrations/jellyfin/progress_snapshot", "jellyfin_integration_progress_snapshot"),
    ("POST", "/integrations/jellyfin/push", "jellyfin_integration_push"),
    ("POST", "/integrations/jellyfin/register", "jellyfin_integration_register"),
    ("GET", "/integrations/jellyfin/status", "jellyfin_integration_status"),
    ("POST", "/integrations/jellyfin/stopped", "jellyfin_integration_stopped"),
    ("GET", "/integrations/jellyfin/stopped_snapshot", "jellyfin_integration_stopped_snapshot"),
    ("GET", "/iptv/channels", "iptv_channels"),
    ("POST", "/iptv/channels/reorder", "iptv_channel_reorder"),
    ("POST", "/iptv/channels/remove-unavailable", "iptv_channels_remove_unavailable"),
    ("POST", "/iptv/channels/visibility", "iptv_channel_visibility"),
    ("PATCH", "/iptv/channels/{channel_id}", "iptv_channel_update"),
    ("POST", "/iptv/channels/{channel_id}/action", "iptv_channel_action"),
    ("POST", "/iptv/channels/{channel_id}/check", "iptv_channel_check"),
    ("GET", "/iptv/directory", "iptv_directory"),
    ("POST", "/iptv/directory/{preset_id}/add", "iptv_directory_add"),
    ("GET", "/iptv/sources", "iptv_sources"),
    ("POST", "/iptv/sources", "iptv_source_create"),
    ("DELETE", "/iptv/sources/{source_id}", "iptv_source_delete"),
    ("PATCH", "/iptv/sources/{source_id}", "iptv_source_update"),
    ("POST", "/iptv/sources/{source_id}/refresh", "iptv_source_refresh"),
    ("POST", "/jellyfin/action", "jellyfin_item_action"),
    ("GET", "/jellyfin/audio/options", "jellyfin_audio_options"),
    ("POST", "/jellyfin/audio/select", "jellyfin_audio_select"),
    ("GET", "/jellyfin/home", "jellyfin_home"),
    ("GET", "/jellyfin/item/{item_id}", "jellyfin_item_detail"),
    ("GET", "/jellyfin/item/{item_id}/adjacent", "jellyfin_item_adjacent"),
    ("GET", "/jellyfin/movies", "jellyfin_movies"),
    ("GET", "/jellyfin/search", "jellyfin_search"),
    ("GET", "/jellyfin/subtitle/options", "jellyfin_subtitle_options"),
    ("POST", "/jellyfin/subtitle/select", "jellyfin_subtitle_select"),
    ("GET", "/jellyfin/tv/series", "jellyfin_tv_series"),
    ("GET", "/jellyfin/tv/series/{series_id}/episodes", "jellyfin_tv_series_episodes"),
    ("POST", "/jellyfin/tv/series/{series_id}/play_all", "jellyfin_tv_series_play_all"),
    ("GET", "/jellyfin/tv/series/{series_id}/seasons", "jellyfin_tv_series_seasons"),
    ("GET", "/manifest.json", "pwa_manifest"),
    ("GET", "/media/uploads/{upload_id}/{filename}", "get_uploaded_media"),
    ("POST", "/mute", "mute"),
    ("POST", "/next", "next_track"),
    ("GET", "/notifications/capabilities", "notifications_capabilities"),
    ("POST", "/notify", "notify"),
    ("POST", "/now_playing/clear", "clear_now_playing"),
    ("POST", "/overlay", "overlay"),
    ("POST", "/pause", "pause"),
    ("GET", "/peers", "peers_list"),
    ("POST", "/peers", "peers_add"),
    ("GET", "/peers/identity", "peers_identity"),
    ("POST", "/peers/probe", "peers_probe"),
    ("DELETE", "/peers/{peer_id}", "peers_remove"),
    ("PATCH", "/peers/{peer_id}", "peers_update"),
    ("POST", "/peers/{peer_id}/probe", "peers_probe_saved"),
    ("POST", "/peers/{peer_id}/send", "peers_send"),
    ("POST", "/play", "play"),
    ("POST", "/play_at", "play_at"),
    ("POST", "/play_now", "play_now"),
    ("POST", "/play_temporary", "play_temporary"),
    ("POST", "/play_temporary/cancel", "play_temporary_cancel"),
    ("POST", "/playback/play", "playback_play"),
    ("GET", "/playback/state", "playback_state"),
    ("POST", "/playback/toggle", "playback_toggle"),
    ("GET", "/postlive/{token}.mkv", "postlive_stream"),
    ("POST", "/previous", "previous"),
    ("GET", "/pwa/brand/banner.png", "pwa_brand_banner_png_asset"),
    ("GET", "/pwa/brand/banner.svg", "pwa_brand_banner_svg_asset"),
    ("GET", "/pwa/brand/logo.svg", "pwa_brand_logo_svg_asset"),
    ("GET", "/pwa/emby.svg", "pwa_emby_svg"),
    ("GET", "/pwa/icon.svg", "pwa_icon_svg"),
    ("GET", "/pwa/jellyfin.svg", "pwa_jellyfin_svg"),
    ("GET", "/pwa/splash.svg", "pwa_splash_svg"),
    ("GET", "/pwa/weather/{asset_name}", "pwa_weather_asset"),
    ("GET", "/pwa/{asset_path:path}", "pwa_static_asset"),
    ("GET", "/qr/connect.svg", "qr_connect_svg"),
    ("GET", "/queue", "queue"),
    ("POST", "/queue/add", "enqueue"),
    ("POST", "/queue/dedupe", "queue_dedupe"),
    ("POST", "/queue/import", "queue_import"),
    ("POST", "/queue/move", "queue_move"),
    ("POST", "/queue/remove", "queue_remove"),
    ("POST", "/resume", "resume"),
    ("POST", "/resume/clear", "clear_resumable_session"),
    ("POST", "/resume_session", "resume_session"),
    ("GET", "/runtime/capabilities", "runtime_capabilities"),
    ("POST", "/seek", "seek"),
    ("POST", "/seek_abs", "seek_abs"),
    ("GET", "/settings", "get_settings"),
    ("POST", "/settings", "update_settings"),
    ("POST", "/settings/youtube/cookies", "upload_youtube_cookies"),
    ("POST", "/settings/youtube/cookies/clear", "clear_youtube_cookies"),
    ("GET", "/share", "share"),
    ("POST", "/smart", "smart"),
    ("GET", "/snapshot", "snapshot"),
    ("POST", "/snapshot", "snapshot"),
    ("GET", "/snapshots/{filename}", "get_snapshot"),
    ("GET", "/status", "status"),
    ("GET", "/static/ui/{asset_name}", "ui_static_asset"),
    ("POST", "/stop", "stop"),
    ("GET", "/sw.js", "pwa_sw"),
    ("GET", "/thumbs/{filename}", "thumbs"),
    ("POST", "/toast", "toast"),
    ("POST", "/toggle_pause", "toggle_pause"),
    ("GET", "/tv/status", "tv_status"),
    ("GET", "/ui", "ui"),
    ("GET", "/ui/events", "ui_events"),
    ("POST", "/v1/queue/add", "enqueue"),
    ("POST", "/volume", "volume"),
    ("GET", "/x11/host_urls", "x11_host_urls"),
    ("GET", "/x11/overlay", "x11_overlay_page"),
    ("POST", "/x11/overlay/client_state", "x11_overlay_client_state"),
    ("GET", "/x11/overlay/events", "x11_overlay_events"),
}

EXPECTED_ALIAS_GROUPS = {
    "enqueue": {
        ("POST", "/enqueue"),
        ("POST", "/queue/add"),
        ("POST", "/api/queue/add"),
        ("POST", "/v1/queue/add"),
    },
    "snapshot": {
        ("GET", "/snapshot"),
        ("POST", "/snapshot"),
    },
    "overlay_toast_notify": {
        ("POST", "/overlay"),
        ("POST", "/toast"),
        ("POST", "/notify"),
    },
}


def _collect_api_routes(routes, prefix: str = "") -> set[tuple[str, str, str]]:
    """Flatten APIRoutes across FastAPI versions.

    FastAPI >= 0.129 keeps included routers as lazy `_IncludedRouter` entries
    instead of flattening them into `app.routes`, so recurse through them.
    """
    out: set[tuple[str, str, str]] = set()
    for route in routes:
        if isinstance(route, APIRoute):
            for method in sorted(route.methods or []):
                out.add((method, prefix + route.path, route.name))
            continue
        included = getattr(route, "original_router", None)
        if included is not None:
            context = getattr(route, "include_context", None)
            included_prefix = str(getattr(context, "prefix", "") or "")
            out |= _collect_api_routes(included.routes, prefix + included_prefix)
    return out


def _route_inventory() -> set[tuple[str, str, str]]:
    app = create_app(testing=True)
    return _collect_api_routes(app.routes)


def test_public_route_inventory_is_stable() -> None:
    assert _route_inventory() == EXPECTED_ROUTES


def test_compatibility_aliases_are_preserved() -> None:
    routes_by_endpoint: dict[str, set[tuple[str, str]]] = {}
    for method, path, endpoint in _route_inventory():
        routes_by_endpoint.setdefault(endpoint, set()).add((method, path))

    for endpoint, expected in EXPECTED_ALIAS_GROUPS.items():
        if endpoint == "overlay_toast_notify":
            actual = {
                (method, path)
                for method, path, route_endpoint in _route_inventory()
                if route_endpoint in {"overlay", "toast", "notify"}
            }
        else:
            actual = routes_by_endpoint.get(endpoint, set())
        assert expected.issubset(actual)
