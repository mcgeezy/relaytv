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
