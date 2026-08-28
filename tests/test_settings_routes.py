# SPDX-License-Identifier: GPL-3.0-only

from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.routes import settings as settings_routes
from relaytv_app.main import create_app
from relaytv_app.config import runtime_config


def test_get_settings_route_sanitizes_secret_values(monkeypatch, tmp_path) -> None:
    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(
        routes.state,
        "get_settings",
        lambda: {
            "jellyfin_password": "secret",
            "jellyfin_api_key": "api-secret",
            "seerr_api_key": "seerr-secret",
            "youtube_cookies_path": str(cookies_path),
            "youtube_use_invidious": 1,
            "youtube_invidious_base": " https://invidious.example ",
            "idle_dashboard_enabled": False,
        },
    )

    client = TestClient(create_app(testing=True))
    response = client.get("/settings")

    assert response.status_code == 200
    body = response.json()
    assert body["jellyfin_password"] == ""
    assert body["jellyfin_api_key"] == ""
    assert body["jellyfin_password_configured"] is True
    assert body["seerr_api_key"] == ""
    assert body["seerr_api_key_configured"] is True
    assert body["jellyfin_api_key_configured"] is True
    assert body["youtube_cookies_path"] == ""
    assert body["youtube_cookies_configured"] is True
    assert body["youtube_use_invidious"] is True
    assert body["youtube_invidious_base"] == "https://invidious.example"
    assert body["idle_dashboard_enabled"] is False
    assert body["idle_notifications_enabled"] is True


def test_seerr_api_key_is_write_only_and_requires_explicit_clear(monkeypatch) -> None:
    current = {
        "seerr_enabled": True,
        "seerr_server_url": "https://seerr.example",
        "seerr_api_key": "existing-secret",
    }

    def update(patch):
        current.update(patch)
        return dict(current)

    monkeypatch.setattr(routes.state, "get_settings", lambda: dict(current))
    monkeypatch.setattr(routes.state, "update_settings", update)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    client = TestClient(create_app(testing=True))

    preserved = client.post("/settings", json={"seerr_api_key": ""})
    assert preserved.status_code == 200
    assert current["seerr_api_key"] == "existing-secret"
    assert preserved.json()["settings"]["seerr_api_key"] == ""
    assert preserved.json()["settings"]["seerr_api_key_configured"] is True

    cleared = client.post("/settings", json={"seerr_api_key_clear": True})
    assert cleared.status_code == 200
    assert current["seerr_api_key"] == ""
    assert cleared.json()["settings"]["seerr_api_key_configured"] is False


def test_seerr_settings_normalize_url_and_reject_credentials(monkeypatch) -> None:
    current: dict[str, object] = {}

    def update(patch):
        current.update(patch)
        return dict(current)

    monkeypatch.setattr(routes.state, "get_settings", lambda: dict(current))
    monkeypatch.setattr(routes.state, "update_settings", update)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    client = TestClient(create_app(testing=True))

    saved = client.post(
        "/settings",
        json={
            "seerr_enabled": True,
            "seerr_server_url": " HTTPS://Seerr.Example/base/api/v1/ ",
            "seerr_api_key": "new-secret",
        },
    )
    assert saved.status_code == 200
    assert current["seerr_server_url"] == "https://seerr.example/base"
    assert runtime_config.snapshot().raw("RELAYTV_SEERR_API_KEY") == "new-secret"

    rejected = client.post(
        "/settings",
        json={"seerr_server_url": "https://admin:secret@seerr.example"},
    )
    assert rejected.status_code == 400


def test_seerr_request_mode_is_explicit_and_legacy_toggle_migrates(monkeypatch) -> None:
    current: dict[str, object] = {}

    def update(patch):
        current.update(patch)
        return dict(current)

    monkeypatch.setattr(routes.state, "get_settings", lambda: dict(current))
    monkeypatch.setattr(routes.state, "update_settings", update)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    client = TestClient(create_app(testing=True))

    caller = client.post("/settings", json={"seerr_request_mode": "caller_session"})
    assert caller.status_code == 200
    assert current["seerr_request_mode"] == "caller_session"
    assert current["seerr_shared_requests_enabled"] is False
    assert runtime_config.snapshot().raw("RELAYTV_SEERR_REQUEST_MODE") == "caller_session"

    legacy = client.post("/settings", json={"seerr_shared_requests_enabled": True})
    assert legacy.status_code == 200
    assert current["seerr_request_mode"] == "shared_admin"

    invalid = client.post("/settings", json={"seerr_request_mode": "automatic"})
    assert invalid.status_code == 400


def test_seerr_identity_change_retires_sessions_but_noop_does_not(monkeypatch) -> None:
    current: dict[str, object] = {
        "seerr_enabled": True,
        "seerr_server_url": "https://seerr.example",
        "seerr_request_mode": "caller_session",
    }
    retired = []

    def update(patch):
        current.update(patch)
        return dict(current)

    monkeypatch.setattr(routes.state, "get_settings", lambda: dict(current))
    monkeypatch.setattr(routes.state, "update_settings", update)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(
        settings_routes.seerr_sessions, "retire_all", lambda: retired.append(True)
    )
    client = TestClient(create_app(testing=True))

    assert client.post("/settings", json={"seerr_request_mode": "caller_session"}).status_code == 200
    assert retired == []

    assert client.post("/settings", json={"seerr_server_url": "https://new.example"}).status_code == 200
    assert retired == [True]


def test_settings_normalize_jellyfin_server_type(monkeypatch) -> None:
    from relaytv_app import state

    assert state._normalize_jellyfin_server_type(" EMBY ") == "emby"
    assert state._normalize_jellyfin_server_type("Jellyfin") == "jellyfin"
    assert state._normalize_jellyfin_server_type("plex") == "jellyfin"
    assert state._normalize_jellyfin_server_type(None) == "jellyfin"
    assert state._default_settings()["jellyfin_server_type"] == "jellyfin"

    monkeypatch.setattr(state, "_atomic_write_json", lambda path, payload: None)
    assert state.update_settings({"jellyfin_server_type": " EMBY "})["jellyfin_server_type"] == "emby"
    assert state.update_settings({"jellyfin_server_type": "bogus"})["jellyfin_server_type"] == "jellyfin"


def test_settings_api_key_omission_preserves_and_explicit_values_replace_or_clear(monkeypatch) -> None:
    stored: dict[str, object] = {
        "jellyfin_enabled": True,
        "jellyfin_server_url": "https://jf.example",
        "jellyfin_api_key": "old-key",
        "jellyfin_username": "",
        "jellyfin_password": "",
    }
    patches: list[dict[str, object]] = []

    monkeypatch.setattr(routes.state, "get_settings", lambda: dict(stored))

    def update(patch: dict[str, object]) -> dict[str, object]:
        patches.append(dict(patch))
        stored.update(patch)
        return dict(stored)

    monkeypatch.setattr(routes.state, "update_settings", update)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.jellyfin_receiver, "connect", lambda **kwargs: None)

    client = TestClient(create_app(testing=True))

    preserved = client.post("/settings", json={"jellyfin_server_url": "https://jf.example"})
    replaced = client.post("/settings", json={"jellyfin_api_key": "new-key"})
    cleared = client.post("/settings", json={"jellyfin_api_key": ""})

    assert preserved.status_code == 200
    assert "jellyfin_api_key" not in patches[0]
    assert replaced.status_code == 200
    assert patches[1]["jellyfin_api_key"] == "new-key"
    assert cleared.status_code == 200
    assert patches[2]["jellyfin_api_key"] == ""
    assert replaced.json()["settings"]["jellyfin_api_key"] == ""
    assert replaced.json()["settings"]["jellyfin_api_key_configured"] is True
    assert cleared.json()["settings"]["jellyfin_api_key_configured"] is False


def test_settings_rejects_unknown_jellyfin_auth_mode(monkeypatch) -> None:
    monkeypatch.setattr(routes.state, "get_settings", lambda: {})
    client = TestClient(create_app(testing=True))

    response = client.post("/settings", json={"jellyfin_auth_mode": "automatic"})

    assert response.status_code == 400
    assert response.json()["detail"] == "Invalid Jellyfin authentication mode"


def test_youtube_cookies_routes_upload_and_clear(monkeypatch, tmp_path) -> None:
    updates: list[dict[str, object]] = []
    target = tmp_path / "cookies.txt"

    monkeypatch.setenv("RELAYTV_YTDLP_COOKIES_UPLOAD_PATH", str(target))
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: updates.append(dict(patch)) or dict(patch))

    client = TestClient(create_app(testing=True))
    upload = client.post(
        "/settings/youtube/cookies",
        json={"cookies_text": "# Netscape HTTP Cookie File\n.example\tTRUE\t/\tFALSE\t0\tname\tvalue\n"},
    )

    assert upload.status_code == 200
    assert target.read_text(encoding="utf-8").endswith("name\tvalue\n")
    assert runtime_config.snapshot().raw("RELAYTV_YTDLP_COOKIES") == str(target)
    assert updates[-1] == {"youtube_cookies_path": str(target)}
    assert upload.json()["settings"]["youtube_cookies_path"] == ""

    clear = client.post("/settings/youtube/cookies/clear")

    assert clear.status_code == 200
    assert runtime_config.snapshot().raw("RELAYTV_YTDLP_COOKIES") == ""
    assert updates[-1] == {"youtube_cookies_path": ""}


def test_update_settings_ytdlp_auto_update_toggle_syncs_and_kicks(monkeypatch) -> None:
    from relaytv_app import ytdlp_update

    updates: list[dict[str, object]] = []
    kicks: list[bool] = []

    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "0")
    monkeypatch.setattr(routes.state, "get_settings", lambda: {})
    monkeypatch.setattr(
        routes.state,
        "update_settings",
        lambda patch: updates.append(dict(patch)) or dict(patch),
    )
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(ytdlp_update, "kick_async", lambda *, force=False: kicks.append(force))

    client = TestClient(create_app(testing=True))
    enable = client.post("/settings", json={"ytdlp_auto_update_enabled": True})

    assert enable.status_code == 200
    body = enable.json()
    assert body["settings"]["ytdlp_auto_update_enabled"] is True
    assert "ytdlp_auto_update_enabled" in body["live_applied"]
    assert updates[-1] == {"ytdlp_auto_update_enabled": True}
    assert runtime_config.snapshot().raw("RELAYTV_YTDLP_AUTO_UPDATE") == "1"
    # Turning the toggle on runs an immediate forced check.
    assert kicks == [True]

    disable = client.post("/settings", json={"ytdlp_auto_update_enabled": False})

    assert disable.status_code == 200
    assert disable.json()["settings"]["ytdlp_auto_update_enabled"] is False
    assert runtime_config.snapshot().raw("RELAYTV_YTDLP_AUTO_UPDATE") == "0"
    # Disabling (or re-saving while already on) does not kick another check.
    assert kicks == [True]


def test_update_settings_route_rejects_invidious_without_server(monkeypatch) -> None:
    monkeypatch.setattr(routes.state, "get_settings", lambda: {})

    client = TestClient(create_app(testing=True))
    response = client.post("/settings", json={"youtube_use_invidious": True, "youtube_invidious_base": "not-a-url"})

    assert response.status_code == 400
    assert response.json()["detail"] == "YouTube Invidious server is required when Invidious mode is enabled"


def test_update_settings_route_syncs_runtime_env_and_live_settings(monkeypatch) -> None:
    updates: list[dict[str, object]] = []
    cec_stops: list[bool] = []
    cleanup_calls: list[dict[str, object]] = []
    idle_syncs: list[bool] = []

    monkeypatch.setenv("RELAYTV_CEC", "1")
    runtime_config.refresh_from_env()
    monkeypatch.setattr(routes.state, "get_settings", lambda: {"cec_enabled": "1"})
    monkeypatch.setattr(
        routes.state,
        "update_settings",
        lambda patch: updates.append(dict(patch))
        or {
            "cec_enabled": "0",
            "quality_mode": "auto_profile",
            "uploads": {"max_size_gb": 2.5, "retention_hours": 48},
            "idle_dashboard_enabled": False,
        },
    )
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "stop_cec_monitor", lambda: cec_stops.append(True))
    monkeypatch.setattr(routes.player, "start_cec_monitor", lambda: None)
    monkeypatch.setattr(routes.upload_store, "cleanup_uploads", lambda settings: cleanup_calls.append(dict(settings)) or {})
    monkeypatch.setattr(routes, "_sync_idle_visual_surfaces_after_settings", lambda: idle_syncs.append(True))

    client = TestClient(create_app(testing=True))
    response = client.post(
        "/settings",
        json={
            "cec_enabled": "0",
            "quality_mode": "auto_profile",
            "uploads": {"max_size_gb": 2.5, "retention_hours": 48},
            "idle_dashboard_enabled": False,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert "cec_enabled" in body["live_applied"]
    assert "idle_dashboard_enabled" in body["live_applied"]
    assert updates[-1]["cec_enabled"] == "0"
    assert runtime_config.snapshot().raw("RELAYTV_CEC") == "0"
    assert runtime_config.snapshot().raw("RELAYTV_CEC_ENABLED") == "0"
    assert runtime_config.snapshot().raw("RELAYTV_QUALITY_MODE") == "auto_profile"
    assert runtime_config.snapshot().raw("YTDLP_FORMAT") == ""
    assert runtime_config.snapshot().raw("RELAYTV_UPLOAD_MAX_SIZE_GB") == "2.5"
    assert runtime_config.snapshot().raw("RELAYTV_UPLOAD_RETENTION_HOURS") == "48"
    assert runtime_config.snapshot().raw("RELAYTV_IDLE_DASHBOARD_ENABLED") == "0"
    assert cec_stops == [True]
    assert cleanup_calls
    assert idle_syncs == [True]
