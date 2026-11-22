# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for Phase 6 TV visual specification (tv_theme, /idle, /x11/overlay, Qt shell)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from relaytv_app import tv_theme
from relaytv_app.main import create_app


ROOT_DIR = Path(__file__).resolve().parents[1]
STATIC_UI_DIR = ROOT_DIR / "app" / "relaytv_app" / "static" / "ui"


def _extract_root_css_vars(css_or_html: str) -> dict[str, str]:
    match = re.search(r":root\s*\{([^}]*)\}", css_or_html, re.S)
    assert match is not None, "Expected a :root CSS declaration block"
    block = match.group(1)
    variables: dict[str, str] = {}
    for line in block.splitlines():
        cleaned = re.sub(r"/\*.*?\*/", "", line).strip()
        if not cleaned.startswith("--") or ":" not in cleaned:
            continue
        name, raw_value = cleaned.split(":", 1)
        variables[name.strip()] = raw_value.rstrip(";").strip()
    return variables


def test_tv_theme_is_pure_python_and_Matches_foundation_dark_palette() -> None:
    assert tv_theme.TV_BACKGROUND == "#0b1017"
    assert tv_theme.TV_BACKGROUND_SECONDARY == "#111824"
    assert tv_theme.TV_SURFACE == "#151d28"
    assert tv_theme.TV_SURFACE_RAISED == "#1c2735"
    assert tv_theme.TV_TEXT_PRIMARY == "#f3f6fa"
    assert tv_theme.TV_TEXT_SECONDARY == "#a7b4c5"
    assert tv_theme.TV_TEXT_TERTIARY == "#8999ad"
    assert tv_theme.TV_ACCENT == "#60a5fa"
    assert tv_theme.TV_STATUS_SUCCESS == "#34d399"
    assert tv_theme.TV_STATUS_WARNING == "#fbbf24"
    assert tv_theme.TV_STATUS_DANGER == "#fb7185"

    foundation_vars = _extract_root_css_vars(
        (STATIC_UI_DIR / "foundation.css").read_text(encoding="utf-8")
    )
    shared_keys = (
        "--background",
        "--surface",
        "--surface-raised",
        "--text-primary",
        "--text-secondary",
        "--text-tertiary",
        "--border",
        "--border-subtle",
        "--border-strong",
        "--accent",
        "--accent-soft",
        "--accent-strong",
        "--status-success",
        "--status-warning",
        "--status-danger",
    )
    for key in shared_keys:
        assert tv_theme.TV_PALETTE[key] == foundation_vars[key], f"Mismatch on {key}"


def test_tv_theme_palette_matches_idle_and_overlay_templates() -> None:
    idle_vars = _extract_root_css_vars((STATIC_UI_DIR / "idle.html").read_text(encoding="utf-8"))
    overlay_vars = _extract_root_css_vars(
        (STATIC_UI_DIR / "x11-overlay.html").read_text(encoding="utf-8")
    )
    for token, expected in tv_theme.TV_PALETTE.items():
        assert idle_vars.get(token) == expected, f"idle.html mismatch for {token}"
        assert overlay_vars.get(token) == expected, f"x11-overlay.html mismatch for {token}"


def test_idle_and_overlay_routes_substitute_placeholders_and_preserve_security_guards() -> None:
    client = TestClient(create_app(testing=True))

    idle_res = client.get("/idle")
    assert idle_res.status_code == 200
    for placeholder in (
        "__IDLE_CATALOG__",
        "__CLOCK_OFFSET_MINUTES__",
        "__SERVER_NOW_MS__",
        "__UI_ASSET_V__",
    ):
        assert placeholder not in idle_res.text
    for marker in (
        "function escIdleHtml(v)",
        "__cachedSettings",
        "clockEl.textContent !== timeStr",
        "dateEl.textContent !== dateStr",
        "async function refreshUrls()",
        "function setIdleQr(url)",
        "async function weatherForIdle(settings)",
        "function renderWeatherCard(",
    ):
        assert marker in idle_res.text

    overlay_res = client.get("/x11/overlay")
    assert overlay_res.status_code == 200
    for placeholder in (
        "__OVERLAY_DEBUG_BG__",
        "__PLAYBACK_NOTIFY_FADE_MS__",
        "__PLAYBACK_NOTIFY_DISPLAY_SEC__",
        "__OVERLAY_ALLOW_IMAGES__",
        "__OVERLAY_CLIENT_TOKEN__",
        "__UI_ASSET_V__",
    ):
        assert placeholder not in overlay_res.text
    for marker in (
        "X-RelayTV-Overlay-Token",
        "const safeLinkUrl = /^(https?:\\/\\/|\\/)/i.test(rawLinkUrl) && !/^\\/\\//.test(rawLinkUrl)",
        "const safeImageUrl = /^(https?:\\/\\/|\\/|data:image\\/)/i.test(rawImageUrl)",
        "function refreshIdleFrame(force=false)",
        "function overlayPlaybackVisible(state)",
        "RelayTVRealtime.createPolicy",
        "body,.stage{pointer-events:none;}",
    ):
        assert marker in overlay_res.text


def test_qt_shell_app_uses_tv_theme_qss_helpers() -> None:
    from relaytv_app import qt_shell_app

    assert qt_shell_app.tv_theme is tv_theme
    assert "PySide6" not in sys.modules or tv_theme.__name__ in sys.modules

    qt_source = (ROOT_DIR / "app" / "relaytv_app" / "qt_shell_app.py").read_text(encoding="utf-8")
    expected_calls = (
        "tv_theme.qt_toast_frame_qss(",
        "tv_theme.qt_toast_link_qss()",
        "tv_theme.qt_toast_image_qss()",
        "tv_theme.qt_idle_logo_qss()",
        "tv_theme.qt_idle_rail_qss()",
        "tv_theme.qt_idle_clock_qss()",
        "tv_theme.qt_idle_date_qss()",
        "tv_theme.qt_idle_divider_qss()",
        "tv_theme.qt_idle_weather_panel_qss()",
        "tv_theme.qt_idle_weather_location_qss()",
        "tv_theme.qt_idle_weather_temp_qss()",
        "tv_theme.qt_idle_weather_summary_qss()",
        "tv_theme.qt_idle_weather_meta_qss()",
        "tv_theme.qt_idle_forecast_strip_qss()",
        "tv_theme.qt_idle_forecast_col_qss()",
        "tv_theme.qt_idle_forecast_dow_qss()",
        "tv_theme.qt_idle_forecast_summary_qss()",
        "tv_theme.qt_idle_forecast_range_qss()",
        "tv_theme.qt_idle_forecast_meta_qss()",
        "tv_theme.qt_idle_device_footer_qss()",
        "tv_theme.qt_idle_qr_frame_qss()",
        "tv_theme.qt_idle_qr_label_qss()",
        "tv_theme.qt_idle_qr_caption_qss()",
    )
    for call in expected_calls:
        assert call in qt_source, f"Expected qt_shell_app.py to use {call}"

    assert tv_theme.TV_TEXT_PRIMARY in tv_theme.qt_toast_frame_qss("info")
    assert tv_theme.TV_STATUS_SUCCESS in tv_theme.qt_toast_frame_qss("success")
    assert tv_theme.TV_STATUS_WARNING in tv_theme.qt_toast_frame_qss("warn")
    assert tv_theme.TV_STATUS_DANGER in tv_theme.qt_toast_frame_qss("error")
    assert tv_theme.TV_ACCENT_STRONG in tv_theme.qt_toast_link_qss()
    assert tv_theme.TV_TEXT_PRIMARY in tv_theme.qt_idle_clock_qss()
    assert tv_theme.TV_TEXT_SECONDARY in tv_theme.qt_idle_date_qss()
    assert tv_theme.TV_TEXT_TERTIARY in tv_theme.qt_idle_weather_location_qss()
