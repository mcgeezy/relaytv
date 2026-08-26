# SPDX-License-Identifier: GPL-3.0-only
"""Guardrail: settings apply and startup sync flow through RuntimeConfig.

These tests pin the settings-bus writes performed by the settings apply path
(``routes/settings.py``) and the startup sync (``main.py``): values land in
the RuntimeConfig snapshot with the same normalization the legacy env bus
used, and ``os.environ`` receives only the pinned subprocess mirroring
contract (``MIRRORED_TO_ENV``).
"""
import os

import pytest
from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.config import MIRRORED_TO_ENV, SETTINGS_BUS_VARS, runtime_config
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
    """Stub the settings-apply side effects that are not config sync."""
    monkeypatch.setattr(routes.state, "get_settings", lambda: {})
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "start_cec_monitor", lambda: None)
    monkeypatch.setattr(routes.player, "stop_cec_monitor", lambda: None)
    monkeypatch.setattr(settings_routes.upload_store, "cleanup_uploads", lambda settings: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "set_device_identity", lambda name: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "set_server_type", lambda server_type: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "connect", lambda **kwargs: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "disconnect", lambda: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "mark_error", lambda reason: None)
    monkeypatch.setattr(settings_routes.jellyfin_receiver, "refresh_catalog_profile", lambda: None)
    monkeypatch.setattr(routes, "_sync_idle_visual_surfaces_after_settings", lambda: None)


def _apply(**kwargs) -> dict:
    return settings_routes.update_settings(settings_routes.SettingsReq(**kwargs))


def _cfg(name: str) -> str | None:
    return runtime_config.snapshot().raw(name)


def test_settings_apply_syncs_playback_config(quiet_settings_apply) -> None:
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

    assert _cfg("RELAYTV_QUALITY_MODE") == "manual"
    assert _cfg("RELAYTV_QUALITY_CAP") == "1080p"
    assert _cfg("YTDLP_FORMAT") == "bv*+ba/b"
    assert _cfg("RELAYTV_YTDLP_COOKIES") == "/data/cookies.txt"
    assert _cfg("MPV_AUDIO_DEVICE") == "alsa/hdmi:CARD=vc4hdmi0,DEV=0"
    assert _cfg("RELAYTV_VIDEO_MODE") == "drm"
    assert _cfg("RELAYTV_DRM_CONNECTOR") == "HDMI-A-1"
    assert _cfg("RELAYTV_SUB_LANG") == "en"


def test_settings_apply_auto_quality_mode_clears_ytdlp_format(quiet_settings_apply) -> None:
    runtime_config.set_value("YTDLP_FORMAT", "stale-manual-format")

    _apply(quality_mode="auto", ytdlp_format="ignored-when-auto")

    assert _cfg("RELAYTV_QUALITY_MODE") == "auto"
    assert _cfg("YTDLP_FORMAT") == ""


def test_settings_apply_ytdlp_format_alone_implies_manual(quiet_settings_apply) -> None:
    _apply(ytdlp_format="bv*[height<=720]+ba/b")

    assert _cfg("YTDLP_FORMAT") == "bv*[height<=720]+ba/b"


def test_settings_apply_syncs_invidious_config(quiet_settings_apply) -> None:
    _apply(youtube_use_invidious=True, youtube_invidious_base="https://invidious.example/")

    assert _cfg("USE_INVIDIOUS") == "true"
    assert _cfg("INVIDIOUS_BASE") == "https://invidious.example"

    _apply(youtube_use_invidious=False, youtube_invidious_base="")

    assert _cfg("USE_INVIDIOUS") == "false"
    assert _cfg("INVIDIOUS_BASE") == ""


def test_settings_apply_clamps_upload_config(quiet_settings_apply) -> None:
    _apply(uploads={"max_size_gb": 10000, "retention_hours": 0})

    assert _cfg("RELAYTV_UPLOAD_MAX_SIZE_GB") == "500.0"
    assert _cfg("RELAYTV_UPLOAD_RETENTION_HOURS") == "1"

    _apply(uploads={"max_size_gb": 0.01, "retention_hours": 999999})

    assert _cfg("RELAYTV_UPLOAD_MAX_SIZE_GB") == "0.25"
    assert _cfg("RELAYTV_UPLOAD_RETENTION_HOURS") == str(24 * 90)


def test_settings_apply_syncs_device_name_trio_and_mirrors_env(quiet_settings_apply) -> None:
    _apply(device_name="Living Room")

    assert _cfg("RELAYTV_DEVICE_NAME") == "Living Room"
    assert _cfg("RELAYTV_JELLYFIN_DEVICE_NAME") == "Living Room"
    assert _cfg("RELAYTV_JELLYFIN_CLIENT_NAME") == "Living Room"
    # The one pinned subprocess mirror: qt_shell_app's legacy fallback.
    assert os.environ["RELAYTV_DEVICE_NAME"] == "Living Room"


def test_settings_apply_syncs_flag_config_as_binary(quiet_settings_apply) -> None:
    _apply(
        cec_enabled="1",
        idle_dashboard_enabled=True,
        idle_notifications_enabled=False,
        idle_qr_enabled=True,
        idle_qr_size=280,
    )

    assert _cfg("RELAYTV_CEC") == "1"
    assert _cfg("RELAYTV_CEC_ENABLED") == "1"
    assert _cfg("RELAYTV_IDLE_DASHBOARD_ENABLED") == "1"
    assert _cfg("RELAYTV_IDLE_NOTIFICATIONS_ENABLED") == "0"
    assert _cfg("RELAYTV_IDLE_QR_ENABLED") == "1"
    assert _cfg("RELAYTV_IDLE_QR_SIZE") == "280"


def test_settings_apply_syncs_jellyfin_config(quiet_settings_apply) -> None:
    runtime_config.set_value("RELAYTV_JELLYFIN_AUTH_ENABLED", "0")

    _apply(
        jellyfin_enabled=True,
        jellyfin_server_url=" https://jf.example ",
        jellyfin_username=" mark ",
        jellyfin_password=" hunter2 ",
        jellyfin_user_id=" uid-1 ",
        jellyfin_audio_lang=" ENG ",
        jellyfin_sub_lang=" OFF ",
        jellyfin_playback_mode=" Native ",
        jellyfin_server_type=" EMBY ",
    )

    assert _cfg("RELAYTV_JELLYFIN_ENABLED") == "1"
    assert _cfg("RELAYTV_JELLYFIN_SERVER_URL") == "https://jf.example"
    assert _cfg("RELAYTV_JELLYFIN_USERNAME") == "mark"
    assert _cfg("RELAYTV_JELLYFIN_PASSWORD") == "hunter2"
    assert _cfg("RELAYTV_JELLYFIN_USER_ID") == "uid-1"
    assert _cfg("RELAYTV_JELLYFIN_AUDIO_LANG") == "eng"
    assert _cfg("RELAYTV_JELLYFIN_SUB_LANG") == "off"
    assert _cfg("RELAYTV_JELLYFIN_PLAYBACK_MODE") == "native"
    assert _cfg("RELAYTV_JELLYFIN_SERVER_TYPE") == "emby"
    # Touching any core Jellyfin key force-enables username/password auth mode.
    assert _cfg("RELAYTV_JELLYFIN_AUTH_ENABLED") == "1"


def test_settings_apply_syncs_seerr_config(quiet_settings_apply) -> None:
    _apply(
        seerr_enabled=True,
        seerr_server_url=" https://seerr.example/api/v1 ",
        seerr_api_key=" secret-key ",
        seerr_request_mode="shared_admin",
        seerr_request_user_id=7,
    )

    assert _cfg("RELAYTV_SEERR_ENABLED") == "1"
    assert _cfg("RELAYTV_SEERR_SERVER_URL") == "https://seerr.example"
    assert _cfg("RELAYTV_SEERR_API_KEY") == "secret-key"
    assert _cfg("RELAYTV_SEERR_SHARED_REQUESTS_ENABLED") == "1"
    assert _cfg("RELAYTV_SEERR_REQUEST_MODE") == "shared_admin"
    assert _cfg("RELAYTV_SEERR_REQUEST_USER_ID") == "7"


def test_settings_apply_syncs_server_type_to_live_receiver(quiet_settings_apply, monkeypatch) -> None:
    applied: list[str] = []
    monkeypatch.setattr(
        settings_routes.jellyfin_receiver,
        "set_server_type",
        lambda server_type: applied.append(str(server_type)),
    )

    response = _apply(jellyfin_server_type=" EMBY ")

    assert applied == ["emby"]
    assert "jellyfin_server_type" in response["live_applied"]
    assert "jellyfin_server_type" not in response["live_apply_failed"]


def test_settings_apply_does_not_write_env_beyond_mirror_contract(quiet_settings_apply) -> None:
    env_before = {name: os.environ.get(name) for name in sorted(SETTINGS_BUS_VARS)}

    _apply(
        quality_mode="manual",
        quality_cap="1080p",
        ytdlp_format="bv*+ba/b",
        youtube_cookies_path="/data/cookies.txt",
        youtube_use_invidious=True,
        youtube_invidious_base="https://invidious.example",
        uploads={"max_size_gb": 5.0, "retention_hours": 24},
        audio_device="auto",
        video_mode="drm",
        device_name="Containment TV",
        drm_connector="HDMI-A-1",
        sub_lang="en",
        cec_enabled="1",
        idle_dashboard_enabled=True,
        idle_notifications_enabled=True,
        idle_qr_enabled=False,
        idle_qr_size=200,
        jellyfin_enabled=True,
        jellyfin_server_url="https://jf.example",
        jellyfin_username="mark",
        jellyfin_password="hunter2",
        jellyfin_user_id="uid-1",
        jellyfin_audio_lang="eng",
        jellyfin_sub_lang="off",
        jellyfin_playback_mode="auto",
        jellyfin_server_type="emby",
        seerr_enabled=True,
        seerr_server_url="https://seerr.example",
        seerr_api_key="seerr-secret",
        seerr_shared_requests_enabled=False,
        seerr_request_user_id=7,
    )

    for name in sorted(SETTINGS_BUS_VARS):
        if name in MIRRORED_TO_ENV:
            assert os.environ.get(name) == "Containment TV", name
        else:
            assert os.environ.get(name) == env_before[name], name


def test_startup_sync_populates_runtime_config_from_persisted_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        "seerr_enabled": True,
        "seerr_server_url": "https://seerr.example",
        "seerr_api_key": "seerr-secret",
        "seerr_shared_requests_enabled": False,
        "seerr_request_user_id": 7,
    }
    monkeypatch.delenv("RELAYTV_JELLYFIN_SERVER_URL", raising=False)
    monkeypatch.setattr("relaytv_app.main.get_settings", lambda: dict(persisted))
    monkeypatch.setattr("relaytv_app.main.load_state_from_disk", lambda: None)
    monkeypatch.setattr("relaytv_app.upload_store.cleanup_uploads", lambda settings: None)

    with TestClient(create_app(testing=True)):
        assert _cfg("RELAYTV_YTDLP_COOKIES") == "/data/cookies.txt"
        assert _cfg("USE_INVIDIOUS") == "true"
        assert _cfg("INVIDIOUS_BASE") == "https://invidious.example"
        assert _cfg("RELAYTV_JELLYFIN_ENABLED") == "1"
        assert _cfg("RELAYTV_JELLYFIN_API_KEY") == "legacy-key"
        assert _cfg("RELAYTV_JELLYFIN_AUTH_ENABLED") == "1"
        assert _cfg("RELAYTV_JELLYFIN_SERVER_URL") == "https://jf.example"
        assert _cfg("RELAYTV_JELLYFIN_USERNAME") == "mark"
        assert _cfg("RELAYTV_JELLYFIN_PASSWORD") == "hunter2"
        assert _cfg("RELAYTV_JELLYFIN_USER_ID") == "uid-1"
        assert _cfg("RELAYTV_JELLYFIN_AUDIO_LANG") == "eng"
        assert _cfg("RELAYTV_JELLYFIN_SUB_LANG") == "off"
        assert _cfg("RELAYTV_JELLYFIN_PLAYBACK_MODE") == "auto"
        assert _cfg("RELAYTV_SEERR_ENABLED") == "1"
        assert _cfg("RELAYTV_SEERR_SERVER_URL") == "https://seerr.example"
        assert _cfg("RELAYTV_SEERR_API_KEY") == "seerr-secret"
        assert _cfg("RELAYTV_SEERR_SHARED_REQUESTS_ENABLED") == "0"
        assert _cfg("RELAYTV_SEERR_REQUEST_USER_ID") == "7"
        assert _cfg("RELAYTV_UPLOAD_MAX_SIZE_GB") == "12.5"
        assert _cfg("RELAYTV_UPLOAD_RETENTION_HOURS") == "48"
        # The startup sync writes nothing to the environment.
        assert os.environ.get("RELAYTV_JELLYFIN_SERVER_URL") is None


def test_startup_sync_keeps_operator_env_as_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Operator env is captured at startup and persisted settings win over it."""
    monkeypatch.setenv("RELAYTV_QUALITY_CAP", "720p")
    monkeypatch.setenv("RELAYTV_JELLYFIN_SERVER_URL", "https://operator.example")
    monkeypatch.setattr(
        "relaytv_app.main.get_settings",
        lambda: {"jellyfin_server_url": "https://persisted.example"},
    )
    monkeypatch.setattr("relaytv_app.main.load_state_from_disk", lambda: None)
    monkeypatch.setattr("relaytv_app.upload_store.cleanup_uploads", lambda settings: None)

    with TestClient(create_app(testing=True)):
        # Never-synced operator value survives via refresh_from_env.
        assert _cfg("RELAYTV_QUALITY_CAP") == "720p"
        # Persisted settings overwrite the operator value in the snapshot.
        assert _cfg("RELAYTV_JELLYFIN_SERVER_URL") == "https://persisted.example"
        # ... without touching the environment.
        assert os.environ["RELAYTV_JELLYFIN_SERVER_URL"] == "https://operator.example"
