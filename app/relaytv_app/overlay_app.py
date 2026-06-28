# SPDX-License-Identifier: GPL-3.0-only
"""
RelayTV X11 Overlay App (transparent always-on-top browser window).

GTK/WebKitGTK is preferred when installed. Qt WebEngine is used as a fallback
because the RelayTV image already ships the Qt stack for the shell UI.
"""

from __future__ import annotations

import argparse
import os
import platform
import shlex
import sys

from .debug import get_logger


logger = get_logger("overlay")

def _eprint(*a: object) -> None:
    logger.info(" ".join(str(part) for part in a))

def _env_choice(name: str) -> bool | None:
    v = os.getenv(name)
    if v is None:
        return None
    text = v.strip().lower()
    if text in ("1", "true", "yes", "on"):
        return True
    if text in ("0", "false", "no", "off"):
        return False
    return None

def _append_env_flags(name: str, flags: list[str]) -> None:
    current = (os.getenv(name) or "").strip()
    try:
        parts = shlex.split(current) if current else []
    except Exception:
        parts = current.split() if current else []
    changed = False
    for flag in flags:
        if flag not in parts:
            parts.append(flag)
            changed = True
    if changed or not current:
        os.environ[name] = " ".join(parts).strip()

def _qt_overlay_software_mode_enabled() -> bool:
    override = _env_choice("RELAYTV_QT_OVERLAY_SOFTWARE")
    if override is not None:
        return bool(override)
    arch = (platform.machine() or "").strip().lower()
    return arch in ("aarch64", "arm64", "armv7l", "armv6l")

def _run_qt_overlay(url: str, *, click_through: bool) -> int:
    if _qt_overlay_software_mode_enabled():
        _append_env_flags(
            "QTWEBENGINE_CHROMIUM_FLAGS",
            [
                "--disable-gpu",
                "--disable-gpu-compositing",
                "--disable-gpu-rasterization",
                "--disable-zero-copy",
            ],
        )

    try:
        from PySide6.QtCore import Qt, QTimer, QUrl
        from PySide6.QtWidgets import QApplication, QMainWindow
        from PySide6.QtWebEngineCore import QWebEngineProfile, QWebEngineSettings
        from PySide6.QtWebEngineWidgets import QWebEngineView
    except Exception as e:
        _eprint("RelayTV overlay Qt fallback requires PySide6 QtWebEngine.")
        _eprint("Error:", e)
        return 2

    app = QApplication([sys.argv[0]])
    win = QMainWindow()
    win.setWindowTitle("RelayTV Overlay")
    win.setWindowFlags(
        Qt.FramelessWindowHint
        | Qt.WindowStaysOnTopHint
        | Qt.Tool
        | Qt.X11BypassWindowManagerHint
    )
    win.setAttribute(Qt.WA_TranslucentBackground, True)
    win.setAttribute(Qt.WA_ShowWithoutActivating, True)
    if click_through:
        win.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    win.setStyleSheet("background: transparent;")

    view = QWebEngineView(win)
    view.setAttribute(Qt.WA_TranslucentBackground, True)
    if click_through:
        view.setAttribute(Qt.WA_TransparentForMouseEvents, True)
    view.setStyleSheet("background: transparent;")
    view.page().setBackgroundColor(Qt.transparent)
    try:
        profile = view.page().profile()
        profile.setHttpCacheType(QWebEngineProfile.NoCache)
        profile.setPersistentCookiesPolicy(QWebEngineProfile.NoPersistentCookies)
    except Exception:
        pass
    try:
        settings = view.settings()
        settings.setAttribute(QWebEngineSettings.JavascriptEnabled, True)
        settings.setAttribute(QWebEngineSettings.LocalStorageEnabled, False)
        settings.setAttribute(QWebEngineSettings.ErrorPageEnabled, False)
    except Exception:
        pass
    win.setCentralWidget(view)

    def _layout() -> None:
        screen = app.primaryScreen()
        if screen is not None:
            win.setGeometry(screen.geometry())
        view.setGeometry(win.rect())
        win.showFullScreen()
        win.raise_()

    QTimer.singleShot(0, _layout)
    keep_above = QTimer()
    keep_above.setInterval(2000)
    keep_above.timeout.connect(_layout)
    keep_above.start()

    view.load(QUrl(url))
    return int(app.exec())

def _run_gtk_overlay(url: str, *, click_through: bool) -> int:
    try:
        import gi
        gi.require_version("Gtk", "3.0")

        # Prefer WebKitGTK 4.1 (Ubuntu 24.04 / newer Debian). Fall back to 4.0 if needed.
        try:
            gi.require_version("WebKit2", "4.1")
        except ValueError:
            gi.require_version("WebKit2", "4.0")

        from gi.repository import Gtk, WebKit2, GLib, Gdk
    except Exception as e:
        _eprint("GTK/WebKitGTK overlay backend unavailable; trying Qt WebEngine fallback.")
        _eprint("GTK error:", e)
        return _run_qt_overlay(url, click_through=click_through)

    # Must be on X11 for the always-on-top transparent overlay semantics we rely on.
    if os.getenv("XDG_SESSION_TYPE", "").strip().lower() == "wayland":
        _eprint("Wayland session detected; X11 overlay disabled.")
        return 3
    if not os.getenv("DISPLAY"):
        _eprint("No DISPLAY set; X11 overlay disabled.")
        return 3

    win = Gtk.Window(title="RelayTV Overlay")
    win.set_decorated(False)
    win.set_keep_above(True)
    win.set_skip_taskbar_hint(True)
    win.set_skip_pager_hint(True)
    win.set_accept_focus(False)
    win.set_app_paintable(True)

    # Transparency
    screen = win.get_screen()
    rgba = screen.get_rgba_visual()
    if rgba is not None:
        win.set_visual(rgba)

    def _on_draw(_w, cr):
        cr.set_source_rgba(0, 0, 0, 0)
        cr.set_operator(0)  # CLEAR
        cr.paint()
        return False

    win.connect("draw", _on_draw)

    # WebView
    view = WebKit2.WebView()
    settings = view.get_settings()
    settings.set_property("enable-webgl", False)
    settings.set_property("enable-plugins", False)
    settings.set_property("enable-write-console-messages-to-stdout", False)
    # Ensure a transparent page background if supported.
    try:
        view.set_background_color(Gdk.RGBA(0, 0, 0, 0))
    except Exception:
        pass

    win.add(view)

    def _go_fullscreen():
        try:
            win.fullscreen()
        except Exception:
            pass
        return False

    GLib.idle_add(_go_fullscreen)

    def _on_key(_w, ev):
        # Escape closes overlay.
        try:
            keyval = ev.keyval
        except Exception:
            return False
        if keyval == Gdk.KEY_Escape:
            Gtk.main_quit()
            return True
        return False

    win.connect("key-press-event", _on_key)

    # Optional click-through: make the window input-transparent to mouse events.
    # This only works on X11 and requires an X11 window to exist (after realize).
    def _enable_click_through():
        if not click_through:
            return
        try:
            gdk_win = win.get_window()
            if not gdk_win:
                return
            # Empty input shape region -> all input passes through.
            region = Gdk.Region()  # empty
            gdk_win.input_shape_combine_region(region, 0, 0)
        except Exception:
            pass

    win.connect("realize", lambda *_: _enable_click_through())

    # Close behavior
    win.connect("destroy", lambda *_: Gtk.main_quit())

    view.load_uri(url)

    win.show_all()
    Gtk.main()
    return 0

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    ap = argparse.ArgumentParser(description="RelayTV X11 Overlay (WebKitGTK)")
    ap.add_argument("--url", default=os.getenv("RELAYTV_OVERLAY_URL", "http://127.0.0.1:8787/x11/overlay"))
    click_through_default = os.getenv("RELAYTV_OVERLAY_CLICKTHROUGH", "1").strip().lower() in ("1","true","yes","on")
    ap.add_argument("--click-through", action="store_true", default=click_through_default)
    args = ap.parse_args(argv)

    return _run_gtk_overlay(args.url, click_through=args.click_through)


if __name__ == "__main__":
    raise SystemExit(main())
