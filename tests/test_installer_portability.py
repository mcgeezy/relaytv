# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import os
import pwd
import shutil
import stat
import subprocess

import pytest


ROOT_DIR = Path(__file__).resolve().parents[1]


def _copy_host_installer(tmp_path: Path) -> Path:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    installer = scripts_dir / "install.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "install.sh", installer)
    return installer


def _run_host_installer(
    tmp_path: Path,
    args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    installer = _copy_host_installer(tmp_path)
    env = os.environ.copy()
    for key in (
        "DISPLAY",
        "QT_QPA_PLATFORM",
        "RELAYTV_HOST_PROFILE",
        "RELAYTV_INSTALL_MODE",
        "RELAYTV_MODE",
        "RELAYTV_TARGET_HOME",
        "WAYLAND_DISPLAY",
        "XAUTHORITY",
        "XDG_RUNTIME_DIR",
        "XDG_SESSION_TYPE",
    ):
        env.pop(key, None)
    env.update(
        {
            "RELAYTV_TARGET_USER": pwd.getpwuid(os.getuid()).pw_name,
            "RELAYTV_CEC_ENABLED": "0",
            "RELAYTV_PI_VIDEO_DEVICES_ENABLED": "0",
        }
    )
    if env_overrides:
        env.update(env_overrides)
    return subprocess.run(
        [str(installer), *(args or ["--mode", "headless"])],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _resolve_host_ops_delegate_qpa(
    tmp_path: Path,
    *,
    env_file_qpa: str | None = None,
    process_qpa: str | None = None,
) -> subprocess.CompletedProcess[str]:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    host_ops = scripts_dir / "host-ops.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "host-ops.sh", host_ops)
    if env_file_qpa is not None:
        (tmp_path / ".env").write_text(f"QT_QPA_PLATFORM={env_file_qpa}\n", encoding="utf-8")
    env = os.environ.copy()
    env.pop("QT_QPA_PLATFORM", None)
    if process_qpa is not None:
        env["QT_QPA_PLATFORM"] = process_qpa
    return subprocess.run(
        ["bash", "-c", 'source "$1"; resolve_delegate_qpa wayland-native', "host-ops-test", str(host_ops)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def test_wayland_host_ops_preserves_installed_qpa_delegate(tmp_path: Path) -> None:
    result = _resolve_host_ops_delegate_qpa(tmp_path, env_file_qpa="xcb")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "xcb"


def test_wayland_host_ops_allows_explicit_qpa_override(tmp_path: Path) -> None:
    result = _resolve_host_ops_delegate_qpa(tmp_path, env_file_qpa="xcb", process_qpa="wayland")

    assert result.returncode == 0, result.stderr
    assert result.stdout == "wayland"


def test_wayland_host_ops_defaults_to_direct_wayland_without_a_pin(tmp_path: Path) -> None:
    result = _resolve_host_ops_delegate_qpa(tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "wayland"


def _mock_command_path(tmp_path: Path, *, arch: str = "x86_64") -> Path:
    command_dir = tmp_path / "commands"
    command_dir.mkdir()
    for command in ("chmod", "cp", "curl", "grep", "id", "mkdir", "mktemp"):
        source = shutil.which(command)
        assert source is not None
        (command_dir / command).symlink_to(source)

    uname = command_dir / "uname"
    uname.write_text(
        "#!/bin/sh\n"
        "case \"$1\" in\n"
        "  -s) printf 'Linux\\n' ;;\n"
        f"  -m) printf '{arch}\\n' ;;\n"
        f"  *) printf '{arch}\\n' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    uname.chmod(0o755)
    return command_dir


def test_host_installer_preserves_operator_env_and_replaces_owned_values(tmp_path: Path) -> None:
    secret = "unit-test-token-value"
    (tmp_path / ".env").write_text(
        "# operator settings\n"
        f"RELAYTV_API_TOKEN={secret}\n"
        "RELAYTV_PORT=8899\n"
        "RELAYTV_MODE=x11\n"
        "RELAYTV_XAUTHORITY_HOST_PATH=/run/user/1000/.mutter-Xwaylandauth.OLD\n",
        encoding="utf-8",
    )

    result = _run_host_installer(tmp_path)

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert f"RELAYTV_API_TOKEN={secret}" in env_text
    assert "RELAYTV_PORT=8899" in env_text
    assert env_text.count("RELAYTV_MODE=") == 1
    assert "RELAYTV_MODE=headless" in env_text
    assert "RELAYTV_XAUTHORITY_HOST_PATH" not in env_text
    assert secret not in result.stdout
    assert secret not in result.stderr
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600


def test_host_override_uses_only_existing_long_syntax_binds(tmp_path: Path) -> None:
    result = _run_host_installer(tmp_path)

    assert result.returncode == 0, result.stderr
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "create_host_path: false" in override
    assert "/etc/timezone" not in override
    assert "source: \"/sys\"" in override
    assert "/tmp/.X11-unix" not in override
    assert "/run/user/" not in override


def test_raspi_wayland_installer_defaults_to_xcb_with_xauthority(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "wayland-0").touch()
    target_home = tmp_path / "home"
    target_home.mkdir()
    (target_home / ".Xauthority").write_text("cookie", encoding="utf-8")

    result = _run_host_installer(
        tmp_path,
        args=["--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "RELAYTV_HOST_PROFILE": "raspi",
            "RELAYTV_TARGET_HOME": str(target_home),
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_SESSION_TYPE": "wayland",
        },
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "RELAYTV_MODE=wayland" in env_text
    assert "WAYLAND_DISPLAY=wayland-0" in env_text
    assert "QT_QPA_PLATFORM=xcb" in env_text
    assert "XAUTHORITY=/tmp/.Xauthority" in env_text
    assert "RELAYTV_HOST_PROFILE=raspi" in env_text
    assert f'source: "{target_home}/.Xauthority"' in override
    assert 'target: "/tmp/.Xauthority"' in override


def test_raspi_wayland_installer_preserves_explicit_wayland_qpa(tmp_path: Path) -> None:
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    (runtime_dir / "wayland-0").touch()
    target_home = tmp_path / "home"
    target_home.mkdir()
    (target_home / ".Xauthority").write_text("cookie", encoding="utf-8")

    result = _run_host_installer(
        tmp_path,
        args=["--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "QT_QPA_PLATFORM": "wayland",
            "RELAYTV_HOST_PROFILE": "raspi",
            "RELAYTV_TARGET_HOME": str(target_home),
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_SESSION_TYPE": "wayland",
        },
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "RELAYTV_MODE=wayland" in env_text
    assert "QT_QPA_PLATFORM=wayland" in env_text
    assert "XAUTHORITY=/tmp/.Xauthority" not in env_text
    assert ".Xauthority" not in override


def test_host_installer_does_not_overwrite_user_compose_override(tmp_path: Path) -> None:
    custom_override = "services:\n  relaytv:\n    environment:\n      CUSTOM_VALUE: kept\n"
    override_path = tmp_path / "docker-compose.override.yml"
    override_path.write_text(custom_override, encoding="utf-8")

    result = _run_host_installer(tmp_path)

    assert result.returncode != 0
    assert "is user-managed; refusing to overwrite it" in result.stderr
    assert override_path.read_text(encoding="utf-8") == custom_override


def test_generated_override_is_valid_compose(tmp_path: Path) -> None:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is not installed")
    compose = tmp_path / "docker-compose.yml"
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", compose)
    result = _run_host_installer(tmp_path)
    assert result.returncode == 0, result.stderr

    config = subprocess.run(
        ["docker", "compose", "config"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    assert config.returncode == 0, config.stderr
    assert "source: /sys" in config.stdout
    assert "/etc/timezone" not in config.stdout


def test_base_compose_files_do_not_bind_optional_system_paths() -> None:
    for filename in ("docker-compose.yml", "docker-compose.release.yml"):
        compose = (ROOT_DIR / filename).read_text(encoding="utf-8")
        assert "/etc/timezone" not in compose
        assert "/run/udev" not in compose
        assert "/tmp/.X11-unix" not in compose
        assert "RELAYTV_XAUTHORITY_HOST_PATH" not in compose
        assert "XAUTHORITY=/tmp/.Xauthority" not in compose
        assert "- /run/user/${PUID:-1000}:" not in compose


def test_installer_never_pins_session_scoped_xauthority() -> None:
    installer = (ROOT_DIR / "scripts" / "install.sh").read_text(encoding="utf-8")

    assert "append_bind_mount \"$XAUTH_HOST_PATH\"" not in installer
    assert "latest_mutter_xwayland_auth" not in installer
    assert 'append_bind_mount "/run/user" "/run/user" "false"' in installer
    # Keep the legacy name installer-owned so reruns remove it from old .env files.
    assert "RELAYTV_XAUTHORITY_HOST_PATH|" in installer


def test_bootstrap_rejects_unsupported_published_image_architecture(tmp_path: Path) -> None:
    command_dir = _mock_command_path(tmp_path, arch="armv7l")
    env = os.environ.copy()
    env["PATH"] = str(command_dir)

    result = subprocess.run(
        [
            "/bin/bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(tmp_path / "install"),
            "--yes",
            "--no-install-docker",
            "--no-pull",
            "--no-start",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "support only 64-bit amd64 and arm64" in result.stderr


def test_bootstrap_no_install_docker_fails_with_actionable_guidance(tmp_path: Path) -> None:
    command_dir = _mock_command_path(tmp_path)
    env = os.environ.copy()
    env["PATH"] = str(command_dir)

    result = subprocess.run(
        [
            "/bin/bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(tmp_path / "install"),
            "--yes",
            "--no-install-docker",
            "--no-pull",
            "--no-start",
        ],
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Docker is not installed" in result.stderr
    assert "Docker Engine with the Compose plugin" in result.stderr
