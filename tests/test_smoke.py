# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import json
import os
import re
import shutil
import subprocess
import threading
import tomllib

import pytest

from fastapi.testclient import TestClient

from relaytv_app.main import create_app
from relaytv_app.config import runtime_config
from relaytv_app import container_entrypoint
from relaytv_app import player
from relaytv_app import playback_service
from relaytv_app import postlive_relay
from relaytv_app import qt_shell_app
from relaytv_app import resolver
from relaytv_app import routes
from relaytv_app import state
from relaytv_app.integrations import jellyfin_service
from relaytv_app import upload_store
from relaytv_app import ytdlp_format_policy
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
    jellyfin_css_response = client.get('/static/ui/jellyfin.css')
    realtime_policy_response = client.get('/static/ui/realtime_transport.js')
    js_response = client.get('/static/ui/app.js')
    jellyfin_js_response = client.get('/static/ui/jellyfin.js')
    iptv_css_response = client.get('/static/ui/iptv.css')
    iptv_js_response = client.get('/static/ui/iptv.js')
    seerr_css_response = client.get('/static/ui/seerr.css')
    seerr_js_response = client.get('/static/ui/seerr.js')
    jellyfin_playwright = (ROOT_DIR / 'scripts' / 'jellyfin-ui-smoke.js').read_text(encoding='utf-8')
    iptv_playwright = (ROOT_DIR / 'scripts' / 'iptv-ui-smoke.js').read_text(encoding='utf-8')
    seerr_playwright = (ROOT_DIR / 'scripts' / 'seerr-ui-smoke.js').read_text(encoding='utf-8')

    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert re.search(r'<link rel="stylesheet" href="/static/ui/app\.css\?v=\d+" />', response.text)
    assert re.search(r'<link rel="stylesheet" href="/static/ui/jellyfin\.css\?v=\d+" />', response.text)
    realtime_policy_tag = re.search(
        r'<script src="/static/ui/realtime_transport\.js\?v=\d+" defer></script>',
        response.text,
    )
    assert realtime_policy_tag
    assert re.search(r'<script src="/static/ui/app\.js\?v=\d+" defer></script>', response.text)
    assert realtime_policy_tag.start() < response.text.index('<script src="/static/ui/app.js')
    assert re.search(r'<script src="/static/ui/jellyfin\.js\?v=\d+" defer></script>', response.text)
    assert re.search(r'<link rel="stylesheet" href="/static/ui/iptv\.css\?v=\d+" />', response.text)
    assert re.search(r'<script src="/static/ui/iptv\.js\?v=\d+" defer></script>', response.text)
    assert re.search(r'<link rel="stylesheet" href="/static/ui/seerr\.css\?v=\d+" />', response.text)
    assert re.search(r'<script src="/static/ui/seerr\.js\?v=\d+" defer></script>', response.text)
    assert response.headers.get('cache-control') == 'no-cache'
    assert 'window.RELAYTV_IDLE_PANEL_CATALOG = ' in response.text
    assert '<style>' not in response.text
    assert css_response.status_code == 200
    assert 'text/css' in css_response.headers['content-type']
    css = css_response.text
    assert jellyfin_css_response.status_code == 200
    assert iptv_css_response.status_code == 200
    assert iptv_js_response.status_code == 200
    assert seerr_css_response.status_code == 200
    assert seerr_js_response.status_code == 200
    assert realtime_policy_response.status_code == 200
    assert 'javascript' in realtime_policy_response.headers['content-type']
    assert 'createPolicy' in realtime_policy_response.text
    assert 'text/css' in jellyfin_css_response.headers['content-type']
    jellyfin_css = jellyfin_css_response.text
    assert js_response.status_code == 200
    assert 'javascript' in js_response.headers['content-type']
    js = js_response.text
    assert jellyfin_js_response.status_code == 200
    assert 'javascript' in jellyfin_js_response.headers['content-type']
    jellyfin_js = jellyfin_js_response.text
    seerr_js = seerr_js_response.text
    assert 'const IDLE_PANEL_CATALOG = window.RELAYTV_IDLE_PANEL_CATALOG || {};' in js
    assert 'RelayTV' in response.text
    assert 'id="jfActionStatus"' in response.text
    assert 'id="jellyfinOpenBtn"' in response.text
    assert 'id="jellyfinShell"' in response.text
    assert 'id="seerrOpenBtn"' in response.text
    assert 'id="seerrShell"' in response.text
    assert 'id="seerrSearchInput"' in response.text
    assert 'id="seerrConnectBackdrop"' in response.text
    assert 'id="seerrConnectCode"' in response.text
    assert 'id="setSeerrEnabled"' in response.text
    assert 'id="setSeerrApiKey"' in response.text
    assert 'id="setSeerrClearApiKey"' in response.text
    assert 'id="setSeerrRequestMode"' in response.text
    assert '<option value="shared_admin">Shared administrator API</option>' in response.text
    assert '<option value="caller_session">Caller-specific sign-in</option>' in response.text
    assert 'administrator API identity and may auto-approve' in js
    assert 'function _seerrAbortBrowse' in seerr_js
    assert 'new AbortController()' in seerr_js
    assert 'const __SEERR_REQUEST_POLL_MS = 30000;' in seerr_js
    assert "document.visibilityState !== 'visible'" in seerr_js
    assert "image.loading = 'lazy';" in seerr_js
    assert 'innerHTML' not in seerr_js
    assert 'X-Api-Key' not in seerr_js
    assert '/integrations/seerr/session/quick-connect' in seerr_js
    assert "fetch('/seerr/playback'" in seerr_js
    assert 'jellyfin_item_id' not in seerr_js
    assert 'secret' not in seerr_js.lower()
    assert "chromium.connect(wsEndpoint)" in seerr_playwright
    assert "query === 'retired'" in seerr_playwright
    assert 'nestedInteractive' in seerr_playwright
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
    assert "fetch('/tv/status')" in js
    assert "SETTINGS_TV_CONTROL_BASELINE" in js
    assert "Object.entries(tvControl).forEach" in js
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
    assert 'function openAbout' in js
    assert "async function loadAboutInfo" in js
    assert "fetch('/app/info'" in js
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
    assert "async function submitNotificationToast()" in js
    assert "const imageUrl = file ? await readNotifyImageDataUrl(file) : String(imageUrlEl?.value || '').trim();" in js
    assert "await _fetchWithTimeout('/overlay'" in js
    assert 'bindAboutUi();' in js
    assert 'class="nMetaRow"' in response.text
    assert 'id="nHeroArt"' in response.text
    assert 'id="nowStateDot"' in response.text
    assert 'function _isNowPlayingLive(np)' in js
    assert "const posTxt = liveNow ? 'LIVE' : fmtTime(st.position);" in js
    assert ".nowCard.isLive .progress{ display: none; }" in css
    assert 'id="langBackdrop"' in response.text
    assert 'id="subLangBackdrop"' in response.text
    assert 'role="tablist"' in response.text
    assert 'role="tab"' in response.text
    assert 'id="jfDetailBackdrop"' in response.text
    assert 'role="dialog" aria-modal="true" aria-hidden="true" aria-labelledby="jfDetailTitle"' in response.text
    assert 'id="jfSortSelect"' in response.text
    assert 'id="jfAlphaIndicator"' in response.text
    assert 'id="jfConnection"' in response.text
    assert 'class="jfWorkspace"' in response.text
    assert 'class="jfShellEyebrow">RelayTV library' in response.text
    assert 'id="remoteVolSlider"' in response.text
    assert 'id="remoteVolValue"' in response.text
    assert 'id="setUploadMaxSize"' in response.text
    assert 'id="setUploadRetentionHours"' in response.text
    assert 'id="setIdleDashboardEnabled"' in response.text
    assert 'id="setYtUseInvidious"' in response.text
    assert 'id="setIdleQrEnabled"' in response.text
    assert 'id="setJfEnabled"' in response.text
    assert 'id="setJfApiKey"' in response.text
    assert 'id="setJfClearApiKey"' in response.text
    assert js.count("payload.jellyfin_api_key = jfClearApiKey ? '' : jfApiKey;") == 2
    assert 'id="setJfClearPassword"' in response.text
    assert 'id="setJfStatus" class="sectionStatus unknown">Disabled</span>' in response.text
    assert "castScope === 'shared' ? 'Shared Cast' : 'Cast Ready'" in js
    assert 'class="toggleSwitch"' in response.text
    assert 'data-idle-enable="${key}"' in js
    assert 'class="chk"' not in response.text
    assert '.settingsBody input.input:not([type])' in css
    assert '.settingsBody select.input{' in css
    assert 'appearance:none;' in css
    assert 'Show idle dashboard between plays' in response.text
    assert 'Use Invidious server for YouTube playback' in response.text
    assert 'Show connect QR in idle' in response.text
    assert 'Enable <span class="jfBrand">Jellyfin / Emby</span> integration' in response.text
    assert 'function _uploadBadge(item)' in js
    assert 'function _uploadSummary(item)' in js
    assert 'function _formatUploadSize(bytes)' in js
    assert 'mediaBadge' in js
    assert 'isUnavailable' in js
    assert 'Upload removed' in js
    assert 'onclick="post(\'/close\')"' in response.text
    assert "await post('/now_playing/clear');" in js
    assert 'id="jfSearchBtn"' not in response.text
    assert 'id="jfRefreshBtn"' not in response.text
    assert 'id="jfReconnectBtn"' not in response.text
    assert 'function _jfSetActionStatus' in jellyfin_js
    assert 'function _jfSetLaunchVisible' in jellyfin_js
    assert 'function _jfCloseDetailPanel' in jellyfin_js
    assert 'function _labelNowSubtitleLanguage' in js
    assert 'function _renderNowSubtitleButton' in js
    assert 'function _fetchNowSubtitleOptions' in js
    assert 'function _renderNowSubtitleOptions' in js
    assert 'function openNowSubtitleModal' in js
    assert 'function bindNowSubtitleUi' in js
    assert 'class="jfShell jfModern hidden"' in response.text
    assert 'function loadJellyfinMovies' in jellyfin_js
    assert 'function loadJellyfinTvSeries' in jellyfin_js
    assert 'function _jfPlayAllSeries' in jellyfin_js
    assert 'function _jfSyncTabControls' in jellyfin_js
    assert 'function _jfScheduleSearch' in jellyfin_js
    assert 'function _jfBuildRowItemCard' in jellyfin_js
    assert "btn.classList.add(`jfType-${itemType.replace" in jellyfin_js
    assert "progressTrack.className = 'jfMediaProgress';" in jellyfin_js
    assert 'function _jfBindImageFallback' in jellyfin_js
    assert 'function _jfHasFiniteNumber' in jellyfin_js
    assert "id: 'tv_series_header'" in jellyfin_js
    assert "id: 'tv_season_chooser'" in jellyfin_js
    assert "itTitle.textContent = itemType === 'episode' ? (subtitleText || 'Episode') : titleText;" in jellyfin_js
    assert "itSub.textContent = itemType === 'episode' ? titleText : subtitleText;" in jellyfin_js
    assert "item.backdrop || item.poster_local" in jellyfin_js
    assert "mkBtn('Queue Last', 'play_last')" in jellyfin_js
    assert "params.get('jfui')" not in jellyfin_js
    assert "relaytv_jellyfin_ui" not in jellyfin_js
    assert 'const __JF_CATALOG_PAGE_SIZE = 48;' in jellyfin_js
    assert 'function _jfLoadNextCatalogPage' in jellyfin_js
    assert 'new IntersectionObserver' in jellyfin_js
    assert "img.loading = 'lazy';" in jellyfin_js
    assert "qs.set('limit', String(__JF_CATALOG_PAGE_SIZE));" in jellyfin_js
    assert 'qs.set(\'limit\', String(__JF_CATALOG_LIMIT));' not in jellyfin_js
    assert 'state.itemIds.has(itemId)' in jellyfin_js
    assert 'function _jfAbortBrowseRequest' in jellyfin_js
    assert 'const __JF_REQ_TIMEOUT_MS' in jellyfin_js
    assert 'function _jfFetchWithTimeout' in jellyfin_js
    assert 'function _applyQueueSnapshot' in js
    assert 'touch-action: none;' in jellyfin_css
    assert '.jfCatalogSentinel{' in jellyfin_css
    assert '.jfModern .jfWorkspace{' in jellyfin_css
    assert '.jfModern .jfConnection{' in jellyfin_css
    assert '.jfModern .jfDetail{' in jellyfin_css
    assert '.jfMediaProgress{' in jellyfin_css
    assert '.jfSeriesHero{' in jellyfin_css
    assert '.jfSeasonModal{' in jellyfin_css
    assert '.jfModern .jfScroller:not(.jfCatalogScroller) .jfItem{' in jellyfin_css
    assert "chromium.connect(wsEndpoint)" in jellyfin_playwright
    assert "--${name}=" in jellyfin_playwright
    assert "nestedInteractive" in jellyfin_playwright
    assert "chromium.connect(wsEndpoint)" in iptv_playwright
    assert "[data-iptv-section=\"favorites\"]" in iptv_playwright
    assert "nestedInteractive" in iptv_playwright
    assert "livePanel.position === 'LIVE'" in iptv_playwright
    assert "_applyQueueSnapshot(payload);" in js
    assert "await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});" in js
    assert "play.disabled = !available;" in js
    assert "queue.disabled = !available;" in js
    assert "await fetch('/jellyfin/subtitle/select'" in js
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


def test_image_bundles_pinned_deno_js_runtime() -> None:
    # Deno is yt-dlp's only default-enabled JS runtime for YouTube challenge
    # solving; shipping it covers every yt-dlp invocation (including mpv's
    # ytdl hook) without per-call flags. Pin the version and verify sha256 so
    # the image stays traceable; 32-bit ARM keeps the node fallback.
    dockerfile = (ROOT_DIR / "app/Dockerfile").read_text()
    compose = (ROOT_DIR / "docker-compose.yml").read_text()
    install_doc = (ROOT_DIR / "docs/INSTALL.md").read_text()

    assert "ARG RELAYTV_INSTALL_DENO=1" in dockerfile
    assert "ARG RELAYTV_DENO_VERSION=" in dockerfile
    assert dockerfile.count('deno_sha256="') == 2
    assert "sha256sum -c -" in dockerfile
    assert 'deno-${deno_target}.zip' in dockerfile
    assert "RELAYTV_INSTALL_DENO: ${RELAYTV_INSTALL_DENO:-1}" in compose
    assert "RELAYTV_INSTALL_DENO=1" in install_doc


def test_rumble_browser_impersonation_dependency_is_bundled_and_declared() -> None:
    dockerfile = (ROOT_DIR / 'app/Dockerfile').read_text()
    pyproject = (ROOT_DIR / 'pyproject.toml').read_text()
    notices = (ROOT_DIR / 'THIRD_PARTY_LICENSES.md').read_text()

    requirement = 'yt-dlp[default,curl-cffi]'
    assert requirement in dockerfile
    assert requirement in pyproject
    assert '`curl-cffi`' in notices


def test_compose_device_passthrough_lives_in_generated_override() -> None:
    # A device mapped in the base compose that the host lacks makes compose
    # refuse to create the container, and overrides can add devices but never
    # remove them — so the base files must map none and the installer probes
    # existence for each node it writes into the override.
    compose = (ROOT_DIR / "docker-compose.yml").read_text()
    release = (ROOT_DIR / "docker-compose.release.yml").read_text()
    installer = (ROOT_DIR / "scripts/install.sh").read_text()

    assert "devices:" not in compose
    assert "devices:" not in release
    assert 'CORE_DEVICE_NODES="/dev/snd /dev/dri"' in installer
    assert "for node in $CORE_DEVICE_NODES" in installer
    assert "/dev/dri (GPU/KMS) not found" in installer
    assert "/dev/snd not found" in installer


def test_yt_dlp_update_interval_gate_and_force(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    import time as _time

    pip_calls: list[object] = []
    state_file = tmp_path / "update.json"
    # Record the channel too: without it the state reads as a stable-era check,
    # and the channel-switch guard would force a run before the interval.
    state_file.write_text(
        json.dumps({"last_check_ts": _time.time(), "channel": "nightly"}), encoding="utf-8"
    )
    env = {
        "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE": str(state_file),
        "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS": "24",
        "RELAYTV_YTDLP_UPDATE_DIR": str(tmp_path / "ytdlp"),
    }
    monkeypatch.setattr(
        container_entrypoint, "_yt_dlp_version", lambda env, *, path=None, user_site=True: "2026.01.01"
    )
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "run",
        lambda *args, **kwargs: pip_calls.append(args) or subprocess.CompletedProcess(args, 0, "", ""),
    )

    assert container_entrypoint.run_yt_dlp_update(env) is False
    assert pip_calls == []

    assert container_entrypoint.run_yt_dlp_update(env, force=True) is True
    assert len(pip_calls) == 1
    assert json.loads(state_file.read_text(encoding="utf-8"))["ok"] is True


def test_ytdlp_update_worker_gates_on_runtime_toggle(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import ytdlp_update

    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "1")
    assert ytdlp_update.enabled() is True
    runtime_config.set_value("RELAYTV_YTDLP_AUTO_UPDATE", "0")
    assert ytdlp_update.enabled() is False

    delegated: list[bool] = []
    monkeypatch.setattr(
        ytdlp_update.container_entrypoint,
        "run_yt_dlp_update",
        lambda env, *, force=False: delegated.append(force) or True,
    )
    assert ytdlp_update.run_update_check(force=True) is True
    assert delegated == [True]


def test_repo_installer_maps_only_existing_pi_video_nodes() -> None:
    # Pi 5 has no bcm2835-codec, so /dev/video10..13 do not exist there and an
    # unconditional device mapping makes docker compose fail to create the
    # container. The installer must probe node existence for both the default
    # and the generated override.
    text = (ROOT_DIR / "scripts/install.sh").read_text()

    assert "PI_VIDEO_DECODE_NODES=" in text
    assert "detect_pi_video_default" in text
    assert '[ -e "$node" ] || continue' in text
    assert '- /dev/video10:/dev/video10"' not in text
    assert text.count("for node in $PI_VIDEO_DECODE_NODES") >= 2


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
    runtime_config.refresh_from_env()
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


def test_cec_monitor_log_level_includes_traffic_and_notice() -> None:
    # ERROR(1) | NOTICE(4) | TRAFFIC(8): the monitor parses ">>" traffic lines
    # and learns our physical address from the NOTICE registration line.
    level = int(player.CEC_MONITOR_LOG_LEVEL)
    assert level & 8, "TRAFFIC bit required to see standby/source events"
    assert level & 4, "NOTICE bit required to auto-detect physical address"


def test_cec_parse_traffic_extracts_opcode_and_operands() -> None:
    line = "TRAFFIC: [  123]\t>> 0f:82:10:00"
    assert player._parse_cec_traffic(line) == ("82", ["10", "00"])
    assert player._parse_cec_traffic("TRAFFIC: [ 5]\t<< 10:36") is None


def test_cec_phys_addr_normalization_matches_traffic_operands(monkeypatch: pytest.MonkeyPatch) -> None:
    assert player._normalize_phys_addr("10", "00") == "1000"

    for env_form in ("1000", "1.0.0.0", "10:00"):
        monkeypatch.setenv("RELAYTV_CEC_PHYS_ADDR", env_form)
        assert player._our_phys_addr() == "1000"

    monkeypatch.delenv("RELAYTV_CEC_PHYS_ADDR", raising=False)
    monkeypatch.setitem(player._CEC_CONTROLLER_STATUS, "phys_addr", "2000")
    assert player._our_phys_addr() == "2000"


def test_cec_phys_addr_detected_from_registration_line() -> None:
    line = (
        "NOTICE:  [   424]\tCEC client registered: libCEC version = 6.0.2, "
        "client version = 6.0.2, firmware version = 4, "
        "logical address(es) = Playback 1 (4) , physical address: 1.0.0.0"
    )
    assert player._detect_phys_addr_line(line) == "1000"
    assert player._detect_phys_addr_line("TRAFFIC: [ 1]\t>> 0f:82:10:00") is None


def test_cec_source_switch_pauses_away_and_resumes_on_return(monkeypatch: pytest.MonkeyPatch) -> None:
    mpv_sets: list[tuple[str, object]] = []

    monkeypatch.setenv("RELAYTV_CEC_PHYS_ADDR", "1000")
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda name: 12.5)
    monkeypatch.setattr(player, "mpv_set", lambda name, value: mpv_sets.append((name, value)))
    monkeypatch.setattr(
        state,
        "get_settings",
        lambda: {"tv_pause_on_input_change": "1", "tv_auto_resume_on_return": "1"},
    )

    player._handle_tv_source_switch("2000", event="active_source")
    assert mpv_sets == [("pause", True)]
    assert state.get_pause_reason() == "input_changed"

    player._handle_tv_source_switch("1000", event="set_stream_path")
    assert mpv_sets == [("pause", True), ("pause", False)]
    assert state.get_pause_reason() is None


def test_cec_source_return_resumes_after_tv_standby_pause(monkeypatch: pytest.MonkeyPatch) -> None:
    mpv_sets: list[tuple[str, object]] = []

    monkeypatch.setenv("RELAYTV_CEC_PHYS_ADDR", "1000")
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_set", lambda name, value: mpv_sets.append((name, value)))
    monkeypatch.setattr(
        state,
        "get_settings",
        lambda: {"tv_auto_resume_on_return": "1"},
    )
    state.set_pause_reason("tv_standby")

    player._handle_tv_source_switch("1000", event="set_stream_path")

    assert mpv_sets == [("pause", False)]
    assert state.get_pause_reason() is None


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
    runtime_config.refresh_from_env()
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
    runtime_config.refresh_from_env()
    monkeypatch.delenv("RELAYTV_CEC_ALLOW_REQUEST_OVERRIDE", raising=False)
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "0"})

    assert player.cec_enabled(True) is False
    assert player.cec_auto_on_switch(True) is False

    monkeypatch.setenv("RELAYTV_CEC_ALLOW_REQUEST_OVERRIDE", "1")

    assert player.cec_enabled(True) is True
    assert player.cec_auto_on_switch(True) is True


def test_cec_env_controls_runtime_policy_over_stale_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RELAYTV_CEC", "1")
    runtime_config.refresh_from_env()
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "0"})

    assert player.cec_enabled(False) is True
    assert player.cec_monitor_enabled() is True

    monkeypatch.setenv("RELAYTV_CEC", "0")
    runtime_config.refresh_from_env()
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "1"})

    assert player.cec_enabled(False) is False
    assert player.cec_monitor_enabled() is False


def test_cec_setting_controls_runtime_policy_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_CEC", raising=False)
    monkeypatch.delenv("RELAYTV_CEC_ENABLED", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "0"})

    assert player.cec_enabled(False) is False
    assert player.cec_monitor_enabled() is False

    monkeypatch.setattr(player.state, "get_settings", lambda: {"cec_enabled": "1"})

    assert player.cec_enabled(False) is True
    assert player.cec_monitor_enabled() is True


def test_cec_legacy_enabled_env_is_runtime_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RELAYTV_CEC", raising=False)
    monkeypatch.setenv("RELAYTV_CEC_ENABLED", "1")
    runtime_config.refresh_from_env()
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
    runtime_config.refresh_from_env()
    monkeypatch.setattr(routes.state, "get_settings", lambda: {"cec_enabled": "1"})
    monkeypatch.setattr(routes.state, "update_settings", lambda patch: {**{"cec_enabled": "1"}, **patch})
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "stop_cec_monitor", lambda: stopped.append(True))
    monkeypatch.setattr(routes.player, "start_cec_monitor", lambda: None)

    response = routes.update_settings(routes.SettingsReq(cec_enabled="0"))

    assert runtime_config.snapshot().raw("RELAYTV_CEC") == "0"
    assert runtime_config.snapshot().raw("RELAYTV_CEC_ENABLED") == "0"
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
    runtime_config.refresh_from_env()
    monkeypatch.setattr(
        routes.state,
        "get_settings",
        lambda: {"idle_notifications_enabled": True, "idle_dashboard_enabled": False},
    )

    assert x11_overlay.overlay_enabled() is True


def test_x11_overlay_default_disabled_when_idle_dashboard_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import x11_overlay

    monkeypatch.delenv("RELAYTV_X11_OVERLAY", raising=False)
    monkeypatch.delenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(
        routes.state,
        "get_settings",
        lambda: {"idle_notifications_enabled": True, "idle_dashboard_enabled": True},
    )

    assert x11_overlay.overlay_enabled() is False


def test_x11_overlay_can_be_disabled_with_idle_notifications_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import x11_overlay

    monkeypatch.delenv("RELAYTV_X11_OVERLAY", raising=False)
    monkeypatch.setenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED", "0")
    runtime_config.refresh_from_env()
    monkeypatch.setattr(
        routes.state,
        "get_settings",
        lambda: {"idle_notifications_enabled": True, "idle_dashboard_enabled": False},
    )

    assert x11_overlay.overlay_enabled() is False


def test_x11_overlay_honors_explicit_disable(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import x11_overlay

    monkeypatch.setenv("RELAYTV_X11_OVERLAY", "0")
    monkeypatch.setenv("RELAYTV_IDLE_NOTIFICATIONS_ENABLED", "1")
    runtime_config.refresh_from_env()
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
    monkeypatch.setenv("RELAYTV_X11_OVERLAY", "1")
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
    monkeypatch.setenv("RELAYTV_X11_OVERLAY", "1")
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


def test_pi_ytdlp_defaults_prefer_1080p_non_av1_without_progressive_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        'YTDLP_FORMAT',
        'YTDLP_FORMAT_YOUTUBE',
        'YTDLP_FORMAT_RUMBLE',
        'RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT',
        'RELAYTV_YOUTUBE_PROGRESSIVE_FIRST',
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('relaytv_app.ytdlp_format_policy.platform.machine', lambda: 'aarch64')
    profile = {'decode_profile': 'arm_safe', 'display_cap_height': 1080, 'av1_allowed': False}

    youtube_fmt = ytdlp_format_policy.effective_ytdlp_format({}, provider='youtube', profile=profile)
    rumble_fmt = ytdlp_format_policy.effective_ytdlp_format({}, provider='rumble', profile=profile)

    assert youtube_fmt == 'bestvideo[vcodec!*=av01][height<=1080][fps<=60]+bestaudio/best[vcodec!*=av01][height<=1080]/best'
    assert rumble_fmt == 'best*[height<=1080][fps<=60]/best*[height<=1080]/best[height<=1080][fps<=60]/best'
    assert ytdlp_format_policy.youtube_progressive_startup_enabled(profile) is False


def test_pi_ytdlp_safe_selector_remains_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ('YTDLP_FORMAT', 'YTDLP_FORMAT_YOUTUBE', 'RELAYTV_YOUTUBE_PROGRESSIVE_FIRST'):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv('RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT', '1')
    monkeypatch.setattr('relaytv_app.ytdlp_format_policy.platform.machine', lambda: 'aarch64')
    profile = {'decode_profile': 'arm_safe', 'display_cap_height': 1080, 'av1_allowed': False}

    fmt = ytdlp_format_policy.effective_ytdlp_format({}, provider='youtube', profile=profile)

    assert fmt == 'best[height<=1080][fps<=30][vcodec^=avc1]/best[height<=1080][fps<=30]/best[height<=1080]/best'


def test_pi_youtube_resolver_does_not_fall_back_to_auto_when_av1_disallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stdout = 'https://video.example/stream.mp4\nhttps://audio.example/stream.m4a\n'
        stderr = ''

    for key in (
        'YTDLP_FORMAT',
        'YTDLP_FORMAT_YOUTUBE',
        'RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT',
        'RELAYTV_YOUTUBE_PROGRESSIVE_FIRST',
        'YTDLP_ARGS',
        'RELAYTV_YTDLP_JS_RUNTIME',
        'YTDLP_JS_RUNTIME',
    ):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr('relaytv_app.ytdlp_format_policy.platform.machine', lambda: 'aarch64')
    monkeypatch.setattr('relaytv_app.resolver.platform.machine', lambda: 'aarch64')
    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr(
        'relaytv_app.video_profile.get_profile',
        lambda: {'decode_profile': 'arm_safe', 'display_cap_height': 1080, 'av1_allowed': False},
    )
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)

    def fake_run(cmd, check=False):
        calls.append(list(cmd))
        return Proc()

    monkeypatch.setattr(resolver, 'run', fake_run)

    stream, audio = resolver.resolve_streams_ytdlp('https://www.youtube.com/watch?v=abc123')

    assert stream == 'https://video.example/stream.mp4'
    assert audio == 'https://audio.example/stream.m4a'
    assert calls
    assert '-f' in calls[0]
    assert 'vcodec!*=av01' in calls[0][calls[0].index('-f') + 1]


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
    monkeypatch.setattr(jellyfin_service, "retarget_queue_stream_preferences", lambda: 0)
    monkeypatch.setattr(routes.player, "is_playing", lambda: False)
    monkeypatch.setattr(routes.player, "mpv_get_many", lambda props: {})
    monkeypatch.setattr(jellyfin_service, "try_set_mpv_subtitle_track", lambda **kwargs: True)
    captured_now: dict[str, object] = {}
    monkeypatch.setattr(routes.state, "set_now_playing", lambda now: captured_now.update(now))
    monkeypatch.setattr(jellyfin_service, "emit_progress_hint", lambda: None)

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


def test_idle_settings_sync_starts_dashboard_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    ensure_calls: list[dict[str, object]] = []
    notification_calls: list[bool] = []
    overlay_stops: list[bool] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes, '_idle_dashboard_enabled_for_player', lambda: True)
    monkeypatch.setattr(routes, '_idle_notifications_enabled_for_player', lambda: False)
    monkeypatch.setattr(routes, '_idle_visual_surface_enabled_for_player', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'ensure_qt_shell_idle', lambda **kwargs: ensure_calls.append(dict(kwargs)))
    monkeypatch.setattr(
        routes,
        '_ensure_notification_surface',
        lambda wait_for_subscriber=False: notification_calls.append(bool(wait_for_subscriber)),
    )
    monkeypatch.setattr(routes.x11_overlay, 'stop_overlay', lambda: overlay_stops.append(True))

    routes._sync_idle_visual_surfaces_after_settings()

    assert ensure_calls == [{'force': True}]
    assert notification_calls == [False]
    assert overlay_stops == [True]


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
    monkeypatch.setenv('QT_QPA_PLATFORM', 'xcb')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
    monkeypatch.delenv('RELAYTV_HOST_SESSION_TYPE', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'x86_64')

    assert _libmpv_enabled() is True
    assert _native_overlay_toasts_enabled() is False
    assert _overlay_software_mode_enabled() is False


def test_qt_overlay_software_mode_defaults_on_for_wayland(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_SOFTWARE', raising=False)
    monkeypatch.setenv('QT_QPA_PLATFORM', 'wayland')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'wayland')
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'x86_64')

    assert _overlay_software_mode_enabled() is True


def test_qt_overlay_software_mode_defaults_on_for_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_SOFTWARE', raising=False)
    monkeypatch.setenv('QT_QPA_PLATFORM', 'xcb')
    monkeypatch.setenv('XDG_SESSION_TYPE', 'x11')
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


def test_pi_qt_mpv_args_do_not_use_fast_profile_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_ARM_FAST_PROFILE', raising=False)
    monkeypatch.delenv('RELAYTV_QT_SHELL_MPV_ARGS', raising=False)
    monkeypatch.delenv('MPV_ARGS', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'aarch64')

    args = qt_shell_app._build_mpv_args('https://example.com/video.mp4', 123)

    assert '--profile=fast' not in args


def test_qt_subprocess_mpv_args_keep_player_alive_after_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_SHELL_MPV_ARGS', raising=False)
    monkeypatch.delenv('MPV_ARGS', raising=False)

    args = qt_shell_app._build_mpv_args('https://example.com/video.mp4', 123)

    # mpv must survive `stop`/EOF so the Qt shell heartbeat (which quits when
    # the mpv child dies) keeps the shell alive for the idle surface.
    assert '--idle=yes' in args


def test_pi_qt_mpv_args_allow_explicit_fast_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RELAYTV_ARM_FAST_PROFILE', '1')
    monkeypatch.delenv('RELAYTV_QT_SHELL_MPV_ARGS', raising=False)
    monkeypatch.delenv('MPV_ARGS', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'aarch64')

    args = qt_shell_app._build_mpv_args('https://example.com/video.mp4', 123)

    assert '--profile=fast' in args


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


def test_youtube_cookie_strategies_do_not_use_android_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, '_preferred_js_runtime_spec', lambda: 'node')

    strategies = resolver._build_youtube_strategies(
        ['yt-dlp', '--cookies', '/data/cookies.txt', '--js-runtimes', 'node', '--no-playlist'],
        ['best'],
    )

    assert all('youtube:player_client=android' not in args for args, _candidates in strategies)


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


def test_playback_runtime_idle_or_ended_holds_incomplete_runtime_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'title': 'Shared stream', 'resume_pos': 12.0, 'duration_sec': 120.0, 'started': now_ts - 20.0},
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setenv('RELAYTV_PLAYBACK_RUNTIME_GAP_CONFIRM_SEC', '1.0')
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', player.time.time() - 2.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


def test_playback_runtime_idle_or_ended_holds_implausible_completed_runtime_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'title': 'Shared stream', 'resume_pos': 120.0, 'duration_sec': 120.0, 'started': now_ts - 20.0},
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setenv('RELAYTV_PLAYBACK_RUNTIME_GAP_CONFIRM_SEC', '1.0')
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', now_ts - 2.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


def test_playback_runtime_idle_or_ended_recovers_completed_runtime_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'title': 'Shared stream', 'resume_pos': 119.0, 'duration_sec': 120.0, 'started': now_ts - 121.0},
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setenv('RELAYTV_PLAYBACK_RUNTIME_GAP_CONFIRM_SEC', '1.0')
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', 0.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False

    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', player.time.time() - 2.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is True
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


def test_runtime_gap_completion_uses_started_position_for_resumed_items(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    now = {
        'title': 'Resumed movie',
        'resume_pos': 120.0,
        'duration_sec': 120.0,
        'started': now_ts - 11.0,
        '_playback_started_pos': 110.0,
    }

    assert player._runtime_gap_completion_plausible(now) is True


def test_playback_runtime_idle_or_ended_holds_implausible_qt_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'title': 'Shared stream', 'resume_pos': 120.0, 'duration_sec': 120.0, 'started': now_ts - 20.0},
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(
        player,
        'mpv_get_many',
        lambda props: {
            'core-idle': True,
            'eof-reached': True,
            'pause': False,
            'path': '',
            'time-pos': 120.0,
            'duration': 120.0,
        },
    )
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', now_ts - 2.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


def test_playback_runtime_idle_or_ended_holds_implausible_native_qt_end(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'title': 'Shared stream', 'resume_pos': 120.0, 'duration_sec': 120.0, 'started': now_ts - 20.0},
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, 'native_qt_playback_explicitly_ended', lambda: True)
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', now_ts - 2.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


def test_playback_runtime_idle_or_ended_ignores_iptv_live_window_end(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {
            'title': 'Live channel',
            'provider': 'iptv',
            'resume_pos': 29.5,
            'duration_sec': 30.0,
            'started': now_ts - 120.0,
        },
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(
        player,
        'mpv_get_many',
        lambda props: {
            'core-idle': True,
            'eof-reached': False,
            'pause': False,
            'path': 'https://example.com/live.m3u8',
            'time-pos': 29.5,
            'duration': 30.0,
        },
    )
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', now_ts - 2.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


def test_playback_runtime_idle_or_ended_holds_live_telemetry_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    now_ts = player.time.time()
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {
            'title': 'Live channel',
            'provider': 'iptv',
            'resume_pos': 29.5,
            'duration_sec': 30.0,
            'started': now_ts - 120.0,
        },
        raising=False,
    )
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(player, 'native_qt_playback_explicitly_ended', lambda: False)
    monkeypatch.setattr(player, '_PLAYBACK_IDLE_CANDIDATE_SINCE', now_ts - 5.0, raising=False)

    assert player._playback_runtime_idle_or_ended() is False
    assert player._PLAYBACK_IDLE_CANDIDATE_SINCE == 0.0


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
    monkeypatch.setattr(routes.player, 'startup_session_restore_pending', lambda: False)
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
    monkeypatch.setattr(playback_service, 'restore_playback_state', lambda snapshot: (_ for _ in ()).throw(AssertionError('close must not restore temporary playback')))
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


def test_notification_surface_does_not_start_x11_overlay_when_qt_shell_running(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setattr(routes, '_idle_notifications_enabled_for_player', lambda: True)
    monkeypatch.setattr(routes, '_idle_dashboard_enabled_for_player', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.x11_overlay, 'start_overlay', lambda: calls.append('start_overlay'))
    monkeypatch.setattr(routes.x11_overlay, 'stop_overlay', lambda: calls.append('stop_overlay'))
    monkeypatch.setattr(routes.x11_overlay, 'overlay_running', lambda: False)
    monkeypatch.setattr(routes.player, 'ensure_qt_shell_idle', lambda **kwargs: calls.append('ensure_qt'))

    routes._ensure_notification_surface(wait_for_subscriber=False)

    assert calls == ['stop_overlay', 'ensure_qt']


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
    monkeypatch.setattr(routes.player, 'startup_session_restore_pending', lambda: False)
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


def test_status_preserves_playing_session_during_runtime_dropout(monkeypatch: pytest.MonkeyPatch) -> None:
    session_sets: list[str] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'startup_session_restore_pending', lambda: False)
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'live channel', 'provider': 'iptv'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda val: session_sets.append(val))
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})
    monkeypatch.setattr(
        routes,
        '_runtime_capabilities',
        lambda playing=None: {
            'backend_ready': None,
            'native_qt_mpv_runtime_paused': False,
        },
    )

    payload = routes.status()

    assert payload['state'] == 'playing'
    assert payload['playing'] is False
    assert payload['now_playing']['title'] == 'live channel'
    assert payload['playback_runtime_state'] == 'buffering'
    assert payload['playback_runtime_state_reason'] == 'session_runtime_gap'
    assert session_sets == []


def test_status_preserves_playing_session_while_startup_restore_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    session_sets: list[str] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'startup_session_restore_pending', lambda: True)
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'startup resume'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda val: session_sets.append(val))
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})

    payload = routes.status()

    assert payload['state'] == 'playing'
    assert payload['playing'] is False
    assert payload['now_playing']['title'] == 'startup resume'
    assert session_sets == []


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

    # Start position is file-scoped so a reused mpv process does not seek
    # every later loadfile to the resume offset.
    assert args[args.index('--{'):] == ['--{', '--start=42.5', 'https://example.com/video.mp4', '--}']


def test_mpv_split_audio_is_file_scoped_not_process_global(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'get_settings', lambda: {'volume': 75})
    monkeypatch.setattr(player, '_effective_audio_device', lambda settings=None: '')
    monkeypatch.setattr(player, '_x11_mode_active', lambda selected_mode=None: False)
    monkeypatch.setattr(player, '_x11_overlay_enabled', lambda: False)
    monkeypatch.setattr(player, '_provider_hint_for_stream', lambda *_a, **_k: 'generic')
    monkeypatch.setattr(player, '_should_force_ytdl_off', lambda *_a, **_k: False)
    monkeypatch.setattr(player, '_effective_ytdl_format', lambda *_a, **_k: '')

    args = player._build_mpv_args(
        'https://example.com/video.mp4', 'https://example.com/audio.m4a', 'x11'
    )

    # A process-global --audio-file sticks on the idle mpv process and bleeds
    # the first video's audio into every later seamless-replace loadfile.
    assert args[args.index('--{'):] == [
        '--{',
        '--audio-file=https://example.com/audio.m4a',
        'https://example.com/video.mp4',
        '--}',
    ]


def test_qt_external_mpv_args_keep_grouped_file_spec(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'get_settings', lambda: {'volume': 75})
    monkeypatch.setattr(player, '_effective_audio_device', lambda settings=None: '')
    monkeypatch.setattr(player, '_x11_mode_active', lambda selected_mode=None: False)
    monkeypatch.setattr(player, '_x11_overlay_enabled', lambda: False)
    monkeypatch.setattr(player, '_provider_hint_for_stream', lambda *_a, **_k: 'generic')
    monkeypatch.setattr(player, '_should_force_ytdl_off', lambda *_a, **_k: False)
    monkeypatch.setattr(player, '_effective_ytdl_format', lambda *_a, **_k: '')

    args = player._build_qt_external_mpv_args(
        'https://example.com/video.mp4', 'https://example.com/audio.m4a', start_pos=42.5
    )

    # The grouped per-file spec must stay intact and the media URL must appear
    # exactly once (not duplicated by an unclosed --{ group).
    assert args[-1] == '--}'
    assert args.count('https://example.com/video.mp4') == 1
    assert args[args.index('--{'):] == [
        '--{',
        '--audio-file=https://example.com/audio.m4a',
        '--start=42.5',
        'https://example.com/video.mp4',
        '--}',
    ]

    plain = player._build_qt_external_mpv_args('https://example.com/video.mp4', None)
    assert '--{' not in plain
    assert plain[-1] == 'https://example.com/video.mp4'


def test_qt_subprocess_mpv_args_scope_audio_and_start_to_first_file(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_SHELL_MPV_ARGS', raising=False)
    monkeypatch.delenv('MPV_ARGS', raising=False)

    args = qt_shell_app._build_mpv_args(
        'https://example.com/video.mp4',
        123,
        audio='https://example.com/audio.m4a',
        start_pos=12.0,
    )

    # The Pi's --idle=yes subprocess is reused across queue items via
    # `loadfile ... replace`; file-scoped options must not leak into later loads.
    assert args[args.index('--{'):] == [
        '--{',
        '--audio-file=https://example.com/audio.m4a',
        '--start=12',
        'https://example.com/video.mp4',
        '--}',
    ]

    plain = qt_shell_app._build_mpv_args('https://example.com/video.mp4', 123)
    assert plain[-1] == 'https://example.com/video.mp4'
    assert '--{' not in plain


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


def test_preserve_current_redacts_iptv_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    secret_url = 'https://cdn.example/live/abc123?token=SECRET-CRED'

    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 41.0 if prop == 'time-pos' else 0.0)
    monkeypatch.setattr(routes.player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {
        'url': secret_url,
        'title': 'Al Jazeera English',
        'provider': 'iptv',
        'iptv_source_id': 'src-1',
        'iptv_channel_id': 'chan-9',
        'http_headers': {'User-Agent': 'x'},
        '_resolved_stream': secret_url,
    }, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))

    preserved = routes._preserve_current_to_queue_front()

    assert preserved is not None
    entry = persisted[-1]['queue'][0]
    # Only the opaque catalog reference is persisted — never the credential URL.
    assert entry['url'] == 'https://iptv.invalid/src-1/chan-9'
    assert entry['iptv_source_id'] == 'src-1'
    assert entry['iptv_channel_id'] == 'chan-9'
    assert '_resolved_stream' not in entry and '_resolved_source_url' not in entry
    assert entry['resume_pos'] == 41.0
    assert entry['_relaytv_interrupt_preserved'] is True
    assert 'SECRET-CRED' not in json.dumps(persisted[-1])


def test_history_entry_redacts_iptv_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[dict] = []
    secret_url = 'https://cdn.example/live/abc123?token=SECRET-CRED'

    monkeypatch.setattr(routes.state, 'history_contains', lambda hid: False)
    monkeypatch.setattr(routes.state, 'history_add', lambda entry: captured.append(entry))

    routes.player._add_history_entry({
        'url': secret_url,
        'title': 'Al Jazeera English',
        'provider': 'iptv',
        'mode': 'iptv',
        'iptv_source_id': 'src-1',
        'iptv_channel_id': 'chan-9',
        '_resolved_source_url': secret_url,
        '_resolved_stream': secret_url,
    })

    assert len(captured) == 1
    entry = captured[0]
    assert entry['iptv_source_id'] == 'src-1'
    assert entry['iptv_channel_id'] == 'chan-9'
    assert '_resolved_stream' not in entry
    # What actually hits history.json must carry only the opaque reference.
    persistable = routes.state._persistable_history_item(entry)
    assert persistable['url'] == 'https://iptv.invalid/src-1/chan-9'
    assert 'SECRET-CRED' not in json.dumps(persistable)


def test_mpv_up_next_skips_iptv_and_header_bearing_items() -> None:
    # IPTV needs re-resolution + redaction and header-bearing items need their
    # per-channel headers, so both must bypass mpv's direct up-next handoff.
    assert routes.player._mpv_up_next_eligible_item({
        'url': 'https://cdn.example/live.m3u8', 'provider': 'iptv',
        'iptv_source_id': 's', 'iptv_channel_id': 'c',
    }) is False
    assert routes.player._mpv_up_next_eligible_item({
        'url': 'https://cdn.example/vod.mp4', 'provider': 'jellyfin',
        'http_headers': {'User-Agent': 'x'},
    }) is False


def test_preserve_current_does_not_stack_interrupt_items(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    original_resume = {
        'url': 'https://jellyfin.example/title.m3u8',
        'title': 'Interrupted Jellyfin Title',
        'provider': 'jellyfin',
        '_relaytv_interrupt_preserved': True,
        'resume_pos': 120.0,
    }
    remaining = {'url': 'https://jellyfin.example/next.m3u8', 'title': 'Next Jellyfin Title', 'provider': 'jellyfin'}

    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get', lambda prop: 12.0 if prop == 'time-pos' else 60.0)
    monkeypatch.setattr(routes.player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'url': 'https://example.com/temporary-share.mp4', 'title': 'Temporary Share'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [original_resume, remaining], raising=False)
    monkeypatch.setattr(routes.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))

    preserved = routes._preserve_current_to_queue_front()

    assert preserved is None
    assert routes.state.QUEUE == [original_resume, remaining]
    assert persisted == []


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


def test_auto_next_does_not_dequeue_interrupted_item_after_incomplete_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    play_calls: list[object] = []
    now_ts = player.time.time()

    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://example.com/share.mp4', 'resume_pos': 15.0, 'duration_sec': 120.0, 'started': now_ts - 20.0},
        raising=False,
    )
    monkeypatch.setattr(
        player.state,
        'QUEUE',
        [{'url': 'https://example.com/interrupted.mp4', '_relaytv_interrupt_preserved': True}],
        raising=False,
    )
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(player, 'play_item', lambda *args, **kwargs: play_calls.append(args) or {})

    with pytest.raises(player.QueueAdvanceSuppressedError):
        player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    assert player.state.QUEUE == [{'url': 'https://example.com/interrupted.mp4', '_relaytv_interrupt_preserved': True}]
    assert persisted == []
    assert play_calls == []


def test_manual_next_can_dequeue_interrupted_item(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    play_calls: list[dict[str, object]] = []

    monkeypatch.setattr(player.state, 'NOW_PLAYING', {'url': 'https://example.com/share.mp4'}, raising=False)
    monkeypatch.setattr(
        player.state,
        'QUEUE',
        [{'url': 'https://example.com/interrupted.mp4', '_relaytv_interrupt_preserved': True, 'resume_pos': 12.5}],
        raising=False,
    )
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)
    monkeypatch.setattr(
        player,
        'play_item',
        lambda item, **kwargs: play_calls.append({'item': item, **kwargs}) or {'url': item['url']},
    )

    result = player.advance_queue_playback(mode='next', prefer_playlist_next=False)

    assert result['status'] == 'playing_next'
    assert player.state.QUEUE == []
    assert persisted[-1]['queue'] == []
    assert play_calls[-1]['item']['url'] == 'https://example.com/interrupted.mp4'
    assert play_calls[-1]['start_pos'] == 12.5


def test_auto_next_worker_prefers_mpv_playlist_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []
    sleep_calls = 0

    def fake_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise RuntimeError('stop worker')

    def fake_advance(**kwargs):
        calls.append(dict(kwargs))
        raise RuntimeError('stop worker')

    monkeypatch.setattr(player.time, 'sleep', fake_sleep)
    monkeypatch.setattr(player, '_SESSION_RESTORE_ATTEMPTED', True, raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'QUEUE', [{'url': 'https://example.com/next.mp4'}], raising=False)
    monkeypatch.setattr(player, '_playback_runtime_idle_or_ended', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, '_set_auto_next_transition', lambda value: None)
    monkeypatch.setattr(player, 'advance_queue_playback', fake_advance)

    with pytest.raises(RuntimeError, match='stop worker'):
        player._autoplay_next_worker()

    assert calls == [{'mode': 'auto_next', 'prefer_playlist_next': True}]


def test_startup_session_restore_waits_for_ready_qt_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', {'url': 'https://example.com/resume.mp4'}, raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_idle_qt_shell_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_display_stable', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: False)

    assert player._startup_session_restore_waiting_for_qt_runtime() is True

    monkeypatch.setattr(player, '_qt_shell_display_stable', lambda: True)
    assert player._startup_session_restore_waiting_for_qt_runtime() is True

    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda max_age_sec=3.0: {
            'freshness': 'fresh',
            'alive': True,
            'qt_overlay_enabled': True,
            'qt_overlay_load_ok': True,
            'control_file': '/tmp/relaytv-qt-runtime-control.json',
        },
    )
    assert player._startup_session_restore_waiting_for_qt_runtime() is False


def test_startup_session_restore_does_not_wait_without_pending_qt_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, '_qt_shell_display_stable', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)

    assert player._startup_session_restore_waiting_for_qt_runtime() is False

    monkeypatch.setattr(player.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', {'url': 'https://example.com/resume.mp4'}, raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    assert player._startup_session_restore_waiting_for_qt_runtime() is False


def test_auto_next_playlist_handoff_uses_armed_item_after_eof(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[object]] = []

    monkeypatch.setattr(player, 'is_playing', lambda: False)
    monkeypatch.setattr(
        player,
        'prime_mpv_up_next_from_queue',
        lambda force=False: (_ for _ in ()).throw(AssertionError('already armed EOF handoff should not re-prime')),
    )
    monkeypatch.setattr(player.state, 'QUEUE', [{'url': 'https://example.com/next.mp4'}], raising=False)
    monkeypatch.setattr(player, '_MPV_UPNEXT_ARMED_ID', player._queue_item_identity(player.state.QUEUE[0]), raising=False)
    monkeypatch.setattr(player, '_MPV_UPNEXT_ARMED_URL', 'https://example.com/next.mp4', raising=False)
    monkeypatch.setattr(player, '_MPV_UPNEXT_ARMED_AT', 123.0, raising=False)
    monkeypatch.setattr(player, 'mpv_command', lambda command: commands.append(list(command)) or {'error': 'success'})
    monkeypatch.setattr(player, 'mpv_get_many', lambda props: {'playlist-pos': 0, 'playlist-count': 2, 'time-pos': None, 'path': '', 'pause': False})

    method = player._attempt_playlist_next_handoff(poll_sleep=lambda _seconds: None)

    assert method == 'mpv_playlist_next_pending'
    assert commands == [['playlist-next', 'force']]


def test_auto_next_resumes_interrupted_item_without_dropping_queue_tail(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    play_calls: list[dict[str, object]] = []
    resumed = {'url': 'https://jellyfin.example/title.m3u8', '_relaytv_interrupt_preserved': True, 'resume_pos': 120.0}
    remaining = {'url': 'https://jellyfin.example/next.m3u8', 'title': 'Next Jellyfin Title'}
    now_ts = player.time.time()

    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://example.com/share.mp4', 'resume_pos': 119.0, 'duration_sec': 120.0, 'started': now_ts - 119.0},
        raising=False,
    )
    monkeypatch.setattr(player.state, 'QUEUE', [resumed, remaining], raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)
    monkeypatch.setattr(
        player,
        'play_item',
        lambda item, **kwargs: play_calls.append({'item': item, **kwargs}) or {'url': item['url']},
    )

    result = player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    assert result['status'] == 'playing_next'
    assert player.state.QUEUE == [remaining]
    assert persisted[-1]['queue'] == [remaining]
    assert play_calls[-1]['item'] == resumed
    assert play_calls[-1]['clear_queue'] is False
    assert play_calls[-1]['start_pos'] == 120.0


def test_resolver_botcheck_error_is_typed_http_400() -> None:
    from fastapi import HTTPException

    assert issubclass(resolver.YouTubeBotCheckError, HTTPException)
    assert resolver._youtube_error_is_botcheck("sign in to confirm you're not a bot")
    assert resolver._categorize_resolver_error("Sign in to confirm you're not a bot") == 'botcheck'


def test_auto_next_skips_bot_checked_video_instead_of_retrying(monkeypatch: pytest.MonkeyPatch) -> None:
    persisted: list[dict] = []
    play_calls: list[dict] = []
    toasted: list[object] = []
    bot_item = {'url': 'https://www.youtube.com/watch?v=botcheck', 'title': 'Bot Checked'}
    good_item = {'url': 'https://example.com/good.mp4', 'title': 'Good'}

    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'QUEUE', [bot_item, good_item], raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: persisted.append(dict(payload)))
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)
    monkeypatch.setattr(player, '_notify_bot_check_skip', lambda item: toasted.append(item))

    def fake_play(item, **kwargs):
        if item is bot_item:
            raise player.YouTubeBotCheckError(
                status_code=400,
                detail='yt-dlp failed: YouTube requires anti-bot verification/cookies.',
            )
        play_calls.append(dict(item))
        return {'url': item['url']}

    monkeypatch.setattr(player, 'play_item', fake_play)

    result = player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    assert result['status'] == 'playing_next'
    assert result['skipped_unplayable'] == 1
    assert play_calls == [good_item]
    # Skipped item must NOT be re-queued (re-queueing caused a retry loop).
    assert player.state.QUEUE == []
    assert toasted == [bot_item]


def test_auto_next_skips_post_live_processing_video(monkeypatch: pytest.MonkeyPatch) -> None:
    processing = {'url': 'https://youtube.com/watch?v=processing', 'title': 'Processing Live'}
    ready = {'url': 'https://example.com/ready.mp4', 'title': 'Ready'}
    played: list[dict] = []

    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'QUEUE', [processing, ready], raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: None)
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)

    def fake_play(item, **kwargs):
        if item is processing:
            raise player.YouTubePostLiveProcessingError('Processing Live')
        played.append(dict(item))
        return {'url': item['url']}

    monkeypatch.setattr(player, 'play_item', fake_play)

    result = player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    assert result['status'] == 'playing_next'
    assert result['skipped_unplayable'] == 1
    assert played == [ready]
    assert player.state.QUEUE == []


def test_auto_next_drops_bot_checked_last_item_without_requeue(monkeypatch: pytest.MonkeyPatch) -> None:
    bot_item = {'url': 'https://www.youtube.com/watch?v=botcheck', 'title': 'Bot Checked'}

    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'QUEUE', [bot_item], raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: None)
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)
    monkeypatch.setattr(player, '_notify_bot_check_skip', lambda item: None)

    def fake_play(item, **kwargs):
        raise player.YouTubeBotCheckError(status_code=400, detail='bot check')

    monkeypatch.setattr(player, 'play_item', fake_play)

    with pytest.raises(player.QueueAdvanceEmptyError):
        player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    assert player.state.QUEUE == []


def test_auto_next_still_requeues_non_botcheck_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    flaky_item = {'url': 'https://example.com/flaky.mp4', 'title': 'Flaky'}

    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'QUEUE', [flaky_item], raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: None)
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)

    def fake_play(item, **kwargs):
        raise HTTPException(status_code=400, detail='yt-dlp failed: timed out')

    monkeypatch.setattr(player, 'play_item', fake_play)

    with pytest.raises(HTTPException):
        player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    assert player.state.QUEUE == [flaky_item]


def test_bot_check_skip_toast_names_video_and_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[dict] = []
    monkeypatch.setattr(routes, '_push_overlay_toast', lambda **kwargs: toasts.append(dict(kwargs)))

    class _SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self) -> None:
            self._target()

    monkeypatch.setattr(player.threading, 'Thread', _SyncThread)

    player._notify_bot_check_skip({'url': 'https://www.youtube.com/watch?v=x', 'title': 'My Video'})

    assert len(toasts) == 1
    assert 'bot check' in toasts[0]['text'].lower()
    assert 'My Video' in toasts[0]['text']
    assert toasts[0]['level'] == 'warn'


def test_post_live_processing_toast_has_blank_line_and_title(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []
    monkeypatch.setattr(player, '_notify_warn_toast', lambda text: toasts.append(text))

    player._notify_post_live_processing(
        {'url': 'https://youtube.com/watch?v=processing', 'title': 'Processing Live'}
    )

    assert toasts == [
        'YouTube is processing this live stream. Replay is not currently available.\n\nProcessing Live'
    ]


def test_restart_current_keeps_playback_when_bot_check_blocks_resolve(monkeypatch: pytest.MonkeyPatch) -> None:
    toasts: list[str] = []
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://www.youtube.com/watch?v=current', 'title': 'Current Video'},
        raising=False,
    )
    monkeypatch.setattr(player, '_notify_bot_check_toast', lambda text: toasts.append(text))

    def fake_resolve(url):
        raise player.YouTubeBotCheckError(
            status_code=400,
            detail='yt-dlp failed: YouTube requires anti-bot verification/cookies.',
        )

    monkeypatch.setattr(player, 'resolve_streams', fake_resolve)
    monkeypatch.setattr(player, 'stop_mpv', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not stop playback when resolve fails')))
    monkeypatch.setattr(player, 'play_item', lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not replay when resolve fails')))

    assert player.restart_current() is None
    assert len(toasts) == 1
    assert 'bot check' in toasts[0].lower()
    assert 'Current Video' in toasts[0]


@pytest.mark.parametrize('resolve_outcome', ['resolves_post_live_relay_disabled', 'raises_processing'])
def test_restart_current_keeps_playback_when_post_live_is_processing(
    monkeypatch: pytest.MonkeyPatch,
    resolve_outcome: str,
) -> None:
    toasted: list[object] = []
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://youtube.com/watch?v=processing', 'title': 'Processing Live'},
        raising=False,
    )
    # Without the relay a resolved post_live stream is only watchable at the
    # live edge, so a restart must keep the running playback.
    monkeypatch.setenv('RELAYTV_POSTLIVE_RELAY', '0')

    def resolve(url):
        if resolve_outcome == 'raises_processing':
            raise resolver.YouTubePostLiveProcessingError(url)
        return resolver.ResolvedStreams(stream=url, transport='mpv_ytdl', live_status='post_live')

    monkeypatch.setattr(player, 'resolve_streams', resolve)
    monkeypatch.setattr(player, '_notify_post_live_processing', lambda item: toasted.append(item))
    monkeypatch.setattr(
        player,
        'stop_mpv',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must keep playback running')),
    )
    monkeypatch.setattr(
        player,
        'play_item',
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError('must not replay post-live')),
    )

    assert player.restart_current() is None
    assert toasted == [
        {
            'url': 'https://youtube.com/watch?v=processing',
            'title': 'Processing Live',
        }
    ]


def test_restart_current_replays_post_live_through_relay(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    played: list[dict] = []
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://youtube.com/watch?v=processing', 'title': 'Processing Live'},
        raising=False,
    )
    monkeypatch.setattr(
        player,
        'resolve_streams',
        lambda url: (
            calls.append('resolve'),
            resolver.ResolvedStreams(stream=url, transport='mpv_ytdl', live_status='post_live'),
        )[1],
    )
    monkeypatch.setattr(player, 'is_playing', lambda: False)
    monkeypatch.setattr(player, 'stop_mpv', lambda *args, **kwargs: calls.append('stop'))
    monkeypatch.setattr(
        player,
        '_post_live_relay_source',
        lambda item, result: (calls.append('relay'), 'http://127.0.0.1:8787/postlive/tokr.mkv')[1],
    )

    def fake_play(item, **kwargs):
        calls.append('play')
        played.append(dict(item))
        return {'url': item['url']}

    monkeypatch.setattr(player, 'play_item', fake_play)

    now = player.restart_current()

    # The relay session spawns BEFORE playback stops (a spawn failure must
    # keep the current stream running), and play_item consumes the prepared
    # session instead of re-resolving.
    assert now == {'url': 'https://youtube.com/watch?v=processing'}
    assert calls == ['resolve', 'relay', 'stop', 'play']
    assert played[0]['_prepared_post_live_relay'] == {
        'stream': 'http://127.0.0.1:8787/postlive/tokr.mkv'
    }
    assert '_transient_mpv_ytdl_handoff' not in played[0]


def test_restart_current_keeps_playing_when_relay_cannot_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    toasts: list[object] = []
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://youtube.com/watch?v=processing', 'title': 'Processing Live'},
        raising=False,
    )
    monkeypatch.setattr(
        player,
        'resolve_streams',
        lambda url: resolver.ResolvedStreams(stream=url, transport='mpv_ytdl', live_status='post_live'),
    )
    # Relay enabled but the pipeline fails to start (missing ffmpeg, disk,
    # cookie path...): current playback must stay untouched.
    monkeypatch.setattr(player, '_post_live_relay_source', lambda item, result: None)
    monkeypatch.setattr(player, '_notify_post_live_processing', lambda item: toasts.append(item))
    monkeypatch.setattr(player, 'stop_mpv', lambda *a, **k: calls.append('stop'))
    monkeypatch.setattr(player, 'play_item', lambda *a, **k: calls.append('play'))

    assert player.restart_current() is None
    assert calls == []
    assert toasts and toasts[0]['url'] == 'https://youtube.com/watch?v=processing'


def test_restart_current_resolves_before_stopping_and_reuses_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    played: list[dict] = []
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://www.youtube.com/watch?v=current', 'title': 'Current Video'},
        raising=False,
    )
    monkeypatch.setattr(player, 'resolve_streams', lambda url: (calls.append('resolve'), ('https://stream.example/v', 'https://stream.example/a'))[1])
    monkeypatch.setattr(player, 'is_playing', lambda: False)
    monkeypatch.setattr(player, 'stop_mpv', lambda *args, **kwargs: calls.append('stop'))

    def fake_play(item, **kwargs):
        calls.append('play')
        played.append(dict(item))
        return {'url': item['url']}

    monkeypatch.setattr(player, 'play_item', fake_play)

    now = player.restart_current()

    assert now == {'url': 'https://www.youtube.com/watch?v=current'}
    # Resolve must complete before the running player is torn down.
    assert calls == ['resolve', 'stop', 'play']
    assert played[0]['_resolved_stream'] == 'https://stream.example/v'
    assert played[0]['_resolved_audio'] == 'https://stream.example/a'
    assert played[0]['title'] == 'Current Video'


def test_restart_current_non_youtube_does_not_preresolve(monkeypatch: pytest.MonkeyPatch) -> None:
    played: list[object] = []
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(
        player.state,
        'NOW_PLAYING',
        {'url': 'https://example.com/movie.mp4', 'title': 'Movie'},
        raising=False,
    )
    monkeypatch.setattr(player, 'resolve_streams', lambda url: (_ for _ in ()).throw(AssertionError('non-YouTube restart must not pre-resolve')))
    monkeypatch.setattr(player, 'is_playing', lambda: False)
    monkeypatch.setattr(player, 'stop_mpv', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, 'play_item', lambda target, **kwargs: played.append(target) or {'url': 'https://example.com/movie.mp4'})

    assert player.restart_current() == {'url': 'https://example.com/movie.mp4'}
    assert played == ['https://example.com/movie.mp4']


def _patch_resolver_ytdlp_env(monkeypatch: pytest.MonkeyPatch, stdout: str) -> list[list[str]]:
    calls: list[list[str]] = []

    class Proc:
        returncode = 0
        stderr = ''

    Proc.stdout = stdout
    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr(
        'relaytv_app.video_profile.get_profile',
        lambda: {'decode_profile': 'default', 'display_cap_height': 1080, 'av1_allowed': False},
    )
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)

    def fake_run(cmd, check=False):
        calls.append(list(cmd))
        return Proc()

    monkeypatch.setattr(resolver, 'run', fake_run)
    return calls


def test_resolver_defers_postlive_youtube_to_mpv_ytdl_hook(monkeypatch: pytest.MonkeyPatch) -> None:
    url = 'https://www.youtube.com/watch?v=postlive1'
    calls = _patch_resolver_ytdlp_env(
        monkeypatch,
        'https://segments.example/videoplayback?a=1\n'
        'https://segments.example/videoplayback?a=2\n'
        'post_live\n',
    )

    result = resolver.resolve_streams_ytdlp(url)
    stream, audio = result

    # Segmented live formats 204 without per-segment params, so the page URL
    # goes to mpv and its yt-dlp hook drives the manifest.
    assert stream == url
    assert audio is None
    assert '--print' in calls[0]
    assert 'live_status' in calls[0]
    assert '-g' not in calls[0]
    assert result.transport == 'mpv_ytdl'
    assert result.live_status == 'post_live'
    assert result.ytdl_format.startswith('bestvideo[')
    assert result.ytdl_format != 'auto'
    assert 'cookies=' not in result.ytdl_raw_options
    # The winning strategy's base argv is exported verbatim so the post-live
    # relay can re-run the exact strategy: program name + options only, no
    # format selection, print directives, or URL.
    assert result.ytdlp_args
    assert result.ytdlp_args[0] == 'yt-dlp'
    assert list(result.ytdlp_args) == calls[0][: len(result.ytdlp_args)]
    assert calls[0][len(result.ytdlp_args)] in ('-f', '--print')
    assert '--print' not in result.ytdlp_args
    assert url not in result.ytdlp_args


def test_resolver_raises_post_live_processing_when_replay_is_unready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = 'https://www.youtube.com/watch?v=stillcooking'
    calls: list[list[str]] = []

    class Proc:
        returncode = 1
        stdout = ''
        stderr = 'ERROR: [youtube] stillcooking: This live stream recording is not available.'

    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr(
        'relaytv_app.video_profile.get_profile',
        lambda: {'decode_profile': 'default', 'display_cap_height': 1080, 'av1_allowed': False},
    )
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)

    def fake_run(cmd, check=False):
        calls.append(list(cmd))
        return Proc()

    monkeypatch.setattr(resolver, 'run', fake_run)

    with pytest.raises(resolver.YouTubePostLiveProcessingError):
        resolver.resolve_streams_ytdlp(url)
    assert calls


def test_mpv_ytdl_raw_options_quotes_comma_values() -> None:
    value = 'youtube:player_client=default,web_safari'
    out = resolver._mpv_ytdl_raw_options([
        'yt-dlp',
        '--cookies',
        '/data/cookies.txt',
        '--extractor-args',
        value,
        '--no-playlist',
    ])

    # mpv's key/value-list parser has no escape character; comma-bearing
    # values must use its %n% length-prefixed quoting to survive parsing.
    assert out == f'cookies=/data/cookies.txt,extractor-args=%{len(value)}%{value}'


class _FakeRelayPipe:
    def __init__(self, fd: int, chunks: list[bytes] | None = None) -> None:
        self._fd = fd
        self._chunks = list(chunks or [])
        self.closed = False

    def fileno(self) -> int:
        return self._fd

    def read(self, n: int = -1) -> bytes:
        return self._chunks.pop(0) if self._chunks else b''

    def readline(self) -> bytes:
        return b''

    def close(self) -> None:
        self.closed = True


class _FakeRelayProc:
    _next_fd = 100

    def __init__(self, cmd: list[str], kwargs: dict) -> None:
        _FakeRelayProc._next_fd += 1
        self.cmd = list(cmd)
        self.popen_kwargs = kwargs
        self.stdout = _FakeRelayPipe(_FakeRelayProc._next_fd)
        self.stderr = _FakeRelayPipe(_FakeRelayProc._next_fd + 1000)
        self.returncode: int | None = None
        self.terminated = False
        self.killed = False

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        # Mirror a real termination: never exit code 0 — spool completeness
        # detection relies on "0 means the mux finished on its own".
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def _patch_relay_popen(
    monkeypatch: pytest.MonkeyPatch, tmp_path=None
) -> list[_FakeRelayProc]:
    procs: list[_FakeRelayProc] = []

    def fake_popen(cmd, **kwargs):
        proc = _FakeRelayProc(cmd, kwargs)
        procs.append(proc)
        return proc

    import tempfile

    spool_root = str(tmp_path) if tmp_path is not None else tempfile.mkdtemp(prefix='relaytest-postlive-')
    monkeypatch.setattr(postlive_relay, '_spool_root', lambda: spool_root)
    monkeypatch.setattr(postlive_relay.subprocess, 'Popen', fake_popen)
    monkeypatch.setattr(postlive_relay, '_ensure_reaper', lambda: None)
    postlive_relay.close_all(reason='test setup')
    return procs


def test_postlive_relay_splits_merge_format_expressions() -> None:
    fmt = 'bestvideo[vcodec!*=av01][height<=1080][fps<=60]+bestaudio/best'
    assert postlive_relay.split_format_expression(fmt) == (
        'bestvideo[vcodec!*=av01][height<=1080][fps<=60]',
        'bestaudio/best',
    )
    assert postlive_relay.split_format_expression('best') == ('best', None)
    # Empty means the resolver won with yt-dlp's default (bv*+ba/b) — the
    # relay must reproduce that as a split download, never '-f best'
    # (post_live serves no muxed formats; caught live on the appliance).
    assert postlive_relay.split_format_expression('') == ('bv*', 'ba')
    assert postlive_relay.split_format_expression('best[height<=720]/b') == (
        'best[height<=720]/b',
        None,
    )
    # A '+' inside brackets is part of a filter, not a merge.
    assert postlive_relay.split_format_expression('bv[format_note*=a+b]+ba') == (
        'bv[format_note*=a+b]',
        'ba',
    )


def test_postlive_relay_session_spawns_winning_strategy_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)
    monkeypatch.setenv('RELAYTV_PORT', '8787')
    url = 'https://www.youtube.com/watch?v=postlive1'
    base = ('yt-dlp', '--extractor-args', 'youtube:player_client=tv_simply', '--no-playlist')

    session = postlive_relay.create_session(
        url, 'bestvideo[height<=1080]+bestaudio/best', base
    )
    try:
        assert len(procs) == 3
        video_proc, audio_proc, ffmpeg_proc = procs
        # Each downloader re-runs the resolver's winning strategy verbatim.
        assert video_proc.cmd[: len(base)] == list(base)
        assert video_proc.cmd[len(base):] == [
            '-f', 'bestvideo[height<=1080]', '--no-progress', '-o', '-', url,
        ]
        assert audio_proc.cmd[len(base):] == [
            '-f', 'bestaudio/best', '--no-progress', '-o', '-', url,
        ]
        # ffmpeg muxes both downloader stdouts (inherited via pass_fds) into
        # the session's matroska spool file, which it finalizes with
        # duration and cues on clean exit — the basis of the seek upgrade.
        in_fds = [video_proc.stdout.fileno(), audio_proc.stdout.fileno()]
        assert ffmpeg_proc.cmd[0] == 'ffmpeg'
        for fd in in_fds:
            assert f'pipe:{fd}' in ffmpeg_proc.cmd
        assert ffmpeg_proc.cmd[-5:-1] == ['-c', 'copy', '-f', 'matroska']
        assert ffmpeg_proc.cmd[-1] == session.spool_path
        assert session.spool_path == os.path.join(
            postlive_relay._spool_root(), f'{session.token}.mkv'
        )
        assert ffmpeg_proc.popen_kwargs['stdout'] is subprocess.DEVNULL
        assert list(ffmpeg_proc.popen_kwargs['pass_fds']) == in_fds
        # The parent's copies of the downloader read ends are closed so the
        # pipeline's fds die with its processes.
        assert video_proc.stdout.closed
        assert audio_proc.stdout.closed
        assert postlive_relay.relay_url(session.token) == (
            f'http://127.0.0.1:8787/postlive/{session.token}.mkv'
        )
        # Each downloader runs in its own writable workdir: the dash
        # fragment downloader stages '--FragN.part' files in cwd even when
        # streaming to stdout, the server's cwd may be read-only, and a
        # shared dir would collide on the identical .part names.
        video_cwd = video_proc.popen_kwargs['cwd']
        audio_cwd = audio_proc.popen_kwargs['cwd']
        assert video_cwd != audio_cwd
        assert os.path.isdir(video_cwd) and os.path.isdir(audio_cwd)
    finally:
        postlive_relay.close_all(reason='test teardown')
    # close_session removes the workdirs with the pipeline.
    assert not os.path.exists(video_cwd)
    assert not os.path.exists(audio_cwd)


def test_postlive_relay_absolutizes_relative_path_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The children run from temp workdirs, so relative operator paths (e.g.
    # RELAYTV_YTDLP_COOKIES=cookies.txt) that resolved from the server cwd
    # must be pinned to absolute before the cwd changes.
    procs = _patch_relay_popen(monkeypatch)

    postlive_relay.create_session(
        'https://youtube.com/watch?v=x',
        'best',
        ('yt-dlp', '--cookies', 'cookies.txt', '--cache-dir=ytcache', '--no-playlist'),
    )
    try:
        cmd = procs[0].cmd
        cookie_value = cmd[cmd.index('--cookies') + 1]
        assert os.path.isabs(cookie_value)
        assert cookie_value == os.path.abspath('cookies.txt')
        cache_args = [a for a in cmd if a.startswith('--cache-dir=')]
        assert cache_args == [f'--cache-dir={os.path.abspath("ytcache")}']
        assert '--no-playlist' in cmd
    finally:
        postlive_relay.close_all(reason='test teardown')


def test_postlive_relay_spawn_failure_preserves_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Supersession happens only after the new pipeline spawned: a spawn
    # failure must leave the currently playing session untouched
    # (restart-in-place spawns the new session before stopping playback).
    _patch_relay_popen(monkeypatch)
    first = postlive_relay.create_session(
        'https://youtube.com/watch?v=a', 'best', ('yt-dlp',)
    )

    def broken_popen(cmd, **kwargs):
        raise OSError('no such executable')

    monkeypatch.setattr(postlive_relay.subprocess, 'Popen', broken_popen)
    try:
        with pytest.raises(postlive_relay.RelayError):
            postlive_relay.create_session(
                'https://youtube.com/watch?v=b', 'best', ('yt-dlp',)
            )
        assert postlive_relay.get_session(first.token) is first
        assert not first.closed
    finally:
        postlive_relay.close_all(reason='test teardown')


def test_postlive_relay_default_format_spawns_split_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Regression: the resolver's winning post_live attempt often carries no
    # -f at all (yt-dlp default bv*+ba/b). The relay must reproduce that as a
    # video+audio split — '-f best' asks for a muxed format post_live never
    # serves, and the whole pipeline died on the appliance.
    procs = _patch_relay_popen(monkeypatch)

    postlive_relay.create_session('https://youtube.com/watch?v=x', '', ('yt-dlp',))
    try:
        assert len(procs) == 3
        video_proc, audio_proc, ffmpeg_proc = procs
        assert video_proc.cmd[1:3] == ['-f', 'bv*']
        assert audio_proc.cmd[1:3] == ['-f', 'ba']
        assert ffmpeg_proc.cmd.count('-i') == 2
    finally:
        postlive_relay.close_all(reason='test teardown')


def test_postlive_relay_single_format_uses_one_downloader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)

    postlive_relay.create_session('https://youtube.com/watch?v=x', 'best', ('yt-dlp',))
    try:
        assert len(procs) == 2
        ffmpeg_proc = procs[-1]
        assert ffmpeg_proc.cmd.count('-i') == 1
    finally:
        postlive_relay.close_all(reason='test teardown')


def test_postlive_relay_supersedes_previous_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)

    first = postlive_relay.create_session('https://youtube.com/watch?v=a', 'best', ('yt-dlp',))
    second = postlive_relay.create_session('https://youtube.com/watch?v=b', 'best', ('yt-dlp',))
    try:
        # Single-player appliance: the new session tears the old one down.
        assert postlive_relay.get_session(first.token) is None
        assert all(proc.terminated for proc in procs[:2])
        assert postlive_relay.get_session(second.token) is second
        assert first.close_reason == 'superseded'
    finally:
        postlive_relay.close_all(reason='test teardown')


def test_postlive_relay_close_session_terminates_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)

    session = postlive_relay.create_session(
        'https://youtube.com/watch?v=x', 'bv+ba', ('yt-dlp',)
    )
    postlive_relay.close_session(session.token, reason='test')

    assert all(proc.terminated for proc in procs)
    assert postlive_relay.get_session(session.token) is None
    # Idempotent: a second close (e.g. reaper racing the route) is a no-op.
    postlive_relay.close_session(session.token, reason='test again')
    assert session.close_reason == 'test'


def test_postlive_relay_stream_allows_exactly_one_reader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)

    session = postlive_relay.create_session(
        'https://youtube.com/watch?v=x', 'best', ('yt-dlp',)
    )
    with open(session.spool_path, 'wb') as spool:
        spool.write(b'mkv-bytes')
    procs[-1].returncode = 0  # the mux finished on its own

    stream = postlive_relay.iter_stream(session.token)
    assert stream is not None
    # Single-use token: a second attach cannot be served.
    assert postlive_relay.iter_stream(session.token) is None

    assert b''.join(stream) == b'mkv-bytes'
    # Reader EOF closes the session; the downloaders die with it (ffmpeg
    # already exited on its own).
    assert session.closed
    assert session.close_reason == 'reader closed'
    assert all(proc.terminated for proc in procs[:-1])
    # The finalized spool outlives the session for the seek upgrade.
    assert postlive_relay.spool_ready_path(session.token) == session.spool_path
    assert os.path.exists(session.spool_path)
    assert postlive_relay.iter_stream('unknown-token') is None
    postlive_relay.close_all(reason='test teardown')
    assert not os.path.exists(session.spool_path)


def test_postlive_relay_incomplete_spool_is_discarded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_relay_popen(monkeypatch)

    session = postlive_relay.create_session(
        'https://youtube.com/watch?v=x', 'best', ('yt-dlp',)
    )
    with open(session.spool_path, 'wb') as spool:
        spool.write(b'partial')

    # Torn down mid-mux (ffmpeg terminated, never exit 0): the truncated
    # file must never be presented as seekable, nor left on disk.
    assert postlive_relay.spool_ready_path(session.token) is None
    postlive_relay.close_session(session.token, reason='test')
    assert postlive_relay.spool_ready_path(session.token) is None
    assert not os.path.exists(session.spool_path)


def test_postlive_relay_new_session_prunes_completed_spool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)

    first = postlive_relay.create_session(
        'https://youtube.com/watch?v=a', 'best', ('yt-dlp',)
    )
    with open(first.spool_path, 'wb') as spool:
        spool.write(b'done')
    procs[-1].returncode = 0
    postlive_relay.close_session(first.token, reason='reader closed')
    assert postlive_relay.spool_ready_path(first.token) == first.spool_path

    second = postlive_relay.create_session(
        'https://youtube.com/watch?v=b', 'best', ('yt-dlp',)
    )
    try:
        # Single-player appliance: a new play supersedes the kept spool too.
        assert postlive_relay.spool_ready_path(first.token) is None
        assert not os.path.exists(first.spool_path)
    finally:
        postlive_relay.close_session(second.token, reason='test teardown')


def test_postlive_relay_sweep_clears_spool_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    monkeypatch.setattr(postlive_relay, '_spool_root', lambda: str(tmp_path))
    (tmp_path / 'stale.mkv').write_bytes(b'x')
    (tmp_path / 'stale-dir').mkdir()

    postlive_relay.sweep_spool_root()

    assert list(tmp_path.iterdir()) == []


def test_post_live_upgrade_step_swaps_to_finalized_spool(
    monkeypatch: pytest.MonkeyPatch, tmp_path,
) -> None:
    token = 'tokup'
    expected_url = postlive_relay.relay_url(token)
    spool = str(tmp_path / f'{token}.mkv')
    (tmp_path / f'{token}.mkv').write_bytes(b'mkv')
    load_calls: list[tuple] = []
    toasts: list[bool] = []

    monkeypatch.setattr(player, '_load_stream_in_existing_mpv',
                        lambda stream_url, audio_url=None, start_pos=None, **k:
                        load_calls.append((stream_url, start_pos)) or True)
    monkeypatch.setattr(player, '_notify_post_live_seek_ready', lambda: toasts.append(True))

    # Still muxing: keep polling, and a transient path miss is tolerated.
    monkeypatch.setattr(postlive_relay, 'spool_ready_path', lambda t: None)
    monkeypatch.setattr(postlive_relay, 'get_session', lambda t: object())
    monkeypatch.setattr(player, 'mpv_get', lambda key: None)
    assert player._post_live_upgrade_step(token, expected_url, 0) == ('wait', 1)

    # Playback moved on to other media: stop without touching mpv.
    monkeypatch.setattr(player, 'mpv_get', lambda key: 'https://other/media')
    assert player._post_live_upgrade_step(token, expected_url, 0) == ('stop', 0)

    # Session gone and no finalized spool (relay failed): stop.
    monkeypatch.setattr(postlive_relay, 'get_session', lambda t: None)
    assert player._post_live_upgrade_step(token, expected_url, 0) == ('stop', 0)

    # Finalized spool + mpv still on the relay stream: swap at position.
    monkeypatch.setattr(postlive_relay, 'spool_ready_path', lambda t: spool)
    monkeypatch.setattr(
        player, 'mpv_get',
        lambda key: expected_url if key == 'path' else 123.4,
    )
    assert player._post_live_upgrade_step(token, expected_url, 0) == ('upgraded', 0)
    assert load_calls == [(spool, 123.4)]
    assert toasts == [True]


def test_postlive_relay_reaper_reaps_dead_weight_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_relay_popen(monkeypatch)

    session = postlive_relay.create_session(
        'https://youtube.com/watch?v=x', 'best', ('yt-dlp',)
    )
    try:
        now = session.created_at
        assert postlive_relay._session_expired(session, now) == ''
        # mpv never connected within the grace window.
        assert (
            postlive_relay._session_expired(session, now + 61.0)
            == 'no reader attached'
        )
        # The pipeline died before anyone attached.
        session.ffmpeg_proc.returncode = 1
        assert (
            postlive_relay._session_expired(session, now)
            == 'pipeline exited before reader attached'
        )
        session.ffmpeg_proc.returncode = None
        session.reader_attached = True
        assert postlive_relay._session_expired(session, now + 9999.0) == ''
    finally:
        postlive_relay.close_all(reason='test teardown')


def test_postlive_relay_kill_switch_disables_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    procs = _patch_relay_popen(monkeypatch)
    monkeypatch.setenv('RELAYTV_POSTLIVE_RELAY', '0')

    with pytest.raises(postlive_relay.RelayError):
        postlive_relay.create_session('https://youtube.com/watch?v=x', 'best', ('yt-dlp',))
    assert procs == []


def test_server_port_prefers_relaytv_port(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import config as app_config

    monkeypatch.setenv('RELAYTV_PORT', '8790')
    monkeypatch.setenv('PORT', '9000')
    assert app_config.server_port() == 8790
    monkeypatch.delenv('RELAYTV_PORT')
    assert app_config.server_port() == 9000
    monkeypatch.delenv('PORT')
    assert app_config.server_port() == 8787
    monkeypatch.setenv('RELAYTV_PORT', 'bogus')
    assert app_config.server_port() == 8787


def test_container_entrypoint_binds_configured_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The bind port and every generated URL (mDNS, relay loopback) must come
    # from the same source: RELAYTV_PORT moving the URLs while uvicorn stays
    # on 8787 would point mpv at a dead /postlive port.
    from relaytv_app import container_entrypoint

    monkeypatch.setenv('RELAYTV_PORT', '8790')
    args = container_entrypoint._default_server_args()
    assert args[0] == 'uvicorn'
    assert args[-2:] == ['--port', '8790']
    monkeypatch.delenv('RELAYTV_PORT')
    monkeypatch.delenv('PORT', raising=False)
    assert container_entrypoint._default_server_args()[-2:] == ['--port', '8787']


def test_resolver_live_default_candidate_does_not_pass_auto_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = 'https://www.youtube.com/watch?v=postlive-default'
    _patch_resolver_ytdlp_env(
        monkeypatch,
        'https://segments.example/videoplayback?a=1\npost_live\n',
    )
    monkeypatch.setattr(
        'relaytv_app.video_profile.get_profile',
        lambda: {'decode_profile': 'default', 'display_cap_height': 1080, 'av1_allowed': True},
    )

    result = resolver.resolve_streams_ytdlp(url)

    # An empty candidate means yt-dlp's default selection. Do not translate
    # the resolver telemetry label "auto" into the invalid `--format auto`.
    assert result.ytdl_format == ''


def test_resolver_live_handoff_preserves_cookie_and_challenge_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = 'https://www.youtube.com/watch?v=live1'
    calls = _patch_resolver_ytdlp_env(
        monkeypatch,
        'https://segments.example/videoplayback?a=1\nis_live\n',
    )
    monkeypatch.setattr(
        resolver,
        'build_ytdlp_base_args',
        lambda: [
            'yt-dlp',
            '--cookies',
            '/data/cookies.txt',
            '--js-runtimes',
            'deno',
            '--no-playlist',
        ],
    )

    result = resolver.resolve_streams_ytdlp(url)

    assert tuple(result) == (url, None)
    assert result.transport == 'mpv_ytdl'
    assert 'cookies=/data/cookies.txt' in result.ytdl_raw_options
    assert 'js-runtimes=deno' in result.ytdl_raw_options
    assert 'remote-components=ejs:github' in result.ytdl_raw_options
    assert calls


def test_resolver_keeps_resolved_urls_for_vod_youtube(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = _patch_resolver_ytdlp_env(
        monkeypatch,
        'https://cdn.example/video.mp4\n'
        'https://cdn.example/audio.m4a\n'
        'relaytv_format_available_at:1234\n'
        'relaytv_available_at:NA\n'
        'not_live\n',
    )

    result = resolver.resolve_streams_ytdlp('https://www.youtube.com/watch?v=vod1')
    stream, audio = result

    assert stream == 'https://cdn.example/video.mp4'
    assert audio == 'https://cdn.example/audio.m4a'
    assert result.available_at == 1234.0
    assert calls


def test_resolved_media_wait_honors_extractor_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(player.time, 'time', lambda: 1000.4)
    monkeypatch.setattr(player.time, 'sleep', lambda seconds: slept.append(seconds))

    player._wait_for_resolved_media_availability(
        {'_playback_available_at': 1004.0}
    )

    assert slept == [pytest.approx(4.2)]


def test_resolved_media_wait_ignores_expired_or_invalid_availability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slept: list[float] = []
    monkeypatch.setattr(player.time, 'time', lambda: 1000.4)
    monkeypatch.setattr(player.time, 'sleep', lambda seconds: slept.append(seconds))

    player._wait_for_resolved_media_availability({'_resolved_available_at': 999.0})
    player._wait_for_resolved_media_availability({'_resolved_available_at': 'invalid'})

    assert slept == []


def test_resolver_rumble_reads_live_status_with_stream_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _patch_resolver_ytdlp_env(
        monkeypatch,
        'https://cdn.example/rumble-video.mp4\n',
    )

    stream, audio = resolver.resolve_streams_ytdlp('https://rumble.com/v1abcd-some-video.html')

    assert stream == 'https://cdn.example/rumble-video.mp4'
    assert audio is None
    assert '--impersonate' not in calls[0]
    assert '--print' in calls[0]
    assert 'live_status' in calls[0]
    assert '-g' not in calls[0]


def test_rumble_http_403_retries_once_and_preserves_live_handoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = 'https://rumble.com/v1abcd-live-video.html'
    calls: list[list[str]] = []
    responses = [
        subprocess.CompletedProcess(
            [],
            1,
            '',
            'ERROR: Unable to download JSON metadata: HTTP Error 403: Forbidden',
        ),
        subprocess.CompletedProcess(
            [],
            0,
            'https://cdn.example/live.m3u8\nis_live\n',
            '',
        ),
    ]
    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr(
        'relaytv_app.video_profile.get_profile',
        lambda: {'display_cap_height': 1080, 'av1_allowed': False},
    )
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return responses.pop(0)

    monkeypatch.setattr(resolver, 'run', fake_run)

    result = resolver.resolve_streams_ytdlp(url)

    assert len(calls) == 2
    assert '--impersonate' not in calls[0]
    assert calls[1][calls[1].index('--impersonate') + 1] == 'chrome'
    assert result.stream == url
    assert result.transport == 'mpv_ytdl'
    assert result.live_status == 'is_live'
    assert result.ytdl_raw_options.endswith('impersonate=chrome')
    assert '--impersonate' in result.ytdlp_args


def test_rumble_non_403_failure_does_not_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr('relaytv_app.video_profile.get_profile', lambda: {})
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)
    monkeypatch.setattr(
        resolver,
        'run',
        lambda cmd, **kwargs: calls.append(list(cmd))
        or subprocess.CompletedProcess(cmd, 1, '', 'ERROR: This video is private'),
    )

    with pytest.raises(resolver.HTTPException, match='This video is private'):
        resolver.resolve_streams_ytdlp('https://rumble.com/v1abcd-private.html')

    assert len(calls) == 1


def test_rumble_operator_impersonation_is_not_duplicated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setenv('YTDLP_ARGS', '--impersonate safari')
    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr('relaytv_app.video_profile.get_profile', lambda: {})
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)
    monkeypatch.setattr(
        resolver,
        'run',
        lambda cmd, **kwargs: calls.append(list(cmd))
        or subprocess.CompletedProcess(cmd, 1, '', 'HTTP Error 403: Forbidden'),
    )

    with pytest.raises(resolver.HTTPException):
        resolver.resolve_streams_ytdlp('https://rumble.com/v1abcd-blocked.html')

    assert len(calls) == 1
    assert calls[0].count('--impersonate') == 1
    assert calls[0][calls[0].index('--impersonate') + 1] == 'safari'


@pytest.mark.parametrize(
    ('lookup', 'success_stdout', 'expected'),
    [
        ('title', 'Recovered title\n', 'Recovered title'),
        (
            'info',
            '{"title": "Recovered metadata", "live_status": "not_live"}',
            'Recovered metadata',
        ),
    ],
)
def test_rumble_metadata_lookups_share_http_403_fallback(
    monkeypatch: pytest.MonkeyPatch,
    lookup: str,
    success_stdout: str,
    expected: str,
) -> None:
    url = f'https://rumble.com/v1abcd-{lookup}.html'
    calls: list[list[str]] = []
    responses = [
        subprocess.CompletedProcess([], 1, '', 'HTTP Error 403: Forbidden'),
        subprocess.CompletedProcess([], 0, success_stdout, ''),
    ]
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)
    monkeypatch.setattr(
        resolver,
        'run',
        lambda cmd, **kwargs: calls.append(list(cmd)) or responses.pop(0),
    )
    resolver._YTDLP_INFO_CACHE.pop(url, None)

    if lookup == 'title':
        value = resolver.title_from_ytdlp(url)
    else:
        info = resolver.ytdlp_info(url)
        value = info.get('title') if info else None

    assert value == expected
    assert len(calls) == 2
    assert '--impersonate' not in calls[0]
    assert '--impersonate' in calls[1]


def test_rumble_missing_impersonation_support_is_actionable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        subprocess.CompletedProcess([], 1, '', 'HTTP Error 403: Forbidden'),
        subprocess.CompletedProcess(
            [],
            1,
            '',
            'ERROR: Impersonate target "chrome" is not available',
        ),
    ]
    monkeypatch.setattr('relaytv_app.state.get_settings', lambda: {})
    monkeypatch.setattr('relaytv_app.video_profile.get_profile', lambda: {})
    monkeypatch.setattr(resolver.shutil, 'which', lambda name: None)
    monkeypatch.setattr(resolver, 'run', lambda cmd, **kwargs: responses.pop(0))

    with pytest.raises(resolver.HTTPException, match='curl-cffi'):
        resolver.resolve_streams_ytdlp('https://rumble.com/v1abcd-blocked.html')

    runtime = resolver.get_resolver_runtime_state()
    assert runtime['last_outcome_category'] == 'provider_challenge'


def test_playback_start_watchdog_aborts_stream_that_never_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import playback_service

    toasts: list[str] = []
    stops: list[list] = []
    ended: list[str] = []

    class _SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self) -> None:
            self._target()

    now_item = {
        'url': 'https://www.youtube.com/watch?v=stuck',
        'title': 'Stuck Stream',
        'history_id': 'h-stuck',
        'started': 1234,
    }
    monkeypatch.setenv('RELAYTV_PLAYBACK_START_TIMEOUT_SEC', '0.2')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', dict(now_item), raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.threading, 'Thread', _SyncThread)
    monkeypatch.setattr(player, '_playback_runtime_started', lambda: False)
    monkeypatch.setattr(player, '_notify_warn_toast', lambda text: toasts.append(text))
    monkeypatch.setattr(player, 'mpv_command', lambda cmd: stops.append(list(cmd)))
    monkeypatch.setattr(playback_service, 'natural_end', lambda: ended.append('natural_end') or 'idle')

    player._arm_playback_start_watchdog(now_item)

    assert len(toasts) == 1
    assert "Can't start stream" in toasts[0]
    assert 'Stuck Stream' in toasts[0]
    assert ['stop'] in stops
    assert ended == ['natural_end']


def test_playback_runtime_started_uses_live_ipc_not_stale_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    # The previous media's cached time-pos must not count as "started" while
    # the new stream is stuck loading.
    monkeypatch.setattr(player, 'mpv_command', lambda cmd: {'error': 'property unavailable'})
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda **kwargs: {'mpv_runtime_playback_started': False, 'mpv_runtime_time_pos': None},
    )
    assert player._playback_runtime_started() is False

    monkeypatch.setattr(player, 'mpv_command', lambda cmd: {'error': 'success', 'data': 12.5})
    assert player._playback_runtime_started() is True


def test_playback_start_watchdog_noop_once_playback_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    from relaytv_app import playback_service

    toasts: list[str] = []
    ended: list[str] = []

    class _SyncThread:
        def __init__(self, target=None, daemon=None, **kwargs):
            self._target = target

        def start(self) -> None:
            self._target()

    now_item = {
        'url': 'https://www.youtube.com/watch?v=fine',
        'title': 'Working Stream',
        'history_id': 'h-fine',
        'started': 1234,
    }
    monkeypatch.setenv('RELAYTV_PLAYBACK_START_TIMEOUT_SEC', '0.2')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', dict(now_item), raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.threading, 'Thread', _SyncThread)
    monkeypatch.setattr(player, '_playback_runtime_started', lambda: True)
    monkeypatch.setattr(player, '_notify_warn_toast', lambda text: toasts.append(text))
    monkeypatch.setattr(playback_service, 'natural_end', lambda: ended.append('natural_end') or 'idle')

    player._arm_playback_start_watchdog(now_item)

    assert toasts == []
    assert ended == []


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
    events: list[object] = []

    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, '_mark_playback_transition', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda cec: False)
    monkeypatch.setattr(
        player,
        '_load_stream_in_existing_mpv',
        lambda *a, **k: events.append('load') or False,
    )

    def start_mpv(stream_url, audio_url=None, start_pos=None):
        events.append('start')
        start_calls.append(
            {'stream': stream_url, 'audio': audio_url, 'start_pos': start_pos}
        )

    monkeypatch.setattr(
        player,
        'start_mpv',
        start_mpv,
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
    monkeypatch.setattr(
        player.time,
        'sleep',
        lambda seconds: events.append(('sleep', seconds)),
    )

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
            '_resolved_available_at': 1004.0,
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
    assert events[0] == ('sleep', pytest.approx(4.2))
    assert events[1] == 'load'
    assert events[-1] == 'start'


@pytest.mark.parametrize(
    ('provider_name', 'url', 'raw_options'),
    [
        (
            'youtube',
            'https://youtube.com/watch?v=live1',
            'cookies=/data/cookies.txt,js-runtimes=deno',
        ),
        (
            'rumble',
            'https://rumble.com/v1abcd-live.html',
            'impersonate=chrome',
        ),
    ],
)
def test_play_item_forwards_live_ytdl_handoff_without_caching_page_url(
    monkeypatch: pytest.MonkeyPatch,
    provider_name: str,
    url: str,
    raw_options: str,
) -> None:
    load_calls: list[dict[str, object]] = []
    start_calls: list[dict[str, object]] = []
    resolve_calls: list[str] = []

    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, '_mark_playback_transition', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda cec: False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(
        player,
        '_load_stream_in_existing_mpv',
        lambda stream_url, audio_url=None, start_pos=None, **kwargs: load_calls.append(
            {
                'stream': stream_url,
                'audio': audio_url,
                'start_pos': start_pos,
                **kwargs,
            }
        )
        or False,
    )
    monkeypatch.setattr(
        player,
        'start_mpv',
        lambda stream_url, audio_url=None, start_pos=None, **kwargs: start_calls.append(
            {
                'stream': stream_url,
                'audio': audio_url,
                'start_pos': start_pos,
                **kwargs,
            }
        ),
    )
    monkeypatch.setattr(player, '_add_history_entry', lambda now: None)
    monkeypatch.setattr(player, '_prime_mpv_up_next_from_queue', lambda force=False: False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'get_tv_state', lambda: {})
    monkeypatch.setattr(player.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(player.state, 'set_pause_reason', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(
        player,
        'resolve_streams',
        lambda requested_url: resolve_calls.append(requested_url) or resolver.ResolvedStreams(
            stream=requested_url,
            transport='mpv_ytdl',
            ytdl_format='best',
            ytdl_raw_options=raw_options,
            live_status='is_live',
        ),
    )

    now = player.play_item(
        {'url': url, 'title': 'Live stream', 'provider': provider_name, 'is_live': True},
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode='play',
    )

    expected_handoff = {
        'stream': url,
        'audio': None,
        'start_pos': None,
        'ytdl_format_override': 'best',
        'ytdl_raw_options_override': raw_options,
    }
    assert resolve_calls == [url]
    assert load_calls == [expected_handoff]
    assert start_calls == [expected_handoff]
    assert now['stream'] == url
    assert now['is_live'] is True
    assert '_resolved_stream' not in now


@pytest.mark.parametrize(
    'resolve_outcome',
    ['post_live_relay_disabled', 'post_live_relay_fails', 'raises_processing'],
)
def test_play_item_toasts_and_clears_prefetch_when_replay_is_unready(
    monkeypatch: pytest.MonkeyPatch,
    resolve_outcome: str,
) -> None:
    url = 'https://youtube.com/watch?v=stillcooking'
    toasted: list[object] = []

    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda cec: False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'get_tv_state', lambda: {})
    monkeypatch.setattr(player, '_notify_post_live_processing', lambda item: toasted.append(item))
    monkeypatch.setattr(
        player,
        'start_mpv',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('must not start playback')),
    )
    # The relay is the primary post_live path; the skip+toast fallback must
    # survive both a disabled relay and one that fails to start.
    if resolve_outcome == 'post_live_relay_disabled':
        monkeypatch.setenv('RELAYTV_POSTLIVE_RELAY', '0')
    else:
        monkeypatch.setattr(
            postlive_relay,
            'create_session',
            lambda *a, **k: (_ for _ in ()).throw(postlive_relay.RelayError('spawn failed')),
        )

    def resolve(requested_url):
        if resolve_outcome == 'raises_processing':
            raise resolver.YouTubePostLiveProcessingError(requested_url)
        return resolver.ResolvedStreams(
            stream=requested_url,
            transport='mpv_ytdl',
            live_status='post_live',
        )

    monkeypatch.setattr(player, 'resolve_streams', resolve)

    item = {
        'url': url,
        'title': 'Still cooking',
        'provider': 'youtube',
        '_resolved_source_url': url,
        '_resolved_stream': 'https://stale.example/segments',
        '_resolved_audio': None,
        '_resolved_at': 0.0,
    }
    with pytest.raises(resolver.YouTubePostLiveProcessingError):
        player.play_item(item, use_resolver=True, cec=False, clear_queue=False, mode='play')

    assert len(toasted) == 1
    assert toasted[0]['url'] == url
    assert '_resolved_stream' not in toasted[0]


def test_play_item_relays_post_live_replay_through_local_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = 'https://youtube.com/watch?v=stillcooking'
    load_calls: list[dict[str, object]] = []
    sessions: list[tuple] = []
    relay_toasts: list[object] = []

    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, '_mark_playback_transition', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda cec: False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(
        player,
        '_load_stream_in_existing_mpv',
        lambda stream_url, audio_url=None, start_pos=None, **kwargs: load_calls.append(
            {'stream': stream_url, 'audio': audio_url, 'start_pos': start_pos, **kwargs}
        )
        or True,
    )
    monkeypatch.setattr(
        player,
        'start_mpv',
        lambda *a, **k: (_ for _ in ()).throw(AssertionError('reused runtime must handle the load')),
    )
    monkeypatch.setattr(player, '_add_history_entry', lambda now: None)
    monkeypatch.setattr(player, '_prime_mpv_up_next_from_queue', lambda force=False: False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'get_tv_state', lambda: {})
    monkeypatch.setattr(player.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(player.state, 'set_pause_reason', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(player, '_notify_post_live_relay', lambda item: relay_toasts.append(item))
    armed: list[str] = []
    monkeypatch.setattr(
        player, '_arm_post_live_relay_upgrade', lambda stream_url: armed.append(stream_url)
    )

    class _Session:
        token = 'tok1'

    def fake_create_session(page_url, ytdl_format, ytdlp_args=()):
        sessions.append((page_url, ytdl_format, tuple(ytdlp_args)))
        return _Session()

    monkeypatch.setattr(postlive_relay, 'create_session', fake_create_session)
    monkeypatch.setattr(
        player,
        'resolve_streams',
        lambda requested_url: resolver.ResolvedStreams(
            stream=requested_url,
            transport='mpv_ytdl',
            ytdl_format='bestvideo[height<=1080]+bestaudio/best',
            ytdl_raw_options='extractor-args=youtube:player_client=tv_simply',
            live_status='post_live',
            ytdlp_args=('yt-dlp', '--no-playlist'),
        ),
    )

    now = player.play_item(
        {'url': url, 'title': 'Still cooking', 'provider': 'youtube'},
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode='resume',
        start_pos=42.5,
    )

    # The relay session runs the resolver's winning strategy, and mpv gets a
    # plain loopback stream: muxed audio, no ytdl overrides, and no resume
    # position (the progressive relay is not seekable).
    assert sessions == [
        (url, 'bestvideo[height<=1080]+bestaudio/best', ('yt-dlp', '--no-playlist'))
    ]
    assert load_calls == [
        {
            'stream': postlive_relay.relay_url('tok1'),
            'audio': None,
            'start_pos': None,
        }
    ]
    assert relay_toasts and relay_toasts[0]['url'] == url
    assert now['stream'] == postlive_relay.relay_url('tok1')
    # The seek-upgrade watch arms against the exact stream mpv is playing.
    assert armed == [postlive_relay.relay_url('tok1')]


def test_play_item_consumes_prepared_post_live_relay_without_resolving(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # restart_current pre-spawns the relay session and stashes its URL;
    # play_item must load it directly — no second resolve, no second
    # session, and no resolved-stream caching of the single-use token.
    url = 'https://youtube.com/watch?v=prepared'
    relay_stream = 'http://127.0.0.1:8787/postlive/tokprep.mkv'
    load_calls: list[dict[str, object]] = []
    armed: list[str] = []

    monkeypatch.setattr(player, 'update_history_progress', lambda *a, **k: None)
    monkeypatch.setattr(player, '_mark_playback_transition', lambda *a, **k: None)
    monkeypatch.setattr(player, 'cec_auto_on_switch', lambda cec: False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(
        player,
        '_load_stream_in_existing_mpv',
        lambda stream_url, audio_url=None, start_pos=None, **kwargs: load_calls.append(
            {'stream': stream_url, 'audio': audio_url, 'start_pos': start_pos}
        )
        or True,
    )
    monkeypatch.setattr(player, '_add_history_entry', lambda now: None)
    monkeypatch.setattr(player, '_prime_mpv_up_next_from_queue', lambda force=False: False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'get_tv_state', lambda: {})
    monkeypatch.setattr(player.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(player.state, 'set_pause_reason', lambda value: None)
    monkeypatch.setattr(player.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(player, '_arm_post_live_relay_upgrade', lambda s: armed.append(s))
    monkeypatch.setattr(
        player,
        'resolve_streams',
        lambda requested_url: (_ for _ in ()).throw(
            AssertionError('prepared relay must not resolve again')
        ),
    )
    monkeypatch.setattr(
        postlive_relay,
        'create_session',
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError('prepared relay must not spawn a second session')
        ),
    )

    now = player.play_item(
        {
            'url': url,
            'title': 'Prepared replay',
            'provider': 'youtube',
            '_prepared_post_live_relay': {'stream': relay_stream},
        },
        use_resolver=True,
        cec=False,
        clear_queue=False,
        mode='resume',
        start_pos=99.0,
    )

    assert load_calls == [{'stream': relay_stream, 'audio': None, 'start_pos': None}]
    assert armed == [relay_stream]
    assert now['stream'] == relay_stream
    assert '_resolved_stream' not in now
    assert '_resolved_stream' not in now


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


def test_qt_runtime_serializes_the_control_file_until_ack(monkeypatch: pytest.MonkeyPatch) -> None:
    first_waiting_for_ack = threading.Event()
    release_first_ack = threading.Event()
    second_command_published = threading.Event()
    command_calls: list[list[object]] = []
    results: list[dict[str, object]] = []
    errors: list[BaseException] = []

    monkeypatch.setattr(player, '_qt_shell_runtime_accepts_mpv_commands', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_preferred', lambda: True)

    def fake_command(cmd: list[object]) -> dict[str, object]:
        command_calls.append(list(cmd))
        request_id = f'control-{len(command_calls)}'
        if request_id == 'control-2':
            second_command_published.set()
        return {'error': 'success', 'request_id': request_id}

    def fake_finalize(result: dict[str, object], *, timeout_sec=None) -> dict[str, object]:
        if result['request_id'] == 'control-1':
            first_waiting_for_ack.set()
            if not release_first_ack.wait(2.0):
                raise TimeoutError('test did not release first acknowledgement')
        return {**result, 'ack_observed': True, 'ack_reason': 'control_acknowledged'}

    def send_volume(value: int) -> None:
        try:
            results.append(player.mpv_command(['set_property', 'volume', value]))
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(player, '_qt_shell_runtime_command', fake_command)
    monkeypatch.setattr(player, '_qt_shell_runtime_finalize_control_result', fake_finalize)

    first = threading.Thread(target=send_volume, args=(20,))
    second = threading.Thread(target=send_volume, args=(80,))
    first.start()
    assert first_waiting_for_ack.wait(1.0)
    second.start()
    try:
        assert not second_command_published.wait(0.2)
        assert command_calls == [['set_property', 'volume', 20]]
    finally:
        release_first_ack.set()
        first.join(2.0)
        second.join(2.0)

    assert not first.is_alive()
    assert not second.is_alive()
    assert errors == []
    assert command_calls == [
        ['set_property', 'volume', 20],
        ['set_property', 'volume', 80],
    ]
    assert [result['request_id'] for result in results] == ['control-1', 'control-2']


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
    assert 'qt_shell_supervisor_enabled' in payload
    assert 'qt_shell_supervisor_last_action' in payload
    assert 'qt_shell_display_boot_grace_remaining_sec' in payload


def test_qt_shell_supervisor_repairs_stale_idle_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv('RELAYTV_QT_SHELL_BOOT_GRACE_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_DISPLAY_SETTLE_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_SUPERVISOR_COOLDOWN_SEC', '0')
    monkeypatch.setattr(player, '_QT_SHELL_DISPLAY_READY_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_QT_SHELL_SUPERVISOR_LAST_RESTART_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_QT_SHELL_SUPERVISOR_THREAD_STARTED', True, raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_display_available', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setattr(player, '_idle_qt_shell_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda **_: {'selected': True, 'available': False, 'freshness': 'stale', 'alive': False},
    )
    monkeypatch.setattr(player, '_stop_qt_shell', lambda: calls.append('stop_shell'))
    monkeypatch.setattr(player, 'ensure_qt_shell_idle', lambda force=False: calls.append(f'ensure:{force}'))

    assert player._qt_shell_supervisor_tick() is True

    supervisor = player.qt_shell_supervisor_state()
    assert calls == ['stop_shell', 'ensure:True']
    assert supervisor['last_action'] == 'restarted_idle_shell'
    assert supervisor['last_reason'] == 'idle_telemetry_stale'


def test_qt_shell_supervisor_repairs_idle_overlay_load_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv('RELAYTV_QT_SHELL_BOOT_GRACE_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_DISPLAY_SETTLE_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_SUPERVISOR_COOLDOWN_SEC', '0')
    monkeypatch.setattr(player, '_QT_SHELL_DISPLAY_READY_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_QT_SHELL_SUPERVISOR_LAST_RESTART_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_QT_SHELL_SUPERVISOR_THREAD_STARTED', True, raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_display_available', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setattr(player, '_idle_qt_shell_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda **_: {
            'selected': True,
            'available': True,
            'freshness': 'fresh',
            'alive': True,
            'qt_overlay_enabled': True,
            'qt_overlay_load_ok': False,
        },
    )
    monkeypatch.setattr(player, '_stop_qt_shell', lambda: calls.append('stop_shell'))
    monkeypatch.setattr(player, 'ensure_qt_shell_idle', lambda force=False: calls.append(f'ensure:{force}'))

    assert player._qt_shell_supervisor_tick() is True

    supervisor = player.qt_shell_supervisor_state()
    assert calls == ['stop_shell', 'ensure:True']
    assert supervisor['last_action'] == 'restarted_idle_shell'
    assert supervisor['last_reason'] == 'idle_overlay_load_failed'


def test_ensure_qt_shell_idle_waits_for_boot_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv('RELAYTV_QT_SHELL_DISPLAY_SETTLE_SEC', '0')
    monkeypatch.setattr(player, '_QT_SHELL_DISPLAY_READY_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_idle_qt_shell_enabled', lambda allow_notification_fallback=False: True)
    monkeypatch.setattr(player, '_has_x11_display', lambda: False)
    monkeypatch.setattr(player, '_has_wayland_display', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_boot_grace_remaining', lambda: 30.0)
    monkeypatch.setattr(player, '_start_qt_shell', lambda *args, **kwargs: calls.append('start'))

    player.ensure_qt_shell_idle()

    assert calls == []
    assert player.qt_shell_supervisor_state()['display_ready'] is False


def test_ensure_qt_shell_idle_starts_after_boot_grace(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    monkeypatch.setenv('RELAYTV_QT_SHELL_DISPLAY_SETTLE_SEC', '0')
    monkeypatch.setattr(player, '_QT_SHELL_DISPLAY_READY_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_idle_qt_shell_enabled', lambda allow_notification_fallback=False: True)
    monkeypatch.setattr(player, '_has_x11_display', lambda: False)
    monkeypatch.setattr(player, '_has_wayland_display', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_boot_grace_remaining', lambda: 0.0)
    monkeypatch.setattr(player, '_start_qt_shell', lambda *args, **kwargs: calls.append('start'))

    player.ensure_qt_shell_idle()

    assert calls == ['start']


def test_qt_shell_supervisor_recovers_active_audio_without_video(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, object]] = []
    now = {
        'url': 'https://jellyfin.example/items/1/stream',
        'title': 'Movie',
        'stream': 'https://jellyfin.example/resolved/video.m3u8',
        'audio': 'https://jellyfin.example/resolved/audio.m4a',
        'started': player.time.time() - 30,
        'resume_pos': 12.0,
    }

    monkeypatch.setenv('RELAYTV_QT_SHELL_BOOT_GRACE_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_DISPLAY_SETTLE_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_SUPERVISOR_COOLDOWN_SEC', '0')
    monkeypatch.setenv('RELAYTV_QT_SHELL_VIDEO_GRACE_SEC', '0')
    monkeypatch.setattr(player, '_QT_SHELL_DISPLAY_READY_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_QT_SHELL_SUPERVISOR_LAST_RESTART_MONOTONIC', 0.0, raising=False)
    monkeypatch.setattr(player, '_QT_SHELL_SUPERVISOR_THREAD_STARTED', True, raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', dict(now), raising=False)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_display_available', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player, '_is_playing', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda **_: {'selected': True, 'available': True, 'freshness': 'fresh', 'alive': True},
    )
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_output_state',
        lambda max_age_sec=2.0: {
            'path': 'https://jellyfin.example/resolved/video.m3u8',
            'current_vo': '',
            'current_ao': 'pulse',
            'aid': 1,
            'playback_active': True,
            'stream_loaded': True,
            'playback_started': True,
            'sample_detail': '',
        },
    )
    monkeypatch.setattr(player, 'mpv_get', lambda prop: 42.5 if prop == 'time-pos' else False)

    def fake_start_mpv(stream: str, audio_url: str | None = None, start_pos: float | None = None):
        calls.append(('start_mpv', {'stream': stream, 'audio': audio_url, 'start_pos': start_pos}))

    def fake_set_now_playing(payload: dict):
        player.state.NOW_PLAYING = dict(payload)
        calls.append(('now', dict(payload)))

    monkeypatch.setattr(player, 'start_mpv', fake_start_mpv)
    monkeypatch.setattr(player.state, 'set_now_playing', fake_set_now_playing)
    monkeypatch.setattr(player.state, 'set_session_state', lambda value: calls.append(('state', value)))
    monkeypatch.setattr(player.state, 'set_session_position', lambda value: calls.append(('position', value)))

    assert player._qt_shell_supervisor_tick() is True

    supervisor = player.qt_shell_supervisor_state()
    assert calls[0] == (
        'start_mpv',
        {
            'stream': 'https://jellyfin.example/resolved/video.m3u8',
            'audio': 'https://jellyfin.example/resolved/audio.m4a',
            'start_pos': 42.5,
        },
    )
    assert player.state.NOW_PLAYING['mode'] == 'supervisor_recover'
    assert player.state.NOW_PLAYING['resume_pos'] == 42.5
    assert supervisor['last_action'] == 'restarted_active_playback'
    assert supervisor['last_reason'] == 'active_audio_without_video'


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


def test_auto_next_skips_stale_iptv_channel_instead_of_blocking(monkeypatch: pytest.MonkeyPatch) -> None:
    play_calls: list[dict] = []
    stale_iptv = {
        'url': 'https://iptv.invalid/src/chan', 'title': 'Gone Channel',
        'provider': 'iptv', 'iptv_source_id': 'src', 'iptv_channel_id': 'chan',
    }
    good_item = {'url': 'https://example.com/good.mp4', 'title': 'Good'}

    monkeypatch.setattr(player.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(player.state, 'QUEUE', [stale_iptv, good_item], raising=False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'playing', raising=False)
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(player.state, 'persist_queue_payload', lambda payload: None)
    monkeypatch.setattr(player, 'update_history_progress', lambda *args, **kwargs: None)
    monkeypatch.setattr(player, '_emit_jellyfin_stopped_from_now', lambda now: None)

    def fake_play(item, **kwargs):
        if item is stale_iptv:
            raise player.HTTPException(status_code=404, detail='IPTV channel is unavailable')
        play_calls.append(dict(item))
        return {'url': item['url']}

    monkeypatch.setattr(player, 'play_item', fake_play)

    result = player.advance_queue_playback(mode='auto_next', prefer_playlist_next=False)

    # The stale IPTV item is skipped (not re-queued) so autoplay is not blocked.
    assert result['status'] == 'playing_next'
    assert result['skipped_unplayable'] == 1
    assert play_calls == [good_item]
    assert player.state.QUEUE == []


# --- yt-dlp update durability ------------------------------------------------
#
# A six-week-old yt-dlp broke YouTube playback: the resolve succeeded, mpv got
# 403 on the stream, and the device went idle ~10s in. The updater had been
# running and reporting success the whole time, because its install landed in
# tmpfs while its state file lived on /data.


def test_update_path_points_at_the_persistent_volume() -> None:
    env = {"PATH": "/usr/local/bin:/usr/bin", "HOME": "/tmp"}
    container_entrypoint._normalize_path_env(env)

    assert env["PYTHONUSERBASE"] == "/data/ytdlp"
    assert env["PATH"].split(":")[0] == "/data/ytdlp/bin"
    # $HOME is /tmp and /tmp is tmpfs; an install there dies on every recreate.
    assert "/tmp/.local/bin" not in env["PATH"]


def test_update_dir_is_configurable() -> None:
    env = {"PATH": "/usr/bin", "RELAYTV_YTDLP_UPDATE_DIR": "/data/custom"}
    container_entrypoint._normalize_path_env(env)
    assert env["PYTHONUSERBASE"] == "/data/custom"
    assert env["PATH"].startswith("/data/custom/bin:")


def test_version_key_orders_yt_dlp_versions() -> None:
    key = container_entrypoint._version_key
    assert key("2026.8.19") > key("2026.7.4")
    assert key("2026.8.19") > key("2026.8.4")
    assert key("") == ()


def test_a_persisted_copy_older_than_the_image_is_discarded(monkeypatch, tmp_path) -> None:
    """A tree on /data outlives the image around it.

    Left alone it stays first on PATH, so a rebuilt image shipping a newer
    yt-dlp would be shadowed by the old persisted one indefinitely.
    """
    update_dir = tmp_path / "ytdlp"
    (update_dir / "bin").mkdir(parents=True)
    (update_dir / "bin" / "yt-dlp").write_text("#!/bin/sh\n", encoding="utf-8")
    env = {"PATH": f"{update_dir}/bin:/usr/bin", "RELAYTV_YTDLP_UPDATE_DIR": str(update_dir)}

    def _version(_env, *, path=None, user_site=True):
        return "2026.01.01" if path is None else "2026.08.19"  # persisted, image

    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", _version)
    container_entrypoint._prune_persisted_ytdlp(env)

    assert not (update_dir / "bin" / "yt-dlp").exists()


def test_a_persisted_copy_newer_than_the_image_is_kept(monkeypatch, tmp_path) -> None:
    update_dir = tmp_path / "ytdlp"
    (update_dir / "bin").mkdir(parents=True)
    (update_dir / "bin" / "yt-dlp").write_text("#!/bin/sh\n", encoding="utf-8")
    env = {"PATH": f"{update_dir}/bin:/usr/bin", "RELAYTV_YTDLP_UPDATE_DIR": str(update_dir)}

    def _version(_env, *, path=None, user_site=True):
        return "2026.08.19" if path is None else "2026.01.01"

    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", _version)
    container_entrypoint._prune_persisted_ytdlp(env)

    assert update_dir.exists(), "the newer persisted copy was thrown away"


def test_a_persisted_copy_that_cannot_run_is_discarded(monkeypatch, tmp_path) -> None:
    """Usually a console-script shebang naming an interpreter the image dropped."""
    update_dir = tmp_path / "ytdlp"
    (update_dir / "bin").mkdir(parents=True)
    (update_dir / "bin" / "yt-dlp").write_text("#!/usr/bin/python3.9\n", encoding="utf-8")
    env = {"PATH": f"{update_dir}/bin:/usr/bin", "RELAYTV_YTDLP_UPDATE_DIR": str(update_dir)}

    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "")
    container_entrypoint._prune_persisted_ytdlp(env)

    assert not (update_dir / "bin" / "yt-dlp").exists()


def _update_env(tmp_path, **extra):
    state_file = tmp_path / "update.json"
    env = {
        "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE": str(state_file),
        "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS": "6",
        "RELAYTV_YTDLP_UPDATE_DIR": str(tmp_path / "ytdlp"),
    }
    env.update(extra)
    return env, state_file


def test_a_reverted_install_forces_a_check_despite_a_fresh_timestamp(monkeypatch, tmp_path) -> None:
    """The post-deploy case that kept devices on a stale yt-dlp for hours.

    The state file survives on /data; the install used not to. So after a
    recreate the state said "checked minutes ago" while the binary had gone
    back to the image's copy, and the interval gate suppressed the re-check.
    """
    import time as _time

    env, state_file = _update_env(tmp_path)
    state_file.write_text(
        json.dumps({"last_check_ts": _time.time(), "after_version": "2026.08.19"}), encoding="utf-8"
    )
    pip_calls: list[list[str]] = []
    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "2026.07.04")
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "run",
        lambda cmd, **kw: pip_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    container_entrypoint.run_yt_dlp_update(env)

    assert pip_calls, "a reverted install was not re-checked"


def test_a_matching_install_still_honours_the_interval(monkeypatch, tmp_path) -> None:
    import time as _time

    env, state_file = _update_env(tmp_path)
    # Same version and same channel as the state records: nothing has moved, so
    # the interval is the only thing that should decide.
    state_file.write_text(
        json.dumps(
            {"last_check_ts": _time.time(), "after_version": "2026.08.19", "channel": "nightly"}
        ),
        encoding="utf-8",
    )
    pip_calls: list[list[str]] = []
    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "2026.08.19")
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "run",
        lambda cmd, **kw: pip_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    assert container_entrypoint.run_yt_dlp_update(env) is False
    assert pip_calls == []


def test_nightly_channel_passes_pre_and_stable_does_not(monkeypatch, tmp_path) -> None:
    """Every release between 2026.7.4 and 2026.8.19 was a .dev0 pre-release.

    `pip install --upgrade` skips those by design, which is how a device sat on
    a six-week-old yt-dlp while every check honestly reported "nothing newer".
    """
    for channel, expect_pre in (("nightly", True), ("stable", False)):
        env, _ = _update_env(tmp_path / channel, RELAYTV_YTDLP_UPDATE_CHANNEL=channel)
        (tmp_path / channel).mkdir(parents=True, exist_ok=True)
        pip_calls: list[list[str]] = []
        monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "2026.08.19")
        monkeypatch.setattr(
            container_entrypoint.subprocess,
            "run",
            lambda cmd, **kw: pip_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""),
        )
        container_entrypoint.run_yt_dlp_update(env, force=True)
        assert pip_calls, channel
        assert ("--pre" in pip_calls[0]) is expect_pre, channel
        assert pip_calls[0][-1] == "yt-dlp[default,curl-cffi]"


def test_a_failed_nightly_falls_back_to_stable(monkeypatch, tmp_path) -> None:
    """A broken nightly must not leave the device worse off than stable."""
    env, state_file = _update_env(tmp_path)
    (tmp_path / "ytdlp").mkdir(parents=True, exist_ok=True)
    pip_calls: list[list[str]] = []

    def _run(cmd, **kw):
        pip_calls.append(list(cmd))
        rc = 1 if "--pre" in cmd else 0
        return subprocess.CompletedProcess(cmd, rc, "", "boom")

    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "2026.08.19")
    monkeypatch.setattr(container_entrypoint.subprocess, "run", _run)

    assert container_entrypoint.run_yt_dlp_update(env, force=True) is True
    assert len(pip_calls) == 2
    assert "--pre" in pip_calls[0] and "--pre" not in pip_calls[1]
    saved = json.loads(state_file.read_text(encoding="utf-8"))
    # The requested channel is what the staleness check compares against, so a
    # fallback must not masquerade as a channel switch.
    assert saved["channel"] == "nightly"
    assert saved["installed_channel"] == "stable"


def test_an_install_that_does_not_run_is_reverted(monkeypatch, tmp_path) -> None:
    env, state_file = _update_env(tmp_path)
    update_dir = tmp_path / "ytdlp"
    (update_dir / "bin").mkdir(parents=True)
    (update_dir / "bin" / "yt-dlp").write_text("#!/bin/sh\n", encoding="utf-8")

    # Prune sees a working copy; the post-install probe finds it unrunnable.
    calls = {"n": 0}

    def _version(_env, *, path=None, user_site=True):
        calls["n"] += 1
        return "2026.07.04" if calls["n"] <= 3 else ""

    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", _version)
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "run",
        lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    container_entrypoint.run_yt_dlp_update(env, force=True)

    saved = json.loads(state_file.read_text(encoding="utf-8"))
    assert saved["ok"] is False
    assert "did not execute" in saved["error"]


# --- playback failure visibility ---------------------------------------------


def test_mpv_failure_reason_is_extracted_and_redacted(monkeypatch, tmp_path) -> None:
    """mpv runs with --no-terminal, so its log is the only place this exists."""
    from relaytv_app import player

    log = tmp_path / "mpv.log"
    log.write_text(
        "[   0.10][v][cplayer] Starting playback...\n"
        "[  19.85][w][ffmpeg] https: HTTP error 403 Forbidden\n"
        "[  19.85][e][stream] Failed to open https://rr2---sn-x.googlevideo.com/videoplayback?sig=SECRET.\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MPV_LOG_FILE", str(log))

    reason = player.read_mpv_failure_reason()

    assert "Failed to open" in reason
    assert "SECRET" not in reason, "a signed stream URL leaked into the log line"
    assert "<url>" in reason


def test_a_failed_play_records_a_reason_and_a_finished_one_clears_it(monkeypatch, tmp_path) -> None:
    from relaytv_app import player

    log = tmp_path / "mpv.log"
    log.write_text("[ 19.85][e][stream] Failed to open http://x/y.\n", encoding="utf-8")
    monkeypatch.setenv("MPV_LOG_FILE", str(log))

    player.set_last_playback_error(None)
    player.note_playback_failure_if_no_progress({"title": "Something", "resume_pos": 0.0})
    assert "Failed to open" in (player.last_playback_error() or "")

    # An item that actually played is not a failure, whatever the log still holds.
    player.note_playback_failure_if_no_progress({"title": "Something", "resume_pos": 240.0})
    assert player.last_playback_error() is None


def test_the_image_version_probe_ignores_the_persisted_install(monkeypatch) -> None:
    """Stripping PATH alone cannot see the image's yt-dlp.

    PYTHONUSERBASE still steers the import, so /usr/local/bin/yt-dlp would load
    the persisted package and report its version — making the "is the image
    newer?" comparison always compare a version against itself.
    """
    seen: list[dict] = []

    def _run(cmd, **kw):
        seen.append(dict(kw.get("env") or {}))
        return subprocess.CompletedProcess(cmd, 0, "2026.07.04\n", "")

    monkeypatch.setattr(container_entrypoint.subprocess, "run", _run)
    env = {"PATH": "/data/ytdlp/bin:/usr/local/bin", "PYTHONUSERBASE": "/data/ytdlp"}

    container_entrypoint._yt_dlp_version(env, path="/usr/local/bin", user_site=False)
    assert "PYTHONUSERBASE" not in seen[-1]

    container_entrypoint._yt_dlp_version(env)
    assert seen[-1].get("PYTHONUSERBASE") == "/data/ytdlp"


def test_switching_channel_forces_a_check(monkeypatch, tmp_path) -> None:
    """A recorded stable-only check answered a different question.

    Switching to nightly must not wait out an interval that a stable check
    satisfied, or the switch does nothing for hours — exactly when it is being
    made because playback is broken right now.
    """
    import time as _time

    env, state_file = _update_env(tmp_path, RELAYTV_YTDLP_UPDATE_CHANNEL="nightly")
    (tmp_path / "ytdlp").mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        json.dumps(
            {"last_check_ts": _time.time(), "after_version": "2026.07.04", "channel": "stable"}
        ),
        encoding="utf-8",
    )
    pip_calls: list[list[str]] = []
    monkeypatch.setattr(
        container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "2026.07.04"
    )
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "run",
        lambda cmd, **kw: pip_calls.append(list(cmd)) or subprocess.CompletedProcess(cmd, 0, "", ""),
    )

    container_entrypoint.run_yt_dlp_update(env)

    assert pip_calls, "the channel switch was suppressed by the interval"
    assert "--pre" in pip_calls[0]


def test_pruning_never_touches_anything_but_yt_dlp(tmp_path) -> None:
    """RELAYTV_YTDLP_UPDATE_DIR is operator-supplied and easy to point at /data.

    pip will happily create bin/ and lib/ inside a shared directory, so pruning
    must remove yt-dlp's own files rather than the directory it was told to use
    — otherwise one failed probe deletes settings, history, peers, the device id
    and every upload.
    """
    data = tmp_path / "data"
    (data / "bin").mkdir(parents=True)
    (data / "bin" / "yt-dlp").write_text("#!/bin/sh\n", encoding="utf-8")
    site = data / "lib" / "python3.13" / "site-packages"
    site.mkdir(parents=True)
    (site / "yt_dlp").mkdir()
    (site / "yt_dlp" / "__init__.py").write_text("", encoding="utf-8")
    (site / "yt_dlp-2026.8.19.dist-info").mkdir()
    (site / "some_other_package").mkdir()

    precious = ["settings.json", "history.json", "peers.json", "device_id"]
    for name in precious:
        (data / name).write_text("precious", encoding="utf-8")
    (data / "uploads").mkdir()
    (data / "uploads" / "movie.mp4").write_text("x", encoding="utf-8")

    container_entrypoint._discard_persisted_ytdlp(data, "test")

    assert data.exists(), "the update directory itself was deleted"
    for name in precious:
        assert (data / name).read_text(encoding="utf-8") == "precious", name
    assert (data / "uploads" / "movie.mp4").exists()
    assert (site / "some_other_package").exists(), "an unrelated package was removed"
    # ...and yt-dlp really is gone.
    assert not (data / "bin" / "yt-dlp").exists()
    assert not (site / "yt_dlp").exists()
    assert not (site / "yt_dlp-2026.8.19.dist-info").exists()


def test_a_stable_fallback_does_not_retrigger_on_every_poll(monkeypatch, tmp_path) -> None:
    """An unavailable nightly must be retried once per interval, not per poll.

    Driven as a full round trip — write the state by really running a fallback,
    then run again — because recording the fallback channel as the *requested*
    one is a write-side bug that reading a hand-built state file cannot catch.
    """
    env, _state_file = _update_env(tmp_path, RELAYTV_YTDLP_UPDATE_CHANNEL="nightly")
    (tmp_path / "ytdlp").mkdir(parents=True, exist_ok=True)
    pip_calls: list[list[str]] = []

    def _run(cmd, **kw):
        pip_calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 1 if "--pre" in cmd else 0, "", "no nightly")

    monkeypatch.setattr(
        container_entrypoint, "_yt_dlp_version", lambda _env, *, path=None, user_site=True: "2026.07.04"
    )
    monkeypatch.setattr(container_entrypoint.subprocess, "run", _run)

    # First pass: nightly fails, stable succeeds, and the state is written.
    container_entrypoint.run_yt_dlp_update(env, force=True)
    assert len(pip_calls) == 2

    # Second pass, well inside the interval: nothing has actually changed, so
    # this must skip rather than retry the unavailable nightly.
    pip_calls.clear()
    assert container_entrypoint.run_yt_dlp_update(env) is False
    assert pip_calls == []


def test_the_mpv_log_is_rotated_while_the_player_keeps_running(monkeypatch, tmp_path) -> None:
    """mpv runs --idle=yes and is reused across loadfiles.

    A size check that only happens at launch never runs again on a device that
    stays up for weeks, and /tmp is tmpfs. Removing the file alone is not
    enough either — mpv holds the descriptor — so the log-file option has to be
    re-set to make it reopen.
    """
    from relaytv_app import qt_shell_app

    log = tmp_path / "mpv.log"
    log.write_bytes(b"x" * 10)
    monkeypatch.setenv("MPV_LOG_FILE", str(log))
    monkeypatch.setattr(qt_shell_app, "_mpv_log_last_check", 0.0)

    reopened: list[str] = []
    qt_shell_app._rotate_mpv_log_if_needed(reopened.append)
    assert log.exists() and reopened == [], "a small log was rotated needlessly"

    log.write_bytes(b"x" * (qt_shell_app._MPV_LOG_MAX_BYTES + 1))
    monkeypatch.setattr(qt_shell_app, "_mpv_log_last_check", 0.0)
    qt_shell_app._rotate_mpv_log_if_needed(reopened.append)

    assert not log.exists()
    assert reopened == [str(log)], "mpv was never told to reopen its log"


def test_a_failed_resume_still_reports_its_reason(monkeypatch, tmp_path) -> None:
    """resume_pos holds the live position, not progress since the start.

    An item resumed at five minutes already reads as five minutes of progress,
    so a resumed stream that dies instantly used to look like a completed play
    and report nothing — the exact case this diagnostic exists for.
    """
    from relaytv_app import player

    log = tmp_path / "mpv.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("MPV_LOG_FILE", str(log))

    player.note_playback_started(300.0)   # resumed five minutes in
    # mpv writes its error after the play begins, which is the real ordering.
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("[ 19.85][e][stream] Failed to open http://x/y.\n")
    player.note_playback_failure_if_no_progress({"title": "Resumed", "resume_pos": 300.0})
    assert "Failed to open" in (player.last_playback_error() or "")

    # Real progress from that same resume point is not a failure.
    player.note_playback_started(300.0)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("[ 25.00][e][stream] Failed to open http://x/y.\n")
    player.note_playback_failure_if_no_progress({"title": "Resumed", "resume_pos": 340.0})
    assert player.last_playback_error() is None


def test_a_stale_error_is_not_blamed_on_a_later_item(monkeypatch, tmp_path) -> None:
    """The log is cumulative; the diagnostic must not be.

    A short clip that ends quietly after an earlier 403 would otherwise inherit
    that 403, and /status would name the wrong item.
    """
    from relaytv_app import player

    log = tmp_path / "mpv.log"
    monkeypatch.setenv("MPV_LOG_FILE", str(log))
    log.write_text("[ 19.85][e][stream] Failed to open http://old/item.\n", encoding="utf-8")

    # A new play starts; everything above belongs to the previous item.
    player.note_playback_started(0.0)
    player.note_playback_failure_if_no_progress({"title": "Short clip", "resume_pos": 0.5})

    assert player.last_playback_error() is None, "an earlier item's failure was reused"


def test_a_rotated_log_does_not_hide_the_current_failure(monkeypatch, tmp_path) -> None:
    """Rotation restarts the file at zero, so a saved offset would skip past it."""
    from relaytv_app import player

    log = tmp_path / "mpv.log"
    monkeypatch.setenv("MPV_LOG_FILE", str(log))
    log.write_text("x" * 5000, encoding="utf-8")

    player.note_playback_started(0.0)          # offset recorded at 5000
    log.write_text("[ 1.00][e][stream] Failed to open http://x/y.\n", encoding="utf-8")  # rotated

    player.note_playback_failure_if_no_progress({"title": "After rotate", "resume_pos": 0.0})
    assert "Failed to open" in (player.last_playback_error() or "")


def test_every_mpv_backend_writes_the_diagnostic_log(monkeypatch) -> None:
    """The mpv and Qt external_mpv backends launch through _build_mpv_args.

    Gating the log on debug there left last_playback_error reading a file those
    backends never create, despite the feature claiming to explain any failure.
    """
    from relaytv_app import player

    monkeypatch.delenv("MPV_LOG_FILE", raising=False)
    monkeypatch.delenv("RELAYTV_DEBUG", raising=False)
    monkeypatch.delenv("MPV_DEBUG", raising=False)

    args = player._build_mpv_args("http://example.com/video.mp4", None, "play")
    joined = " ".join(args)
    assert "--log-file=" in joined, "no mpv log outside debug: failures would be unexplained"


def test_a_failed_item_is_recorded_even_when_the_queue_advances(monkeypatch) -> None:
    """The common case: a queue is present, so natural_end advances.

    That path never reached the queue-empty handler, and the successor's
    play_item cleared the error, so playlist users saw nothing at all.
    """
    from relaytv_app import playback_service, player, state

    seen: list[object] = []
    monkeypatch.setattr(player, "note_playback_failure_if_no_progress", lambda now: seen.append(now))
    monkeypatch.setattr(player, "_set_auto_next_transition", lambda *_a, **_k: None)
    monkeypatch.setattr(playback_service, "advance_queue", lambda **_k: "advanced")
    monkeypatch.setattr(state, "NOW_PLAYING", {"title": "Failed item", "resume_pos": 0.0})

    with state.QUEUE_LOCK:
        state.QUEUE[:] = [{"url": "http://example.com/next"}]
    try:
        assert playback_service.natural_end() == "advanced"
    finally:
        with state.QUEUE_LOCK:
            state.QUEUE.clear()

    assert seen and (seen[0] or {}).get("title") == "Failed item"


def test_a_stale_persisted_copy_is_pruned_even_with_updates_disabled(monkeypatch, tmp_path) -> None:
    """The PATH prefix is unconditional, so pruning must be too.

    Enable auto-update once, turn it off, then deploy an image carrying a newer
    yt-dlp: the old persisted copy would keep winning indefinitely, because
    pruning used to live only inside the update path.
    """
    update_dir = tmp_path / "ytdlp"
    (update_dir / "bin").mkdir(parents=True)
    (update_dir / "bin" / "yt-dlp").write_text("#!/bin/sh\n", encoding="utf-8")

    monkeypatch.setenv("RELAYTV_YTDLP_UPDATE_DIR", str(update_dir))
    monkeypatch.setenv("RELAYTV_YTDLP_AUTO_UPDATE", "0")

    def _version(_env, *, path=None, user_site=True):
        return "2026.01.01" if user_site else "2026.08.19"   # persisted vs image

    monkeypatch.setattr(container_entrypoint, "_yt_dlp_version", _version)
    monkeypatch.setattr(container_entrypoint, "_normalize_runtime_defaults", lambda env: None)
    monkeypatch.setattr(container_entrypoint, "_sync_legacy_brand_assets", lambda: None)
    monkeypatch.setattr(container_entrypoint, "refresh_display_credentials", lambda env: dict(env))

    started: list[list[str]] = []

    class _Proc:
        returncode = 0

        def wait(self, *a, **k):
            return 0

        def poll(self):
            return 0

    monkeypatch.setattr(
        container_entrypoint.subprocess, "Popen", lambda args, **kw: started.append(list(args)) or _Proc()
    )

    container_entrypoint.main(["true"])

    assert not (update_dir / "bin" / "yt-dlp").exists(), "the stale copy still shadows the image"


def test_the_reader_follows_an_operator_log_file_override(monkeypatch) -> None:
    """The builders skip their own --log-file when an operator supplies one.

    If the reader keeps opening /tmp/mpv.log it reports nothing, or a stale
    reason from a different backend, on exactly those devices.
    """
    from relaytv_app import player

    monkeypatch.delenv("MPV_LOG_FILE", raising=False)
    for name in player._MPV_ARG_ENV_VARS:
        monkeypatch.delenv(name, raising=False)

    assert player._mpv_log_path() == "/tmp/mpv.log"

    monkeypatch.setenv("RELAYTV_QT_SHELL_MPV_ARGS", "--gpu-api=opengl --log-file=/data/mpv-custom.log")
    assert player._mpv_log_path() == "/data/mpv-custom.log"

    # The separated form too, and MPV_LOG_FILE still wins when both are set.
    monkeypatch.delenv("RELAYTV_QT_SHELL_MPV_ARGS")
    monkeypatch.setenv("MPV_ARGS", "--log-file /var/log/mpv.log")
    assert player._mpv_log_path() == "/var/log/mpv.log"
    monkeypatch.setenv("MPV_LOG_FILE", "/explicit.log")
    assert player._mpv_log_path() == "/explicit.log"


def test_the_installer_interval_default_matches_the_app_default() -> None:
    """The installer omits the variable when it equals its own default.

    If the two defaults drift, regenerating .env silently rewrites an explicit
    schedule — an operator who asked for 24h would quietly get the app default
    instead, with nothing in the file to show it happened.
    """
    installer = (ROOT_DIR / "scripts" / "install.sh").read_text(encoding="utf-8")
    app_default = container_entrypoint._parse_float_env({}, "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS", 6.0)

    assert f'YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL="{int(app_default)}"' in installer
    assert f'"${{YTDLP_AUTO_UPDATE_INTERVAL_HOURS_VAL}}" != "{int(app_default)}"' in installer


def test_a_queued_failure_is_still_visible_while_the_next_item_starts(monkeypatch, tmp_path) -> None:
    """The successor starts within milliseconds of the failure being recorded.

    Clearing on start meant /status never exposed it whenever a queue advanced,
    which is the common case — the earlier natural_end fix alone did not help.
    """
    from relaytv_app import player, state

    log = tmp_path / "mpv.log"
    log.write_text("", encoding="utf-8")
    monkeypatch.setenv("MPV_LOG_FILE", str(log))

    # Item A plays, fails, and its reason is recorded.
    player.note_playback_started(0.0)
    with open(log, "a", encoding="utf-8") as handle:
        handle.write("[ 9.9][e][stream] Failed to open http://a/.\n")
    monkeypatch.setattr(state, "NOW_PLAYING", {"title": "A", "resume_pos": 0.0})
    player.note_playback_failure_if_no_progress(state.NOW_PLAYING)
    assert "Failed to open" in (player.last_playback_error() or "")

    # The queue advances: B starts immediately, before it has played anything.
    player.note_playback_started(0.0)
    monkeypatch.setattr(state, "NOW_PLAYING", {"title": "B", "resume_pos": 0.0})
    assert "Failed to open" in (player.last_playback_error() or ""), \
        "the failure vanished the moment the queue advanced"

    # Once B is genuinely playing, it supersedes A's failure.
    monkeypatch.setattr(state, "NOW_PLAYING", {"title": "B", "resume_pos": 30.0})
    assert player.last_playback_error() is None


def test_rotation_follows_an_operator_log_file_override(monkeypatch, tmp_path) -> None:
    """_build_mpv_args suppresses its own --log-file when an operator supplies one.

    Rotating the default path then leaves the log mpv is really writing to
    growing unbounded for the life of the idle player — on tmpfs.
    """
    from relaytv_app import qt_shell_app

    monkeypatch.delenv("MPV_LOG_FILE", raising=False)
    monkeypatch.delenv("MPV_ARGS", raising=False)
    custom = tmp_path / "custom-mpv.log"
    monkeypatch.setenv("RELAYTV_QT_SHELL_MPV_ARGS", f"--gpu-api=opengl --log-file={custom}")

    assert qt_shell_app._effective_mpv_log_path() == str(custom)

    custom.write_bytes(b"x" * (qt_shell_app._MPV_LOG_MAX_BYTES + 1))
    monkeypatch.setattr(qt_shell_app, "_mpv_log_last_check", 0.0)
    reopened: list[str] = []
    qt_shell_app._rotate_mpv_log_if_needed(reopened.append)

    assert not custom.exists(), "the operator's log was left to grow unbounded"
    assert reopened == [str(custom)]
