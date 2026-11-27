# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import os
import pwd
import shutil
import shlex
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
    host_model: str | None = None,
) -> subprocess.CompletedProcess[str]:
    installer = _copy_host_installer(tmp_path)
    if host_model is not None:
        # Substitute only the hardware probe result; run the real installer and
        # its generation detection/default/persistence paths unchanged.
        text = installer.read_text()
        text = text.replace(
            "\ndetect_host_profile() {",
            f"\nHOST_MODEL={shlex.quote(host_model)}\ndetect_host_profile() {{",
            1,
        )
        installer.write_text(text)
    env = os.environ.copy()
    for key in (
        "DISPLAY",
        "MPV_AUDIO_DEVICE",
        "PORT",
        "QT_QPA_PLATFORM",
        "RELAYTV_ARM_DEFAULT_QUALITY",
        "RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT",
        "RELAYTV_ARM_FAST_PROFILE",
        "RELAYTV_CEC",
        "RELAYTV_CEC_ENABLED",
        "RELAYTV_CEC_MONITOR",
        "RELAYTV_DISPLAY_CAP_HEIGHT",
        "RELAYTV_HEADLESS_REMOTE_DISPLAY",
        "RELAYTV_HEADLESS_REMOTE_ENABLED",
        "RELAYTV_HEADLESS_REMOTE_RESOLUTION",
        "RELAYTV_HEADLESS_REMOTE_SOFTWARE",
        "RELAYTV_HEADLESS_VNC_ENABLED",
        "RELAYTV_HEADLESS_VNC_LISTEN",
        "RELAYTV_HEADLESS_VNC_PASSWORD_FILE",
        "RELAYTV_HEADLESS_VNC_PORT",
        "RELAYTV_HOST_PROFILE",
        "RELAYTV_IMAGE_REF",
        "RELAYTV_INSTALL_HEADLESS",
        "RELAYTV_INSTALL_IDLE_BROWSER",
        "RELAYTV_INSTALL_MODE",
        "RELAYTV_INSTALL_OPS_TOOLS",
        "RELAYTV_INSTALL_X11_OVERLAY",
        "RELAYTV_INSTALL_YES",
        "RELAYTV_MODE",
        "RELAYTV_PI_GENERATION",
        "RELAYTV_PLAYER_BACKEND",
        "RELAYTV_PORT",
        "RELAYTV_QT_RUNTIME_MODE",
        "RELAYTV_QT_SHELL_MODULE",
        "RELAYTV_QT_SHELL_MPV_ARGS",
        "RELAYTV_TARGET_HOME",
        "RELAYTV_X11_OVERLAY",
        "RELAYTV_YTDLP_AUTO_UPDATE",
        "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS",
        "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE",
        "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC",
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
        [
            "bash",
            "-c",
            'source "$1"; resolve_delegate_qpa wayland-native',
            "host-ops-test",
            str(host_ops),
        ],
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
        'case "$1" in\n'
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
    assert 'source: "/sys"' in override
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

    assert 'append_bind_mount "$XAUTH_HOST_PATH"' not in installer
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


@pytest.mark.parametrize(
    ("model", "generation"),
    [
        ("Raspberry Pi 4 Model B Rev 1.5", "4"),
        ("Raspberry Pi 3 Model B Rev 1.4", "3"),
        ("Raspberry Pi 5 Model B Rev 1.0", "5"),
        ("Raspberry Pi Compute Module 4 Rev 1.5", "4"),
        ("Raspberry Pi 400 Rev 1.5", "4"),
        ("Raspberry Pi Zero 2 W Rev 1.5", "3"),
    ],
)
def test_installer_pi_generation_ignores_revision(
    tmp_path: Path, model: str, generation: str
) -> None:
    result = _run_host_installer(tmp_path, host_model=model)
    assert result.returncode == 0, result.stderr
    env = (tmp_path / ".env").read_text()
    assert f"RELAYTV_PI_GENERATION={generation}\n" in env
    assert "RELAYTV_ARM_FAST_PROFILE=1\n" in env
    assert "RELAYTV_ARM_DEFAULT_QUALITY=1080\n" in env
    if generation == "5":
        assert "RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT=0\n" in env
        assert "RELAYTV_DISPLAY_CAP_HEIGHT=" not in env
    else:
        assert "RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT=1\n" in env
        assert "RELAYTV_DISPLAY_CAP_HEIGHT=1080\n" in env


@pytest.mark.parametrize("profile", ["raspi", "amd64", "arm"])
@pytest.mark.parametrize("shell_override", [False, True])
def test_installer_preserves_playback_overrides(
    tmp_path: Path, profile: str, shell_override: bool
) -> None:
    saved = {
        "RELAYTV_ARM_FAST_PROFILE": "0",
        "RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT": "1",
        "RELAYTV_ARM_DEFAULT_QUALITY": "720",
        "RELAYTV_DISPLAY_CAP_HEIGHT": "720",
    }
    # Preserve raw dotenv quoting and comments; never evaluate shell text.
    saved_lines = [f'export {key}="{value}" # operator override' for key, value in saved.items()]
    (tmp_path / ".env").write_text("\n".join(saved_lines) + "\n")
    overrides = {"RELAYTV_HOST_PROFILE": profile}
    if shell_override:
        overrides.update(
            {key: "0" if "PROFILE" in key or "FORMAT" in key else "480" for key in saved}
        )
    result = _run_host_installer(
        tmp_path, env_overrides=overrides, host_model="Raspberry Pi 5 Model B Rev 1.0"
    )
    assert result.returncode == 0, result.stderr
    env = (tmp_path / ".env").read_text()
    for key, original_line in zip(saved, saved_lines):
        assert env.count(f"{key}=") == 1
        assert (f"{key}={overrides[key]}\n" if shell_override else original_line + "\n") in env
    # A second installation must retain the same choices.
    shutil.rmtree(tmp_path / "scripts")
    result = _run_host_installer(tmp_path, env_overrides={"RELAYTV_HOST_PROFILE": profile})
    assert result.returncode == 0, result.stderr
    for key in saved:
        before = next(line for line in env.splitlines() if f"{key}=" in line)
        assert before in (tmp_path / ".env").read_text().splitlines()


@pytest.mark.parametrize(
    ("saved", "shell", "expected"),
    [
        (None, "5", "5"),
        ('"5" # force generation', None, "5"),
        ("'5'", "3", "3"),
        (None, "4", "4"),
        ('""', None, "4"),
        (None, "", "4"),
    ],
)
def test_installer_pi_generation_override_precedes_detection(
    tmp_path: Path, saved: str | None, shell: str | None, expected: str
) -> None:
    if saved is not None:
        (tmp_path / ".env").write_text(f"RELAYTV_PI_GENERATION={saved}\n")
    overrides = {"RELAYTV_HOST_PROFILE": "raspi"}
    if shell is not None:
        overrides["RELAYTV_PI_GENERATION"] = shell
    result = _run_host_installer(
        tmp_path, env_overrides=overrides, host_model="Raspberry Pi 4 Model B Rev 1.4"
    )
    assert result.returncode == 0, result.stderr
    env = (tmp_path / ".env").read_text()
    assert env.count("RELAYTV_PI_GENERATION=") == 1
    assert f"RELAYTV_PI_GENERATION={shell if shell is not None else saved}\n" in env
    assert f"RELAYTV_ARM_ENFORCE_SAFE_YTDL_FORMAT={'0' if expected == '5' else '1'}\n" in env
    assert ("RELAYTV_DISPLAY_CAP_HEIGHT=1080\n" in env) == (expected != "5")


@pytest.mark.parametrize("source", ["shell", "file"])
def test_installer_rejects_invalid_pi_generation_without_evaluating_it(
    tmp_path: Path, source: str
) -> None:
    malicious = f"$(touch {tmp_path / 'should-not-exist'})"
    overrides = {"RELAYTV_HOST_PROFILE": "raspi"}
    original = "RELAYTV_DISPLAY_CAP_HEIGHT=720\n"
    if source == "shell":
        overrides["RELAYTV_PI_GENERATION"] = malicious
    else:
        original += f"RELAYTV_PI_GENERATION={malicious}\n"
    (tmp_path / ".env").write_text(original)
    result = _run_host_installer(tmp_path, env_overrides=overrides)
    assert result.returncode == 2
    assert "RELAYTV_PI_GENERATION" in result.stderr
    assert not (tmp_path / "should-not-exist").exists()
    assert (tmp_path / ".env").read_text() == original


def test_release_compose_headless_install_disables_xvfb_remote_by_default(tmp_path: Path) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")

    result = _run_host_installer(tmp_path, args=["--mode", "headless"])

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_MODE=headless\n" in env_text
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=0\n" in env_text
    assert "RELAYTV_PLAYER_BACKEND=mpv\n" in env_text
    assert "RELAYTV_INSTALL_HEADLESS=1" not in env_text


def test_source_compose_headless_honors_explicit_remote_disable(tmp_path: Path) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.yml", tmp_path / "docker-compose.yml")

    result = _run_host_installer(
        tmp_path,
        args=["--mode", "headless"],
        env_overrides={"RELAYTV_HEADLESS_REMOTE_ENABLED": "0"},
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    # Must explicitly write =0 because container_entrypoint defaults unset to =1.
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=0\n" in env_text
    assert "RELAYTV_PLAYER_BACKEND=mpv\n" in env_text


def test_host_installer_preserves_documented_operator_settings_across_reruns(
    tmp_path: Path,
) -> None:
    (tmp_path / ".env").write_text(
        'export MPV_AUDIO_DEVICE="alsa/hdmi:CARD=PCH,DEV=0" # pinned sink\n'
        "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:v0.12.2\n"
        "RELAYTV_YTDLP_AUTO_UPDATE=1\n"
        "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS=24\n"
        "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC=300\n"
        "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE=/data/custom-ytdlp.json\n"
        "RELAYTV_CEC_ENABLED=1\n"
        "RELAYTV_CEC=1\n"
        "RELAYTV_CEC_MONITOR=0\n"
        "RELAYTV_QT_RUNTIME_MODE=external_mpv\n"
        'RELAYTV_QT_SHELL_MPV_ARGS="--gpu-api=opengl"\n'
        "RELAYTV_HEADLESS_VNC_PORT=5901\n"
        "RELAYTV_PORT=8899\n",
        encoding="utf-8",
    )

    installer = _copy_host_installer(tmp_path)
    env = os.environ.copy()
    for key in (
        "MPV_AUDIO_DEVICE",
        "RELAYTV_IMAGE_REF",
        "RELAYTV_YTDLP_AUTO_UPDATE",
        "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS",
        "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC",
        "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE",
        "RELAYTV_CEC_ENABLED",
        "RELAYTV_CEC",
        "RELAYTV_CEC_MONITOR",
        "RELAYTV_QT_RUNTIME_MODE",
        "RELAYTV_QT_SHELL_MPV_ARGS",
        "RELAYTV_HEADLESS_VNC_PORT",
        "RELAYTV_PORT",
    ):
        env.pop(key, None)
    env.update(
        {
            "RELAYTV_TARGET_USER": pwd.getpwuid(os.getuid()).pw_name,
            "RELAYTV_PI_VIDEO_DEVICES_ENABLED": "0",
        }
    )

    result = subprocess.run(
        [str(installer), "--mode", "headless"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert 'export MPV_AUDIO_DEVICE="alsa/hdmi:CARD=PCH,DEV=0" # pinned sink\n' in env_text
    assert "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:v0.12.2\n" in env_text
    assert "RELAYTV_YTDLP_AUTO_UPDATE=1\n" in env_text
    assert "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS=24\n" in env_text
    assert "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC=300\n" in env_text
    assert "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE=/data/custom-ytdlp.json\n" in env_text
    assert "RELAYTV_CEC_ENABLED=1\n" in env_text
    assert "RELAYTV_CEC=1\n" in env_text
    assert "RELAYTV_CEC_MONITOR=0\n" in env_text
    assert "RELAYTV_QT_RUNTIME_MODE=external_mpv\n" in env_text
    assert 'RELAYTV_QT_SHELL_MPV_ARGS="--gpu-api=opengl"\n' in env_text
    assert "RELAYTV_HEADLESS_VNC_PORT=5901\n" in env_text
    assert "RELAYTV_PORT=8899\n" in env_text
    assert env_text.count("RELAYTV_PORT=") == 1
    assert env_text.count("MPV_AUDIO_DEVICE=") == 1


def test_non_tty_detached_sessions_do_not_crash_on_dev_tty(tmp_path: Path) -> None:
    if shutil.which("setsid") is None:
        pytest.skip("setsid is unavailable")

    # 1. Root bootstrap without --dir/--yes in a session with no controlling TTY.
    boot_dir = tmp_path / "boot-cwd"
    boot_dir.mkdir()
    boot = subprocess.run(
        ["setsid", "/bin/bash", str(ROOT_DIR / "install.sh")],
        cwd=boot_dir,
        stdin=subprocess.DEVNULL,
        check=False,
        capture_output=True,
        text=True,
    )
    assert boot.returncode != 0
    assert "No such device or address" not in boot.stderr
    assert "RelayTV installs into the current directory by default" in boot.stderr

    # 2. scripts/install.sh with detected CEC hardware and RELAYTV_CEC_ENABLED=auto
    # in a session with no controlling TTY must default cleanly to 0 instead of ENXIO.
    host_dir = tmp_path / "host-cec"
    host_dir.mkdir()
    installer = _copy_host_installer(host_dir)
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    cec_client = bin_dir / "cec-client"
    cec_client.write_text("#!/bin/sh\nprintf 'com port: /dev/ttyACM0\\n'\n", encoding="utf-8")
    cec_client.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env.get('PATH', '')}"
    env["RELAYTV_TARGET_USER"] = pwd.getpwuid(os.getuid()).pw_name
    env["RELAYTV_CEC_ENABLED"] = "auto"
    env["RELAYTV_PI_VIDEO_DEVICES_ENABLED"] = "0"
    env.pop("RELAYTV_INSTALL_YES", None)

    host_res = subprocess.run(
        ["setsid", str(installer), "--mode", "headless"],
        cwd=host_dir,
        stdin=subprocess.DEVNULL,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert host_res.returncode == 0, host_res.stderr
    assert "No such device or address" not in host_res.stderr
    assert (host_dir / ".env").is_file()


def test_x11_mode_leaves_legacy_overlay_unset_unless_explicit(tmp_path: Path) -> None:
    result = _run_host_installer(
        tmp_path,
        args=["--mode", "x11", "--use-shell-env"],
        env_overrides={"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"},
    )
    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_MODE=x11\n" in env_text
    assert "RELAYTV_X11_OVERLAY=" not in env_text
    assert "RELAYTV_INSTALL_X11_OVERLAY=" not in env_text

    shutil.rmtree(tmp_path / "scripts")
    explicit = _run_host_installer(
        tmp_path,
        args=["--mode", "x11", "--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "XDG_SESSION_TYPE": "x11",
            "RELAYTV_X11_OVERLAY": "1",
        },
    )
    assert explicit.returncode == 0, explicit.stderr
    explicit_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_X11_OVERLAY=1\n" in explicit_env
    assert "RELAYTV_INSTALL_X11_OVERLAY=1\n" in explicit_env


def test_doctor_redacts_secrets_and_respects_configured_port(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    doctor = scripts_dir / "doctor.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "doctor.sh", doctor)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/bin/sh\nprintf '200'\n", encoding="utf-8")
    fake_curl.chmod(0o755)
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o755)

    api_secret = "token-secret-xyz987"
    jf_pass = "jellyfin-pass-abc123"
    jf_key = "jellyfin-key-def456"
    seerr_key = "seerr-key-ghi789"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"RELAYTV_API_TOKEN={api_secret}\n"
        f"export RELAYTV_JELLYFIN_PASSWORD='{jf_pass}'\n"
        f'RELAYTV_JELLYFIN_API_KEY="{jf_key}"\n'
        f"RELAYTV_SEERR_API_KEY={seerr_key}\n"
        "RELAYTV_JELLYFIN_AUTH_ENABLED=1\n"
        "RELAYTV_HEADLESS_VNC_PASSWORD_FILE=/data/vnc.pass\n"
        'export RELAYTV_PORT="8899" # custom port\n',
        encoding="utf-8",
    )
    env_file.chmod(0o644)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env.pop("RELAYTV_BASE", None)
    env.pop("RELAYTV_PORT", None)

    result = subprocess.run(
        [str(doctor)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    combined = result.stdout + "\n" + result.stderr
    for secret in (api_secret, jf_pass, jf_key, seerr_key):
        assert secret not in combined
    assert "RELAYTV_API_TOKEN=<redacted>" in result.stdout
    assert "export RELAYTV_JELLYFIN_PASSWORD=<redacted>" in result.stdout
    assert "RELAYTV_JELLYFIN_API_KEY=<redacted>" in result.stdout
    assert "RELAYTV_SEERR_API_KEY=<redacted>" in result.stdout
    assert "RELAYTV_JELLYFIN_AUTH_ENABLED=1" in result.stdout
    assert "RELAYTV_HEADLESS_VNC_PASSWORD_FILE=/data/vnc.pass" in result.stdout
    assert "WARN: .env mode is 644 (expected 600)" in result.stdout
    assert "http://127.0.0.1:8899/health" in result.stdout


def test_wayland_socket_discovery_ignores_stale_lock_files(tmp_path: Path) -> None:
    import socket
    import tempfile

    with tempfile.TemporaryDirectory(dir="/tmp") as short_dir:
        runtime_dir = Path(short_dir)
        (runtime_dir / "wayland-0.lock").write_text("stale", encoding="utf-8")
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.bind(str(runtime_dir / "wayland-1"))
        try:
            result = _run_host_installer(
                tmp_path,
                args=["--mode", "wayland", "--use-shell-env"],
                env_overrides={
                    "XDG_RUNTIME_DIR": str(runtime_dir),
                    "XDG_SESSION_TYPE": "wayland",
                },
            )
            assert result.returncode == 0, result.stderr
            env_text = (tmp_path / ".env").read_text(encoding="utf-8")
            assert "WAYLAND_DISPLAY=wayland-1\n" in env_text
            assert "wayland-0.lock" not in env_text

            host_ops = tmp_path / "scripts" / "host-ops.sh"
            shutil.copy2(ROOT_DIR / "scripts" / "host-ops.sh", host_ops)
            ops_env = os.environ.copy()
            ops_env.pop("WAYLAND_DISPLAY", None)
            ops_res = subprocess.run(
                [
                    "bash",
                    "-c",
                    'source "$1"; resolve_wayland_display_name "$2"',
                    "host-ops-wayland-test",
                    str(host_ops),
                    str(runtime_dir),
                ],
                cwd=tmp_path,
                env=ops_env,
                check=False,
                capture_output=True,
                text=True,
            )
            assert ops_res.returncode == 0, ops_res.stderr
            assert ops_res.stdout == "wayland-1"
        finally:
            sock.close()


def test_prelogin_desktop_install_preserves_run_user_mount(tmp_path: Path) -> None:
    if not Path("/run/user").is_dir():
        pytest.skip("/run/user is not present on this host")

    result = _run_host_installer(
        tmp_path,
        args=["--mode", "wayland", "--use-shell-env"],
        env_overrides={
            "XDG_RUNTIME_DIR": "/run/user/999999",
            "XDG_SESSION_TYPE": "wayland",
        },
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "XDG_RUNTIME_DIR=/tmp" not in env_text
    assert 'source: "/run/user"' in override


def test_explicit_headless_mode_does_not_leak_host_display_or_x11_mount(tmp_path: Path) -> None:
    result = _run_host_installer(
        tmp_path,
        args=["--mode", "headless", "--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_SESSION_TYPE": "wayland",
        },
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")
    assert "DISPLAY=" not in env_text
    assert "WAYLAND_DISPLAY=" not in env_text
    assert "/tmp/.X11-unix" not in override


def test_feature_branch_checkout_defaults_image_ref_to_latest(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "checkout", "-q", "-b", "fix/custom-feature"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    (tmp_path / "README").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=tmp_path, check=True)

    result = _run_host_installer(tmp_path)
    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_IMAGE_REF=" not in env_text

    subprocess.run(["git", "tag", "v0.12.2"], cwd=tmp_path, check=True)
    shutil.rmtree(tmp_path / "scripts")
    tagged = _run_host_installer(tmp_path)
    assert tagged.returncode == 0, tagged.stderr
    tagged_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:v0.12.2\n" in tagged_env


def test_bootstrap_refuses_implicit_home_install_with_yes(tmp_path: Path) -> None:
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env.pop("RELAYTV_INSTALL_DIR", None)

    result = subprocess.run(
        ["/bin/bash", str(ROOT_DIR / "install.sh"), "--yes", "--no-pull", "--no-start"],
        cwd=fake_home,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode != 0
    assert "Refusing to install directly into your home directory" in result.stderr


def test_env_defaults_references_current_qt_shell_and_session_vars() -> None:
    defaults = (ROOT_DIR / ".env.defaults").read_text(encoding="utf-8")
    assert "RELAYTV_QT_SHELL_MODULE=relaytv_app.qt_shell_app" in defaults
    assert "shell_v2_app" not in defaults
    assert "RELAYTV_SHELL_V2_" not in defaults
    assert "RELAYTV_XDG_SESSION_TYPE" not in defaults
    assert "RELAYTV_HOST_SESSION_TYPE=x11" in defaults


@pytest.mark.parametrize("enabled", ["0", "1"])
@pytest.mark.parametrize("individual_override", [False, True])
def test_explicit_cec_choice_replaces_saved_flags(
    tmp_path: Path,
    enabled: str,
    individual_override: bool,
) -> None:
    previous = "1" if enabled == "0" else "0"
    (tmp_path / ".env").write_text(
        f"RELAYTV_CEC_ENABLED={previous}\nRELAYTV_CEC={previous}\nRELAYTV_CEC_MONITOR={previous}\n",
        encoding="utf-8",
    )
    overrides = {"RELAYTV_CEC_ENABLED": enabled}
    if individual_override:
        overrides.update(RELAYTV_CEC=previous, RELAYTV_CEC_MONITOR=previous)

    result = _run_host_installer(tmp_path, env_overrides=overrides)

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text()
    expected = previous if individual_override else enabled
    assert f"RELAYTV_CEC_ENABLED={enabled}\n" in env_text
    assert f"RELAYTV_CEC={expected}\n" in env_text
    assert f"RELAYTV_CEC_MONITOR={expected}\n" in env_text


@pytest.mark.parametrize("mode", ["x11", "wayland", "headless"])
@pytest.mark.parametrize("explicit_remote", [False, True])
def test_headless_rerun_recomputes_remote_default_when_switching_modes(
    tmp_path: Path,
    mode: str,
    explicit_remote: bool,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.yml", tmp_path / "docker-compose.yml")
    first = _run_host_installer(tmp_path, args=["--mode", "headless"])
    assert first.returncode == 0, first.stderr
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=1\n" in (tmp_path / ".env").read_text()
    shutil.rmtree(tmp_path / "scripts")
    overrides = {"DISPLAY": ":0", "XDG_SESSION_TYPE": mode}
    if explicit_remote:
        overrides["RELAYTV_HEADLESS_REMOTE_ENABLED"] = "1"

    result = _run_host_installer(
        tmp_path,
        args=["--mode", mode, "--use-shell-env"],
        env_overrides=overrides,
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text()
    assert f"RELAYTV_MODE={mode}\n" in env_text
    if explicit_remote or mode == "headless":
        assert "RELAYTV_HEADLESS_REMOTE_ENABLED=1\n" in env_text
    else:
        assert "RELAYTV_HEADLESS_REMOTE_ENABLED=1\n" not in env_text
        assert "RELAYTV_INSTALL_HEADLESS=1\n" not in env_text


@pytest.mark.parametrize(
    ("choice", "saved", "expected", "monitor"),
    [(None, "1", "1", "0"), ("--enable-cec", "0", "1", "1"), ("--no-cec", "1", "0", "0")],
)
def test_bootstrap_distinguishes_saved_cec_from_explicit_choice(
    tmp_path: Path,
    choice: str | None,
    saved: str,
    expected: str,
    monitor: str,
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / ".env").write_text(
        f"RELAYTV_CEC_ENABLED={saved}\nRELAYTV_CEC={saved}\nRELAYTV_CEC_MONITOR=0\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name, content in {
        "docker": "#!/bin/sh\nexit 0\n",
        "curl": (
            "#!/bin/bash\n"
            'url="${@: -1}"\n'
            f'cat {shlex.quote(str(ROOT_DIR))}/"${{url#https://review.invalid/}}"\n'
        ),
    }.items():
        command = fake_bin / name
        command.write_text(content)
        command.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
    env.update(
        PATH=f"{fake_bin}:{env.get('PATH', '')}",
        RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
        RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
        RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
    )
    result = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(install_dir),
            "--yes",
            "--no-pull",
            "--no-start",
            "--mode",
            "headless",
            *([choice] if choice else []),
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    env_text = (install_dir / ".env").read_text()
    assert f"RELAYTV_CEC_ENABLED={expected}\n" in env_text
    assert f"RELAYTV_CEC={expected}\n" in env_text
    assert f"RELAYTV_CEC_MONITOR={monitor}\n" in env_text


def test_api_only_headless_does_not_poison_vnc_enabled_on_later_remote_upgrade(
    tmp_path: Path,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")
    first = _run_host_installer(tmp_path, args=["--mode", "headless"])
    assert first.returncode == 0, first.stderr
    first_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=0\n" in first_env
    assert "RELAYTV_HEADLESS_VNC_ENABLED=0" not in first_env

    shutil.rmtree(tmp_path / "scripts")
    shutil.copy2(ROOT_DIR / "docker-compose.yml", tmp_path / "docker-compose.yml")
    second = _run_host_installer(
        tmp_path,
        args=["--mode", "headless"],
        env_overrides={"RELAYTV_HEADLESS_REMOTE_ENABLED": "1"},
    )
    assert second.returncode == 0, second.stderr
    second_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=1\n" in second_env
    assert "RELAYTV_HEADLESS_VNC_ENABLED=0" not in second_env


def test_interactive_cec_auto_syncs_dependent_flags_and_persists_decline(
    tmp_path: Path,
) -> None:
    import fcntl
    import pty
    import select
    import termios

    def run_with_pty(work_dir: Path, answer: bytes, extra_env: dict[str, str]) -> tuple[int, str]:
        installer = _copy_host_installer(work_dir)
        fake_bin = work_dir / "cec-bin"
        fake_bin.mkdir(exist_ok=True)
        cec_client = fake_bin / "cec-client"
        cec_client.write_text("#!/bin/sh\nprintf 'com port: /dev/ttyACM0\\n'\n", encoding="utf-8")
        cec_client.chmod(0o755)

        env = os.environ.copy()
        for key in (
            "RELAYTV_CEC",
            "RELAYTV_CEC_ENABLED",
            "RELAYTV_CEC_MONITOR",
            "RELAYTV_INSTALL_YES",
        ):
            env.pop(key, None)
        env.update(
            {
                "PATH": f"{fake_bin}:{env.get('PATH', '')}",
                "RELAYTV_TARGET_USER": pwd.getpwuid(os.getuid()).pw_name,
                "RELAYTV_PI_VIDEO_DEVICES_ENABLED": "0",
                **extra_env,
            }
        )

        master_fd, slave_fd = pty.openpty()
        try:
            if answer:
                os.write(master_fd, answer)

            def attach_ctty() -> None:
                os.setsid()
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

            proc = subprocess.run(
                [str(installer), "--mode", "headless"],
                cwd=work_dir,
                env=env,
                stdin=slave_fd,
                capture_output=True,
                text=True,
                preexec_fn=attach_ctty,
                timeout=10,
                check=False,
            )
            os.close(slave_fd)
            slave_fd = -1
            pty_chunks: list[bytes] = []
            while select.select([master_fd], [], [], 0.05)[0]:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                pty_chunks.append(data)
            return proc.returncode, b"".join(pty_chunks).decode("utf-8", errors="replace")
        finally:
            if slave_fd >= 0:
                os.close(slave_fd)
            os.close(master_fd)

    # 1. Explicit RELAYTV_CEC_ENABLED=auto answered "y" updates saved 0 flags to 1.
    auto_dir = tmp_path / "auto-yes"
    auto_dir.mkdir()
    (auto_dir / ".env").write_text(
        "RELAYTV_CEC_ENABLED=0\nRELAYTV_CEC=0\nRELAYTV_CEC_MONITOR=0\n",
        encoding="utf-8",
    )
    rc, pty_out = run_with_pty(auto_dir, b"y\n", {"RELAYTV_CEC_ENABLED": "auto"})
    assert rc == 0
    assert "Enable HDMI-CEC passthrough?" in pty_out
    auto_env = (auto_dir / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_CEC_ENABLED=1\n" in auto_env
    assert "RELAYTV_CEC=1\n" in auto_env
    assert "RELAYTV_CEC_MONITOR=1\n" in auto_env

    # 2. Fresh interactive decline ("n") persists RELAYTV_CEC_ENABLED=0 so reruns do not re-prompt.
    decline_dir = tmp_path / "fresh-no"
    decline_dir.mkdir()
    rc1, pty_out1 = run_with_pty(decline_dir, b"n\n", {})
    assert rc1 == 0
    assert "Enable HDMI-CEC passthrough?" in pty_out1
    decline_env = (decline_dir / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_CEC_ENABLED=0\n" in decline_env

    shutil.rmtree(decline_dir / "scripts")
    rc2, pty_out2 = run_with_pty(decline_dir, b"", {})
    assert rc2 == 0
    assert "Enable HDMI-CEC passthrough?" not in pty_out2


def test_bootstrap_branch_main_unpins_saved_tag_and_empty_cec_env_preserves_saved(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / ".env").write_text(
        "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:v0.12.1\n"
        "RELAYTV_CEC_ENABLED=1\n"
        "RELAYTV_CEC=1\n"
        "RELAYTV_CEC_MONITOR=0\n",
        encoding="utf-8",
    )
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    for name, content in {
        "docker": "#!/bin/sh\nexit 0\n",
        "curl": (
            "#!/bin/bash\n"
            'url="${@: -1}"\n'
            f'cat {shlex.quote(str(ROOT_DIR))}/"${{url#https://review.invalid/}}"\n'
        ),
    }.items():
        command = fake_bin / name
        command.write_text(content, encoding="utf-8")
        command.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
    env.update(
        PATH=f"{fake_bin}:{env.get('PATH', '')}",
        RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
        RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
        RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
        RELAYTV_CEC_ENABLED="",
    )

    result = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(install_dir),
            "--branch",
            "main",
            "--yes",
            "--no-pull",
            "--no-start",
            "--mode",
            "headless",
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    env_text = (install_dir / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_IMAGE_REF=" not in env_text
    assert "RELAYTV_CEC_ENABLED=1\n" in env_text
    assert "RELAYTV_CEC=1\n" in env_text
    assert "RELAYTV_CEC_MONITOR=0\n" in env_text


def test_headless_mode_switches_preserve_vnc_tunables_and_disable_xvfb_on_release_compose(
    tmp_path: Path,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.yml", tmp_path / "docker-compose.yml")
    first = _run_host_installer(
        tmp_path,
        args=["--mode", "headless"],
        env_overrides={
            "RELAYTV_HEADLESS_VNC_PORT": "5901",
            "RELAYTV_HEADLESS_VNC_PASSWORD_FILE": "/data/vnc.pass",
        },
    )
    assert first.returncode == 0, first.stderr

    # Switch to x11: remote Xvfb disables, but operator VNC tunables stay saved.
    shutil.rmtree(tmp_path / "scripts")
    to_x11 = _run_host_installer(
        tmp_path,
        args=["--mode", "x11", "--use-shell-env"],
        env_overrides={"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11"},
    )
    assert to_x11.returncode == 0, to_x11.stderr
    x11_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=1" not in x11_env
    assert "RELAYTV_HEADLESS_VNC_PORT=5901\n" in x11_env
    assert "RELAYTV_HEADLESS_VNC_PASSWORD_FILE=/data/vnc.pass\n" in x11_env

    # Switch back to source headless, then onto release-only compose without Xvfb.
    shutil.rmtree(tmp_path / "scripts")
    back_source = _run_host_installer(tmp_path, args=["--mode", "headless"])
    assert back_source.returncode == 0, back_source.stderr
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=1\n" in (tmp_path / ".env").read_text(encoding="utf-8")

    shutil.rmtree(tmp_path / "scripts")
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")
    to_release = _run_host_installer(tmp_path, args=["--mode", "headless"])
    assert to_release.returncode == 0, to_release.stderr
    release_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=0\n" in release_env
    assert "RELAYTV_HEADLESS_VNC_PORT=5901\n" in release_env
    assert "RELAYTV_HEADLESS_VNC_PASSWORD_FILE=/data/vnc.pass\n" in release_env


@pytest.mark.parametrize(
    "image_ref",
    ["example/relaytv:with-xvfb", "ghcr.io/mcgeezy/relaytv@sha256:" + "a" * 64],
)
@pytest.mark.parametrize("remote_enabled", ["0", "1"])
def test_custom_headless_image_preserves_remote_choice_on_rerun(
    tmp_path: Path, image_ref: str, remote_enabled: str,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")
    first = _run_host_installer(
        tmp_path,
        args=["--mode", "headless"],
        env_overrides={
            "RELAYTV_IMAGE_REF": image_ref,
            "RELAYTV_HEADLESS_REMOTE_ENABLED": remote_enabled,
        },
    )
    assert first.returncode == 0, first.stderr
    assert f"RELAYTV_HEADLESS_REMOTE_ENABLED={remote_enabled}\n" in (tmp_path / ".env").read_text()
    shutil.rmtree(tmp_path / "scripts")

    rerun = _run_host_installer(tmp_path, args=["--mode", "headless"])

    assert rerun.returncode == 0, rerun.stderr
    env_text = (tmp_path / ".env").read_text()
    assert f"RELAYTV_IMAGE_REF={image_ref}\n" in env_text
    assert f"RELAYTV_HEADLESS_REMOTE_ENABLED={remote_enabled}\n" in env_text
    if remote_enabled == "1":
        assert "RELAYTV_PLAYER_BACKEND=mpv\n" not in env_text
        assert "RELAYTV_HEADLESS_VNC_ENABLED=0\n" not in env_text


@pytest.mark.parametrize(
    "image_tag",
    ["latest", "main", "v0.12.2", "0.12.2", "0.12", "v0.12.2@sha256:" + "b" * 64],
)
def test_switch_to_standard_release_image_disables_saved_remote(
    tmp_path: Path, image_tag: str,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")
    (tmp_path / ".env").write_text(
        "RELAYTV_MODE=headless\n"
        "RELAYTV_IMAGE_REF=example/relaytv:with-xvfb\n"
        "RELAYTV_HEADLESS_REMOTE_ENABLED=1\n",
        encoding="utf-8",
    )

    result = _run_host_installer(
        tmp_path,
        args=["--mode", "headless"],
        env_overrides={"RELAYTV_IMAGE_REF": f"ghcr.io/mcgeezy/relaytv:{image_tag}"},
    )

    assert result.returncode == 0, result.stderr
    env_text = (tmp_path / ".env").read_text()
    assert "RELAYTV_HEADLESS_REMOTE_ENABLED=0\n" in env_text
    assert "RELAYTV_PLAYER_BACKEND=mpv\n" in env_text


def _ref_state_key(ref: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in ref)


def _write_stateful_fake_docker_and_curl(
    fake_bin: Path,
    state_dir: Path,
    remote_dir: Path,
) -> None:
    """Create stateful fake `docker` and `curl` shims for bootstrap update tests."""
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        f"STATE={shlex.quote(str(state_dir))}\n"
        "ref_key() { printf '%s' \"$1\" | sed 's/[^A-Za-z0-9]/_/g'; }\n"
        "configured_image_ref() {\n"
        "  local line val=\"\"\n"
        "  if [ -f .env ]; then\n"
        "    while IFS= read -r line || [ -n \"$line\" ]; do\n"
        "      case \"$line\" in\n"
        "        RELAYTV_IMAGE_REF=*) val=\"${line#RELAYTV_IMAGE_REF=}\" ;;\n"
        "      esac\n"
        "    done < .env\n"
        "  fi\n"
        "  val=\"${val%\\\"}\"; val=\"${val#\\\"}\"; val=\"${val%\\'}\"; val=\"${val#\\'}\"\n"
        "  printf '%s' \"${val:-ghcr.io/mcgeezy/relaytv:latest}\"\n"
        "}\n"
        "configured_pull_policy() {\n"
        "  local line val=\"\"\n"
        "  if [ -f .env ]; then\n"
        "    while IFS= read -r line || [ -n \"$line\" ]; do\n"
        "      case \"$line\" in\n"
        "        RELAYTV_PULL_POLICY=*) val=\"${line#RELAYTV_PULL_POLICY=}\" ;;\n"
        "      esac\n"
        "    done < .env\n"
        "  fi\n"
        "  printf '%s' \"${val:-missing}\"\n"
        "}\n"
        'cmd="${1:-}"\n'
        'if [ "$cmd" = "info" ]; then\n'
        "  printf 'Server Version: 27.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$cmd" = "compose" ]; then\n'
        '  sub="${2:-}"\n'
        '  case "$sub" in\n'
        "    version) printf 'Docker Compose version v2.29.0\\n'; exit 0 ;;\n"
        "    pull)\n"
        '      ref="$(configured_image_ref)"\n'
        '      rkey="$(ref_key "$ref")"\n'
        '      cat "$STATE/remote_image_id" > "$STATE/ref_${rkey}"\n'
        '      cat "$STATE/remote_image_id" > "$STATE/tag_image_id"\n'
        '      cp "$STATE/remote_version" "$STATE/img_$(cat "$STATE/remote_image_id" | tr : _)_version"\n'
        '      cp "$STATE/remote_revision" "$STATE/img_$(cat "$STATE/remote_image_id" | tr : _)_revision"\n'
        "      exit 0\n"
        "      ;;\n"
        "    up)\n"
        '      printf "%s\\n" "$*" >> "$STATE/compose_up_args.log"\n'
        '      ref="$(configured_image_ref)"\n'
        '      rkey="$(ref_key "$ref")"\n'
        '      if [ "$(configured_pull_policy)" = "always" ] && [[ " $* " != *" --pull never "* ]]; then\n'
        '        cat "$STATE/remote_image_id" > "$STATE/ref_${rkey}"\n'
        '        cat "$STATE/remote_image_id" > "$STATE/tag_image_id"\n'
        "      fi\n"
        '      pwd -P > "$STATE/container_working_dir"\n'
        '      if [ -f "$STATE/ref_${rkey}" ]; then\n'
        '        cat "$STATE/ref_${rkey}" > "$STATE/container_image_id"\n'
        "      else\n"
        '        cat "$STATE/tag_image_id" > "$STATE/container_image_id"\n'
        "      fi\n"
        '      printf "true" > "$STATE/container_running"\n'
        "      exit 0\n"
        "      ;;\n"
        "    down)\n"
        '      printf "false" > "$STATE/container_running"\n'
        "      exit 0\n"
        "      ;;\n"
        "    ps|logs)\n"
        '      printf "fake compose %s output\\n" "$sub"\n'
        "      exit 0\n"
        "      ;;\n"
        "  esac\n"
        "fi\n"
        'if [ "$cmd" = "image" ]; then\n'
        '  sub="${2:-}"\n'
        '  case "$sub" in\n'
        "    inspect)\n"
        '      ref="${@: -1}"\n'
        '      rkey="$(ref_key "$ref")"\n'
        '      [ -f "$STATE/ref_${rkey}" ] || exit 1\n'
        '      cat "$STATE/ref_${rkey}"\n'
        "      printf '\\n'\n"
        "      exit 0\n"
        "      ;;\n"
        "    tag)\n"
        '      src="${3:-}"\n'
        '      dst="${4:-}"\n'
        '      rkey="$(ref_key "$dst")"\n'
        '      printf "%s" "$src" > "$STATE/ref_${rkey}"\n'
        '      printf "%s" "$src" > "$STATE/tag_image_id"\n'
        '      printf "tagged:%s->%s\\n" "$src" "$dst" >> "$STATE/image_ops.log"\n'
        "      exit 0\n"
        "      ;;\n"
        "    rm)\n"
        '      target="${3:-}"\n'
        '      if [ -f "$STATE/container_running" ] && [ "$(cat "$STATE/container_running")" = "true" ] && [ -f "$STATE/container_image_id" ] && [ "$(cat "$STATE/container_image_id")" = "$target" ]; then\n'
        '        printf "Error response from daemon: conflict: unable to delete %s (container is running)\\n" "$target" >&2\n'
        "        exit 1\n"
        "      fi\n"
        '      printf "rm:%s\\n" "$target" >> "$STATE/image_ops.log"\n'
        "      exit 0\n"
        "      ;;\n"
        "  esac\n"
        "fi\n"
        'if [ "$cmd" = "inspect" ]; then\n'
        '  fmt=""\n'
        '  target=""\n'
        "  shift\n"
        "  while [ $# -gt 0 ]; do\n"
        '    case "$1" in\n'
        '      -f|--format) shift; fmt="$1" ;;\n'
        '      *) target="$1" ;;\n'
        "    esac\n"
        "    shift\n"
        "  done\n"
        '  if [ "$target" = "relaytv" ]; then\n'
        '    [ -f "$STATE/container_image_id" ] || exit 1\n'
        '    img="$(cat "$STATE/container_image_id")"\n'
        '    case "$fmt" in\n'
        '      *working_dir*) cat "$STATE/container_working_dir"; printf "\\n"; exit 0 ;;\n'
        '      *State.Running*) cat "$STATE/container_running"; printf "\\n"; exit 0 ;;\n'
        '      *".Image"*) printf "%s\\n" "$img"; exit 0 ;;\n'
        "    esac\n"
        "  else\n"
        '    img="$target"\n'
        "  fi\n"
        '  key="$(printf "%s" "$img" | tr : _)"\n'
        '  case "$fmt" in\n'
        '    *image.version*) [ -f "$STATE/img_${key}_version" ] && cat "$STATE/img_${key}_version"; printf "\\n"; exit 0 ;;\n'
        '    *image.revision*) [ -f "$STATE/img_${key}_revision" ] && cat "$STATE/img_${key}_revision"; printf "\\n"; exit 0 ;;\n'
        "  esac\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    real_curl = shutil.which("curl")
    assert real_curl is not None
    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'url="${@: -1}"\n'
        'if [[ "$url" == https://review.invalid/* ]]; then\n'
        '  rel="${url#https://review.invalid/}"\n'
        f'  cat {shlex.quote(str(remote_dir))}/"$rel"\n'
        "  exit 0\n"
        "fi\n"
        f'exec {shlex.quote(real_curl)} "$@"\n',
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)


def _populate_remote_bundle(remote_dir: Path, compose_comment: str = "# v1 release") -> None:
    (remote_dir / "scripts").mkdir(parents=True, exist_ok=True)
    compose_body = (ROOT_DIR / "docker-compose.release.yml").read_text(encoding="utf-8")
    (remote_dir / "docker-compose.release.yml").write_text(
        f"{compose_comment}\n{compose_body}",
        encoding="utf-8",
    )
    shutil.copy2(ROOT_DIR / "install.sh", remote_dir / "install.sh")
    shutil.copy2(ROOT_DIR / "scripts" / "install.sh", remote_dir / "scripts" / "install.sh")
    shutil.copy2(ROOT_DIR / "scripts" / "doctor.sh", remote_dir / "scripts" / "doctor.sh")
    shutil.copy2(ROOT_DIR / "scripts" / "host-ops.sh", remote_dir / "scripts" / "host-ops.sh")


def _seed_fake_docker_image_state(
    state_dir: Path,
    *,
    current_id: str,
    current_version: str,
    current_revision: str,
    remote_id: str,
    remote_version: str,
    remote_revision: str,
    current_ref: str = "ghcr.io/mcgeezy/relaytv:latest",
) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    (state_dir / "tag_image_id").write_text(current_id, encoding="utf-8")
    (state_dir / f"ref_{_ref_state_key(current_ref)}").write_text(current_id, encoding="utf-8")
    (state_dir / f"img_{current_id.replace(':', '_')}_version").write_text(
        current_version, encoding="utf-8"
    )
    (state_dir / f"img_{current_id.replace(':', '_')}_revision").write_text(
        current_revision, encoding="utf-8"
    )
    (state_dir / "remote_image_id").write_text(remote_id, encoding="utf-8")
    (state_dir / "remote_version").write_text(remote_version, encoding="utf-8")
    (state_dir / "remote_revision").write_text(remote_revision, encoding="utf-8")


def test_bootstrap_same_command_updates_existing_install_without_prompt_and_preserves_state(
    tmp_path: Path,
) -> None:
    import http.server
    import threading

    if shutil.which("setsid") is None:
        pytest.skip("setsid is unavailable")

    class HealthyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        install_dir = tmp_path / "relaytv"
        install_dir.mkdir()
        remote_dir = tmp_path / "remote"
        _populate_remote_bundle(remote_dir, "# release v1")
        state_dir = tmp_path / "docker-state"
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        _write_stateful_fake_docker_and_curl(fake_bin, state_dir, remote_dir)

        v1_id = "sha256:" + "1" * 64
        v2_id = "sha256:" + "2" * 64
        _seed_fake_docker_image_state(
            state_dir,
            current_id=v1_id,
            current_version="0.12.3",
            current_revision="a8bc5dc000001111",
            remote_id=v1_id,
            remote_version="0.12.3",
            remote_revision="a8bc5dc000001111",
        )

        base_env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
        base_env.update(
            PATH=f"{fake_bin}:{base_env.get('PATH', '')}",
            RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
            RELAYTV_BOOTSTRAP_DISCOVER_DOCKER="0",
            RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
            RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
            RELAYTV_PORT=str(port),
        )

        # 1. Initial headless install.
        first = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--yes", "--mode", "headless"],
            cwd=install_dir,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert first.returncode == 0, first.stderr
        assert "RelayTV install complete." in first.stdout

        # Persist operator secret and data volume file.
        secret = "operator-secret-token-keep"
        with (install_dir / ".env").open("a", encoding="utf-8") as fh:
            fh.write(f"\nRELAYTV_API_TOKEN={secret}\n")
        (install_dir / "data" / "settings.json").write_text('{"device_name":"Living Room"}\n')

        # 2. Publish v2 remotely and run exact flagless `install.sh` in a detached non-TTY session
        #    (no --yes, no --dir, no --update, no --mode).
        _populate_remote_bundle(remote_dir, "# release v2")
        (state_dir / "remote_image_id").write_text(v2_id, encoding="utf-8")
        (state_dir / "remote_version").write_text("0.13.1", encoding="utf-8")
        (state_dir / "remote_revision").write_text("c5ab8a2000002222", encoding="utf-8")

        update_env = dict(base_env)
        update_env.pop("RELAYTV_PORT", None)  # must read saved RELAYTV_PORT from .env
        update = subprocess.run(
            ["/bin/bash", str(ROOT_DIR / "install.sh")],
            cwd=install_dir,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=15,
            env=update_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert update.returncode == 0, update.stderr
        assert "RelayTV updater" in update.stdout
        assert "RelayTV update complete." in update.stdout
        assert "0.12.3 (a8bc5dc00000) -> 0.13.1 (c5ab8a200000)" in update.stdout
        assert (install_dir / "docker-compose.yml").read_text(encoding="utf-8").startswith(
            "# release v2\n"
        )
        env_after = (install_dir / ".env").read_text(encoding="utf-8")
        assert f"RELAYTV_API_TOKEN={secret}\n" in env_after
        assert "RELAYTV_MODE=headless\n" in env_after
        assert f"RELAYTV_PORT={port}\n" in env_after
        assert secret not in update.stdout
        assert secret not in update.stderr
        assert (install_dir / "data" / "settings.json").read_text() == '{"device_name":"Living Room"}\n'
        assert not (state_dir / "image_ops.log").exists()
    finally:
        server.shutdown()
        server.server_close()


def test_bootstrap_discovers_existing_install_from_outside_dir_and_supports_update_flag(
    tmp_path: Path,
) -> None:
    import fcntl
    import pty
    import select
    import termios

    remote_dir = tmp_path / "remote"
    _populate_remote_bundle(remote_dir, "# discovered v2")
    state_dir = tmp_path / "docker-state"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_stateful_fake_docker_and_curl(fake_bin, state_dir, remote_dir)

    v1_id = "sha256:" + "3" * 64
    v2_id = "sha256:" + "4" * 64
    _seed_fake_docker_image_state(
        state_dir,
        current_id=v1_id,
        current_version="0.12.3",
        current_revision="a8bc5dc00000",
        remote_id=v2_id,
        remote_version="0.13.1",
        remote_revision="c5ab8a200000",
    )

    # Seed an existing deployed install at tmp_path/opt-relaytv tracked via Docker label.
    opt_dir = tmp_path / "opt-relaytv"
    (opt_dir / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", opt_dir / "docker-compose.yml")
    shutil.copy2(ROOT_DIR / "scripts" / "install.sh", opt_dir / "scripts" / "install.sh")
    (opt_dir / ".env").write_text("RELAYTV_MODE=headless\nRELAYTV_CEC_ENABLED=0\n", encoding="utf-8")
    (state_dir / "container_working_dir").write_text(str(opt_dir), encoding="utf-8")
    (state_dir / "container_image_id").write_text(v1_id, encoding="utf-8")
    (state_dir / "container_running").write_text("true", encoding="utf-8")

    fake_home = tmp_path / "home"
    fake_home.mkdir()
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
    base_env.update(
        HOME=str(fake_home),
        PATH=f"{fake_bin}:{base_env.get('PATH', '')}",
        RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
        RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
        RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
    )

    # 1. Interactive PTY from fake_home with no flags: offers opt_dir and updates on Enter.
    master_fd, slave_fd = pty.openpty()
    try:
        os.write(master_fd, b"\n")

        def attach_ctty() -> None:
            os.setsid()
            fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

        pty_proc = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--no-pull", "--no-start"],
            cwd=fake_home,
            env=base_env,
            stdin=slave_fd,
            capture_output=True,
            text=True,
            preexec_fn=attach_ctty,
            timeout=10,
            check=False,
        )
        os.close(slave_fd)
        slave_fd = -1
        pty_chunks: list[bytes] = []
        while select.select([master_fd], [], [], 0.05)[0]:
            try:
                chunk = os.read(master_fd, 4096)
            except OSError:
                break
            if not chunk:
                break
            pty_chunks.append(chunk)
        pty_text = b"".join(pty_chunks).decode("utf-8", errors="replace")
    finally:
        if slave_fd >= 0:
            os.close(slave_fd)
        os.close(master_fd)

    assert pty_proc.returncode == 0, pty_proc.stderr
    assert "Existing RelayTV installation detected in:" in pty_text
    assert str(opt_dir) in pty_text
    assert (opt_dir / "docker-compose.yml").read_text(encoding="utf-8").startswith(
        "# discovered v2\n"
    )
    assert list(fake_home.iterdir()) == []

    # 2. --update --yes falls back to ~/relaytv when Docker container label is absent.
    (state_dir / "container_image_id").unlink()
    home_install = fake_home / "relaytv"
    (home_install / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", home_install / "docker-compose.yml")
    (home_install / ".env").write_text("RELAYTV_MODE=headless\nRELAYTV_CEC_ENABLED=0\n", encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    flag_update = subprocess.run(
        ["bash", str(ROOT_DIR / "install.sh"), "--update", "--yes", "--no-pull", "--no-start"],
        cwd=elsewhere,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert flag_update.returncode == 0, flag_update.stderr
    assert (home_install / "docker-compose.yml").read_text(encoding="utf-8").startswith(
        "# discovered v2\n"
    )
    assert list(elsewhere.iterdir()) == []

    # 3. Saved ~/relaytv/install.sh invoked directly from elsewhere targets its own directory.
    _populate_remote_bundle(remote_dir, "# local-self-update v3")
    self_update = subprocess.run(
        ["bash", str(home_install / "install.sh"), "--no-pull", "--no-start"],
        cwd=elsewhere,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        timeout=15,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert self_update.returncode == 0, self_update.stderr
    assert (home_install / "docker-compose.yml").read_text(encoding="utf-8").startswith(
        "# local-self-update v3\n"
    )

    # 4. --update when no installation exists anywhere fails cleanly without creating files.
    empty_home = tmp_path / "empty-home"
    empty_home.mkdir()
    missing_dir = tmp_path / "missing-install"
    missing_res = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(missing_dir),
            "--update",
            "--yes",
        ],
        cwd=elsewhere,
        env={**base_env, "HOME": str(empty_home)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert missing_res.returncode != 0
    assert "No existing RelayTV installation found" in missing_res.stderr
    assert not missing_dir.exists()


def test_bootstrap_update_auto_rolls_back_config_and_image_when_health_check_fails(
    tmp_path: Path,
) -> None:
    import http.server
    import threading
    import urllib.request

    state_dir = tmp_path / "docker-state"
    v1_id = "sha256:" + "a" * 64
    v2_id = "sha256:" + "b" * 64

    class VersionGateHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            running = (state_dir / "container_running").read_text(encoding="utf-8").strip()
            active_img = (state_dir / "container_image_id").read_text(encoding="utf-8").strip()
            if running == "true" and active_img == v1_id:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b'{"ok":true}')
            else:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(b'{"ok":false}')

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), VersionGateHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        install_dir = tmp_path / "relaytv"
        install_dir.mkdir()
        remote_dir = tmp_path / "remote"
        _populate_remote_bundle(remote_dir, "# good v1")
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        _write_stateful_fake_docker_and_curl(fake_bin, state_dir, remote_dir)
        _seed_fake_docker_image_state(
            state_dir,
            current_id=v1_id,
            current_version="0.12.3",
            current_revision="a8bc5dc00000",
            remote_id=v1_id,
            remote_version="0.12.3",
            remote_revision="a8bc5dc00000",
        )

        env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
        env.update(
            PATH=f"{fake_bin}:{env.get('PATH', '')}",
            RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
            RELAYTV_BOOTSTRAP_DISCOVER_DOCKER="0",
            RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
            RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
            RELAYTV_PORT=str(port),
        )

        # Install healthy v1, with RELAYTV_PULL_POLICY=always so rollback must pass --pull never.
        init_res = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--yes", "--mode", "headless"],
            cwd=install_dir,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert init_res.returncode == 0, init_res.stderr
        with (install_dir / ".env").open("a", encoding="utf-8") as fh:
            fh.write("\nRELAYTV_API_TOKEN=rollback-token-keep\nRELAYTV_PULL_POLICY=always\n")
        v1_env_snapshot = (install_dir / ".env").read_text(encoding="utf-8")
        v1_override_snapshot = (install_dir / "docker-compose.override.yml").read_text(
            encoding="utf-8"
        )

        # Publish broken v2 image that fails /health with HTTP 500.
        _populate_remote_bundle(remote_dir, "# broken v2")
        (state_dir / "remote_image_id").write_text(v2_id, encoding="utf-8")
        (state_dir / "remote_version").write_text("0.13.1", encoding="utf-8")
        (state_dir / "remote_revision").write_text("c5ab8a200000", encoding="utf-8")

        upd_env = dict(env)
        upd_env.pop("RELAYTV_PORT", None)
        upd_env["RELAYTV_INSTALL_HEALTH_TIMEOUT"] = "2"
        failed_upd = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--yes", "--mode", "headless"],
            cwd=install_dir,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=15,
            env=upd_env,
            capture_output=True,
            text=True,
            check=False,
        )

        assert failed_upd.returncode != 0
        assert "RelayTV is healthy." not in failed_upd.stdout
        assert "Update failed during health verification; rolling back RelayTV" in failed_upd.stderr
        assert (
            "Rollback succeeded: previous RelayTV (0.12.3 (a8bc5dc00000)) is running and healthy."
            in failed_upd.stderr
        )
        compose_ups = (state_dir / "compose_up_args.log").read_text(encoding="utf-8").splitlines()
        assert any("--pull never --force-recreate" in line for line in compose_ups)
        # Compose, .env, override, image tag, and running container must all be back on v1.
        assert (install_dir / "docker-compose.yml").read_text(encoding="utf-8").startswith(
            "# good v1\n"
        )
        assert (install_dir / ".env").read_text(encoding="utf-8") == v1_env_snapshot
        assert stat.S_IMODE((install_dir / ".env").stat().st_mode) == 0o600
        assert (
            install_dir / "docker-compose.override.yml"
        ).read_text(encoding="utf-8") == v1_override_snapshot
        assert (state_dir / "tag_image_id").read_text(encoding="utf-8") == v1_id
        assert (state_dir / "container_image_id").read_text(encoding="utf-8") == v1_id
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as resp:
            assert resp.status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_bootstrap_update_prune_and_pinned_image_notice(tmp_path: Path) -> None:
    import http.server
    import threading

    class HealthyHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args: object) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthyHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        install_dir = tmp_path / "relaytv"
        (install_dir / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT_DIR / "docker-compose.release.yml", install_dir / "docker-compose.yml")
        (install_dir / ".env").write_text(
            "RELAYTV_MODE=headless\n"
            "RELAYTV_CEC_ENABLED=0\n"
            f"RELAYTV_PORT={port}\n"
            "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:v0.12.2\n",
            encoding="utf-8",
        )

        remote_dir = tmp_path / "remote"
        _populate_remote_bundle(remote_dir, "# prune check")
        state_dir = tmp_path / "docker-state"
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        _write_stateful_fake_docker_and_curl(fake_bin, state_dir, remote_dir)

        v1_id = "sha256:" + "5" * 64
        v2_id = "sha256:" + "6" * 64
        _seed_fake_docker_image_state(
            state_dir,
            current_id=v1_id,
            current_version="0.12.2",
            current_revision="111111111111",
            remote_id=v1_id,
            remote_version="0.12.2",
            remote_revision="111111111111",
            current_ref="ghcr.io/mcgeezy/relaytv:v0.12.2",
        )
        (state_dir / "container_working_dir").write_text(str(install_dir), encoding="utf-8")
        (state_dir / "container_image_id").write_text(v1_id, encoding="utf-8")
        (state_dir / "container_running").write_text("true", encoding="utf-8")

        env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
        env.update(
            PATH=f"{fake_bin}:{env.get('PATH', '')}",
            RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
            RELAYTV_BOOTSTRAP_DISCOVER_DOCKER="0",
            RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
            RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
        )

        # 1. Pinned image notice + no prune when image ID is unchanged.
        same_res = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--prune", "--no-start"],
            cwd=install_dir,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=15,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert same_res.returncode == 0, same_res.stderr
        assert "Branch/tag: v0.12.2" in same_res.stdout
        assert (
            "NOTE: RELAYTV_IMAGE_REF is pinned to ghcr.io/mcgeezy/relaytv:v0.12.2"
            in same_res.stdout
        )
        assert "--branch main" in same_res.stdout
        assert "(already up to date)" in same_res.stdout
        assert not (state_dir / "image_ops.log").exists()

        # 2. --prune with --no-start must NOT remove the running container's v1_id before restart.
        (state_dir / "remote_image_id").write_text(v2_id, encoding="utf-8")
        (state_dir / "remote_version").write_text("0.13.1", encoding="utf-8")
        (state_dir / "remote_revision").write_text("222222222222", encoding="utf-8")
        staged_res = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--branch", "main", "--prune", "--no-start"],
            cwd=install_dir,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=15,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert staged_res.returncode == 0, staged_res.stderr
        assert "Skipping --prune because --no-start was specified." in staged_res.stdout
        assert "(staged; --no-start)" in staged_res.stdout
        assert not (state_dir / "image_ops.log").exists()

        # 3. Real started + healthy --prune update removes superseded v1_id, including when
        #    no container existed beforehand (reading previous RELAYTV_IMAGE_REF from .env).
        (install_dir / ".env").write_text(
            "RELAYTV_MODE=headless\n"
            "RELAYTV_CEC_ENABLED=0\n"
            f"RELAYTV_PORT={port}\n"
            "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:v0.12.2\n",
            encoding="utf-8",
        )
        (state_dir / "container_image_id").unlink()
        (state_dir / "container_running").unlink()
        prune_res = subprocess.run(
            ["bash", str(ROOT_DIR / "install.sh"), "--branch", "main", "--prune"],
            cwd=install_dir,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            timeout=15,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert prune_res.returncode == 0, prune_res.stderr
        assert "Removing superseded RelayTV image" in prune_res.stdout
        assert (state_dir / "image_ops.log").read_text(encoding="utf-8") == f"rm:{v1_id}\n"
        assert "RELAYTV_IMAGE_REF=" not in (install_dir / ".env").read_text(encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()


def test_host_installer_preserves_headless_offline_drm_and_raspi_wayland_qpa_on_flagless_rerun(
    tmp_path: Path,
) -> None:
    fake_bin = tmp_path / "no-session-bin"
    fake_bin.mkdir()
    for tool in ("loginctl", "systemctl"):
        shim = fake_bin / tool
        shim.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        shim.chmod(0o755)

    def prepare_offline_installer(work_dir: Path) -> Path:
        installer = _copy_host_installer(work_dir)
        text = installer.read_text(encoding="utf-8").replace(
            'DRM_CONNECTOR_DETECTED="$(detect_connected_drm_connector)"',
            'DRM_CONNECTOR_DETECTED=""',
            1,
        )
        installer.write_text(text, encoding="utf-8")
        return installer

    base_env = {
        "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        "RELAYTV_TARGET_USER": pwd.getpwuid(os.getuid()).pw_name,
        "RELAYTV_CEC_ENABLED": "0",
        "RELAYTV_PI_VIDEO_DEVICES_ENABLED": "0",
    }

    # 1. Saved RELAYTV_MODE=headless survives flagless rerun on a host with no display/DRM.
    headless_dir = tmp_path / "headless-rerun"
    headless_dir.mkdir()
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", headless_dir / "docker-compose.yml")
    (headless_dir / ".env").write_text("RELAYTV_MODE=headless\n", encoding="utf-8")
    hl_installer = prepare_offline_installer(headless_dir)
    hl_res = subprocess.run(
        [str(hl_installer)],
        cwd=headless_dir,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert hl_res.returncode == 0, hl_res.stderr
    assert "RELAYTV_MODE=headless\n" in (headless_dir / ".env").read_text(encoding="utf-8")

    # Explicit --mode auto on that same offline host still enforces live display/DRM detection.
    explicit_auto = subprocess.run(
        [str(hl_installer), "--mode", "auto"],
        cwd=headless_dir,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert explicit_auto.returncode == 3

    # 2. Saved RELAYTV_MODE=drm + RELAYTV_DRM_CONNECTOR=HDMI-A-1 survives SSH update while TV is off.
    drm_dir = tmp_path / "drm-offline"
    drm_dir.mkdir()
    (drm_dir / ".env").write_text(
        "RELAYTV_MODE=drm\nRELAYTV_DRM_CONNECTOR=HDMI-A-1\n",
        encoding="utf-8",
    )
    drm_installer = prepare_offline_installer(drm_dir)
    drm_res = subprocess.run(
        [str(drm_installer)],
        cwd=drm_dir,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert drm_res.returncode == 0, drm_res.stderr
    assert "preserving saved RELAYTV_MODE=drm" in drm_res.stdout
    drm_env = (drm_dir / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_MODE=drm\n" in drm_env
    assert "RELAYTV_DRM_CONNECTOR=HDMI-A-1\n" in drm_env

    # 3. Raspberry Pi Wayland explicit QT_QPA_PLATFORM=wayland opt-in survives flagless update.
    raspi_dir = tmp_path / "raspi-wayland"
    raspi_dir.mkdir()
    runtime_dir = tmp_path / "wl-runtime"
    runtime_dir.mkdir()
    (runtime_dir / "wayland-0").touch()
    first_pi = _run_host_installer(
        raspi_dir,
        args=["--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "QT_QPA_PLATFORM": "wayland",
            "RELAYTV_HOST_PROFILE": "raspi",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_SESSION_TYPE": "wayland",
        },
    )
    assert first_pi.returncode == 0, first_pi.stderr
    assert "QT_QPA_PLATFORM=wayland\n" in (raspi_dir / ".env").read_text(encoding="utf-8")

    shutil.rmtree(raspi_dir / "scripts")
    rerun_pi = _run_host_installer(
        raspi_dir,
        args=["--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "RELAYTV_HOST_PROFILE": "raspi",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_RUNTIME_DIR": str(runtime_dir),
            "XDG_SESSION_TYPE": "wayland",
        },
    )
    assert rerun_pi.returncode == 0, rerun_pi.stderr
    assert "QT_QPA_PLATFORM=wayland\n" in (raspi_dir / ".env").read_text(encoding="utf-8")

    # 4. Logged-out / greeter SSH rerun on a desktop host with connected HDMI (DRM_CONNECTED=1)
    #    must preserve saved wayland/x11 instead of clobbering to drm, while a fresh install
    #    with no saved RELAYTV_MODE still selects drm.
    def prepare_drm_connected_no_session_installer(work_dir: Path) -> Path:
        installer = _copy_host_installer(work_dir)
        text = installer.read_text(encoding="utf-8").replace(
            'DRM_CONNECTOR_DETECTED="$(detect_connected_drm_connector)"',
            'DRM_CONNECTOR_DETECTED="HDMI-A-1"',
            1,
        )
        installer.write_text(text, encoding="utf-8")
        return installer

    for desktop_mode, extra_lines in (
        ("wayland", "WAYLAND_DISPLAY=wayland-1\nDISPLAY=:1\nXDG_RUNTIME_DIR=/run/user/12345\n"),
        ("x11", "DISPLAY=:2\n"),
    ):
        greeter_dir = tmp_path / f"greeter-{desktop_mode}"
        greeter_dir.mkdir()
        (greeter_dir / ".env").write_text(
            f"RELAYTV_MODE={desktop_mode}\n{extra_lines}",
            encoding="utf-8",
        )
        gr_installer = prepare_drm_connected_no_session_installer(greeter_dir)
        gr_res = subprocess.run(
            [str(gr_installer)],
            cwd=greeter_dir,
            env=base_env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert gr_res.returncode == 0, gr_res.stderr
        assert f"preserving saved RELAYTV_MODE={desktop_mode}" in gr_res.stdout
        gr_env = (greeter_dir / ".env").read_text(encoding="utf-8")
        assert f"RELAYTV_MODE={desktop_mode}\n" in gr_env
        assert "RELAYTV_MODE=drm" not in gr_env
        if desktop_mode == "wayland":
            assert "WAYLAND_DISPLAY=wayland-1\n" in gr_env
            assert "DISPLAY=:1\n" in gr_env
        else:
            assert "DISPLAY=:2\n" in gr_env

    fresh_drm_dir = tmp_path / "fresh-drm"
    fresh_drm_dir.mkdir()
    fresh_drm_installer = prepare_drm_connected_no_session_installer(fresh_drm_dir)
    fresh_drm_res = subprocess.run(
        [str(fresh_drm_installer)],
        cwd=fresh_drm_dir,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert fresh_drm_res.returncode == 0, fresh_drm_res.stderr
    assert "RELAYTV_MODE=drm\n" in (fresh_drm_dir / ".env").read_text(encoding="utf-8")


def test_bootstrap_review_regressions(tmp_path: Path) -> None:
    import fcntl
    import pty
    import select
    import termios

    def run_pty(
        cmd: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        input_bytes: bytes,
    ) -> tuple[subprocess.CompletedProcess[str], str]:
        master_fd, slave_fd = pty.openpty()
        try:
            if input_bytes:
                os.write(master_fd, input_bytes)

            def attach_ctty() -> None:
                os.setsid()
                fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)

            proc = subprocess.run(
                cmd,
                cwd=cwd,
                env=env,
                stdin=slave_fd,
                capture_output=True,
                text=True,
                preexec_fn=attach_ctty,
                timeout=10,
                check=False,
            )
            os.close(slave_fd)
            slave_fd = -1
            chunks: list[bytes] = []
            while select.select([master_fd], [], [], 0.05)[0]:
                try:
                    data = os.read(master_fd, 4096)
                except OSError:
                    break
                if not data:
                    break
                chunks.append(data)
            return proc, b"".join(chunks).decode("utf-8", errors="replace")
        finally:
            if slave_fd >= 0:
                os.close(slave_fd)
            os.close(master_fd)

    remote_dir = tmp_path / "remote"
    _populate_remote_bundle(remote_dir, "# main bundle")
    state_dir = tmp_path / "docker-state"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    _write_stateful_fake_docker_and_curl(fake_bin, state_dir, remote_dir)

    base_env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
    base_env.update(
        PATH=f"{fake_bin}:{base_env.get('PATH', '')}",
        RELAYTV_BOOTSTRAP_RAW_BASE="https://review.invalid",
        RELAYTV_BOOTSTRAP_DISCOVER_DOCKER="0",
        RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
        RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
    )

    # 1. Fresh interactive install prompts "Continue? [y/N]" exactly once (not twice).
    fresh_dir = tmp_path / "fresh-interactive"
    fresh_dir.mkdir()
    fresh_home = tmp_path / "fresh-home"
    fresh_home.mkdir()
    fresh_proc, fresh_pty = run_pty(
        ["bash", str(ROOT_DIR / "install.sh"), "--no-pull", "--no-start", "--mode", "headless"],
        cwd=fresh_dir,
        env={**base_env, "HOME": str(fresh_home)},
        input_bytes=b"y\n",
    )
    assert fresh_proc.returncode == 0, f"stderr={fresh_proc.stderr} pty={fresh_pty}"
    assert fresh_pty.count("Continue? [y/N]") == 1

    # 2. Interactive `install.sh` (without --update) discovers an existing container on a
    #    sudo-only Docker host where `docker` and `sudo -n docker` fail before `ensure_docker_ready`.
    if os.geteuid() != 0:
        sudo_bin = tmp_path / "sudo-bin"
        sudo_bin.mkdir()
        opt_install = tmp_path / "opt-sudo-relaytv"
        (opt_install / "scripts").mkdir(parents=True)
        shutil.copy2(ROOT_DIR / "docker-compose.release.yml", opt_install / "docker-compose.yml")
        (opt_install / ".env").write_text(
            "RELAYTV_MODE=headless\nRELAYTV_CEC_ENABLED=0\n",
            encoding="utf-8",
        )
        sudo_docker = sudo_bin / "docker"
        sudo_docker.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "compose" ] && [ "$2" = "version" ]; then\n'
            "  printf 'Docker Compose version v2.29.0\\n'\n"
            "  exit 0\n"
            "fi\n"
            'if [ "${FAKE_SUDO_OK:-0}" != "1" ]; then\n'
            "  printf 'permission denied\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
            'if [ "$1" = "info" ]; then\n'
            "  printf 'Server Version: 27.0\\n'\n"
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "inspect" ]; then\n'
            '  case "$*" in\n'
            f'    *working_dir*) printf "%s\\n" {shlex.quote(str(opt_install))}; exit 0 ;;\n'
            "  esac\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        sudo_docker.chmod(0o755)
        fake_sudo = sudo_bin / "sudo"
        fake_sudo.write_text(
            "#!/bin/sh\n"
            'if [ "${1:-}" = "-n" ]; then\n'
            "  printf 'sudo: a password is required\\n' >&2\n"
            "  exit 1\n"
            "fi\n"
            'FAKE_SUDO_OK=1 exec "$@"\n',
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        fake_systemctl = sudo_bin / "systemctl"
        fake_systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_systemctl.chmod(0o755)
        shutil.copy2(fake_bin / "curl", sudo_bin / "curl")

        sudo_home = tmp_path / "sudo-home"
        sudo_home.mkdir()
        sudo_proc, sudo_pty = run_pty(
            ["bash", str(ROOT_DIR / "install.sh"), "--no-pull", "--no-start"],
            cwd=sudo_home,
            env={
                **base_env,
                "HOME": str(sudo_home),
                "PATH": f"{sudo_bin}:{os.environ.get('PATH', '')}",
                "RELAYTV_BOOTSTRAP_DISCOVER_DOCKER": "1",
            },
            input_bytes=b"y\n",
        )
        assert sudo_proc.returncode == 0, f"stderr={sudo_proc.stderr} pty={sudo_pty}"
        assert "Existing RelayTV installation detected in:" in sudo_pty
        assert str(opt_install) in sudo_pty
        assert "Continue? [y/N]" not in sudo_pty
        assert list(sudo_home.iterdir()) == []

    # 3. Root / sudo HOME=/root discovers ~${SUDO_USER}/relaytv via getent passwd.
    sudo_user_home = tmp_path / "home-tvuser"
    (sudo_user_home / "relaytv" / "scripts").mkdir(parents=True)
    shutil.copy2(
        ROOT_DIR / "docker-compose.release.yml",
        sudo_user_home / "relaytv" / "docker-compose.yml",
    )
    (sudo_user_home / "relaytv" / ".env").write_text("RELAYTV_MODE=headless\n", encoding="utf-8")
    root_shim_bin = tmp_path / "root-shim"
    root_shim_bin.mkdir()
    (root_shim_bin / "id").write_text("#!/bin/sh\nprintf '0\\n'\n", encoding="utf-8")
    (root_shim_bin / "id").chmod(0o755)
    (root_shim_bin / "getent").write_text(
        f"#!/bin/sh\nprintf 'tvuser:x:1000:1000::%s:/bin/bash\\n' {shlex.quote(str(sudo_user_home))}\n",
        encoding="utf-8",
    )
    (root_shim_bin / "getent").chmod(0o755)
    discover_script = (
        "set -euo pipefail\n"
        "DISCOVER_DOCKER=0\n"
        "FORCE_INSTALL=0\n"
        + "\n".join(
            _extract_fn(ROOT_DIR / "install.sh", name)
            for name in (
                "compose_file_has_local_build",
                "is_deployed_relaytv_dir",
                "probe_docker_compose_working_dir",
                "discover_existing_install_dir",
            )
        )
        + "\ndiscover_existing_install_dir\n"
    )
    disc_res = subprocess.run(
        ["bash", "-c", discover_script],
        env={
            "PATH": f"{root_shim_bin}:/usr/bin:/bin",
            "HOME": "/root",
            "SUDO_USER": "tvuser",
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert disc_res.returncode == 0, disc_res.stderr
    assert disc_res.stdout.strip() == str((sudo_user_home / "relaytv").resolve())

    # 4. Rolling back / pinning to a pre-updater tag (whose install.sh lacks --update and whose
    #    scripts/install.sh lacks SAVED_RELAYTV_MODE) keeps the saved install.sh capable of
    #    --update and --prune on headless/offline hosts without --mode, and subsequent flagless
    #    updates fetch scripts from that pinned tag rather than skewing main onto the old image.
    tag_root = tmp_path / "github-raw"
    _populate_remote_bundle(tag_root / "main", "# main compose")
    _populate_remote_bundle(tag_root / "v0.13.1", "# v0.13.1 compose")
    # Simulate pre-updater v0.13.1 install.sh (lacks --update) and v0.13.1 scripts/install.sh
    # (lacks SAVED_RELAYTV_MODE and sees no live display or connected DRM output).
    (tag_root / "v0.13.1" / "install.sh").write_text(
        "#!/usr/bin/env bash\nprintf 'ERROR: legacy install.sh without update flag\\n' >&2\nexit 2\n",
        encoding="utf-8",
    )
    legacy_host_installer = (
        (ROOT_DIR / "scripts" / "install.sh")
        .read_text(encoding="utf-8")
        .replace(
            'SAVED_RELAYTV_MODE="$(existing_env_value RELAYTV_MODE || true)"',
            'SAVED_RELAYTV_MODE=""',
            1,
        )
        .replace(
            'DRM_CONNECTOR_DETECTED="$(detect_connected_drm_connector)"',
            'DRM_CONNECTOR_DETECTED=""',
            1,
        )
    )
    legacy_host_installer = legacy_host_installer.replace(
        "SAVED_RELAYTV_MODE", "LEGACY_DISABLED_MODE"
    )
    assert "SAVED_RELAYTV_MODE" not in legacy_host_installer
    (tag_root / "v0.13.1" / "scripts" / "install.sh").write_text(
        legacy_host_installer,
        encoding="utf-8",
    )
    branch_curl_log = tmp_path / "branch-curl.log"
    branch_bin = tmp_path / "branch-bin"
    branch_bin.mkdir()
    shutil.copy2(fake_bin / "docker", branch_bin / "docker")
    for tool in ("loginctl", "systemctl"):
        (branch_bin / tool).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        (branch_bin / tool).chmod(0o755)
    real_curl = shutil.which("curl")
    assert real_curl is not None
    (branch_bin / "curl").write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        'url="${@: -1}"\n'
        f'printf "%s\\n" "$url" >> {shlex.quote(str(branch_curl_log))}\n'
        'if [[ "$url" == https://raw.githubusercontent.com/mcgeezy/relaytv/* ]]; then\n'
        '  rel="${url#https://raw.githubusercontent.com/mcgeezy/relaytv/}"\n'
        f'  cat {shlex.quote(str(tag_root))}/"$rel"\n'
        "  exit 0\n"
        "fi\n"
        f'exec {shlex.quote(real_curl)} "$@"\n',
        encoding="utf-8",
    )
    (branch_bin / "curl").chmod(0o755)

    pinned_dir = tmp_path / "pinned-install"
    pinned_dir.mkdir()
    shutil.copy2(ROOT_DIR / "install.sh", pinned_dir / "install.sh")
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", pinned_dir / "docker-compose.yml")
    (pinned_dir / ".env").write_text("RELAYTV_MODE=headless\nRELAYTV_CEC_ENABLED=0\n", encoding="utf-8")

    pin_env = {k: v for k, v in os.environ.items() if not k.startswith("RELAYTV_")}
    pin_env.update(
        PATH=f"{branch_bin}:{pin_env.get('PATH', '')}",
        RELAYTV_BOOTSTRAP_DISCOVER_DOCKER="0",
        RELAYTV_TARGET_USER=pwd.getpwuid(os.getuid()).pw_name,
        RELAYTV_PI_VIDEO_DEVICES_ENABLED="0",
    )
    # Also test piped stdin (`curl ... | bash -s -- --branch v0.13.1`, BASH_SOURCE[0] unset)
    # WITHOUT passing --mode headless, verifying legacy scripts/install.sh receives saved mode.
    piped_pin = subprocess.run(
        ["bash", "-s", "--", "--branch", "v0.13.1", "--no-pull", "--no-start"],
        input=(ROOT_DIR / "install.sh").read_text(encoding="utf-8"),
        cwd=pinned_dir,
        env=pin_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert piped_pin.returncode == 0, piped_pin.stderr
    assert (pinned_dir / "docker-compose.yml").read_text(encoding="utf-8").startswith(
        "# v0.13.1 compose\n"
    )
    assert "RELAYTV_MODE=headless\n" in (pinned_dir / ".env").read_text(encoding="utf-8")
    assert "--update" in (pinned_dir / "install.sh").read_text(encoding="utf-8")

    branch_curl_log.write_text("", encoding="utf-8")
    followup_upd = subprocess.run(
        [str(pinned_dir / "install.sh"), "--update", "--no-pull", "--no-start"],
        cwd=pinned_dir,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        timeout=15,
        env=pin_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert followup_upd.returncode == 0, followup_upd.stderr
    logged_urls = branch_curl_log.read_text(encoding="utf-8").splitlines()
    assert any("/v0.13.1/docker-compose.release.yml" in u for u in logged_urls)
    assert not any("/main/docker-compose.release.yml" in u for u in logged_urls)

    # 4b. Interrupted / truncated download of install.sh (even past line 27 `--update`) must never
    #     overwrite $INSTALL_DIR/install.sh when piped via stdin and fallback fetch also fails.
    trunc_bin = tmp_path / "trunc-bin"
    trunc_bin.mkdir()
    shutil.copy2(fake_bin / "docker", trunc_bin / "docker")
    full_install_lines = (ROOT_DIR / "install.sh").read_text(encoding="utf-8").splitlines()
    truncated_payload = "\n".join(full_install_lines[:60]) + "\nif [\n"
    assert "--update" in truncated_payload
    (trunc_bin / "curl").write_text(
        "#!/bin/bash\n"
        'url="${@: -1}"\n'
        'if [[ "$url" == */install.sh ]]; then\n'
        f"  printf '%s' {shlex.quote(truncated_payload)}\n"
        "  exit 18\n"
        "fi\n"
        'rel="${url#https://review.invalid/}"\n'
        f'cat {shlex.quote(str(remote_dir))}/"$rel"\n',
        encoding="utf-8",
    )
    (trunc_bin / "curl").chmod(0o755)
    trunc_dir = tmp_path / "trunc-install"
    trunc_dir.mkdir()
    shutil.copy2(ROOT_DIR / "install.sh", trunc_dir / "install.sh")
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", trunc_dir / "docker-compose.yml")
    (trunc_dir / ".env").write_text("RELAYTV_MODE=headless\nRELAYTV_CEC_ENABLED=0\n", encoding="utf-8")
    original_saved_install = (trunc_dir / "install.sh").read_text(encoding="utf-8")
    trunc_res = subprocess.run(
        ["bash", "-s", "--", "--update", "--no-pull", "--no-start"],
        input=original_saved_install,
        cwd=trunc_dir,
        env={**base_env, "PATH": f"{trunc_bin}:{os.environ.get('PATH', '')}"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert trunc_res.returncode != 0
    assert (trunc_dir / "install.sh").read_text(encoding="utf-8") == original_saved_install

    # 5. Rollback preserves restrictive permissions (0600) on a user-managed override.
    custom_ovr_dir = tmp_path / "custom-override-install"
    (custom_ovr_dir / "scripts").mkdir(parents=True)
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", custom_ovr_dir / "docker-compose.yml")
    (custom_ovr_dir / ".env").write_text("RELAYTV_MODE=headless\nRELAYTV_CEC_ENABLED=0\n")
    custom_ovr = custom_ovr_dir / "docker-compose.override.yml"
    custom_ovr.write_text("services:\n  relaytv:\n    environment:\n      SECRET_KEY: keep\n")
    custom_ovr.chmod(0o600)
    ovr_fail = subprocess.run(
        ["bash", str(ROOT_DIR / "install.sh"), "--update", "--no-pull", "--no-start"],
        cwd=custom_ovr_dir,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        timeout=15,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ovr_fail.returncode != 0
    assert "Update failed during runtime configuration; rolling back" in ovr_fail.stderr
    assert stat.S_IMODE(custom_ovr.stat().st_mode) == 0o600

    # 6. Git worktrees (.git file) and single-line `build: .` compose files are rejected without --force.
    wt_dir = tmp_path / "git-worktree"
    wt_dir.mkdir()
    (wt_dir / ".git").write_text("gitdir: /tmp/somewhere/.git/worktrees/wt\n", encoding="utf-8")
    wt_res = subprocess.run(
        ["bash", str(ROOT_DIR / "install.sh"), "--dir", str(wt_dir), "--yes", "--no-pull", "--no-start"],
        cwd=tmp_path,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert wt_res.returncode != 0
    assert "looks like a source checkout" in wt_res.stderr

    inline_build_dir = tmp_path / "inline-build"
    inline_build_dir.mkdir()
    (inline_build_dir / "docker-compose.yml").write_text(
        "services:\n  relaytv:\n    build: .\n",
        encoding="utf-8",
    )
    ib_res = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(inline_build_dir),
            "--yes",
            "--no-pull",
            "--no-start",
        ],
        cwd=tmp_path,
        env=base_env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert ib_res.returncode != 0
    assert "source-build compose file" in ib_res.stderr


def _extract_fn(script_path: Path, func_name: str) -> str:
    text = script_path.read_text(encoding="utf-8")
    marker = f"{func_name}() {{"
    start = text.index(marker)
    depth = 0
    for idx in range(start, len(text)):
        ch = text[idx]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : idx + 1]
    raise AssertionError(f"Could not extract {func_name} from {script_path}")
