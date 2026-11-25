# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import time
from types import SimpleNamespace

import pytest

from relaytv_app import container_entrypoint
from relaytv_app.realtime import OVERLAY_CHANNEL, UI_CHANNEL, realtime_hub
from relaytv_app.routes import _ui_events_sse, _x11_overlay_sse, stop_realtime_runtime


ROOT_DIR = Path(__file__).resolve().parents[1]


# --- 1. Realtime SSE subscription lifecycle & stop_realtime_runtime ----------


def test_sse_endpoints_do_not_leak_subscription_when_response_is_never_iterated() -> None:
    async def _run() -> None:
        class _FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        ui_before = realtime_hub.subscriber_count(UI_CHANNEL)
        ov_before = realtime_hub.subscriber_count(OVERLAY_CHANNEL)

        ui_resp = await _ui_events_sse(_FakeRequest())
        ov_resp = await _x11_overlay_sse()

        assert realtime_hub.subscriber_count(UI_CHANNEL) == ui_before
        assert realtime_hub.subscriber_count(OVERLAY_CHANNEL) == ov_before

        # Closing unstarted async generators cleanly does not raise
        await ui_resp.body_iterator.aclose()
        await ov_resp.body_iterator.aclose()

    asyncio.run(_run())


def test_stop_realtime_runtime_wakes_and_closes_active_sse_streams() -> None:
    async def _run() -> None:
        class _FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        ui_resp = await _ui_events_sse(_FakeRequest())
        ov_resp = await _x11_overlay_sse()
        ui_iter = ui_resp.body_iterator
        ov_iter = ov_resp.body_iterator

        first_ui = await anext(ui_iter)
        first_ov = await anext(ov_iter)
        assert "event: hello" in first_ui
        assert '"type": "hello"' in first_ov
        assert realtime_hub.subscriber_count(UI_CHANNEL) >= 1
        assert realtime_hub.subscriber_count(OVERLAY_CHANNEL) >= 1

        # Waiters blocked on subscription.get() must exit promptly when runtime stops
        ui_next = asyncio.create_task(anext(ui_iter, None))
        ov_next = asyncio.create_task(anext(ov_iter, None))
        await asyncio.sleep(0.05)

        await stop_realtime_runtime()

        ui_res, ov_res = await asyncio.wait_for(asyncio.gather(ui_next, ov_next), timeout=2.0)
        assert ui_res is None
        assert ov_res is None
        assert realtime_hub.subscriber_count(UI_CHANNEL) == 0
        assert realtime_hub.subscriber_count(OVERLAY_CHANNEL) == 0

    asyncio.run(_run())


# --- 2. container_entrypoint VNC password file, log FD close & fast exit -----


def test_start_headless_remote_uses_0600_passwdfile_without_storepasswd_argv_and_closes_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[list[str]] = []
    opened_logs: list[object] = []
    storepasswd_called = False

    orig_open = open

    def _tracking_open(file, *args, **kwargs):
        fh = orig_open(file, *args, **kwargs)
        if str(file) in ("/tmp/xvfb.log", "/tmp/x11vnc.log"):
            opened_logs.append(fh)
        return fh

    def _forbid_run(cmd, *args, **kwargs):
        nonlocal storepasswd_called
        if isinstance(cmd, list) and "-storepasswd" in cmd:
            storepasswd_called = True
        raise AssertionError(f"subprocess.run should not be called: {cmd}")

    monkeypatch.setattr("builtins.open", _tracking_open)
    monkeypatch.setattr(container_entrypoint.subprocess, "run", _forbid_run)
    monkeypatch.setattr(container_entrypoint, "shutil_which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(container_entrypoint, "_display_alive", lambda display: False)
    monkeypatch.setattr(container_entrypoint, "_wait_for_socket", lambda *a, **kw: True)
    monkeypatch.setattr(
        container_entrypoint.subprocess,
        "Popen",
        lambda cmd, **kw: spawned.append(list(cmd)) or SimpleNamespace(poll=lambda: None),
    )

    secret = "s3cr3t-vnc-pass!"
    container_entrypoint._TEMP_VNC_PASSWORD_FILES.clear()
    try:
        procs = container_entrypoint._start_headless_remote(
            {
                "RELAYTV_MODE": "headless",
                "RELAYTV_HEADLESS_REMOTE_DISPLAY": ":99",
                "RELAYTV_HEADLESS_VNC_ENABLED": "1",
                "RELAYTV_HEADLESS_VNC_PASSWORD": secret,
            }
        )
        assert len(procs) == 2
        assert storepasswd_called is False
        assert len(opened_logs) == 2
        assert all(fh.closed for fh in opened_logs), "parent log handles must be closed after Popen"

        vnc_cmd = next(cmd for cmd in spawned if cmd[0] == "x11vnc")
        assert secret not in vnc_cmd
        assert "-passwdfile" in vnc_cmd
        pw_path = Path(vnc_cmd[vnc_cmd.index("-passwdfile") + 1])
        assert pw_path.is_file()
        assert stat.S_IMODE(pw_path.stat().st_mode) == 0o600
        assert pw_path.read_text(encoding="utf-8").strip() == secret
    finally:
        while container_entrypoint._TEMP_VNC_PASSWORD_FILES:
            p = container_entrypoint._TEMP_VNC_PASSWORD_FILES.pop()
            p.unlink(missing_ok=True)


def test_wait_for_socket_aborts_early_when_process_exits(tmp_path: Path) -> None:
    missing_sock = tmp_path / "X99"
    dead_proc = SimpleNamespace(poll=lambda: 1)
    started = time.monotonic()
    ok = container_entrypoint._wait_for_socket(missing_sock, timeout_sec=10.0, proc=dead_proc)
    elapsed = time.monotonic() - started
    assert ok is False
    assert elapsed < 1.0


# --- 3. scripts/host-ops.sh safe toast-burst, .env port & curl -K - auth -----


def test_host_ops_toast_burst_safely_escapes_json_honors_env_port_and_passes_token_via_curl_k(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    host_ops = scripts_dir / "host-ops.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "host-ops.sh", host_ops)

    token = 'tok=with="quotes"&spaces'
    (tmp_path / ".env").write_text(
        'RELAYTV_PORT="9123"\n'
        f'RELAYTV_API_TOKEN="{token.replace(chr(34), chr(92) + chr(34))}"\n',
        encoding="utf-8",
    )

    pwn_marker = tmp_path / "should-not-exist"
    captured_argv = tmp_path / "docker-argv.json"
    captured_stdin = tmp_path / "docker-stdin.txt"

    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(captured_argv)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"open({str(captured_stdin)!r}, 'w').write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    for k in ("RELAYTV_PORT", "PORT", "RELAYTV_API_TOKEN", "RELAYTV_CONTAINER"):
        env.pop(k, None)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["RELAYTV_CONTAINER"] = "relaytv-test"

    tricky_text = f"Bob's \"quoted\" toast $(touch {pwn_marker})"
    res = subprocess.run(
        [
            str(host_ops),
            "toast-burst",
            "--count",
            "1",
            "--interval",
            "0",
            "--duration",
            "1.5",
            "--text",
            tricky_text,
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    assert not pwn_marker.exists(), "command substitution inside --text must never execute"

    argv = json.loads(captured_argv.read_text(encoding="utf-8"))
    stdin_text = captured_stdin.read_text(encoding="utf-8")

    # Must invoke curl directly without /bin/sh -lc, use configured port 9123, and pass -K -
    assert "/bin/sh" not in argv
    assert "http://127.0.0.1:9123/toast" in argv
    assert "-K" in argv
    assert "-" in argv
    # Secret token and user text must NOT appear in argv
    assert all(token not in arg for arg in argv)
    assert all("Bob's" not in arg for arg in argv)

    # Stdin curl config carries Authorization header and valid JSON payload
    assert f'header = "Authorization: Bearer {token.replace(chr(34), chr(92) + chr(34))}"' in stdin_text
    assert 'data-binary = "' in stdin_text


def test_host_ops_toast_burst_without_token_uses_data_binary_stdin(tmp_path: Path) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    host_ops = scripts_dir / "host-ops.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "host-ops.sh", host_ops)
    (tmp_path / ".env").write_text("PORT=9222\n", encoding="utf-8")

    captured_argv = tmp_path / "docker-argv.json"
    captured_stdin = tmp_path / "docker-stdin.txt"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({str(captured_argv)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
        f"open({str(captured_stdin)!r}, 'w').write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    env = os.environ.copy()
    for k in ("RELAYTV_PORT", "PORT", "RELAYTV_API_TOKEN", "RELAYTV_CONTAINER"):
        env.pop(k, None)
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["RELAYTV_CONTAINER"] = "relaytv-test"

    res = subprocess.run(
        [str(host_ops), "toast-burst", "--count", "1", "--interval", "0", "--text", "It's \"ok\""],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, res.stderr
    argv = json.loads(captured_argv.read_text(encoding="utf-8"))
    payload = json.loads(captured_stdin.read_text(encoding="utf-8"))
    assert "http://127.0.0.1:9222/toast" in argv
    assert "--data-binary" in argv
    assert "@-" in argv
    assert payload["text"] == 'It\'s "ok" 1/1'


# --- 4. scripts/doctor.sh sudo -n fallback & dynamic port in X11 hint --------


def test_doctor_falls_back_to_sudo_n_docker_and_uses_configured_port_in_overlay_hint(
    tmp_path: Path,
) -> None:
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    doctor = scripts_dir / "doctor.sh"
    shutil.copy2(ROOT_DIR / "scripts" / "doctor.sh", doctor)

    (tmp_path / ".env").write_text("RELAYTV_PORT=9456\n", encoding="utf-8")
    (tmp_path / ".env").chmod(0o600)

    sudo_log = tmp_path / "sudo-calls.log"
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()

    fake_curl = fake_bin / "curl"
    fake_curl.write_text("#!/bin/sh\nprintf '200'\n", encoding="utf-8")
    fake_curl.chmod(0o755)

    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "--version" ]; then printf "Docker version 27.0\\n"; exit 0; fi\n'
        'if [ "${FAKE_SUDO_ACTIVE:-0}" != "1" ]; then\n'
        "  printf 'permission denied while trying to connect to the Docker daemon socket\\n' >&2\n"
        "  exit 1\n"
        "fi\n"
        'if [ "$1" = "compose" ] && [ "$2" = "version" ]; then printf "Docker Compose version v2.29.0\\n"; exit 0; fi\n'
        'if [ "$1" = "ps" ]; then printf "  relaytv\\tUp 1h\\t9456\\n"; exit 0; fi\n'
        "exit 0\n",
        encoding="utf-8",
    )
    fake_docker.chmod(0o755)

    fake_sudo = fake_bin / "sudo"
    fake_sudo.write_text(
        "#!/bin/sh\n"
        f'printf "%s\\n" "$*" >> {shlex.quote(str(sudo_log))}\n'
        'if [ "$1" = "-n" ]; then shift; fi\n'
        'FAKE_SUDO_ACTIVE=1 exec "$@"\n',
        encoding="utf-8",
    )
    fake_sudo.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"
    env["XDG_SESSION_TYPE"] = "x11"
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
    assert "via sudo -n docker" in res.stdout
    assert "docker compose config OK" in res.stdout
    assert "http://127.0.0.1:9456/x11/overlay" in res.stdout
    assert "http://127.0.0.1:8787/x11/overlay" not in res.stdout
    sudo_calls = sudo_log.read_text(encoding="utf-8").splitlines()
    assert any(line.startswith("-n docker info") for line in sudo_calls)
    assert any(line.startswith("-n docker ps") for line in sudo_calls)


# --- 5. CI workflow hygiene --------------------------------------------------


def test_ci_workflow_gates_docker_image_on_test_scopes_push_to_main_and_checks_all_ui_js() -> None:
    ci_text = (ROOT_DIR / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert "branches: [main]" in ci_text
    assert "needs: test" in ci_text
    assert 'for f in app/relaytv_app/static/ui/*.js; do node --check "$f"; done' in ci_text
