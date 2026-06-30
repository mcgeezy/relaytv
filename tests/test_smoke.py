# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import json
import os
import shutil
import subprocess
import tomllib

import pytest

from fastapi.testclient import TestClient

from relaytv_app.main import create_app
from relaytv_app import container_entrypoint
from relaytv_app import player
from relaytv_app import resolver
from relaytv_app import routes
from relaytv_app import upload_store
from relaytv_app.routes import app_info as app_info_routes
from relaytv_app.qt_shell_app import (
    _cursor_hidden_refresh_ms,
    _cursor_mode,
    _embedded_web_overlay_enabled,
    _libmpv_enabled,
    _native_idle_overlay_enabled,
    _native_overlay_toasts_enabled,
    _overlay_software_mode_enabled,
    _native_idle_weather_layout,
)
from relaytv_app.routes import _notification_capabilities, _overlay_prefers_native_qt_toast

pytestmark = pytest.mark.native
ROOT_DIR = Path(__file__).resolve().parents[1]


def test_ui_smoke() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/ui')
    css_response = client.get('/static/ui/app.css')

    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert '<link rel="stylesheet" href="/static/ui/app.css" />' in response.text
    assert '<style>' not in response.text
    assert css_response.status_code == 200
    assert 'text/css' in css_response.headers['content-type']
    css = css_response.text
    assert 'RelayTV' in response.text
    assert 'id="jfActionStatus"' in response.text
    assert 'id="jellyfinOpenBtn"' in response.text
    assert 'id="jellyfinShell"' in response.text
    assert 'id="jfSearchInput"' in response.text
    assert 'id="nowLangBtn"' in response.text
    assert 'id="nowSubLangBtn"' in response.text
    assert 'id="aboutBtn"' in response.text
    assert 'id="aboutBackdrop"' in response.text
    assert 'id="setIdleNotificationsEnabled"' in response.text
    assert 'id="setCecEnabled"' in response.text
    assert 'id="setCecStatus"' in response.text
    assert 'id="setCecAvailabilityHint"' in response.text
    assert 'id="setTvTakeoverEnabled"' in response.text
    assert 'id="setTvPauseOnInputChange"' in response.text
    assert 'id="setTvAutoResumeOnReturn"' in response.text
    assert "fetch('/tv/status')" in response.text
    assert "SETTINGS_TV_CONTROL_BASELINE" in response.text
    assert "Object.entries(tvControl).forEach" in response.text
    assert 'id="aboutGithubLink"' in response.text
    assert 'id="aboutVersionValue"' in response.text
    assert 'id="aboutRevisionValue"' in response.text
    assert 'id="aboutUpdateValue"' in response.text
    assert 'id="aboutChangelogLink"' in response.text
    assert 'id="aboutReleaseLink"' in response.text
    assert 'https://github.com/mcgeezy/relaytv' in response.text
    assert 'id="aboutSupportLink"' in response.text
    assert 'https://buymeacoffee.com/relaytv' in response.text
    assert 'img.buymeacoffee.com/button-api' in response.text
    assert 'function openAbout' in response.text
    assert "async function loadAboutInfo" in response.text
    assert "fetch('/app/info'" in response.text
    assert 'id="notifySection"' in response.text
    assert 'id="notifyTextInput"' in response.text
    assert 'id="notifyImageInput"' in response.text
    assert 'accept="image/*"' in response.text
    assert 'id="notifyImageUrlInput"' in response.text
    assert 'placeholder="Or paste image URL…"' in response.text
    assert 'id="notifyPositionSelect"' in response.text
    assert '<option value="top-left" selected>Top left</option>' in response.text
    assert 'id="notifyDurationInput"' in response.text
    assert 'id="notifySendBtn"' in response.text
    assert "async function submitNotificationToast()" in response.text
    assert "const imageUrl = file ? await readNotifyImageDataUrl(file) : String(imageUrlEl?.value || '').trim();" in response.text
    assert "await _fetchWithTimeout('/overlay'" in response.text
    assert 'bindAboutUi();' in response.text
    assert 'class="nowSubRow"' in response.text
    assert 'id="langBackdrop"' in response.text
    assert 'id="subLangBackdrop"' in response.text
    assert 'role="tablist"' in response.text
    assert 'role="tab"' in response.text
    assert 'id="jfDetailBackdrop"' in response.text
    assert 'id="jfSortSelect"' in response.text
    assert 'id="jfAlphaIndicator"' in response.text
    assert 'id="remoteVolSlider"' in response.text
    assert 'id="remoteVolValue"' in response.text
    assert 'id="setUploadMaxSize"' in response.text
    assert 'id="setUploadRetentionHours"' in response.text
    assert 'id="setIdleDashboardEnabled"' in response.text
    assert 'id="setYtUseInvidious"' in response.text
    assert 'id="setIdleQrEnabled"' in response.text
    assert 'id="setJfEnabled"' in response.text
    assert 'id="setJfClearPassword"' in response.text
    assert 'id="setJfStatus" class="sectionStatus unknown">Disabled</span>' in response.text
    assert "jfBadge.textContent = enabled ? (up ? 'Connected' : 'Down') : 'Disabled';" in response.text
    assert 'class="toggleSwitch"' in response.text
    assert 'data-idle-enable="${key}"' in response.text
    assert 'class="chk"' not in response.text
    assert '.settingsBody input.input:not([type])' in css
    assert '.settingsBody select.input{' in css
    assert 'appearance:none;' in css
    assert 'Show idle dashboard between plays' in response.text
    assert 'Use Invidious server for YouTube playback' in response.text
    assert 'Show connect QR in idle' in response.text
    assert 'Enable Jellyfin integration' in response.text
    assert 'function _uploadBadge(item)' in response.text
    assert 'function _uploadSummary(item)' in response.text
    assert 'function _formatUploadSize(bytes)' in response.text
    assert 'mediaBadge' in response.text
    assert 'isUnavailable' in response.text
    assert 'Playback unavailable: stored upload was removed' in response.text
    assert 'onclick="post(\'/close\')"' in response.text
    assert "await post('/now_playing/clear');" in response.text
    assert 'id="jfSearchBtn"' not in response.text
    assert 'id="jfRefreshBtn"' not in response.text
    assert 'id="jfReconnectBtn"' not in response.text
    assert 'function _jfSetActionStatus' in response.text
    assert 'function _jfSetLaunchVisible' in response.text
    assert 'function _jfCloseDetailPanel' in response.text
    assert 'function _labelNowSubtitleLanguage' in response.text
    assert 'function _renderNowSubtitleButton' in response.text
    assert 'function _fetchNowSubtitleOptions' in response.text
    assert 'function _renderNowSubtitleOptions' in response.text
    assert 'function openNowSubtitleModal' in response.text
    assert 'function bindNowSubtitleUi' in response.text
    assert 'const shellRect = shell.getBoundingClientRect();' in response.text
    assert 'const rawTop = gutter - (gridRect.top - shellRect.top);' in response.text
    assert 'function loadJellyfinMovies' in response.text
    assert 'function loadJellyfinTvSeries' in response.text
    assert 'function _jfPlayAllSeries' in response.text
    assert 'function _jfSyncTabControls' in response.text
    assert 'function _jfScheduleSearch' in response.text
    assert 'function _jfBuildRowItemCard' in response.text
    assert 'const __JF_REQ_TIMEOUT_MS' in response.text
    assert 'function _jfFetchWithTimeout' in response.text
    assert 'function _applyQueueSnapshot' in response.text
    assert 'touch-action: none;' in css
    assert "_applyQueueSnapshot(payload);" in response.text
    assert "await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});" in response.text
    assert "play.disabled = !available;" in response.text
    assert "queue.disabled = !available;" in response.text
    assert "await fetch('/jellyfin/subtitle/select'" in response.text
    assert 'id="setAboutGithubLink"' not in response.text
    assert 'id="setAboutSupportLink"' not in response.text


def test_health_endpoint() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/health')

    assert response.status_code == 200
    assert response.json() == {'ok': True}


def test_app_info_endpoint_reports_version_and_update_status(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYTV_IMAGE_VERSION", "v0.1.0")
    monkeypatch.setenv("RELAYTV_IMAGE_REVISION", "abcdef1234567890")
    monkeypatch.setenv("RELAYTV_IMAGE_CREATED", "2026-06-28T00:00:00Z")
    monkeypatch.setenv("RELAYTV_IMAGE_SOURCE", "https://github.com/mcgeezy/relaytv")
    monkeypatch.setattr(
        app_info_routes,
        "_latest_release_from_github",
        lambda: (
            {
                "tag_name": "v0.2.0",
                "name": "v0.2.0",
                "html_url": "https://github.com/mcgeezy/relaytv/releases/tag/v0.2.0",
                "published_at": "2026-06-28T01:00:00Z",
            },
            "",
            123.0,
        ),
    )
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/app/info')

    assert response.status_code == 200
    payload = response.json()
    assert payload["name"] == "RelayTV"
    assert payload["version"] == "v0.1.0"
    assert payload["revision_short"] == "abcdef123456"
    assert payload["image_created"] == "2026-06-28T00:00:00Z"
    assert payload["changelog_url"] == "https://github.com/mcgeezy/relaytv/blob/main/CHANGELOG.md"
    assert payload["current_release_url"] == "https://github.com/mcgeezy/relaytv/releases/tag/v0.1.0"
    assert payload["latest_release"]["tag_name"] == "v0.2.0"
    assert payload["update_available"] is True


def test_release_compose_uses_published_image_without_source_build() -> None:
    text = (ROOT_DIR / "docker-compose.release.yml").read_text()

    assert "image: \"${RELAYTV_IMAGE_REF:-ghcr.io/mcgeezy/relaytv:latest}\"" in text
    assert "build:" not in text
    assert "context: ./app" not in text
    assert "./data:/data" in text
    assert "XDG_SESSION_TYPE=${RELAYTV_HOST_SESSION_TYPE:-${XDG_SESSION_TYPE-}}" in text
    assert "RELAYTV_HOST_SESSION_TYPE=${RELAYTV_HOST_SESSION_TYPE:-${XDG_SESSION_TYPE-}}" in text


def test_root_bootstrap_installer_downloads_release_bundle() -> None:
    text = (ROOT_DIR / "install.sh").read_text()

    assert "docker-compose.release.yml" in text
    assert "scripts/install.sh" in text
    assert "scripts/doctor.sh" in text
    assert "docker compose pull" in text
    assert "docker compose up -d" in text
    assert "RELAYTV_CEC_ENABLED" in text
    assert "--enable-cec" in text
    assert 'RELAYTV_CEC_ENABLED="$CEC_CHOICE"' in text
    assert 'RELAYTV_INSTALL_YES="$ASSUME_YES"' in text
    assert "detect_cec_devices" not in text
    assert "prompt_enable_cec" not in text
    assert "--force" in text
    assert "INSTALL_DIR=\"$(default_install_dir)\"" in text
    assert "confirm_current_directory_install" in text
    assert "RelayTV will be installed in the current directory" in text


def test_install_scripts_have_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(ROOT_DIR / "install.sh")], check=True)
    subprocess.run(["bash", "-n", str(ROOT_DIR / "scripts/install.sh")], check=True)


def test_repo_installer_persists_detected_host_session_type_for_ssh_installs() -> None:
    text = (ROOT_DIR / "scripts/install.sh").read_text()
    compose = (ROOT_DIR / "docker-compose.yml").read_text()

    assert 'emit_env_line "RELAYTV_HOST_SESSION_TYPE" "${XDG_SESSION_TYPE_VAL}"' in text
    assert 'emit_env_line "RELAYTV_HOST_PROFILE" "${HOST_PROFILE}"' in text
    assert "XDG_SESSION_TYPE=${RELAYTV_HOST_SESSION_TYPE:-${XDG_SESSION_TYPE-}}" in compose
    assert "RELAYTV_HOST_SESSION_TYPE=${RELAYTV_HOST_SESSION_TYPE:-${XDG_SESSION_TYPE-}}" in compose


def test_installer_leaves_app_policy_defaults_to_entrypoint() -> None:
    text = (ROOT_DIR / "scripts/install.sh").read_text()
    compose = (ROOT_DIR / "docker-compose.yml").read_text()

    assert 'QT_RUNTIME_MODE_FROM_ENV="0"' in text
    assert 'QT_SHELL_MPV_ARGS_FROM_ENV="0"' in text
    assert '[ "${RELAYTV_QT_RUNTIME_MODE+x}" = "x" ]' in text
    assert '[ "${RELAYTV_QT_SHELL_MPV_ARGS+x}" = "x" ]' in text
    assert 'if [ "${QT_RUNTIME_MODE_FROM_ENV}" = "1" ]' in text
    assert '[ "${QT_RUNTIME_MODE_VAL}" != "auto" ]' not in text
    assert 'if [ "${QT_SHELL_MPV_ARGS_FROM_ENV}" = "1" ]' in text
    assert 'if [ "${QT_SHELL_MPV_ARGS_FROM_ENV}" = "1" ] && [ -n "${QT_SHELL_MPV_ARGS_VAL}" ]' not in text
    assert "RELAYTV_PLAYER_BACKEND=${RELAYTV_PLAYER_BACKEND:-qt}" not in compose
    assert "RELAYTV_QT_RUNTIME_MODE=${RELAYTV_QT_RUNTIME_MODE:-auto}" not in compose
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=${RELAYTV_HEADLESS_REMOTE_ENABLED:-0}" not in compose
    assert "RELAYTV_YTDLP_AUTO_UPDATE=${RELAYTV_YTDLP_AUTO_UPDATE:-0}" not in compose


def test_entrypoint_fills_runtime_policy_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container_entrypoint, "_host_profile", lambda env: "raspi")
    monkeypatch.setattr(container_entrypoint, "_has_dri", lambda: True)
    env = {
        "RELAYTV_MODE": "wayland",
        "QT_QPA_PLATFORM": "xcb",
    }

    container_entrypoint._normalize_runtime_defaults(env)

    assert env["RELAYTV_QT_RUNTIME_MODE"] == "embed"
    assert env["RELAYTV_QT_SHELL_MPV_ARGS"] == "--gpu-api=opengl --opengl-es=yes"


def test_entrypoint_skips_pi_mpv_args_without_dri(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container_entrypoint, "_host_profile", lambda env: "raspi")
    monkeypatch.setattr(container_entrypoint, "_has_dri", lambda: False)
    env = {
        "RELAYTV_MODE": "wayland",
        "QT_QPA_PLATFORM": "xcb",
    }

    container_entrypoint._normalize_runtime_defaults(env)

    assert env["RELAYTV_QT_RUNTIME_MODE"] == "embed"
    assert "RELAYTV_QT_SHELL_MPV_ARGS" not in env


def test_entrypoint_preserves_explicit_pi_mpv_args(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(container_entrypoint, "_host_profile", lambda env: "raspi")
    monkeypatch.setattr(container_entrypoint, "_has_dri", lambda: True)
    env = {
        "RELAYTV_MODE": "wayland",
        "QT_QPA_PLATFORM": "xcb",
        "RELAYTV_QT_SHELL_MPV_ARGS": "",
    }

    container_entrypoint._normalize_runtime_defaults(env)

    assert env["RELAYTV_QT_SHELL_MPV_ARGS"] == ""


def test_entrypoint_enables_headless_remote_from_mode() -> None:
    env = {"RELAYTV_MODE": "headless"}

    container_entrypoint._normalize_runtime_defaults(env)

    assert env["RELAYTV_HEADLESS_REMOTE_ENABLED"] == "1"


def test_repo_installer_generates_host_device_override_for_cec() -> None:
    text = (ROOT_DIR / "scripts/install.sh").read_text()

    assert "RELAYTV_CEC_ENABLED" in text
    assert "RELAYTV_CEC_MONITOR" in text
    assert "RELAYTV_CEC" in text
    assert "Optional HDMI-CEC control" in text
    assert "detect_cec_device_nodes" in text
    assert "resolve_cec_enabled" in text
    assert "HDMI-CEC hardware was detected" in text
    assert "Enable HDMI-CEC passthrough? [y/N]" in text
    assert 'CEC_ENABLED_VAL="${RELAYTV_CEC_ENABLED:-auto}"' in text
    assert text.index('if [ "$requested" = "1" ]') < text.index('if [ -z "$summary" ]')
    assert "host-device-overrides" in text
    assert "/dev/cec*" in text
    assert "cec-client -l" in text
    assert "/dev/(cec[0-9]+|ttyACM[0-9]+)" in text
    assert "detect_cec_device_group_ids" in text
    assert "group_add:" in text
    assert "sort -u" in text


def test_repo_installer_generates_nvidia_passthrough_when_supported() -> None:
    text = (ROOT_DIR / "scripts/install.sh").read_text()
    install_doc = (ROOT_DIR / "docs/INSTALL.md").read_text()

    assert "detect_nvidia_device" in text
    assert "detect_nvidia_docker_toolkit" in text
    assert "NVIDIA_PASSTHROUGH_ENABLED" in text
    assert "gpus: all" in text
    assert "NVIDIA_VISIBLE_DEVICES=all" in text
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,video,graphics,utility" in text
    assert "NVIDIA decoder passthrough disabled" in text
    assert "Docker NVIDIA toolkit" in text
    assert "Container Toolkit" in install_doc


def test_repo_installer_does_not_print_runtime_test_next_steps() -> None:
    text = (ROOT_DIR / "scripts/install.sh").read_text()

    assert 'say "Next:"' not in text
    assert "host-ops.sh up --" not in text
    assert "host-ops.sh soak" not in text
    assert "host-ops.sh native-ready" not in text


def test_cec_send_uses_running_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []

    class FakeStdin:
        def write(self, value: str) -> None:
            writes.append(value)

        def flush(self) -> None:
            writes.append("<flush>")

    class FakeProc:
        pid = 1234
        stdin = FakeStdin()

        def poll(self):
            return None

    def fail_run(*args, **kwargs):
        raise AssertionError("one-shot cec-client should not run when controller is alive")

    monkeypatch.setenv("RELAYTV_CEC", "1")
    monkeypatch.setenv("RELAYTV_CEC_MONITOR", "1")
    monkeypatch.setattr(player, "_CEC_CONTROLLER_PROC", FakeProc())
    monkeypatch.setattr(player, "cec_probe_status", lambda force=False: {"available": True})
    monkeypatch.setattr(player.subprocess, "run", fail_run)

    player.cec_send("on 0\nas\n")

    assert writes == ["on 0\nas\n", "<flush>"]
    status = player.cec_controller_status()
    assert status["last_command"] == "on 0\nas"
    assert status["last_command_ok"] is True
    assert status["last_command_state"] == "sent"
    assert status["availability"]["available"] is True


def test_cec_send_falls_back_to_one_shot_without_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return Result()

    monkeypatch.setenv("RELAYTV_CEC", "1")
    monkeypatch.setenv("RELAYTV_CEC_MONITOR", "0")
    monkeypatch.setattr(player, "_CEC_CONTROLLER_PROC", None)
    monkeypatch.setattr(player.subprocess, "run", fake_run)
    monkeypatch.setattr(player, "cec_probe_status", lambda force=False: {"available": False})

    player.cec_send("pow 0\n")

    assert calls
    assert calls[0]["args"] == ["cec-client", "-s", "-d", "1"]
    assert calls[0]["input"] == "pow 0\n"
    status = player.cec_controller_status()
    assert status["last_command_state"] == "completed"


def test_share_requests_cec_takeover_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(routes, "_smart_item_from_url", lambda url: {"url": url})

    def fake_play_item(item, use_resolver, cec, clear_queue, mode, start_pos=None):
        observed.update(
            {
                "item": item,
                "use_resolver": use_resolver,
                "cec": cec,
                "clear_queue": clear_queue,
                "mode": mode,
                "start_pos": start_pos,
            }
        )
        return {"url": item["url"]}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)

    response = routes.share(url="https://example.test/video")

    assert response["status"] == "playing"
    assert observed["cec"] is True


def test_cec_request_flag_does_not_bypass_disabled_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_CEC", raising=False)
    monkeypatch.delenv("RELAYTV_CEC_ENABLED", raising=False)
    monkeypatch.delenv("RELAYTV_CEC_ALLOW_REQUEST_OVERRIDE", raising=False)
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "0"})

    assert player.cec_enabled(True) is False
    assert player.cec_auto_on_switch(True) is False

    monkeypatch.setenv("RELAYTV_CEC_ALLOW_REQUEST_OVERRIDE", "1")

    assert player.cec_enabled(True) is True
    assert player.cec_auto_on_switch(True) is True


def test_cec_env_controls_runtime_policy_over_stale_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYTV_CEC", "1")
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "0"})

    assert player.cec_enabled(False) is True
    assert player.cec_monitor_enabled() is True

    monkeypatch.setenv("RELAYTV_CEC", "0")
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "1"})

    assert player.cec_enabled(False) is False
    assert player.cec_monitor_enabled() is False


def test_cec_setting_controls_runtime_policy_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_CEC", raising=False)
    monkeypatch.delenv("RELAYTV_CEC_ENABLED", raising=False)
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "0"})

    assert player.cec_enabled(False) is False
    assert player.cec_monitor_enabled() is False

    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "1"})

    assert player.cec_enabled(False) is True
    assert player.cec_monitor_enabled() is True


def test_cec_legacy_enabled_env_is_runtime_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_CEC", raising=False)
    monkeypatch.setenv("RELAYTV_CEC_ENABLED", "1")
    monkeypatch.setattr(player.state, "get_settings", lambda: {})

    assert player.cec_enabled(False) is True


def test_cec_controller_status_includes_availability(monkeypatch: pytest.MonkeyPatch) -> None:
    availability = {
        "available": False,
        "cec_client_available": True,
        "devices": ["/dev/cec0"],
        "adapters_reported": [],
        "permission_ok": False,
    }
    monkeypatch.setattr(player, "_CEC_CONTROLLER_PROC", None)
    monkeypatch.setattr(player, "cec_probe_status", lambda force=False: availability)

    status = player.cec_controller_status()

    assert status["availability"] == availability


def test_update_settings_syncs_cec_env_and_stops_monitor(monkeypatch: pytest.MonkeyPatch) -> None:
    stopped: list[bool] = []

    monkeypatch.setenv("RELAYTV_CEC", "1")
    monkeypatch.setattr(routes.state, "get_settings", lambda: {"cec_enabled": "1"})
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: {**{"cec_enabled": "1"}, **patch})
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "stop_cec_monitor", lambda: stopped.append(True))
    monkeypatch.setattr(routes.player, "start_cec_monitor", lambda: None)

    response = routes.update_settings(routes.SettingsReq(cec_enabled="0"))

    assert os.environ["RELAYTV_CEC"] == "0"
    assert os.environ["RELAYTV_CEC_ENABLED"] == "0"
    assert stopped == [True]
    assert "cec_enabled" in response["live_applied"]


def test_play_item_attempts_cec_takeover_without_probe_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    takeover_calls: list[bool] = []
    now_values: list[dict] = []

    monkeypatch.setattr(player, "update_history_progress", lambda *a, **k: None)
    monkeypatch.setattr(player, "_mark_playback_transition", lambda *a, **k: None)
    monkeypatch.setattr(player, "cec_auto_on_switch", lambda cec: True)
    monkeypatch.setattr(player, "cec_available", lambda: False)
    monkeypatch.setattr(player, "tv_on_and_switch", lambda: takeover_calls.append(True))
    monkeypatch.setattr(player, "validate_user_url", lambda url: url)
    monkeypatch.setattr(player, "provider_from_url", lambda url: "generic")
    monkeypatch.setattr(player, "_env_bool", lambda name, default=False: False if name == "RELAYTV_MPV_YTDL" else default)
    monkeypatch.setattr(player, "_providers_forced_to_resolve", lambda: set())
    monkeypatch.setattr(player, "_fresh_prefetched_stream", lambda item: None)
    monkeypatch.setattr(player, "_normalize_start_pos", lambda value: value)
    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", lambda *a, **k: False)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "start_mpv", lambda *a, **k: None)
    monkeypatch.setattr(player, "_add_history_entry", lambda now: None)
    monkeypatch.setattr(player, "_prime_mpv_up_next_from_queue", lambda force=False: False)
    monkeypatch.setattr(player.state, "get_tv_state", lambda: {"active_source_phys_addr": "2000"})
    monkeypatch.setattr(player, "_our_phys_addr", lambda: "1000")
    monkeypatch.setattr(player.state, "persist_queue", lambda: None)
    monkeypatch.setattr(player.state, "set_now_playing", lambda value: now_values.append(value))
    monkeypatch.setattr(player.state, "set_session_state", lambda value: None)
    monkeypatch.setattr(player.state, "set_pause_reason", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_position", lambda value: None)

    result = player.play_item(
        {"url": "https://example.test/video", "title": "Example"},
        use_resolver=False,
        cec=True,
        clear_queue=False,
        mode="share",
    )

    assert takeover_calls == [True]
    assert result["url"] == "https://example.test/video"
    assert now_values


def test_public_install_docs_offer_latest_without_full_image_variant() -> None:
    text = "\n".join(
        [
            (ROOT_DIR / "README.md").read_text(),
            (ROOT_DIR / "docs/INSTALL.md").read_text(),
        ]
    )

    assert "ghcr.io/mcgeezy/relaytv:latest" in text
    assert ":full" not in text
    assert "docker-image-full" not in text
    assert "suffix=-full" not in text


def test_main_ci_build_publishes_main_tag_not_release_latest() -> None:
    text = (ROOT_DIR / ".github/workflows/ci.yml").read_text()

    assert "ghcr.io/${{ github.repository }}:main" in text
    assert "ghcr.io/${{ github.repository }}:latest" not in text


def test_release_image_traceability_metadata_is_documented() -> None:
    dockerfile = (ROOT_DIR / "app/Dockerfile").read_text()
    compose = (ROOT_DIR / "docker-compose.yml").read_text()
    workflow = (ROOT_DIR / ".github/workflows/ci.yml").read_text()
    release_doc = (ROOT_DIR / "docs/RELEASE.md").read_text()
    pyproject = (ROOT_DIR / "pyproject.toml").read_text()

    assert "python:3.13-slim@sha256:" in dockerfile
    assert 'org.opencontainers.image.source="${RELAYTV_IMAGE_SOURCE}"' in dockerfile
    assert 'org.opencontainers.image.revision="${RELAYTV_IMAGE_REVISION}"' in dockerfile
    assert 'org.opencontainers.image.licenses="GPL-3.0-only"' in dockerfile
    assert 'ENV RELAYTV_IMAGE_SOURCE="${RELAYTV_IMAGE_SOURCE}"' in dockerfile
    assert 'RELAYTV_IMAGE_VERSION="${RELAYTV_IMAGE_VERSION}"' in dockerfile
    assert "COPY LICENSE COPYING THIRD_PARTY_LICENSES.md ASSETS.md /usr/share/doc/relaytv/" in dockerfile
    assert "context: ." in compose
    assert "dockerfile: app/Dockerfile" in compose
    assert "context: ." in workflow
    assert "file: ./app/Dockerfile" in workflow
    assert "RELAYTV_IMAGE_REVISION=${{ github.sha }}" in workflow
    assert "RELAYTV_YTDLP_AUTO_UPDATE=0" in release_doc
    assert "GPL-3.0-only" in pyproject


def test_release_please_automation_is_configured() -> None:
    config = (ROOT_DIR / "release-please-config.json").read_text()
    manifest = (ROOT_DIR / ".release-please-manifest.json").read_text()
    pyproject = (ROOT_DIR / "pyproject.toml").read_text()
    workflow = (ROOT_DIR / ".github/workflows/release-please.yml").read_text()
    pr_title = (ROOT_DIR / ".github/workflows/pr-title.yml").read_text()
    pr_template = (ROOT_DIR / ".github/pull_request_template.md").read_text()
    agents = (ROOT_DIR / "AGENTS.md").read_text()
    changelog = (ROOT_DIR / "CHANGELOG.md").read_text()
    release_doc = (ROOT_DIR / "docs/RELEASE.md").read_text()

    assert '"release-type": "python"' in config
    assert '"package-name": "relaytv"' in config
    assert '"bootstrap-sha": "0c270faaccf1361416538a6230758b6bbe69bc17"' in config
    assert '"draft": true' in config
    assert '"force-tag-creation": true' in config
    assert json.loads(manifest)["."] == tomllib.loads(pyproject)["project"]["version"]
    assert "googleapis/release-please-action@v4" in workflow
    assert "contents: write" in workflow
    assert "pull-requests: write" in workflow
    assert "packages: write" in workflow
    assert "token: ${{ secrets.GITHUB_TOKEN }}" in workflow
    assert "RELEASE_PLEASE_TOKEN" not in workflow
    assert "ghcr.io/${{ github.repository }}:${{ needs.release-please.outputs.tag_name }}" in workflow
    assert "Publish GitHub Release after image push" in workflow
    assert 'gh release edit "${{ needs.release-please.outputs.tag_name }}" --draft=false' in workflow
    assert workflow.index("Publish release Docker image") < workflow.index("Publish GitHub Release after image push")
    assert "Conventional Commit PR title" in pr_title
    assert "User impact:" in pr_template
    assert "Operator/deployment impact:" in pr_template
    assert "Release Please owns version bumps" in agents
    assert "Release notes are maintained by Release Please." in changelog
    assert "Automated Release Flow" in release_doc
    assert "built-in `GITHUB_TOKEN`" in release_doc
    assert "Only after the image push succeeds" in release_doc


def test_api_docs_include_app_info_endpoint() -> None:
    text = (ROOT_DIR / "docs/API.md").read_text()
    release_doc = (ROOT_DIR / "docs/RELEASE.md").read_text()

    assert "GET /app/info" in text
    assert "RELAYTV_UPDATE_CHECK_DISABLED=1" in release_doc


def test_first_party_source_files_have_spdx_headers() -> None:
    checked: list[Path] = []
    checked.extend((ROOT_DIR / "app/relaytv_app").glob("*.py"))
    checked.extend((ROOT_DIR / "app/relaytv_app/integrations").glob("*.py"))
    checked.extend((ROOT_DIR / "tests").glob("*.py"))
    checked.extend((ROOT_DIR / "scripts").glob("*.sh"))
    checked.append(ROOT_DIR / "install.sh")

    assert checked
    for path in checked:
        head = "\n".join(path.read_text().splitlines()[:3])
        assert "SPDX-License-Identifier: GPL-3.0-only" in head, str(path)


def test_api_docs_include_uploaded_media_endpoints() -> None:
    text = (ROOT_DIR / "docs/API.md").read_text()

    assert "POST /ingest/media" in text
    assert "POST /ingest/media/enqueue" in text
    assert "POST /ingest/media/play" in text
    assert "GET /media/uploads/{upload_id}/{filename}" in text
    assert "multipart/form-data" in text
    assert "uploads.max_size_gb" in text
    assert "uploads.retention_hours" in text


def test_ingest_media_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["title"] == "Shared Clip"
    assert body["item"]["size_bytes"] == len(b"video-bytes")
    assert body["url"].endswith(".mp4")

    fetch = client.get(body["media_path"])
    assert fetch.status_code == 200
    assert fetch.content == b"video-bytes"

    queued = client.post("/enqueue", json={"url": body["url"]})
    assert queued.status_code == 200
    queued_body = queued.json()
    assert queued_body["item"]["provider"] == "upload"
    assert queued_body["item"]["title"] == "Shared Clip"
    assert queued_body["queue_length"] == 1


def test_ingest_audio_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared Audio"},
        files={"file": ("clip.mp3", b"audio-bytes", "audio/mpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["title"] == "Shared Audio"
    assert body["item"]["mime_type"] == "audio/mpeg"
    assert body["item"]["size_bytes"] == len(b"audio-bytes")
    assert body["url"].endswith(".mp3")

    fetch = client.get(body["media_path"])
    assert fetch.status_code == 200
    assert fetch.content == b"audio-bytes"

    queued = client.post("/enqueue", json={"url": body["url"]})
    assert queued.status_code == 200
    queued_body = queued.json()
    assert queued_body["item"]["provider"] == "upload"
    assert queued_body["item"]["mime_type"] == "audio/mpeg"
    assert queued_body["queue_length"] == 1


def test_ingest_m4a_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared M4A"},
        files={"file": ("clip.m4a", b"audio-bytes", "audio/m4a")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["title"] == "Shared M4A"
    assert body["item"]["mime_type"] == "audio/m4a"
    assert body["url"].endswith(".m4a")

    queued = client.post("/enqueue", json={"url": body["url"]})
    assert queued.status_code == 200
    queued_body = queued.json()
    assert queued_body["item"]["provider"] == "upload"
    assert queued_body["item"]["mime_type"] == "audio/m4a"
    assert queued_body["queue_length"] == 1


def test_ingest_audio_ogg_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared OGG"},
        files={"file": ("clip.ogg", b"audio-bytes", "audio/ogg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["mime_type"] == "audio/ogg"
    assert body["url"].endswith(".ogg")


def test_ingest_audio_octet_stream_uses_allowed_extension(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Generic M4A"},
        files={"file": ("clip.m4a", b"audio-bytes", "application/octet-stream")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["mime_type"] == "application/octet-stream"
    assert body["url"].endswith(".m4a")


def test_ingest_media_enqueue_single_call(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media/enqueue",
        data={"title": "Queued Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "enqueue"
    assert body["item"]["provider"] == "upload"
    assert body["result"]["status"] == "queued"
    assert body["result"]["item"]["provider"] == "upload"
    assert body["result"]["queue_length"] == 1


def test_ingest_media_rejects_unsupported_type(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        files={"file": ("clip.mov", b"video-bytes", "video/quicktime")},
    )

    assert response.status_code == 400
    assert "Unsupported media type" in response.json()["detail"]


def test_ingest_media_rejects_empty_upload(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        files={"file": ("clip.mp4", b"", "video/mp4")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded media is empty"


def test_play_now_accepts_uploaded_media_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: None)

    captured: dict[str, object] = {}

    def fake_play_item(url, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["url"] = url
        return {"url": url, "provider": "upload", "title": "Shared Clip"}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    ).json()

    response = client.post("/play_now", json={"url": created["url"]})

    assert response.status_code == 200
    assert captured["url"] == created["url"]
    assert response.json()["now_playing"]["provider"] == "upload"


def test_play_now_accepts_uploaded_audio_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: None)

    captured: dict[str, object] = {}

    def fake_play_item(url, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["url"] = url
        return {"url": url, "provider": "upload", "title": "Shared Audio", "mime_type": "audio/mpeg"}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Shared Audio"},
        files={"file": ("clip.mp3", b"audio-bytes", "audio/mpeg")},
    ).json()

    response = client.post("/play_now", json={"url": created["url"]})

    assert response.status_code == 200
    assert captured["url"] == created["url"]
    assert response.json()["now_playing"]["provider"] == "upload"


def test_upload_items_mark_unavailable_after_removal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)
    monkeypatch.setattr(routes.state, "HISTORY", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    ).json()
    item = dict(created["item"])
    routes.state.QUEUE[:] = [dict(item)]
    routes.state.HISTORY[:] = [dict(item)]

    shutil.rmtree(upload_store.upload_dir(created["media_id"]))

    queue_response = client.get("/queue")
    assert queue_response.status_code == 200
    assert queue_response.json()["queue"][0]["available"] is False

    history_response = client.get("/history")
    assert history_response.status_code == 200
    assert history_response.json()["history"][0]["available"] is False


def test_enqueue_stale_uploaded_media_returns_gone(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    ).json()

    shutil.rmtree(upload_store.upload_dir(created["media_id"]))

    response = client.post("/enqueue", json={"url": created["url"]})

    assert response.status_code == 410
    assert response.json()["detail"] == "Uploaded media expired or removed"


def test_ingest_media_play_starts_progressively(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB", "1")
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS", "1")
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    captured: dict[str, object] = {}
    toasts: list[dict[str, object]] = []

    def fake_play_item(item, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["item"] = dict(item)
        captured["mode"] = mode
        return {"url": item["url"], "provider": "upload", "title": item["title"]}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: toasts.append(dict(kwargs)))

    app = create_app(testing=True)
    client = TestClient(app)

    payload = (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + (b"x" * 1024) + b"moov" + (b"y" * (2 * 1024 * 1024)))
    response = client.post(
        "/ingest/media/play",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["playback_mode"] == "progressive"
    assert body["now_playing"]["provider"] == "upload"
    assert body["fallback_reason"] == ""
    assert captured["mode"] == "ingest_media_play"
    assert str(captured["item"]["_local_stream_path"]).endswith(".mp4")
    assert toasts == []


def test_ingest_media_play_falls_back_to_full_upload_with_toast(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB", "1")
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS", "1")
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    captured: dict[str, object] = {}
    toasts: list[dict[str, object]] = []

    def fake_play_item(item, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["item"] = dict(item)
        captured["mode"] = mode
        return {"url": item["url"], "provider": "upload", "title": item["title"]}

    def fake_progressive_start_ready(meta: dict, session: dict) -> tuple[bool, str]:
        if int(session.get("bytes_received") or 0) >= int(session.get("ready_threshold_bytes") or 0):
            return False, "probe_failed"
        return False, "buffering"

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: toasts.append(dict(kwargs)))
    monkeypatch.setattr(upload_store, "progressive_start_ready", fake_progressive_start_ready)

    app = create_app(testing=True)
    client = TestClient(app)

    payload = (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + (b"x" * (2 * 1024 * 1024)))
    response = client.post(
        "/ingest/media/play",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["playback_mode"] == "full_upload"
    assert body["fallback_reason"] == "probe_failed"
    assert body["now_playing"]["provider"] == "upload"
    assert captured["mode"] == "ingest_media_play"
    assert len(toasts) == 1
    assert "Waiting for full file" in str(toasts[0].get("text") or "")


def test_idle_page_uses_banner_brand_asset() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/idle')

    assert response.status_code == 200
    assert '/pwa/brand/banner.png' in response.text
    assert 'html{font-size:clamp(12px,1.4815vmin,32px)}' in response.text
    assert '.time{font-size:8rem' in response.text
    assert 'width:min(100%,70rem)' in response.text
    assert "root.style.setProperty('--idleQrSizePx', `${size / 16}rem`);" in response.text


def test_overlay_playback_visibility_prefers_session_and_transition_signals() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/x11/overlay')

    assert response.status_code == 200
    assert 'function overlayPlaybackVisible(state)' in response.text
    assert "if (sessionState === 'closed') return false;" in response.text
    assert "j.native_qt_mpv_runtime_stream_loaded === true" in response.text
    assert "j.transition_in_progress === true" in response.text
    assert "return qtRuntimeActive || sessionActive;" in response.text
    assert "function updateOverlayToastScale()" in response.text
    assert "function idleDashboardEnabled(state)" in response.text
    assert "state.idle_dashboard_enabled === false" in response.text
    assert "const idleEnabled = idleDashboardEnabled(j);" in response.text
    assert '<iframe class="idleFrame" src="about:blank"' in response.text
    assert "Math.min(vw / 1920, vh / 1080)" in response.text
    assert "--toast-width" in response.text
    assert "root.style.setProperty(name, `${Math.round(Number(base) * scale)}px`);" in response.text


def test_x11_overlay_enabled_by_idle_notifications_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import x11_overlay

    monkeypatch.delenv("RELAYTV_X11_OVERLAY", raising=False)
    monkeypatch.delenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED", raising=False)
    monkeypatch.setattr(routes.state, "get_settings", lambda: {"idle_notifications_enabled": True})

    assert x11_overlay.overlay_enabled() is True


def test_x11_overlay_can_be_disabled_with_idle_notifications_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import x11_overlay

    monkeypatch.delenv("RELAYTV_X11_OVERLAY", raising=False)
    monkeypatch.setenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED", "0")
    monkeypatch.setattr(routes.state, "get_settings", lambda: {"idle_notifications_enabled": True})

    assert x11_overlay.overlay_enabled() is False


def test_x11_overlay_honors_explicit_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import x11_overlay

    monkeypatch.setenv("RELAYTV_X11_OVERLAY", "0")
    monkeypatch.setenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED", "1")
    monkeypatch.setattr(routes.state, "get_settings", lambda: {"idle_notifications_enabled": True})

    assert x11_overlay.overlay_enabled() is False


def test_x11_overlay_click_through_defaults_on() -> None:
    text = (ROOT_DIR / "app/relaytv_app/overlay_app.py").read_text()
    assert 'os.getenv("RELAYTV_OVERLAY_CLICKTHROUGH", "1")' in text


def test_x11_overlay_uses_qt_fallback_when_gtk_unavailable() -> None:
    text = (ROOT_DIR / "app/relaytv_app/overlay_app.py").read_text()
    assert "GTK/WebKitGTK overlay backend unavailable; trying Qt WebEngine fallback." in text
    assert "from PySide6.QtWebEngineWidgets import QWebEngineView" in text
    assert "Qt.WA_TransparentForMouseEvents" in text
    assert "view.page().setBackgroundColor(Qt.transparent)" in text
    assert "RELAYTV_QT_OVERLAY_SOFTWARE" in text
    assert "--disable-gpu-compositing" in text


def test_x11_overlay_launch_forces_xcb_with_clickthrough(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from relaytv_app import x11_overlay

    calls: list[dict] = []

    class DummyProc:
        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return DummyProc()

    monkeypatch.setattr(x11_overlay, "_OVERLAY_PROC", None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("QT_QPA_PLATFORM", "wayland")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("RELAYTV_OVERLAY_LOG", str(tmp_path / "overlay.log"))

    x11_overlay.start_overlay()

    assert len(calls) == 1
    env = calls[0]["kwargs"]["env"]
    assert env["QT_QPA_PLATFORM"] == "xcb"
    assert env["XDG_SESSION_TYPE"] == "x11"
    assert env["RELAYTV_OVERLAY_CLICKTHROUGH"] == "1"
    assert "WAYLAND_DISPLAY" not in env

    x11_overlay._OVERLAY_PROC = None


def test_x11_overlay_launch_repairs_stale_xauthority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from relaytv_app import x11_overlay

    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    stale_xauthority = tmp_path / ".Xauthority"
    stale_xauthority.write_text("stale")
    valid_xauthority = runtime_dir / ".mutter-Xwaylandauth.TEST"
    valid_xauthority.write_text("auth")
    os.utime(stale_xauthority, (1000, 1000))
    os.utime(valid_xauthority, (2000, 2000))
    calls: list[dict] = []

    class DummyProc:
        def poll(self):
            return None

    def fake_popen(*args, **kwargs):
        calls.append({"args": args, "kwargs": kwargs})
        return DummyProc()

    monkeypatch.setattr(x11_overlay, "_OVERLAY_PROC", None)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("XAUTHORITY", str(stale_xauthority))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_dir))
    monkeypatch.setenv("RELAYTV_OVERLAY_LOG", str(tmp_path / "overlay.log"))

    x11_overlay.start_overlay()

    assert calls[0]["kwargs"]["env"]["XAUTHORITY"] == str(valid_xauthority)

    x11_overlay._OVERLAY_PROC = None


def test_pwa_brand_banner_png_asset_resolves_with_logo_fallback() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/pwa/brand/banner.png')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith(('image/png', 'image/svg+xml'))


def test_jellyfin_plugin_ingress_is_deprecated() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post('/integrations/jellyfin/push', json={'item_id': '123', 'play_command': 'PlayNow'})

    assert response.status_code == 410
    assert response.json() == {
        'detail': 'jellyfin plugin ingress deprecated; use RelayTV native Jellyfin client or /integrations/jellyfin/command'
    }


def test_jellyfin_subtitle_options_include_off_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: {"server_url": "http://jf.local"})
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "provider": "jellyfin",
            "jellyfin_item_id": "item-1",
            "url": "http://jf.local/Videos/item-1/master.m3u8",
            "jellyfin_subtitle_stream_index": "-1",
        },
        raising=False,
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_item_detail",
        lambda item_id, refresh=False: {
            "subtitle_streams": [
                {"index": 0, "language": "en", "display": "English", "is_default": True},
                {"index": 1, "language": "es", "display": "Spanish", "is_default": False},
            ],
            "subtitle_language": "en",
        },
    )
    monkeypatch.setattr(routes.player, "mpv_get_many", lambda props: {"track-list": [], "sid": "no", "sub-visibility": False})

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get("/jellyfin/subtitle/options")

    assert response.status_code == 200
    body = response.json()
    assert body["current_subtitle_off"] is True
    assert body["current_subtitle_stream_index"] == -1
    assert body["current_subtitle_language"] == "off"
    assert body["options"][0]["is_off"] is True
    assert body["options"][0]["is_current"] is True
    assert body["options"][1]["language"] == "en"


def test_jellyfin_subtitle_select_can_turn_subtitles_off_in_place(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes, "_require_jellyfin_catalog_ready", lambda: {"server_url": "http://jf.local"})
    monkeypatch.setattr(
        routes.state,
        "NOW_PLAYING",
        {
            "provider": "jellyfin",
            "jellyfin_item_id": "item-1",
            "url": "http://jf.local/Videos/item-1/master.m3u8?audioStreamIndex=1&subtitleStreamIndex=0",
            "jellyfin_audio_stream_index": "1",
            "jellyfin_subtitle_stream_index": "0",
            "title": "Sample Item",
        },
        raising=False,
    )
    monkeypatch.setattr(
        routes.jellyfin_receiver,
        "get_item_detail",
        lambda item_id: {
            "subtitle_streams": [
                {"index": 0, "language": "en", "display": "English", "is_default": True},
            ],
            "audio_streams": [
                {"index": 1, "language": "en", "display": "English", "is_default": True},
            ],
        },
    )
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: patch)
    monkeypatch.setattr(routes, "_retarget_jellyfin_queue_stream_preferences", lambda: 0)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "mpv_get_many", lambda props: {})
    monkeypatch.setattr(routes, "_jellyfin_try_set_mpv_subtitle_track", lambda **kwargs: True)
    captured_now: dict[str, object] = {}
    monkeypatch.setattr(routes.state, "set_now_playing", lambda now: captured_now.update(now))
    monkeypatch.setattr(routes, "_jellyfin_emit_progress_hint", lambda: None)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post("/jellyfin/subtitle/select", json={"index": -1})

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["method"] == "mpv_runtime_sid"
    assert body["current_subtitle_stream_index"] == -1
    assert body["current_subtitle_off"] is True
    assert body["current_subtitle_language"] == "off"
    assert captured_now["jellyfin_subtitle_stream_index"] == "-1"
    assert captured_now["jellyfin_subtitle_language"] == "off"


def test_settings_apply_now_does_not_restart_closed_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(
        routes.state,
        'NOW_PLAYING',
        {
            'url': 'https://example.com/closed.mp4',
            'title': 'Closed',
            'closed': True,
            'resume_pos': 42.0,
        },
        raising=False,
    )
    monkeypatch.setattr(routes.state, 'get_settings', lambda: {'idle_dashboard_enabled': False})
    monkeypatch.setattr(
        routes.state,
        'update_settings',
        lambda patch: {'idle_dashboard_enabled': bool(patch.get('idle_dashboard_enabled'))},
    )
    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(
        routes.player,
        'restart_current',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('closed session must not restart')),
    )
    monkeypatch.setattr(routes, '_sync_idle_visual_surfaces_after_settings', lambda: None)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post('/settings', json={'idle_dashboard_enabled': True, 'apply_now': True})

    assert response.status_code == 200
    body = response.json()
    assert body['ok'] is True
    assert body['apply_now'] is True
    assert body['apply_performed'] is False
    assert body['apply_succeeded'] is False


def test_native_idle_weather_layout_normalizes_to_supported_values() -> None:
    assert _native_idle_weather_layout({}) == 'split'
    assert _native_idle_weather_layout({'idle_panels': {'weather': {'layout': 'minimal'}}}) == 'minimal'
    assert _native_idle_weather_layout({'idle_panels': {'weather': {'layout': 'hourly'}}}) == 'split'
    assert _native_idle_weather_layout({'idle_panels': {'weather': {'layout': 'unexpected'}}}) == 'split'


def test_qt_idle_defaults_prefer_browser_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_ENABLED', raising=False)
    monkeypatch.delenv('RELAYTV_QT_NATIVE_IDLE', raising=False)

    assert _embedded_web_overlay_enabled() is True
    assert _native_idle_overlay_enabled() is False


def test_qt_runtime_defaults_prefer_libmpv_and_overlay_toasts_on_x86(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_LIBMPV', raising=False)
    monkeypatch.delenv('RELAYTV_QT_NATIVE_TOASTS', raising=False)
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_SOFTWARE', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'x86_64')

    assert _libmpv_enabled() is True
    assert _native_overlay_toasts_enabled() is False
    assert _overlay_software_mode_enabled() is False


def test_qt_overlay_software_mode_defaults_on_for_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_SOFTWARE', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'aarch64')

    assert _overlay_software_mode_enabled() is True


def test_qt_cursor_defaults_to_persistent_hidden(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_CURSOR_MODE', raising=False)
    monkeypatch.delenv('RELAYTV_CURSOR_MODE', raising=False)
    monkeypatch.delenv('RELAYTV_QT_CURSOR_AUTOHIDE', raising=False)
    monkeypatch.delenv('RELAYTV_QT_CURSOR_REFRESH_MS', raising=False)

    assert _cursor_mode() == 'hidden'
    assert _cursor_hidden_refresh_ms() == 1000


def test_qt_cursor_mode_supports_autohide_and_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RELAYTV_QT_CURSOR_MODE', 'autohide')
    assert _cursor_mode() == 'autohide'

    monkeypatch.setenv('RELAYTV_QT_CURSOR_MODE', 'visible')
    assert _cursor_mode() == 'visible'

    monkeypatch.delenv('RELAYTV_QT_CURSOR_MODE', raising=False)
    monkeypatch.setenv('RELAYTV_QT_CURSOR_AUTOHIDE', '1')
    assert _cursor_mode() == 'autohide'

    monkeypatch.setenv('RELAYTV_QT_CURSOR_AUTOHIDE', '0')
    assert _cursor_mode() == 'visible'


def test_qt_cursor_manager_uses_persistent_sweep() -> None:
    text = (ROOT_DIR / 'app/relaytv_app/qt_shell_app.py').read_text()

    assert 'cursor_mode = _cursor_mode()' in text
    assert 'cursor_sweep_timer.timeout.connect(lambda: _hide_cursor(reason="sweep"))' in text
    assert 'QApplication.allWidgets()' in text
    assert 'app.changeOverrideCursor(blank)' in text


def test_qt_overlay_fallback_hides_cursor() -> None:
    text = (ROOT_DIR / 'app/relaytv_app/overlay_app.py').read_text()

    assert 'from PySide6.QtGui import QCursor' in text
    assert 'blank_cursor = QCursor(Qt.BlankCursor)' in text
    assert 'cursor_timer.timeout.connect(_hide_cursor)' in text


def test_qt_runtime_defaults_disable_libmpv_on_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_LIBMPV', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'aarch64')

    assert _libmpv_enabled() is False


def test_qt_libmpv_initial_stream_waits_for_render_context() -> None:
    text = (ROOT_DIR / 'app/relaytv_app/qt_shell_app.py').read_text()

    assert 'Initial media is loaded after QOpenGLWidget.initializeGL() creates the' in text
    assert 'def render_context_ready(self) -> bool:' in text
    assert 'if not libmpv_player.render_context_ready():' in text
    assert 'QTimer.singleShot(50, _load_initial_libmpv_stream)' in text
    assert 'self.load_stream((stream or "").strip()' not in text


def test_resolver_playback_transition_window_sec_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC', raising=False)
    assert player._resolver_playback_transition_window_sec() == 20.0

    monkeypatch.setenv('RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC', '2')
    assert player._resolver_playback_transition_window_sec() == 5.0

    monkeypatch.setenv('RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC', '120')
    assert player._resolver_playback_transition_window_sec() == 60.0


def test_mark_playback_transition_allows_longer_resolve_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.time, 'time', lambda: 1000.0)
    monkeypatch.setattr(player, '_PLAYBACK_TRANSITION_UNTIL', 0.0)

    player._mark_playback_transition(window_sec=20.0)

    assert player._PLAYBACK_TRANSITION_UNTIL == 1020.0


def test_youtube_arm_safe_strategies_prefer_quality_retries_before_plain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, '_preferred_js_runtime_spec', lambda: 'node')

    strategies = resolver._build_youtube_arm_safe_strategies(
        ['yt-dlp', '--cookies', '/data/cookies.txt', '--no-playlist'],
        ['fmt1', 'best'],
    )

    assert '--cookies' in strategies[0][0]
    assert '--remote-components' in strategies[0][0]
    assert strategies[0][1] == ['fmt1', 'best']
    assert strategies[-1][0] == ['yt-dlp', '--no-playlist']
    assert strategies[-1][1] == ['fmt1', 'best']


def test_youtube_strategies_prefer_quality_retries_before_plain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, '_preferred_js_runtime_spec', lambda: 'node')

    strategies = resolver._build_youtube_strategies(
        ['yt-dlp', '--cookies', '/data/cookies.txt', '--js-runtimes', 'node', '--no-playlist'],
        ['fmt1', 'best'],
    )

    assert '--cookies' in strategies[0][0]
    assert '--remote-components' in strategies[0][0]
    assert strategies[0][1] == ['', 'best']
    assert (['yt-dlp', '--no-playlist'], ['fmt1', 'best']) in strategies


def test_repair_orphan_runtime_playback_ignores_idle_core_with_stale_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None)
    monkeypatch.setattr(player.state, 'QUEUE', [])

    assert player._repair_orphan_runtime_playback(
        {
            'path': 'https://example.com/stale.m3u8',
            'core-idle': True,
            'eof-reached': False,
        }
    ) is False


def test_repair_orphan_runtime_playback_ignores_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None)
    monkeypatch.setattr(player.state, 'QUEUE', [])
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', player.time.time() + 3600.0)

    assert player._repair_orphan_runtime_playback(
        {
            'path': 'https://example.com/stale-after-close.m3u8',
            'core-idle': False,
            'eof-reached': False,
        }
    ) is False


def test_repair_orphan_runtime_playback_ignores_natural_idle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None)
    monkeypatch.setattr(player.state, 'QUEUE', [])
    monkeypatch.setattr(player, 'natural_idle_reset_holding', lambda: True)

    assert player._repair_orphan_runtime_playback(
        {
            'path': 'https://example.com/stale-after-queue-end.m3u8',
            'core-idle': False,
            'eof-reached': False,
        }
    ) is False


def test_natural_queue_end_keeps_qt_shell_alive_before_idle_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_calls: list[bool] = []
    ensure_calls: list[bool] = []
    now_values: list[object] = []
    session_values: list[str] = []
    pos_values: list[object] = []

    class ImmediateTimer:
        daemon = False

        def __init__(self, delay, callback):
            self.delay = delay
            self.callback = callback

        def cancel(self):
            pass

        def start(self):
            self.callback()

    monkeypatch.setenv("RELAYTV_NATURAL_IDLE_SETTLE_SEC", "2")
    monkeypatch.setenv("RELAYTV_NATURAL_IDLE_ENSURE_DELAY_SEC", "0.2")
    monkeypatch.setattr(player.time, "time", lambda: 1000.0)
    monkeypatch.setattr(player, "_NATURAL_IDLE_ENSURE_TIMER", None, raising=False)
    monkeypatch.setattr(player, "_idle_dashboard_enabled", lambda: True)
    monkeypatch.setattr(player, "_emit_jellyfin_stopped_from_now", lambda now: None)
    monkeypatch.setattr(player.state, "NOW_PLAYING", {"title": "Ended"}, raising=False)
    monkeypatch.setattr(player.state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(player.state, "set_now_playing", lambda value: now_values.append(value))
    monkeypatch.setattr(player.state, "set_session_state", lambda value: session_values.append(value))
    monkeypatch.setattr(player.state, "set_session_position", lambda value: pos_values.append(value))
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "stop_mpv", lambda restart_splash=True: stop_calls.append(bool(restart_splash)))
    monkeypatch.setattr(player, "ensure_qt_shell_idle", lambda force=False: ensure_calls.append(bool(force)))
    monkeypatch.setattr(player.threading, "Timer", ImmediateTimer)

    player._handle_playback_idle_no_queue()

    assert now_values == [None]
    assert session_values == ["idle"]
    assert pos_values == [None]
    assert stop_calls == []
    assert ensure_calls == [False]
    assert player._NATURAL_IDLE_RESET_UNTIL == 1002.0


def test_natural_queue_end_keeps_qt_shell_for_idle_notifications_without_x11(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_calls: list[bool] = []
    ensure_calls: list[bool] = []

    class ImmediateTimer:
        daemon = False

        def __init__(self, delay, callback):
            self.delay = delay
            self.callback = callback

        def cancel(self):
            pass

        def start(self):
            self.callback()

    monkeypatch.setenv("RELAYTV_NATURAL_IDLE_SETTLE_SEC", "2")
    monkeypatch.setattr(player.time, "time", lambda: 1500.0)
    monkeypatch.setattr(player, "_NATURAL_IDLE_ENSURE_TIMER", None, raising=False)
    monkeypatch.setattr(player, "_emit_jellyfin_stopped_from_now", lambda now: None)
    monkeypatch.setattr(player.state, "NOW_PLAYING", {"title": "Ended"}, raising=False)
    monkeypatch.setattr(player.state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(player.state, "set_now_playing", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_state", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_position", lambda value: None)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "_idle_dashboard_enabled", lambda: False)
    monkeypatch.setattr(player, "_idle_notifications_enabled", lambda: True)
    monkeypatch.setattr(player, "_x11_idle_notifications_available", lambda: False)
    monkeypatch.setattr(player, "stop_mpv", lambda restart_splash=True: stop_calls.append(bool(restart_splash)))
    monkeypatch.setattr(player, "ensure_qt_shell_idle", lambda force=False: ensure_calls.append(bool(force)))
    monkeypatch.setattr(player.threading, "Timer", ImmediateTimer)

    player._handle_playback_idle_no_queue()

    assert stop_calls == []
    assert ensure_calls == [False]
    assert player._NATURAL_IDLE_RESET_UNTIL == 1502.0


def test_natural_queue_end_stops_qt_shell_when_idle_visual_surface_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_calls: list[bool] = []

    monkeypatch.setenv("RELAYTV_NATURAL_IDLE_SETTLE_SEC", "2")
    monkeypatch.setattr(player.time, "time", lambda: 1600.0)
    monkeypatch.setattr(player, "_emit_jellyfin_stopped_from_now", lambda now: None)
    monkeypatch.setattr(player.state, "NOW_PLAYING", {"title": "Ended"}, raising=False)
    monkeypatch.setattr(player.state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(player.state, "set_now_playing", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_state", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_position", lambda value: None)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: True)
    monkeypatch.setattr(player, "_idle_dashboard_enabled", lambda: False)
    monkeypatch.setattr(player, "_idle_notifications_enabled", lambda: False)
    monkeypatch.setattr(player, "stop_mpv", lambda restart_splash=True: stop_calls.append(bool(restart_splash)))

    player._handle_playback_idle_no_queue()

    assert stop_calls == [False]
    assert player._NATURAL_IDLE_RESET_UNTIL == 1602.0


def test_natural_queue_end_starts_splash_for_non_qt_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    splash_calls: list[bool] = []
    stop_calls: list[bool] = []

    monkeypatch.setenv("RELAYTV_NATURAL_IDLE_SETTLE_SEC", "2")
    monkeypatch.setattr(player.time, "time", lambda: 2000.0)
    monkeypatch.setattr(player, "_emit_jellyfin_stopped_from_now", lambda now: None)
    monkeypatch.setattr(player.state, "NOW_PLAYING", {"title": "Ended"}, raising=False)
    monkeypatch.setattr(player.state, "SESSION_STATE", "playing", raising=False)
    monkeypatch.setattr(player.state, "set_now_playing", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_state", lambda value: None)
    monkeypatch.setattr(player.state, "set_session_position", lambda value: None)
    monkeypatch.setattr(player, "_qt_shell_backend_enabled", lambda: False)
    monkeypatch.setattr(player, "stop_mpv", lambda restart_splash=True: stop_calls.append(bool(restart_splash)))
    monkeypatch.setattr(player, "start_splash_screen", lambda: splash_calls.append(True))

    player._handle_playback_idle_no_queue()

    assert stop_calls == []
    assert splash_calls == [True]
    assert player._NATURAL_IDLE_RESET_UNTIL == 2002.0


def test_playback_runtime_idle_or_ended_ignores_active_play_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: True)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)

    assert player._playback_runtime_idle_or_ended() is False


def test_is_playing_ignores_idle_qt_socket_with_stale_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(
        player,
        'mpv_get_many',
        lambda props: {
            'core-idle': True,
            'eof-reached': False,
            'path': 'https://example.com/stale.m3u8',
        },
    )
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=True: False)

    assert player._is_playing() is False


def test_qt_toasts_follow_overlay_by_default_on_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_NATIVE_TOASTS', raising=False)
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_ENABLED', raising=False)
    monkeypatch.setattr('relaytv_app.routes._qt_shell_runtime_running', lambda: True)
    monkeypatch.setattr('relaytv_app.routes.video_profile.get_profile', lambda: {'decode_profile': 'arm_safe'})

    assert _overlay_prefers_native_qt_toast() is False


def test_status_keeps_closed_session_non_playing_during_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'stopped', 'closed': True}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [{'url': 'https://example.com/queued.mp4'}], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', routes.time.time() + 3600.0, raising=False)
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})

    payload = routes.status()

    assert payload['state'] == 'closed'
    assert payload['playing'] is False
    assert payload['resume_available'] is True
    assert payload['queue_length'] == 1
    assert payload['transition_in_progress'] is False


def test_playback_state_keeps_closed_session_non_playing_during_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'stopped', 'closed': True}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [{'url': 'https://example.com/queued.mp4'}], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', routes.time.time() + 3600.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(
        routes.player,
        'qt_shell_runtime_telemetry',
        lambda **_: {'selected': True, 'available': True, 'freshness': 'fresh', 'mpv_runtime_playback_active': True},
    )
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
            'playback_runtime_previous_state': 'playing',
            'playback_runtime_previous_reason': 'runtime_active',
            'playback_runtime_state_since_unix': 1000.0,
            'playback_runtime_last_transition_unix': 1000.0,
            'playback_runtime_time_in_state_sec': 0.0,
        },
    )

    payload = routes.playback_state()

    assert payload['state'] == 'closed'
    assert payload['playing'] is False
    assert payload['has_now_playing'] is True
    assert payload['queue_length'] == 1
    assert payload['transition_in_progress'] is False
    assert payload['native_qt_mpv_runtime_playback_active'] is False


def test_playback_state_uses_mpv_ipc_when_qt_telemetry_is_unselected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'sample'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: False)
    monkeypatch.setattr(routes.player, 'qt_shell_runtime_telemetry', lambda **_: {'selected': False})
    monkeypatch.setattr(
        routes.player,
        'mpv_get_many',
        lambda props: {'pause': False, 'time-pos': 42.5, 'duration': 120.0, 'volume': 80.0, 'mute': False},
    )
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
        },
    )

    payload = routes.playback_state()

    assert payload['playing'] is True
    assert payload['position'] == 42.5
    assert payload['duration'] == 120.0
    assert payload['volume'] == 80.0
    assert payload['mute'] is False
    assert payload['playback_telemetry_source'] == 'mpv_ipc'
    assert payload['playback_runtime_state'] == 'playing'


def test_playback_state_uses_ipc_when_qt_runtime_first_reports_playing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: False)
    monkeypatch.setattr(
        routes.player,
        'qt_shell_runtime_telemetry',
        lambda **_: {
            'selected': True,
            'available': True,
            'freshness': 'fresh',
            'mpv_runtime_playback_active': True,
            'mpv_runtime_sample_detail': 'subprocess_runtime_heartbeat',
        },
    )
    monkeypatch.setattr(
        routes.player,
        'mpv_get_many',
        lambda props: {'pause': False, 'time-pos': 42.5, 'duration': 120.0, 'volume': 80.0, 'mute': False},
    )
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
        },
    )

    payload = routes.playback_state()

    assert payload['playing'] is True
    assert payload['state'] == 'playing'
    assert payload['position'] == 42.5
    assert payload['duration'] == 120.0
    assert payload['volume'] == 80.0
    assert payload['mute'] is False
    assert payload['playback_telemetry_source'] == 'mpv_ipc'


def test_close_preserves_now_playing_and_keeps_qt_shell_when_idle_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    now_values: list[object] = []
    session_values: list[str] = []
    stop_shell_calls: list[bool] = []
    stop_mpv_calls: list[bool] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(routes.player, '_idle_dashboard_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 12.5 if prop == 'time-pos' else 99.0)
    monkeypatch.setattr(routes.player, 'stop_playback_keep_qt_shell', lambda: stop_shell_calls.append(True) or True)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda restart_splash=True: stop_mpv_calls.append(bool(restart_splash)))
    monkeypatch.setattr(routes, '_jellyfin_emit_stopped_hint', lambda pos, dur: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'Clip', 'url': 'https://example.com/video.mp4'}, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: now_values.append(value))
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: session_values.append(value))
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)

    out = routes.close()

    assert out['status'] == 'closed'
    assert out['resume_available'] is True
    assert out['kept_player_shell'] is True
    assert stop_shell_calls == [True]
    assert stop_mpv_calls == []
    assert session_values == ['closed']
    assert now_values[-1]['closed'] is True
    assert now_values[-1]['resume_pos'] == 12.5


def test_close_discards_temporary_restore_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    routes._TEMP_PLAYBACK_STACK.clear()
    routes._TEMP_PLAYBACK_STACK.append({
        'id': 'frame-1',
        'resume': True,
        'snapshot': {'now_playing': {'url': 'https://example.com/interrupted.mp4'}},
    })

    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(routes.player, '_idle_dashboard_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 12.5 if prop == 'time-pos' else 99.0)
    monkeypatch.setattr(routes.player, 'stop_playback_keep_qt_shell', lambda: True)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda restart_splash=True: None)
    monkeypatch.setattr(routes, '_restore_playback_state', lambda snapshot: (_ for _ in ()).throw(AssertionError('close must not restore temporary playback')))
    monkeypatch.setattr(routes, '_jellyfin_emit_stopped_hint', lambda pos, dur: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'Active', 'url': 'https://example.com/active.mp4'}, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)

    try:
        out = routes.close()
        assert out['status'] == 'closed'
        assert routes._TEMP_PLAYBACK_STACK == []
    finally:
        routes._TEMP_PLAYBACK_STACK.clear()


def test_close_preserves_interrupt_queue_items(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    queue_events: list[dict] = []
    interrupted_queue_item = {
        'url': 'https://example.com/interrupted.mp4',
        'title': 'Interrupted',
        'resume_pos': 37.0,
        '_relaytv_interrupt_preserved': True,
    }
    normal_queue_item = {'url': 'https://example.com/normal.mp4', 'title': 'Normal'}

    monkeypatch.setattr(
        routes.state,
        'QUEUE',
        [interrupted_queue_item, normal_queue_item],
        raising=False,
    )
    monkeypatch.setattr(routes.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(routes, '_ui_event_push_queue', lambda event, **payload: queue_events.append({'event': event, **payload}))
    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(routes.player, '_idle_dashboard_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 12.5 if prop == 'time-pos' else 99.0)
    monkeypatch.setattr(routes.player, 'stop_playback_keep_qt_shell', lambda: True)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda restart_splash=True: None)
    monkeypatch.setattr(routes, '_jellyfin_emit_stopped_hint', lambda pos, dur: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'Active', 'url': 'https://example.com/active.mp4'}, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)

    out = routes.close()

    assert out['status'] == 'closed'
    assert routes.state.QUEUE == [interrupted_queue_item, normal_queue_item]
    assert persisted == []
    assert queue_events == []


def test_close_uses_overlay_not_qt_shell_for_idle_notifications_on_x11(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_shell_calls: list[bool] = []
    stop_mpv_calls: list[bool] = []
    ensure_surface_calls: list[bool] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(routes.player, '_idle_dashboard_enabled', lambda: False)
    monkeypatch.setattr(routes.player, 'idle_notifications_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'idle_visual_surface_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 12.5 if prop == 'time-pos' else 99.0)
    monkeypatch.setattr(routes.player, 'stop_playback_keep_qt_shell', lambda: stop_shell_calls.append(True) or False)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda restart_splash=True: stop_mpv_calls.append(bool(restart_splash)))
    monkeypatch.setattr(routes, '_ensure_notification_surface', lambda wait_for_subscriber=False: ensure_surface_calls.append(bool(wait_for_subscriber)))
    monkeypatch.setattr(routes, '_jellyfin_emit_stopped_hint', lambda pos, dur: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'Clip', 'url': 'https://example.com/video.mp4'}, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)

    out = routes.close()

    assert out['status'] == 'closed'
    assert out['kept_player_shell'] is False
    assert stop_shell_calls == [True]
    assert stop_mpv_calls == [True]
    assert ensure_surface_calls == [False]


def test_clear_now_playing_advances_queue_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, bool]] = []
    routes._TEMP_PLAYBACK_STACK.clear()
    routes._TEMP_PLAYBACK_STACK.append({
        'id': 'frame-1',
        'resume': True,
        'snapshot': {'now_playing': {'url': 'https://example.com/interrupted.mp4'}},
    })

    monkeypatch.setattr(routes.state, 'QUEUE', [{'url': 'https://example.com/next.mp4'}], raising=False)
    monkeypatch.setattr(
        routes.player,
        'advance_queue_playback',
        lambda mode, prefer_playlist_next=True, poll_sleep=None: calls.append((mode, bool(prefer_playlist_next))) or {
            'status': 'playing_next',
            'now_playing': {'title': 'Next'},
            'method': 'dequeue_play_item',
        },
    )

    try:
        out = routes.clear_now_playing()

        assert out['status'] == 'playing_next'
        assert out['now_playing']['title'] == 'Next'
        assert 'method' not in out
        assert calls == [('next', True)]
        assert routes._TEMP_PLAYBACK_STACK == []
    finally:
        routes._TEMP_PLAYBACK_STACK.clear()


def test_clear_now_playing_returns_to_idle_without_preserving_current(monkeypatch: pytest.MonkeyPatch) -> None:
    now_values: list[object] = []
    session_values: list[str] = []
    stop_shell_calls: list[bool] = []
    stop_mpv_calls: list[bool] = []

    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'Current'}, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: now_values.append(value))
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: session_values.append(value))
    monkeypatch.setattr(routes.state, 'persist_queue', lambda: None)
    monkeypatch.setattr(routes.player, '_idle_dashboard_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'stop_playback_keep_qt_shell', lambda: stop_shell_calls.append(True) or True)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda restart_splash=True: stop_mpv_calls.append(bool(restart_splash)))

    out = routes.clear_now_playing()

    assert out == {'status': 'cleared', 'resume_available': False, 'kept_player_shell': True}
    assert now_values == [None]
    assert session_values == ['idle']
    assert stop_shell_calls == [True]
    assert stop_mpv_calls == []


def test_clear_now_playing_uses_overlay_not_qt_shell_for_idle_notifications_on_x11(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_shell_calls: list[bool] = []
    stop_mpv_calls: list[bool] = []
    ensure_surface_calls: list[bool] = []

    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'Current'}, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(routes.state, 'persist_queue', lambda: None)
    monkeypatch.setattr(routes.player, '_idle_dashboard_enabled', lambda: False)
    monkeypatch.setattr(routes.player, 'idle_notifications_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'idle_visual_surface_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'stop_playback_keep_qt_shell', lambda: stop_shell_calls.append(True) or False)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda restart_splash=True: stop_mpv_calls.append(bool(restart_splash)))
    monkeypatch.setattr(routes, '_ensure_notification_surface', lambda wait_for_subscriber=False: ensure_surface_calls.append(bool(wait_for_subscriber)))

    out = routes.clear_now_playing()

    assert out == {'status': 'cleared', 'resume_available': False, 'kept_player_shell': False}
    assert stop_shell_calls == [True]
    assert stop_mpv_calls == [True]
    assert ensure_surface_calls == [False]


def test_status_keeps_idle_non_playing_during_natural_idle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})

    payload = routes.status()

    assert payload['state'] == 'idle'
    assert payload['playing'] is False
    assert payload['resume_available'] is False


def test_status_preserves_paused_session_during_runtime_dropout(monkeypatch: pytest.MonkeyPatch) -> None:
    session_sets: list[str] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'sample'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda val: session_sets.append(val))
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})
    monkeypatch.setattr(
        routes,
        '_runtime_capabilities',
        lambda playing=None: {
            'native_qt_mpv_runtime_paused': True,
            'native_qt_mpv_runtime_stream_loaded': True,
            'native_qt_mpv_runtime_path': 'https://example.com/current.mp4',
        },
    )

    payload = routes.status()

    assert payload['state'] == 'paused'
    assert payload['playing'] is True
    assert payload['paused'] is True
    assert session_sets == ['paused']


def test_playback_toggle_resumes_paused_session_without_reloading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(
        routes.state,
        'NOW_PLAYING',
        {
            'url': 'https://example.com/current',
            'stream': 'https://example.com/stream.mp4',
            'resume_pos': 42.0,
        },
        raising=False,
    )
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda _v: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda _v: None)
    monkeypatch.setattr(routes.state, 'set_pause_reason', lambda _v: None)
    monkeypatch.setattr(
        routes.player,
        '_load_stream_in_existing_mpv',
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('paused resume should not reload stream')),
    )
    monkeypatch.setattr(
        routes.player,
        'start_mpv',
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('paused resume should not restart mpv')),
    )
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        routes.player,
        'mpv_set_result',
        lambda prop, value: calls.append((prop, value)) or {
            'error': 'success',
            'request_id': 'qtctl-toggle-resume',
            'ack_observed': True,
            'ack_reason': 'control_acknowledged',
        },
    )

    resp = routes.playback_toggle()

    assert resp['ok'] is True
    assert resp['action'] == 'resume'
    assert resp['paused'] is False
    assert resp['request_id'] == 'qtctl-toggle-resume'
    assert calls == [('pause', False)]


def test_mpv_start_args_include_resume_start_position(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'get_settings', lambda: {'volume': 75})
    monkeypatch.setattr(player, '_effective_audio_device', lambda settings=None: '')
    monkeypatch.setattr(player, '_x11_mode_active', lambda selected_mode=None: False)
    monkeypatch.setattr(player, '_x11_overlay_enabled', lambda: False)
    monkeypatch.setattr(player, '_provider_hint_for_stream', lambda *_a, **_k: 'generic')
    monkeypatch.setattr(player, '_should_force_ytdl_off', lambda *_a, **_k: False)
    monkeypatch.setattr(player, '_effective_ytdl_format', lambda *_a, **_k: '')

    args = player._build_mpv_args('https://example.com/video.mp4', None, 'x11', start_pos=42.5)

    assert '--start=42.5' in args
    assert args[-1] == 'https://example.com/video.mp4'


def test_process_wide_resume_start_disables_mpv_up_next_priming() -> None:
    try:
        player._set_mpv_process_start_option_active(True)

        assert player._mpv_up_next_load_target({'url': 'https://example.com/next.mp4'}) is None
    finally:
        player._set_mpv_process_start_option_active(False)


def test_reused_mpv_process_keeps_resume_start_up_next_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[object]] = []

    class DummyProc:
        def poll(self):
            return None

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(player, 'MPV_PROC', DummyProc())
    monkeypatch.setattr(player.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_accepts_mpv_commands', lambda: False)
    monkeypatch.setattr(player, 'mpv_command', lambda cmd: commands.append(list(cmd)) or {'error': 'success'})

    try:
        player._set_mpv_process_start_option_active(True)

        assert player._load_stream_in_existing_mpv('https://example.com/replacement.mp4') is True

        assert commands == [['loadfile', 'https://example.com/replacement.mp4', 'replace']]
        assert player._mpv_up_next_load_target({'url': 'https://example.com/next.mp4'}) is None
    finally:
        player._set_mpv_process_start_option_active(False)


def test_resume_session_starts_resolved_stream_at_resume_position(monkeypatch: pytest.MonkeyPatch) -> None:
    load_calls: list[dict[str, object]] = []
    start_calls: list[dict[str, object]] = []
    seek_calls: list[object] = []

    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(
        routes.state,
        'NOW_PLAYING',
        {
            'url': 'https://youtube.com/watch?v=abc',
            'stream': 'https://video.example/resolved.mp4',
            'audio': 'https://audio.example/resolved.m4a',
            'resume_pos': 42.5,
        },
        raising=False,
    )
    monkeypatch.setattr(routes.state, 'SESSION_POSITION', 42.5, raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: setattr(routes.state, 'NOW_PLAYING', value))
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: setattr(routes.state, 'SESSION_STATE', value))
    monkeypatch.setattr(
        routes.player,
        '_load_stream_in_existing_mpv',
        lambda stream_url, audio_url=None, start_pos=None: load_calls.append(
            {'stream': stream_url, 'audio': audio_url, 'start_pos': start_pos}
        ) or False,
    )
    monkeypatch.setattr(
        routes.player,
        'start_mpv',
        lambda stream_url, audio_url=None, start_pos=None: start_calls.append(
            {'stream': stream_url, 'audio': audio_url, 'start_pos': start_pos}
        ),
    )
    monkeypatch.setattr(routes.player, 'mpv_seek_absolute_with_retry', lambda *a, **k: seek_calls.append((a, k)))
    monkeypatch.setattr(routes.player, 'mpv_set_result', lambda prop, value: {'error': 'success'})

    out = routes.resume_session()

    assert out['status'] == 'resumed'
    assert load_calls == [{'stream': 'https://video.example/resolved.mp4', 'audio': 'https://audio.example/resolved.m4a', 'start_pos': 42.5}]
    assert start_calls == [{'stream': 'https://video.example/resolved.mp4', 'audio': 'https://audio.example/resolved.m4a', 'start_pos': 42.5}]
    assert seek_calls == []


def test_preserve_current_marks_interrupt_queue_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 37.0 if prop == 'time-pos' else 120.0)
    monkeypatch.setattr(routes.player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'url': 'https://example.com/interrupted.mp4', 'title': 'Interrupted'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(routes.time, 'time', lambda: 1234.0)

    preserved = routes._preserve_current_to_queue_front()

    assert preserved is not None
    assert preserved['_relaytv_interrupt_preserved'] is True
    assert preserved['_relaytv_interrupt_preserved_at'] == 1234
    assert preserved['resume_pos'] == 37.0
    assert routes.state.QUEUE == [preserved]
    assert persisted[-1]['queue'] == [preserved]


def test_persisted_queue_item_keeps_interrupt_preserved_marker() -> None:
    item = {
        'url': 'https://example.com/interrupted.mp4',
        'title': 'Interrupted',
        'resume_pos': 37.0,
        '_relaytv_interrupt_preserved': True,
        '_relaytv_interrupt_preserved_at': 1234,
    }

    persisted = routes.state._persistable_queue_item(item)
    loaded = routes.state._load_persisted_queue_item(item)

    assert persisted is not None
    assert persisted['_relaytv_interrupt_preserved'] is True
    assert persisted['_relaytv_interrupt_preserved_at'] == 1234
    assert loaded is not None
    assert loaded['_relaytv_interrupt_preserved'] is True
    assert loaded['_relaytv_interrupt_preserved_at'] == 1234


def test_interrupt_preserved_queue_item_is_not_mpv_primed() -> None:
    assert player._mpv_up_next_load_target(
        {
            'url': 'https://example.com/interrupted.mp4',
            '_relaytv_interrupt_preserved': True,
            '_resolved_stream': 'https://cdn.example.com/interrupted.mp4',
        }
    ) is None


def test_closed_session_does_not_prime_mpv_up_next(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(
        player.state,
        'QUEUE',
        [{'url': 'https://www.youtube.com/watch?v=queued', 'title': 'Queued'}],
        raising=False,
    )
    monkeypatch.setattr(player, '_is_playing', lambda: True)
    monkeypatch.setattr(
        player,
        'mpv_command',
        lambda command: (_ for _ in ()).throw(AssertionError(f'closed session must not prime mpv queue: {command!r}')),
    )

    assert player._prime_mpv_up_next_from_queue(force=True) is False


def test_session_tracker_does_not_reopen_closed_session(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_calls: list[bool] = []

    monkeypatch.setattr(player.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://example.com/jellyfin.mp4', 'title': 'Closed Jellyfin', 'closed': True},
        raising=False,
    )
    monkeypatch.setattr(player, '_is_playing', lambda: True)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: reset_calls.append(True))
    monkeypatch.setattr(
        player,
        'mpv_get_many',
        lambda props: (_ for _ in ()).throw(AssertionError('closed session tracker must not sample runtime')),
    )
    monkeypatch.setattr(
        player.state,
        'set_session_state',
        lambda value: (_ for _ in ()).throw(AssertionError(f'closed session must not become {value!r}')),
    )
    monkeypatch.setattr(
        player,
        '_prime_mpv_up_next_from_queue',
        lambda force=False: (_ for _ in ()).throw(AssertionError('closed session must not prime up-next')),
    )

    player._session_tracker_tick()

    assert reset_calls == [True]
    assert player.state.SESSION_STATE == 'closed'


def test_play_item_reuses_fresh_resolved_stream_without_ytdlp(monkeypatch: pytest.MonkeyPatch) -> None:
    start_calls: list[dict[str, object]] = []
    now_values: list[dict] = []

    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, '_mark_playback_transition', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda cec: False)
    monkeypatch.setattr(player, '_load_stream_in_existing_mpv', lambda *a, **k: False)
    monkeypatch.setattr(
        player,
        'start_mpv',
        lambda stream_url, audio_url=None, start_pos=None: start_calls.append(
            {'stream': stream_url, 'audio': audio_url, 'start_pos': start_pos}
        ),
    )
    monkeypatch.setattr(player, 'mpv_set', lambda *a, **k: None)
    monkeypatch.setattr(player, '_add_history_entry', lambda now: None)
    monkeypatch.setattr(player, '_prime_mpv_up_next_from_queue', lambda force=False: False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'get_tv_state', lambda: {})
    monkeypatch.setattr(player.state, 'set_now_playing', lambda value: now_values.append(value))
    monkeypatch.setattr(player.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(player.state, 'set_pause_reason', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(player, 'resolve_streams', lambda url: (_ for _ in ()).throw(AssertionError('yt-dlp should not run')))
    monkeypatch.setattr(player.time, 'time', lambda: 1000.0)

    now = player.play_item(
        {
            'url': 'https://youtube.com/watch?v=abc',
            'title': 'Cached clip',
            'provider': 'youtube',
            'resume_pos': 42.5,
            '_resolved_source_url': 'https://youtube.com/watch?v=abc',
            '_resolved_stream': 'https://video.example/resolved.mp4',
            '_resolved_audio': 'https://audio.example/resolved.m4a',
            '_resolved_at': 999.0,
        },
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode='resume',
        start_pos=42.5,
    )

    assert start_calls == [{'stream': 'https://video.example/resolved.mp4', 'audio': 'https://audio.example/resolved.m4a', 'start_pos': 42.5}]
    assert now['stream'] == 'https://video.example/resolved.mp4'
    assert now['_resolved_stream'] == 'https://video.example/resolved.mp4'
    assert now_values[-1]['_resolved_at'] == 999.0


def test_persistable_history_item_keeps_resolved_stream_hint() -> None:
    item = {
        'url': 'https://youtube.com/watch?v=abc',
        'title': 'Cached clip',
        'provider': 'youtube',
        'resume_pos': 42.5,
        '_resolved_source_url': 'https://youtube.com/watch?v=abc',
        '_resolved_stream': 'https://video.example/resolved.mp4',
        '_resolved_audio': 'https://audio.example/resolved.m4a',
        '_resolved_at': 999.0,
    }

    out = routes.state._persistable_history_item(item)

    assert out['_resolved_source_url'] == item['_resolved_source_url']
    assert out['_resolved_stream'] == item['_resolved_stream']
    assert out['_resolved_audio'] == item['_resolved_audio']
    assert out['_resolved_at'] == 999.0


def test_playback_state_keeps_idle_non_playing_during_natural_idle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: True)
    monkeypatch.setattr(
        routes.player,
        'qt_shell_runtime_telemetry',
        lambda **_: {'selected': True, 'available': True, 'freshness': 'fresh', 'mpv_runtime_playback_active': True},
    )
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
            'playback_runtime_previous_state': 'playing',
            'playback_runtime_previous_reason': 'runtime_active',
            'playback_runtime_state_since_unix': 1000.0,
            'playback_runtime_last_transition_unix': 1000.0,
            'playback_runtime_time_in_state_sec': 0.0,
        },
    )

    payload = routes.playback_state()

    assert payload['state'] == 'idle'
    assert payload['playing'] is False
    assert payload['has_now_playing'] is False


def test_playback_state_exposes_transition_during_manual_play_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: True)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: False)
    monkeypatch.setattr(routes.player, 'qt_shell_runtime_telemetry', lambda **_: {'selected': False})
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
        },
    )

    payload = routes.playback_state()

    assert payload['transition_in_progress'] is True
    assert payload['transitioning_between_items'] is True
    assert payload['playback_runtime_state'] == 'buffering'


def test_resume_clear_sets_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_calls: list[str] = []
    persisted: list[str] = []

    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda: stop_calls.append('stop'))
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(routes.state, 'persist_queue', lambda: persisted.append('persist'))

    response = routes.clear_resumable_session()

    assert response == {'status': 'cleared', 'resume_available': False}
    assert stop_calls == ['stop']
    assert persisted == ['persist']
    assert routes.state.AUTO_NEXT_SUPPRESS_UNTIL > routes.time.time() + 3600.0


def test_seek_routes_set_extended_transition_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list[float] = []

    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, '_mark_playback_transition', lambda sec=None: marked.append(float(sec or 0.0)))
    monkeypatch.setattr(routes.player, '_qt_shell_runtime_accepts_mpv_commands', lambda: False)
    monkeypatch.setattr(routes.player, 'mpv_command', lambda cmd: {'error': 'success', 'request_id': 'seek-ok'})

    seek_resp = routes.seek(routes.SeekReq(sec=30))
    seek_abs_resp = routes.seek_abs(routes.SeekAbsReq(sec=120))

    assert seek_resp['ok'] is True
    assert seek_abs_resp['ok'] is True
    assert marked == [6.0, 6.0]
    assert routes.state.AUTO_NEXT_SUPPRESS_UNTIL > routes.time.time() + 5.0


def test_seek_routes_use_time_pos_setter_for_qt_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list[float] = []
    mpv_commands: list[list[object]] = []
    set_calls: list[tuple[str, float]] = []

    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, '_mark_playback_transition', lambda sec=None: marked.append(float(sec or 0.0)))
    monkeypatch.setattr(routes.player, '_qt_shell_runtime_accepts_mpv_commands', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {'time-pos': 90.0, 'duration': 120.0})
    monkeypatch.setattr(
        routes.player,
        'mpv_set_result',
        lambda prop, value: set_calls.append((prop, float(value))) or {'error': 'success', 'request_id': 'seek-set'},
    )
    monkeypatch.setattr(routes.player, 'mpv_command', lambda cmd: mpv_commands.append(list(cmd)) or {'error': 'success'})

    seek_resp = routes.seek(routes.SeekReq(sec=30))
    seek_abs_resp = routes.seek_abs(routes.SeekAbsReq(sec=200))

    assert seek_resp['ok'] is True
    assert seek_abs_resp['ok'] is True
    assert marked == [6.0, 6.0]
    assert set_calls == [('time-pos', 120.0), ('time-pos', 120.0)]
    assert mpv_commands == []


def test_qt_runtime_seek_uses_extended_ack_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[float | None] = []

    monkeypatch.setattr(player, '_qt_shell_runtime_accepts_mpv_commands', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_preferred', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_command', lambda cmd: {'error': 'success', 'request_id': 'seek-ok'})

    def fake_finalize(result, *, timeout_sec=None):
        captured.append(timeout_sec)
        return {'error': 'success', 'request_id': 'seek-ok', 'ack_observed': True, 'ack_reason': 'control_acknowledged'}

    monkeypatch.setattr(player, '_qt_shell_runtime_finalize_control_result', fake_finalize)

    result = player.mpv_command(['seek', 180.0, 'absolute'])

    assert result['error'] == 'success'
    assert captured == [3.5]


def test_pause_timeout_is_tolerated_when_runtime_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        player,
        'mpv_command',
        lambda cmd_list: {
            'error': 'timeout_or_unavailable',
            'request_id': 'qtctl-pause-timeout',
            'ack_observed': False,
            'ack_reason': 'timeout_or_unavailable',
        },
    )
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda max_age_sec=3.0: {'alive': True},
    )
    monkeypatch.setattr(player, '_MPV_PROP_CACHE', {}, raising=False)
    monkeypatch.setattr(player, '_MPV_PROP_CACHE_TS', 0.0, raising=False)

    result = player.mpv_set_result('pause', True)

    assert result['error'] == 'success'
    assert result['request_id'] == 'qtctl-pause-timeout'
    assert result['ack_observed'] is False
    assert result['ack_reason'] == 'control_pending'
    assert player._MPV_PROP_CACHE['pause'] is True


def test_qt_toast_override_can_force_native_toasts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RELAYTV_QT_NATIVE_TOASTS', '1')
    monkeypatch.setenv('RELAYTV_QT_OVERLAY_ENABLED', '1')
    monkeypatch.setattr('relaytv_app.routes._qt_shell_runtime_running', lambda: True)

    assert _overlay_prefers_native_qt_toast() is True


def test_notification_capabilities_expose_native_qt_deprecation_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_NATIVE_IDLE', raising=False)
    monkeypatch.delenv('RELAYTV_QT_NATIVE_TOASTS', raising=False)
    monkeypatch.setattr('relaytv_app.routes._qt_shell_runtime_running', lambda: True)

    caps = _notification_capabilities()

    assert caps['native_qt_idle_deprecated'] is True
    assert caps['native_qt_idle_status'] == 'override_only'
    assert caps['native_qt_idle_override_enabled'] is False
    assert caps['native_qt_toasts_deprecated'] is True
    assert caps['native_qt_toasts_status'] == 'override_only'
    assert caps['native_qt_toasts_override_enabled'] is False


def test_notifications_capabilities_endpoint_includes_native_qt_deprecation_metadata() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/notifications/capabilities')

    assert response.status_code == 200
    payload = response.json()
    assert payload['native_qt_idle_deprecated'] is True
    assert payload['native_qt_idle_status'] == 'override_only'
    assert payload['native_qt_toasts_deprecated'] is True
    assert payload['native_qt_toasts_status'] == 'override_only'


def test_status_endpoint_includes_native_qt_deprecation_metadata() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['native_qt_idle_deprecated'] is True
    assert payload['native_qt_idle_status'] == 'override_only'
    assert payload['native_qt_toasts_deprecated'] is True
    assert payload['native_qt_toasts_status'] == 'override_only'


def test_stop_mpv_persists_live_runtime_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(player, 'mpv_get', lambda prop: 73.0 if prop == 'volume' else None)
    monkeypatch.setattr(player, '_stop_qt_shell', lambda: observed.setdefault('stop_called', True))
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset_called', True))
    monkeypatch.setattr(player, '_cleanup_ipc_socket', lambda: observed.setdefault('cleanup_called', True))
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(player, 'start_splash_screen', lambda: observed.setdefault('splash_called', True))
    monkeypatch.setattr(player.state, 'update_settings', lambda patch: observed.setdefault('patch', dict(patch)))
    monkeypatch.setattr(player, 'MPV_PROC', None)

    player.stop_mpv()

    assert observed['patch'] == {'volume': 73.0}
    assert observed['stop_called'] is True


def test_stop_mpv_ignores_invalid_runtime_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(player, 'mpv_get', lambda prop: 'not-a-number')
    monkeypatch.setattr(player, '_stop_qt_shell', lambda: observed.setdefault('stop_called', True))
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: None)
    monkeypatch.setattr(player, '_cleanup_ipc_socket', lambda: None)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(player, 'start_splash_screen', lambda: None)
    monkeypatch.setattr(player.state, 'update_settings', lambda patch: observed.setdefault('patch', dict(patch)))
    monkeypatch.setattr(player, 'MPV_PROC', None)

    player.stop_mpv()

    assert 'patch' not in observed
    assert observed['stop_called'] is True


def test_stop_playback_keep_qt_shell_clears_mpv_playlist_before_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []

    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_idle_qt_shell_enabled', lambda: True)
    monkeypatch.setattr(player, '_persist_runtime_volume_before_stop', lambda: calls.append('persist_volume'))
    monkeypatch.setattr(player, '_clear_mpv_playlist_before_current', lambda: calls.append('clear_before'))
    monkeypatch.setattr(player, '_clear_mpv_playlist_after_current', lambda: calls.append('clear_after'))
    monkeypatch.setattr(player, 'mpv_command', lambda cmd: calls.append(list(cmd)) or {'error': 'success'})
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: calls.append('reset_up_next'))
    monkeypatch.setattr(player, '_mpv_cache_update', lambda payload: calls.append(('cache', dict(payload))))

    assert player.stop_playback_keep_qt_shell() is True

    assert calls[:5] == ['persist_volume', ['playlist-clear'], 'clear_before', 'clear_after', ['stop']]
    assert 'reset_up_next' in calls
    assert any(isinstance(call, tuple) and call[0] == 'cache' for call in calls)


def test_restart_current_ignores_closed_resumable_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {
            'url': 'https://example.com/closed.mp4',
            'closed': True,
            'resume_pos': 42.0,
        },
        raising=False,
    )
    monkeypatch.setattr(player, 'play_item', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('closed session must not replay')))
    monkeypatch.setattr(player, 'stop_mpv', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('closed session must not stop/restart runtime')))

    assert player.restart_current() is None


def test_idle_qt_shell_can_be_reused_for_stream_load(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'control_file': '/tmp/relaytv-qt-runtime-control.json',
            'mpv_runtime_core_idle': True,
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': False,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_error': '',
            'mpv_runtime_sample_detail': 'heartbeat',
        },
    )
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=False: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: False)

    def fake_load(stream_url: str, audio_url: str | None = None):
        observed['load'] = {'stream': stream_url, 'audio': audio_url}
        return {'error': 'success'}

    monkeypatch.setattr(player, '_qt_shell_runtime_load_stream', fake_load)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset', True))

    assert player._load_stream_in_existing_mpv(
        'https://example.com/stream.m3u8',
        audio_url='https://example.com/audio.m4a',
    ) is True
    assert observed['load'] == {
        'stream': 'https://example.com/stream.m3u8',
        'audio': 'https://example.com/audio.m4a',
    }
    assert observed['reset'] is True


def test_idle_qt_shell_can_be_reused_for_video_only_stream_load(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'alive': True,
            'control_file': '/tmp/relaytv-qt-runtime-control.json',
            'mpv_runtime_core_idle': True,
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': False,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_error': '',
            'mpv_runtime_sample_detail': '',
        },
    )
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=False: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: False)

    def fake_load(stream_url: str, audio_url: str | None = None):
        observed['load'] = {'stream': stream_url, 'audio': audio_url}
        return {'error': 'success'}

    monkeypatch.setattr(player, '_qt_shell_runtime_load_stream', fake_load)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset', True))

    assert player._load_stream_in_existing_mpv('https://example.com/stream.m3u8') is True
    assert observed['load'] == {
        'stream': 'https://example.com/stream.m3u8',
        'audio': None,
    }
    assert observed['reset'] is True


def test_idle_qt_shell_is_not_reused_when_idle_dashboard_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_idle_dashboard_enabled', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'alive': True,
            'control_file': '/tmp/relaytv-qt-runtime-control.json',
            'mpv_runtime_core_idle': True,
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': False,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_error': '',
            'mpv_runtime_sample_detail': '',
        },
    )

    assert player._load_stream_in_existing_mpv('https://example.com/stream.m3u8') is False


def test_idle_qt_shell_can_be_reused_without_fresh_snapshot_control_file(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_snapshot', lambda max_age_sec=3.0: {})
    monkeypatch.setattr(player, '_qt_shell_runtime_control_file', lambda: '/tmp/relaytv-qt-runtime-control.json')
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=False: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: False)

    def fake_load(stream_url: str, audio_url: str | None = None):
        observed['load'] = {'stream': stream_url, 'audio': audio_url}
        return {'error': 'success'}

    monkeypatch.setattr(player, '_qt_shell_runtime_load_stream', fake_load)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset', True))

    assert player._load_stream_in_existing_mpv('https://example.com/stream.m3u8') is True
    assert observed['load'] == {
        'stream': 'https://example.com/stream.m3u8',
        'audio': None,
    }
    assert observed['reset'] is True


def test_qt_runtime_active_treats_paused_loaded_stream_as_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', {'url': 'https://example.com/video.mp4'}, raising=False)
    monkeypatch.setattr(player, '_QT_RUNTIME_ACTIVE_LAST_TS', 0.0, raising=False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': True,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_paused': True,
            'mpv_runtime_core_idle': True,
            'mpv_runtime_eof_reached': False,
        },
    )

    assert player._qt_runtime_active(require_active_session=True) is True


def test_pwa_weather_asset_resolves_google_icon_aliases() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/pwa/weather/partly_cloudy_day.svg?theme=dark')

    assert response.status_code == 200
    assert 'image/svg+xml' in response.headers['content-type']


def test_pwa_weather_asset_uses_theme_directory_when_available() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/pwa/weather/clear_day.svg?theme=light')

    assert response.status_code == 200
    assert 'image/svg+xml' in response.headers['content-type']
