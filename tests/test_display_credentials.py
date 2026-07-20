# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

from relaytv_app.display_credentials import refresh_display_credentials, resolve_xauthority


def test_resolve_xauthority_replaces_stale_mutter_session_file(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    stale = runtime_dir / ".mutter-Xwaylandauth.OLD"
    current = runtime_dir / ".mutter-Xwaylandauth.NEW"
    current.write_text("cookie", encoding="utf-8")

    env = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "XAUTHORITY": str(stale),
    }

    assert resolve_xauthority(env) == str(current)
    assert refresh_display_credentials(env)["XAUTHORITY"] == str(current)


def test_resolve_xauthority_uses_valid_explicit_file_without_newer_session_file(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    explicit = tmp_path / ".Xauthority"
    explicit.write_text("explicit", encoding="utf-8")

    env = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": str(runtime_dir),
        "XAUTHORITY": str(explicit),
    }

    assert resolve_xauthority(env) == str(explicit)


def test_refresh_display_credentials_clears_missing_xauthority(tmp_path: Path) -> None:
    env = {
        "DISPLAY": ":0",
        "XDG_RUNTIME_DIR": str(tmp_path / "missing-runtime"),
        "HOME": str(tmp_path / "missing-home"),
        "XAUTHORITY": str(tmp_path / "stale"),
    }

    refreshed = refresh_display_credentials(env)

    assert "XAUTHORITY" not in refreshed
    assert env["XAUTHORITY"].endswith("stale")


def test_refresh_display_credentials_leaves_headless_environment_unchanged() -> None:
    env = {"XAUTHORITY": "/operator/value", "XDG_RUNTIME_DIR": "/run/user/1000"}

    assert refresh_display_credentials(env) == env
