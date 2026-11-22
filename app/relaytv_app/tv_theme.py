# SPDX-License-Identifier: GPL-3.0-only
"""Shared TV presentation visual tokens and Qt stylesheet helpers.

This module keeps browser TV templates (/idle and /x11/overlay) and native Qt
fallback layers (idle dashboard and toast overlay) aligned with foundation.css
without importing Qt/PySide6 at module load time.
"""

from __future__ import annotations

TV_BACKGROUND = "#0b1017"
TV_BACKGROUND_SECONDARY = "#111824"
TV_SURFACE = "#151d28"
TV_SURFACE_RAISED = "#1c2735"
TV_TEXT_PRIMARY = "#f3f6fa"
TV_TEXT_SECONDARY = "#a7b4c5"
TV_TEXT_TERTIARY = "#8999ad"
TV_BORDER = "#344256"
TV_BORDER_SUBTLE = "rgba(167, 180, 197, .18)"
TV_BORDER_STRONG = "rgba(167, 180, 197, .34)"
TV_ACCENT = "#60a5fa"
TV_ACCENT_SOFT = "rgba(96, 165, 250, .16)"
TV_ACCENT_STRONG = "#93c5fd"
TV_STATUS_SUCCESS = "#34d399"
TV_STATUS_WARNING = "#fbbf24"
TV_STATUS_DANGER = "#fb7185"

# QSS-compatible RGBA equivalents (0-255 alpha channel for Qt widget stylesheets)
TV_QSS_SURFACE_TOAST = "rgba(21, 29, 40, 232)"
TV_QSS_SURFACE_FORECAST = "rgba(21, 29, 40, 184)"
TV_QSS_SURFACE_QR = "rgba(21, 29, 40, 235)"
TV_QSS_SURFACE_RAISED = "rgba(28, 39, 53, 196)"
TV_QSS_BORDER_SUBTLE = "rgba(167, 180, 197, 46)"
TV_QSS_BORDER_STRONG = "rgba(167, 180, 197, 87)"
TV_QSS_DIVIDER = "rgba(167, 180, 197, 64)"

TV_PALETTE: dict[str, str] = {
    "--background": TV_BACKGROUND,
    "--background-secondary": TV_BACKGROUND_SECONDARY,
    "--surface": TV_SURFACE,
    "--surface-raised": TV_SURFACE_RAISED,
    "--text-primary": TV_TEXT_PRIMARY,
    "--text-secondary": TV_TEXT_SECONDARY,
    "--text-tertiary": TV_TEXT_TERTIARY,
    "--border": TV_BORDER,
    "--border-subtle": TV_BORDER_SUBTLE,
    "--border-strong": TV_BORDER_STRONG,
    "--accent": TV_ACCENT,
    "--accent-soft": TV_ACCENT_SOFT,
    "--accent-strong": TV_ACCENT_STRONG,
    "--status-success": TV_STATUS_SUCCESS,
    "--status-warning": TV_STATUS_WARNING,
    "--status-danger": TV_STATUS_DANGER,
}


def toast_border_color_for_level(level: str = "info") -> str:
    normalized = str(level or "info").strip().lower()
    if normalized == "success":
        return TV_STATUS_SUCCESS
    if normalized in ("warn", "warning"):
        return TV_STATUS_WARNING
    if normalized in ("err", "error", "danger"):
        return TV_STATUS_DANGER
    return TV_QSS_BORDER_STRONG


def qt_toast_frame_qss(level: str = "info") -> str:
    border_color = toast_border_color_for_level(level)
    return (
        f"QFrame#qt-native-toast{{background:{TV_QSS_SURFACE_TOAST};"
        f"border:1px solid {border_color};border-radius:12px;}}"
        f" QLabel{{color:{TV_TEXT_PRIMARY};font-size:16px;}}"
    )


def qt_toast_link_qss() -> str:
    return f"color:{TV_ACCENT_STRONG};font-size:13px;font-weight:600;"


def qt_toast_image_qss() -> str:
    return (
        f"background:{TV_QSS_SURFACE_RAISED};"
        f"border:1px solid {TV_QSS_BORDER_SUBTLE};border-radius:8px;"
    )


def qt_idle_logo_qss() -> str:
    return f"background:transparent;color:{TV_TEXT_PRIMARY};font-size:24px;font-weight:700;"


def qt_idle_rail_qss() -> str:
    return (
        "QFrame#qt-native-idle-rail{background:transparent;border:none;}"
        f"QLabel{{color:{TV_TEXT_PRIMARY};}}"
    )


def qt_idle_clock_qss() -> str:
    return f"font-size:86px;font-weight:760;letter-spacing:-2px;color:{TV_TEXT_PRIMARY};"


def qt_idle_date_qss() -> str:
    return f"font-size:28px;color:{TV_TEXT_SECONDARY};"


def qt_idle_divider_qss() -> str:
    return f"background:{TV_QSS_DIVIDER};border:none;"


def qt_idle_weather_panel_qss() -> str:
    return "QFrame#qt-native-idle-weather{background:transparent;border:none;}"


def qt_idle_weather_location_qss() -> str:
    return f"font-size:13px;color:{TV_TEXT_TERTIARY};"


def qt_idle_weather_temp_qss() -> str:
    return f"font-size:58px;font-weight:780;letter-spacing:-2px;color:{TV_TEXT_PRIMARY};"


def qt_idle_weather_summary_qss() -> str:
    return f"font-size:18px;color:{TV_TEXT_SECONDARY};"


def qt_idle_weather_meta_qss() -> str:
    return f"font-size:13px;color:{TV_TEXT_SECONDARY};"


def qt_idle_forecast_strip_qss() -> str:
    return (
        f"QFrame#qt-native-idle-forecast{{background:{TV_QSS_SURFACE_FORECAST};"
        f"border:1px solid {TV_QSS_BORDER_SUBTLE};border-radius:12px;}}"
    )


def qt_idle_forecast_col_qss() -> str:
    return "background:transparent;border:none;"


def qt_idle_forecast_dow_qss() -> str:
    return f"font-size:13px;font-weight:700;color:{TV_TEXT_PRIMARY};"


def qt_idle_forecast_summary_qss() -> str:
    return f"font-size:12px;color:{TV_TEXT_SECONDARY};"


def qt_idle_forecast_range_qss() -> str:
    return f"font-size:14px;font-weight:700;color:{TV_TEXT_PRIMARY};"


def qt_idle_forecast_meta_qss() -> str:
    return f"font-size:11px;color:{TV_TEXT_TERTIARY};"


def qt_idle_device_footer_qss() -> str:
    return f"font-size:28px;font-weight:700;color:{TV_TEXT_PRIMARY};"


def qt_idle_qr_frame_qss() -> str:
    return (
        f"QFrame#qt-native-idle-qr-wrap{{background:{TV_QSS_SURFACE_QR};"
        f"border:1px solid {TV_QSS_BORDER_STRONG};border-radius:12px;}}"
    )


def qt_idle_qr_label_qss() -> str:
    return (
        f"background:{TV_QSS_SURFACE_RAISED};"
        f"border:1px solid {TV_QSS_BORDER_SUBTLE};border-radius:8px;"
    )


def qt_idle_qr_caption_qss() -> str:
    return f"font-size:11px;color:{TV_TEXT_SECONDARY};"
