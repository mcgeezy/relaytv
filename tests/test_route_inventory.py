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
    ("GET", "/idle", "idle_page"),
    ("GET", "/idle/weather", "get_idle_weather"),
    ("POST", "/ingest/media", "ingest_media"),
    ("POST", "/ingest/media/enqueue", "ingest_media_enqueue"),
    ("POST", "/ingest/media/play", "ingest_media_play"),
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
    ("POST", "/play", "play"),
    ("POST", "/play_at", "play_at"),
    ("POST", "/play_now", "play_now"),
    ("POST", "/play_temporary", "play_temporary"),
    ("POST", "/play_temporary/cancel", "play_temporary_cancel"),
    ("POST", "/playback/play", "playback_play"),
    ("GET", "/playback/state", "playback_state"),
    ("POST", "/playback/toggle", "playback_toggle"),
    ("POST", "/previous", "previous"),
    ("GET", "/pwa/brand/banner.png", "pwa_brand_banner_png_asset"),
    ("GET", "/pwa/brand/banner.svg", "pwa_brand_banner_svg_asset"),
    ("GET", "/pwa/brand/logo.svg", "pwa_brand_logo_svg_asset"),
    ("GET", "/pwa/icon.svg", "pwa_icon_svg"),
    ("GET", "/pwa/jellyfin.svg", "pwa_jellyfin_svg"),
    ("GET", "/pwa/splash.svg", "pwa_splash_svg"),
    ("GET", "/pwa/weather/{asset_name}", "pwa_weather_asset"),
    ("GET", "/pwa/{asset_path:path}", "pwa_static_asset"),
    ("GET", "/qr/connect.svg", "qr_connect_svg"),
    ("GET", "/queue", "queue"),
    ("POST", "/queue/add", "enqueue"),
    ("POST", "/queue/dedupe", "queue_dedupe"),
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


def _route_inventory() -> set[tuple[str, str, str]]:
    app = create_app(testing=True)
    out: set[tuple[str, str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in sorted(route.methods or []):
            out.add((method, route.path, route.name))
    return out


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
