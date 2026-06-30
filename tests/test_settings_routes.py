# SPDX-License-Identifier: GPL-3.0-only
import os

from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.main import create_app


def test_get_settings_route_sanitizes_secret_values(monkeypatch, tmp_path) -> None:
    cookies_path = tmp_path / "cookies.txt"
    cookies_path.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    monkeypatch.setattr(
        routes.state,
        "get_settings",
        lambda: {
            "jellyfin_password": "secret",
            "jellyfin_api_key": "api-secret",
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
    assert body["youtube_cookies_path"] == ""
    assert body["youtube_cookies_configured"] is True
    assert body["youtube_use_invidious"] is True
    assert body["youtube_invidious_base"] == "https://invidious.example"
    assert body["idle_dashboard_enabled"] is False
    assert body["idle_notifications_enabled"] is True


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
    assert os.environ["RELAYTV_YTDLP_COOKIES"] == str(target)
    assert updates[-1] == {"youtube_cookies_path": str(target)}
    assert upload.json()["settings"]["youtube_cookies_path"] == ""

    clear = client.post("/settings/youtube/cookies/clear")

    assert clear.status_code == 200
    assert os.environ["RELAYTV_YTDLP_COOKIES"] == ""
    assert updates[-1] == {"youtube_cookies_path": ""}


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
    assert os.environ["RELAYTV_CEC"] == "0"
    assert os.environ["RELAYTV_CEC_ENABLED"] == "0"
    assert os.environ["RELAYTV_QUALITY_MODE"] == "auto_profile"
    assert os.environ["YTDLP_FORMAT"] == ""
    assert os.environ["RELAYTV_UPLOAD_MAX_SIZE_GB"] == "2.5"
    assert os.environ["RELAYTV_UPLOAD_RETENTION_HOURS"] == "48"
    assert os.environ["RELAYTV_IDLE_DASHBOARD_ENABLED"] == "0"
    assert cec_stops == [True]
    assert cleanup_calls
    assert idle_syncs == [True]
