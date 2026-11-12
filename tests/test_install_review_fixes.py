# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from relaytv_app import container_entrypoint


ROOT_DIR = Path(__file__).resolve().parents[1]


def _copy_host_installer(tmp_path: Path) -> Path:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    installer = scripts_dir / "install.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "install.sh", installer)
    return installer


def _clean_installer_env(env_overrides: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key.startswith("RELAYTV_") or key in (
            "DISPLAY",
            "MPV_AUDIO_DEVICE",
            "PORT",
            "QT_QPA_PLATFORM",
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
    return env


def _run_host_installer(
    tmp_path: Path,
    args: list[str] | None = None,
    env_overrides: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    installer = _copy_host_installer(tmp_path)
    env = _clean_installer_env(env_overrides)
    return subprocess.run(
        [str(installer), *(args or ["--mode", "headless"])],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )


def _extract_bash_function(script_path: Path, func_name: str) -> str:
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


# --- Finding 1: emit_env_line & parse_dotenv_scalar quoting/round-trip -------


@pytest.mark.parametrize(
    ("raw_val", "expected_line"),
    [
        ("--osd-playing-msg=${media-title}", "KEY='--osd-playing-msg=${media-title}'"),
        ("value with spaces", "KEY='value with spaces'"),
        ("  leading", "KEY='  leading'"),
        ("trailing  ", "KEY='trailing  '"),
        ("hash#comment", "KEY='hash#comment'"),
        ("back\\slash", "KEY='back\\slash'"),
        ("back`tick`", "KEY='back`tick`'"),
        ("semi;amp&pipe|paren()", "KEY='semi;amp&pipe|paren()'"),
        ('double"quote', 'KEY=\'double"quote\''),
        ("line1\r\nline2", "KEY=line1line2"),
        (
            "--osd-playing-msg='${media-title}' # \"quote\" \\ `cmd`",
            'KEY="--osd-playing-msg=\'\\${media-title}\' # \\"quote\\" \\\\ \\`cmd\\`"',
        ),
    ],
)
def test_emit_env_line_quotes_special_characters_and_strips_newlines(
    raw_val: str, expected_line: str
) -> None:
    emit_fn = _extract_bash_function(ROOT_DIR / "scripts" / "install.sh", "emit_env_line")
    res = subprocess.run(
        ["bash", "-c", f'set -euo pipefail\n{emit_fn}\nemit_env_line KEY "$1"', "bash", raw_val],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout == expected_line + "\n"


@pytest.mark.parametrize("script_rel", ["scripts/install.sh", "install.sh", "scripts/doctor.sh"])
@pytest.mark.parametrize(
    "scalar",
    [
        "--osd-playing-msg=${media-title}",
        "--osd-playing-msg='${media-title}'",
        "it's a \"quoted\" $var with \\backslash\\ and `ticks` # comment",
        'escaped\\"quote\\$dollar\\\\slash\\`tick\'',
        "semi;colon&amp|pipe(paren) # hash",
        "  leading and trailing spaces  ",
        "plain-scalar_1.0:/path",
    ],
)
def test_parse_dotenv_scalar_round_trips_across_all_scripts(
    script_rel: str, scalar: str
) -> None:
    emit_fn = _extract_bash_function(ROOT_DIR / "scripts" / "install.sh", "emit_env_line")
    parse_fn = _extract_bash_function(ROOT_DIR / script_rel, "parse_dotenv_scalar")
    res = subprocess.run(
        [
            "bash",
            "-c",
            f'set -euo pipefail\n{emit_fn}\n{parse_fn}\n'
            'line="$(emit_env_line TEST_KEY "$1")"\n'
            'parse_dotenv_scalar "$line"',
            "bash",
            scalar,
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout == scalar


def test_installer_rerun_preserves_complex_dotenv_values_without_corruption(
    tmp_path: Path,
) -> None:
    mpv_args = "--osd-playing-msg='${media-title}' # \"quoted\" \\ `tick`"
    state_file = "/data/it's a \"state\" $file \\ `path` #1.json"

    first = _run_host_installer(
        tmp_path,
        args=["--mode", "headless"],
        env_overrides={
            "RELAYTV_QT_SHELL_MPV_ARGS": mpv_args,
            "RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE": state_file,
        },
    )
    assert first.returncode == 0, first.stderr
    first_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert (
        'RELAYTV_QT_SHELL_MPV_ARGS="--osd-playing-msg=\'\\${media-title}\' # \\"quoted\\" \\\\ \\`tick\\`"\n'
        in first_env
    )
    assert (
        'RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE="/data/it\'s a \\"state\\" \\$file \\\\ \\`path\\` #1.json"\n'
        in first_env
    )

    shutil.rmtree(tmp_path / "scripts")
    second = _run_host_installer(tmp_path, args=["--mode", "headless"])
    assert second.returncode == 0, second.stderr
    second_env = (tmp_path / ".env").read_text(encoding="utf-8")
    assert (
        'RELAYTV_QT_SHELL_MPV_ARGS="--osd-playing-msg=\'\\${media-title}\' # \\"quoted\\" \\\\ \\`tick\\`"\n'
        in second_env
    )
    assert (
        'RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE="/data/it\'s a \\"state\\" \\$file \\\\ \\`path\\` #1.json"\n'
        in second_env
    )
    assert f"RELAYTV_QT_SHELL_MPV_ARGS: {mpv_args}" in second.stdout


# --- Finding 2: DISPLAY / WAYLAND_DISPLAY / XDG_SESSION_TYPE leak ------------


def test_headless_override_explicitly_blanks_display_and_session_vars(
    tmp_path: Path,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")
    res = _run_host_installer(
        tmp_path,
        args=["--mode", "headless", "--use-shell-env"],
        env_overrides={
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_SESSION_TYPE": "wayland",
        },
    )
    assert res.returncode == 0, res.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")

    # .env must continue omitting DISPLAY= and WAYLAND_DISPLAY=
    assert "DISPLAY=" not in env_text
    assert "WAYLAND_DISPLAY=" not in env_text

    # Override must explicitly blank them so host desktop shell vars do not leak
    assert 'DISPLAY: ""' in override
    assert 'WAYLAND_DISPLAY: ""' in override
    assert 'XDG_SESSION_TYPE: ""' in override
    assert 'RELAYTV_HOST_SESSION_TYPE: ""' in override

    if shutil.which("docker") is not None:
        env = os.environ.copy()
        env.update(
            {
                "DISPLAY": ":0",
                "WAYLAND_DISPLAY": "wayland-0",
                "XDG_SESSION_TYPE": "wayland",
            }
        )
        cfg = subprocess.run(
            ["docker", "compose", "config"],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert cfg.returncode == 0, cfg.stderr
        assert 'DISPLAY: ""' in cfg.stdout
        assert 'WAYLAND_DISPLAY: ""' in cfg.stdout
        assert 'XDG_SESSION_TYPE: ""' in cfg.stdout
        assert 'RELAYTV_HOST_SESSION_TYPE: ""' in cfg.stdout


def test_drm_override_blanks_display_and_stays_valid_with_nvidia(
    tmp_path: Path,
) -> None:
    shutil.copy2(ROOT_DIR / "docker-compose.release.yml", tmp_path / "docker-compose.yml")
    installer = _copy_host_installer(tmp_path)
    text = installer.read_text(encoding="utf-8")
    text = text.replace(
        'DRM_CONNECTOR_DETECTED="$(detect_connected_drm_connector)"',
        'DRM_CONNECTOR_DETECTED="HDMI-A-1"',
        1,
    ).replace(
        'NVIDIA_DEVICE_DETECTED="$(detect_nvidia_device)"',
        'NVIDIA_DEVICE_DETECTED="1"',
        1,
    )
    installer.write_text(text, encoding="utf-8")

    env = _clean_installer_env(
        {
            "DISPLAY": ":0",
            "WAYLAND_DISPLAY": "wayland-0",
            "XDG_SESSION_TYPE": "wayland",
            "RELAYTV_NVIDIA_DOCKER_TOOLKIT": "1",
        }
    )
    res = subprocess.run(
        [str(installer), "--mode", "drm", "--use-shell-env"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    env_text = (tmp_path / ".env").read_text(encoding="utf-8")
    override = (tmp_path / "docker-compose.override.yml").read_text(encoding="utf-8")

    assert "DISPLAY=" not in env_text
    assert "WAYLAND_DISPLAY=" not in env_text
    assert "XDG_SESSION_TYPE=tty\n" in env_text
    assert 'DISPLAY: ""' in override
    assert 'WAYLAND_DISPLAY: ""' in override
    assert 'XDG_SESSION_TYPE: ""' not in override
    assert "gpus: all" in override
    assert override.count("environment:") == 1

    if shutil.which("docker") is not None:
        cfg = subprocess.run(
            ["docker", "compose", "config"],
            cwd=tmp_path,
            env={**env, "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0"},
            check=False,
            capture_output=True,
            text=True,
        )
        assert cfg.returncode == 0, cfg.stderr
        assert 'DISPLAY: ""' in cfg.stdout
        assert 'WAYLAND_DISPLAY: ""' in cfg.stdout


def test_entrypoint_clears_inherited_display_vars_in_drm_and_non_remote_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. DRM mode clears DISPLAY and WAYLAND_DISPLAY, preserves XDG_SESSION_TYPE=tty
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "tty")
    drm_env = {
        "RELAYTV_MODE": "drm",
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_SESSION_TYPE": "tty",
    }
    container_entrypoint._normalize_runtime_defaults(drm_env)
    assert "DISPLAY" not in drm_env
    assert "WAYLAND_DISPLAY" not in drm_env
    assert "DISPLAY" not in os.environ
    assert "WAYLAND_DISPLAY" not in os.environ
    assert drm_env["XDG_SESSION_TYPE"] == "tty"

    # 2. Headless mode with RELAYTV_HEADLESS_REMOTE_ENABLED=0 clears all four
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    monkeypatch.setenv("RELAYTV_HOST_SESSION_TYPE", "wayland")
    headless_env = {
        "RELAYTV_MODE": "headless",
        "RELAYTV_HEADLESS_REMOTE_ENABLED": "0",
        "DISPLAY": ":0",
        "WAYLAND_DISPLAY": "wayland-0",
        "XDG_SESSION_TYPE": "wayland",
        "RELAYTV_HOST_SESSION_TYPE": "wayland",
    }
    container_entrypoint._normalize_runtime_defaults(headless_env)
    for key in ("DISPLAY", "WAYLAND_DISPLAY", "XDG_SESSION_TYPE", "RELAYTV_HOST_SESSION_TYPE"):
        assert key not in headless_env
        assert key not in os.environ


# --- Finding 3: detect_from_session_leader SIGPIPE under pipefail ------------


def test_detect_from_session_leader_handles_large_proc_environ_without_sigpipe(
    tmp_path: Path,
) -> None:
    child_env = os.environ.copy()
    child_env["DISPLAY"] = ":42"
    child_env["WAYLAND_DISPLAY"] = "wayland-9"
    child_env["XDG_RUNTIME_DIR"] = "/run/user/1000"
    for i in range(100):
        child_env[f"ZZZ_PADDING_{i:03d}"] = "x" * 4096

    sleeper = subprocess.Popen(["sleep", "10"], env=child_env)
    try:
        user = pwd.getpwuid(os.getuid()).pw_name
        uid = os.getuid()
        fake_bin = tmp_path / "fake-bin"
        fake_bin.mkdir()
        loginctl = fake_bin / "loginctl"
        loginctl.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "list-sessions" ]; then\n'
            f'  printf "1 {uid} {user} seat0 -\\n"\n'
            "  exit 0\n"
            "fi\n"
            'if [ "$1" = "show-session" ]; then\n'
            '  case "$*" in\n'
            '    *"-p Type"*) printf "x11\\n" ;;\n'
            '    *"-p Active"*) printf "yes\\n" ;;\n'
            '    *"-p Display"*) printf "\\n" ;;\n'
            f'    *"-p Leader"*) printf "{sleeper.pid}\\n" ;;\n'
            "  esac\n"
            "fi\n",
            encoding="utf-8",
        )
        loginctl.chmod(0o755)

        systemctl = fake_bin / "systemctl"
        systemctl.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        systemctl.chmod(0o755)

        res = _run_host_installer(
            tmp_path,
            args=["--mode", "x11", "--clean-autodetect"],
            env_overrides={"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
        )
        assert res.returncode == 0, f"rc={res.returncode} stderr={res.stderr}"
        env_text = (tmp_path / ".env").read_text(encoding="utf-8")
        assert "DISPLAY=:42\n" in env_text
    finally:
        sleeper.terminate()
        sleeper.wait()


# --- Finding 4: scripts/doctor.sh octal port crash & compose config leak -----


def test_doctor_normalizes_leading_zero_port_and_suppresses_compose_config_stdout(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    doctor = scripts_dir / "doctor.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "doctor.sh", doctor)

    secret_val = "super-secret-compose-interpolated-token"
    (tmp_path / ".env").write_text(
        f"RELAYTV_API_TOKEN={secret_val}\n"
        "RELAYTV_PORT=08787\n",
        encoding="utf-8",
    )
    (tmp_path / ".env").chmod(0o600)

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/bin/sh\nprintf '200'\n", encoding="utf-8")
    fake_curl.chmod(0o755)

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "compose" ] && [ "$2" = "version" ]; then\n'
        "  printf 'Docker Compose version v2.29.0\\n'\n"
        "  exit 0\n"
        "fi\n"
        'if [ "$1" = "compose" ] && [ "$2" = "config" ]; then\n'
        f"  printf 'RELAYTV_API_TOKEN: {secret_val}\\n'\n"
        "  printf 'validating docker-compose.yml: invalid section\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env.pop("RELAYTV_PORT", None)
    env.pop("RELAYTV_BASE", None)

    res = subprocess.run(
        [str(doctor)],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert "http://127.0.0.1:8787/health" in res.stdout
    assert "http://127.0.0.1:08787/health" not in res.stdout
    assert "docker compose config FAILED" in res.stdout
    assert "validating docker-compose.yml: invalid section" in res.stderr
    assert secret_val not in res.stdout
    assert secret_val not in res.stderr


# --- Finding 5: install.sh bare semver --branch retries with v${BRANCH} ------


def test_bootstrap_bare_semver_branch_retries_with_v_prefix(tmp_path: Path) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    requests_log = tmp_path / "curl-requests.log"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/bin/bash\n"
        'url="${@: -1}"\n'
        f'printf "%s\\n" "$url" >> {shlex.quote(str(requests_log))}\n'
        'if [[ "$url" == *"/0.12.2/"* ]]; then\n'
        "  exit 22\n"
        "fi\n"
        'if [[ "$url" == *"/v0.12.2/"* ]]; then\n'
        '  rel="${url#*/v0.12.2/}"\n'
        f'  cat {shlex.quote(str(ROOT_DIR))}/"$rel"\n'
        "  exit 0\n"
        "fi\n"
        "exit 22\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    env = _clean_installer_env({"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"})
    res = subprocess.run(
        [
            "bash",
            str(ROOT_DIR / "install.sh"),
            "--dir",
            str(install_dir),
            "--branch",
            "0.12.2",
            "--yes",
            "--no-pull",
            "--no-start",
            "--mode",
            "headless",
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    logged = requests_log.read_text(encoding="utf-8").splitlines()
    assert any("/0.12.2/docker-compose.release.yml" in u for u in logged)
    assert any("/v0.12.2/docker-compose.release.yml" in u for u in logged)
    env_text = (install_dir / ".env").read_text(encoding="utf-8")
    assert "RELAYTV_IMAGE_REF=ghcr.io/mcgeezy/relaytv:0.12.2\n" in env_text


def test_bootstrap_non_semver_branch_does_not_retry_with_v_prefix(
    tmp_path: Path,
) -> None:
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    requests_log = tmp_path / "curl-requests.log"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_docker.chmod(0o755)

    fake_curl = fake_bin / "curl"
    fake_curl.write_text(
        "#!/bin/bash\n"
        'url="${@: -1}"\n'
        f'printf "%s\\n" "$url" >> {shlex.quote(str(requests_log))}\n'
        "exit 22\n",
        encoding="utf-8",
    )
    fake_curl.chmod(0o755)

    env = _clean_installer_env({"PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"})
    res = subprocess.run(
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
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode != 0
    logged = requests_log.read_text(encoding="utf-8").splitlines()
    assert not any("/vmain/" in u for u in logged)


# --- Finding 6: RELAYTV_HEADLESS_VNC_PASSWORD_FILE fail-closed ---------------


@pytest.mark.parametrize("case", ["missing", "empty", "unreadable"])
def test_start_headless_remote_fails_closed_when_vnc_password_file_unusable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    case: str,
) -> None:
    if case == "missing":
        pw_path = tmp_path / "missing.pass"
    elif case == "empty":
        pw_path = tmp_path / "empty.pass"
        pw_path.write_text("", encoding="utf-8")
    else:
        pw_path = tmp_path / "unreadable.pass"
        pw_path.write_text("secret\n", encoding="utf-8")
        pw_path.chmod(0o000)

    logged: list[str] = []
    spawned: list[list[str]] = []

    monkeypatch.setattr(container_entrypoint, "shutil_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(container_entrypoint, "_display_alive", lambda display: True)
    monkeypatch.setattr(container_entrypoint.Path, "exists", lambda self: True)
    monkeypatch.setattr(
        container_entrypoint,
        "_eprint",
        lambda *parts: logged.append(" ".join(str(p) for p in parts)),
    )
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "Popen",
        lambda cmd, **kw: spawned.append(list(cmd)) or SimpleNamespace(poll=lambda: None),
    )

    try:
        env = {
            "RELAYTV_MODE": "headless",
            "RELAYTV_HEADLESS_REMOTE_DISPLAY": ":99",
            "RELAYTV_HEADLESS_VNC_ENABLED": "1",
            "RELAYTV_HEADLESS_VNC_PASSWORD_FILE": str(pw_path),
        }
        procs = container_entrypoint._start_headless_remote(env)
        assert procs == []
        assert not any(cmd[0] == "x11vnc" for cmd in spawned)
        assert any(
            "RELAYTV_HEADLESS_VNC_PASSWORD_FILE" in msg and "refusing to start x11vnc" in msg
            for msg in logged
        )
    finally:
        if case == "unreadable" and pw_path.exists():
            pw_path.chmod(0o600)


def test_start_headless_remote_starts_x11vnc_with_valid_password_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pw_path = tmp_path / "valid.pass"
    pw_path.write_text("secret\n", encoding="utf-8")
    pw_path.chmod(0o600)

    spawned: list[list[str]] = []
    monkeypatch.setattr(container_entrypoint, "shutil_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(container_entrypoint, "_display_alive", lambda display: True)
    monkeypatch.setattr(container_entrypoint.Path, "exists", lambda self: True)
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "Popen",
        lambda cmd, **kw: spawned.append(list(cmd)) or SimpleNamespace(poll=lambda: None),
    )

    env = {
        "RELAYTV_MODE": "headless",
        "RELAYTV_HEADLESS_REMOTE_DISPLAY": ":99",
        "RELAYTV_HEADLESS_VNC_ENABLED": "1",
        "RELAYTV_HEADLESS_VNC_PASSWORD_FILE": str(pw_path),
    }
    procs = container_entrypoint._start_headless_remote(env)
    assert len(procs) == 1
    assert len(spawned) == 1
    assert spawned[0][0] == "x11vnc"
    assert "-rfbauth" in spawned[0]
    assert str(pw_path) in spawned[0]
    assert "-nopw" not in spawned[0]
