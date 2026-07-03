# SPDX-License-Identifier: GPL-3.0-only
"""Guardrail: capture today's settings-to-env sync behavior before Phase 2 moves it.

These tests pin the exact ``os.environ`` writes performed by the settings
apply path (``routes/settings.py``) and the startup sync (``main.py``) so the
Phase 2 RuntimeConfig migration can prove behavior is preserved. They assert
current behavior, not desired behavior.
"""
import os

import pytest
from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.main import create_app
from relaytv_app.routes import settings as settings_routes


@pytest.fixture(autouse=True)
def _restore_environ():
    snapshot = dict(os.environ)
    yield
    for key in set(os.environ) - set(snapshot):
        del os.environ[key]
    for key, value in snapshot.items():
        if os.environ.get(key) != value:
            os.environ[key] = value


@pytest.fixture
def quiet_settings_apply(monkeypatch: pytest.MonkeyPatch):
    """Stub the settings-apply side effects that are not env sync."""
    monkeypatch.setattr(routes.state, "get_settings", lambda: {})
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "start_cec_monitor", lambda: None)
    monkeypatch.setattr(routes.player, "stop_cec_monitor", lambda: None)
    monkeypatch.setattr(settings_routes.upload_store, "cleanup_uploads", lambda settings: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "set_device_identity", lambda name: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "connect", lambda **kwargs: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "disconnect", lambda: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "mark_error", lambda reason: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "refresh_catalog_profile", lambda: None)
    monkeypatch.setattr(routes, "_sync_idle_visual_surfaces_after_settings", lambda: None)


def _apply(**kwargs) -> dict:
    return settings_routes.update_settings(settings_routes.SettingsReq(**kwargs))


def test_settings_apply_syncs_playback_env(quiet_settings_apply) -> None:
    _apply(
        quality_mode="manual",
        quality_cap="1080p",
        ytdlp_format="bv*+ba/b",
        youtube_cookies_path="/data/cookies.txt",
        audio_device="alsa/hdmi:CARD=vc4hdmi0,DEV=0",
        video_mode="drm",
        drm_connector="HDMI-A-1",
        sub_lang="en",
    )

    assert os.environ["RELAYTV_QUALITY_MODE"] == "manual"
    assert os.environ["RELAYTV_QUALITY_CAP"] == "1080p"
    assert os.environ["YTDLP_FORMAT"] == "bv*+ba/b"
    assert os.environ["RELAYTV_YTDLP_COOKIES"] == "/data/cookies.txt"
    assert os.environ["MPV_AUDIO_DEVICE"] == "alsa/hdmi:CARD=vc4hdmi0,DEV=0"
    assert os.environ["RELAYTV_VIDEO_MODE"] == "drm"
    assert os.environ["RELAYTV_DRM_CONNECTOR"] == "HDMI-A-1"
    assert os.environ["RELAYTV_SUB_LANG"] == "en"


def test_settings_apply_auto_quality_mode_clears_ytdlp_format(quiet_settings_apply) -> None:
    os.environ["YTDLP_FORMAT"] = "stale-manual-format"

    _apply(quality_mode="auto", ytdlp_format="ignored-when-auto")

    assert os.environ["RELAYTV_QUALITY_MODE"] == "auto"
    assert os.environ["YTDLP_FORMAT"] == ""


def test_settings_apply_ytdlp_format_alone_implies_manual(quiet_settings_apply) -> None:
    _apply(ytdlp_format="bv*[height<=720]+ba/b")

    assert os.environ["YTDLP_FORMAT"] == "bv*[height<=720]+ba/b"


def test_settings_apply_syncs_invidious_env(quiet_settings_apply) -> None:
    _apply(youtube_use_invidious=True, youtube_invidious_base="https://invidious.example/")

    assert os.environ["USE_INVIDIOUS"] == "true"
    assert os.environ["INVIDIOUS_BASE"] == "https://invidious.example"

    _apply(youtube_use_invidious=False, youtube_invidious_base="")

    assert os.environ["USE_INVIDIOUS"] == "false"
    assert os.environ["INVIDIOUS_BASE"] == ""


def test_settings_apply_clamps_upload_env(quiet_settings_apply) -> None:
    _apply(uploads={"max_size_gb": 10000, "retention_hours": 0})

    assert os.environ["RELAYTV_UPLOAD_MAX_SIZE_GB"] == "500.0"
    assert os.environ["RELAYTV_UPLOAD_RETENTION_HOURS"] == "1"

    _apply(uploads={"max_size_gb": 0.01, "retention_hours": 999999})

    assert os.environ["RELAYTV_UPLOAD_MAX_SIZE_GB"] == "0.25"
    assert os.environ["RELAYTV_UPLOAD_RETENTION_HOURS"] == str(24 * 90)


def test_settings_apply_syncs_device_name_env_trio(quiet_settings_apply) -> None:
    _apply(device_name="Living Room")

    assert os.environ["RELAYTV_DEVICE_NAME"] == "Living Room"
    assert os.environ["RELAYTV_JELLYFIN_DEVICE_NAME"] == "Living Room"
    assert os.environ["RELAYTV_JELLYFIN_CLIENT_NAME"] == "Living Room"


def test_settings_apply_syncs_flag_env_as_binary(quiet_settings_apply) -> None:
    _apply(
        cec_enabled="1",
        idle_dashboard_enabled=True,
        idle_notifications_enabled=False,
        idle_qr_enabled=True,
        idle_qr_size=280,
    )

    assert os.environ["RELAYTV_CEC"] == "1"
    assert os.environ["RELAYTV_CEC_ENABLED"] == "1"
    assert os.environ["RELAYTV_IDLE_DASHBOARD_ENABLED"] == "1"
    assert os.environ["RELAYTV_IDLE_NOTIFICATIONS_ENABLED"] == "0"
    assert os.environ["RELAYTV_IDLE_QR_ENABLED"] == "1"
    assert os.environ["RELAYTV_IDLE_QR_SIZE"] == "280"


def test_settings_apply_syncs_jellyfin_env(quiet_settings_apply) -> None:
    os.environ["RELAYTV_JELLYFIN_AUTH_ENABLED"] = "0"

    _apply(
        jellyfin_enabled=True,
        jellyfin_server_url=" https://jf.example ",
        jellyfin_username=" mark ",
        jellyfin_password=" hunter2 ",
        jellyfin_user_id=" uid-1 ",
        jellyfin_audio_lang=" ENG ",
        jellyfin_sub_lang=" OFF ",
        jellyfin_playback_mode=" Native ",
    )

    assert os.environ["RELAYTV_JELLYFIN_ENABLED"] == "1"
    assert os.environ["RELAYTV_JELLYFIN_SERVER_URL"] == "https://jf.example"
    assert os.environ["RELAYTV_JELLYFIN_USERNAME"] == "mark"
    assert os.environ["RELAYTV_JELLYFIN_PASSWORD"] == "hunter2"
    assert os.environ["RELAYTV_JELLYFIN_USER_ID"] == "uid-1"
    assert os.environ["RELAYTV_JELLYFIN_AUDIO_LANG"] == "eng"
    assert os.environ["RELAYTV_JELLYFIN_SUB_LANG"] == "off"
    assert os.environ["RELAYTV_JELLYFIN_PLAYBACK_MODE"] == "native"
    # Touching any core Jellyfin key force-enables username/password auth mode.
    assert os.environ["RELAYTV_JELLYFIN_AUTH_ENABLED"] == "1"


def test_startup_sync_mirrors_persisted_settings_to_env(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted = {
        "youtube_cookies_path": "/data/cookies.txt",
        "youtube_use_invidious": True,
        "youtube_invidious_base": "https://invidious.example",
        "jellyfin_enabled": True,
        "jellyfin_api_key": "legacy-key",
        "jellyfin_auth_enabled": True,
        "jellyfin_server_url": "https://jf.example",
        "jellyfin_username": "mark",
        "jellyfin_password": "hunter2",
        "jellyfin_user_id": "uid-1",
        "jellyfin_audio_lang": "eng",
        "jellyfin_sub_lang": "off",
        "jellyfin_playback_mode": "auto",
        "uploads": {"max_size_gb": 12.5, "retention_hours": 48},
    }
    monkeypatch.setattr("relaytv_app.main.get_settings", lambda: dict(persisted))
    monkeypatch.setattr("relaytv_app.main.load_state_from_disk", lambda: None)
    monkeypatch.setattr("relaytv_app.upload_store.cleanup_uploads", lambda settings: None)

    with TestClient(create_app(testing=True)):
        assert os.environ["RELAYTV_YTDLP_COOKIES"] == "/data/cookies.txt"
        assert os.environ["USE_INVIDIOUS"] == "true"
        assert os.environ["INVIDIOUS_BASE"] == "https://invidious.example"
        assert os.environ["RELAYTV_JELLYFIN_ENABLED"] == "1"
        assert os.environ["RELAYTV_JELLYFIN_API_KEY"] == "legacy-key"
        assert os.environ["RELAYTV_JELLYFIN_AUTH_ENABLED"] == "1"
        assert os.environ["RELAYTV_JELLYFIN_SERVER_URL"] == "https://jf.example"
        assert os.environ["RELAYTV_JELLYFIN_USERNAME"] == "mark"
        assert os.environ["RELAYTV_JELLYFIN_PASSWORD"] == "hunter2"
        assert os.environ["RELAYTV_JELLYFIN_USER_ID"] == "uid-1"
        assert os.environ["RELAYTV_JELLYFIN_AUDIO_LANG"] == "eng"
        assert os.environ["RELAYTV_JELLYFIN_SUB_LANG"] == "off"
        assert os.environ["RELAYTV_JELLYFIN_PLAYBACK_MODE"] == "auto"
        assert os.environ["RELAYTV_UPLOAD_MAX_SIZE_GB"] == "12.5"
        assert os.environ["RELAYTV_UPLOAD_RETENTION_HOURS"] == "48"
