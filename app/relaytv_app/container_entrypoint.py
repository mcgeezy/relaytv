# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from .debug import configure_logging, get_logger, access_logging_enabled


configure_logging()
_ENTRY_LOGGER = get_logger("entrypoint")


def _eprint(*parts: object) -> None:
    _ENTRY_LOGGER.info(" ".join(str(part) for part in parts))


def _is_true(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on", "enable", "enabled")


def _wait_for_socket(path: Path, timeout_sec: float = 12.0) -> bool:
    deadline = time.monotonic() + max(0.1, float(timeout_sec))
    while time.monotonic() < deadline:
        if path.exists():
            return True
        time.sleep(0.1)
    return path.exists()


def _sync_legacy_brand_assets() -> None:
    """Ensure legacy /data/assets files exist when /data is a bind-mounted volume."""
    src_dir = Path("/app/relaytv_app/static/brand")
    dst_dir = Path("/data/assets")
    if not src_dir.is_dir():
        return
    try:
        dst_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        _eprint(f"entrypoint: failed to create {dst_dir}: {exc}")
        return

    for name in ("splash.png", "logo.svg", "banner.svg", "banner.png"):
        src = src_dir / name
        dst = dst_dir / name
        if not src.is_file():
            continue
        if dst.is_file():
            continue
        try:
            dst.write_bytes(src.read_bytes())
            _eprint(f"entrypoint: seeded legacy asset {dst}")
        except Exception as exc:
            _eprint(f"entrypoint: failed to seed legacy asset {dst}: {exc}")


def _normalize_path_env(env: dict[str, str]) -> None:
    """Ensure user-level script installs (for yt-dlp auto-update) are callable."""
    home = (env.get("HOME") or "").strip() or "/tmp"
    user_bin = str(Path(home) / ".local" / "bin")
    cur = env.get("PATH") or ""
    parts = [p for p in cur.split(":") if p]
    if user_bin not in parts:
        env["PATH"] = f"{user_bin}:{cur}" if cur else user_bin


def _host_model() -> str:
    for candidate in (Path("/proc/device-tree/model"), Path("/sys/firmware/devicetree/base/model")):
        try:
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="ignore").replace("\x00", "").strip()
        except Exception:
            continue
    return ""


def _host_profile(env: dict[str, str]) -> str:
    explicit = (env.get("RELAYTV_HOST_PROFILE") or "").strip().lower()
    if explicit:
        return explicit
    model = _host_model().lower()
    if "raspberry pi" in model:
        return "raspi"
    machine = ""
    try:
        machine = os.uname().machine.lower()
    except Exception:
        pass
    if machine in ("aarch64", "arm64") or machine.startswith(("armv6", "armv7", "armv8")):
        return "arm"
    if machine in ("x86_64", "amd64"):
        return "amd64"
    return "generic"


def _has_dri() -> bool:
    return Path("/dev/dri").exists()


def _normalize_runtime_defaults(env: dict[str, str]) -> None:
    """Fill app-policy defaults that do not need installer-generated .env rows."""
    mode = (env.get("RELAYTV_MODE") or "").strip().lower()
    qpa = (env.get("QT_QPA_PLATFORM") or "").strip().lower()
    host_profile = _host_profile(env)
    qt_shell_module = (env.get("RELAYTV_QT_SHELL_MODULE") or "relaytv_app.qt_shell_app").strip()

    if mode == "headless" and not (env.get("RELAYTV_HEADLESS_REMOTE_ENABLED") or "").strip():
        env["RELAYTV_HEADLESS_REMOTE_ENABLED"] = "1"

    if not (env.get("RELAYTV_QT_RUNTIME_MODE") or "").strip() and mode in ("wayland", "x11"):
        env["RELAYTV_QT_RUNTIME_MODE"] = "embed"

    if (
        host_profile == "raspi"
        and mode == "wayland"
        and qt_shell_module == "relaytv_app.qt_shell_app"
        and (qpa in ("xcb", "x11") or qpa.startswith("xcb:"))
        and _has_dri()
    ):
        env.setdefault("RELAYTV_QT_SHELL_MPV_ARGS", "--gpu-api=opengl --opengl-es=yes")


def _parse_float_env(env: dict[str, str], name: str, default: float) -> float:
    try:
        return float((env.get(name) or "").strip())
    except Exception:
        return float(default)


def _read_json_file(path: Path) -> dict:
    try:
        if not path.is_file():
            return {}
        text = path.read_text(encoding="utf-8")
        obj = json.loads(text)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass
    return {}


def _write_json_file(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, separators=(",", ":"), ensure_ascii=True), encoding="utf-8")
    except Exception as exc:
        _eprint(f"entrypoint: failed to write {path}: {exc}")


def _yt_dlp_version(env: dict[str, str]) -> str:
    try:
        p = subprocess.run(
            ["yt-dlp", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    return (p.stdout or "").strip().splitlines()[0] if (p.stdout or "").strip() else ""


def _yt_dlp_auto_update(env: dict[str, str]) -> None:
    if not _is_true(env.get("RELAYTV_YTDLP_AUTO_UPDATE"), False):
        return

    interval_hours = max(0.0, _parse_float_env(env, "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS", 24.0))
    timeout_sec = max(10.0, _parse_float_env(env, "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC", 180.0))
    state_path_raw = (env.get("RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE") or "/data/.relaytv-ytdlp-update.json").strip()
    state_path = Path(state_path_raw)
    if not state_path.is_absolute():
        state_path = Path("/data") / state_path

    now = float(time.time())
    state = _read_json_file(state_path)
    last_ts = float(state.get("last_check_ts") or 0.0)
    next_due_ts = last_ts + (interval_hours * 3600.0)
    if interval_hours > 0 and last_ts > 0 and now < next_due_ts:
        _eprint(
            f"entrypoint: yt-dlp auto-update skipped (next check in {int(next_due_ts - now)}s)"
        )
        return

    before = _yt_dlp_version(env)
    _eprint(f"entrypoint: yt-dlp auto-update check start (current={before or 'unknown'})")

    rc = -1
    err = ""
    try:
        p = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--user",
                "--upgrade",
                "--no-cache-dir",
                "yt-dlp",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            env=env,
        )
        rc = int(p.returncode)
        if p.returncode != 0:
            err = (p.stderr or p.stdout or "").strip()[:600]
    except Exception as exc:
        err = str(exc)

    after = _yt_dlp_version(env)
    changed = bool(before and after and before != after)
    ok = (rc == 0)
    _write_json_file(
        state_path,
        {
            "last_check_ts": now,
            "ok": ok,
            "rc": rc,
            "before_version": before,
            "after_version": after,
            "updated": changed,
            "error": err,
        },
    )
    if ok:
        _eprint(
            f"entrypoint: yt-dlp auto-update done (before={before or 'unknown'} after={after or 'unknown'} updated={1 if changed else 0})"
        )
    else:
        _eprint(
            f"entrypoint: yt-dlp auto-update failed (before={before or 'unknown'} rc={rc} error={err or 'unknown'})"
        )


def _display_alive(display: str) -> bool:
    if not display:
        return False
    if not shutil_which("xdpyinfo"):
        return False
    try:
        rc = subprocess.run(
            ["xdpyinfo", "-display", display],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        ).returncode
    except Exception:
        return False
    return rc == 0


def shutil_which(name: str) -> str | None:
    for base in os.getenv("PATH", "").split(":"):
        if not base:
            continue
        cand = Path(base) / name
        try:
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
        except Exception:
            continue
    return None


def _start_headless_remote(env: dict[str, str]) -> list[subprocess.Popen]:
    if not shutil_which("Xvfb"):
        raise RuntimeError("RELAYTV_HEADLESS_REMOTE_ENABLED=1 but Xvfb is not installed")
    if not shutil_which("x11vnc"):
        raise RuntimeError("RELAYTV_HEADLESS_REMOTE_ENABLED=1 but x11vnc is not installed")

    display = (env.get("RELAYTV_HEADLESS_REMOTE_DISPLAY") or env.get("DISPLAY") or ":99").strip()
    env["DISPLAY"] = display
    env.setdefault("XDG_SESSION_TYPE", "x11")
    relay_mode = (env.get("RELAYTV_MODE") or "headless-remote").strip().lower()
    if relay_mode in ("", "headless"):
        env["RELAYTV_MODE"] = "headless-remote"
    qpa = (env.get("QT_QPA_PLATFORM") or "").strip().lower()
    if qpa in ("", "offscreen", "minimal", "vnc"):
        env["QT_QPA_PLATFORM"] = "xcb"

    if _is_true(env.get("RELAYTV_HEADLESS_REMOTE_SOFTWARE"), True):
        env.setdefault("LIBGL_ALWAYS_SOFTWARE", "1")
        env.setdefault("QT_QUICK_BACKEND", "software")
        env.setdefault("QT_OPENGL", "software")

    if not display.startswith(":"):
        raise RuntimeError(f"Unsupported DISPLAY format for headless remote: {display}")
    display_num = display[1:].split(".", 1)[0].strip()
    if not display_num.isdigit():
        raise RuntimeError(f"Unsupported DISPLAY format for headless remote: {display}")
    socket_path = Path(f"/tmp/.X11-unix/X{display_num}")

    procs: list[subprocess.Popen] = []
    if socket_path.exists() and not _display_alive(display):
        _eprint(f"entrypoint: stale X socket at {socket_path}; restarting Xvfb")
        try:
            socket_path.unlink()
        except Exception:
            pass

    if (not socket_path.exists()) or (not _display_alive(display)):
        screen = (env.get("RELAYTV_HEADLESS_REMOTE_RESOLUTION") or "1920x1080x24").strip()
        _eprint(f"entrypoint: starting Xvfb display={display} screen={screen}")
        xvfb_log = open("/tmp/xvfb.log", "ab")
        xvfb = subprocess.Popen(
            ["Xvfb", display, "-screen", "0", screen, "-ac", "+extension", "GLX", "+render", "-noreset"],
            stdout=xvfb_log,
            stderr=subprocess.STDOUT,
            env=env,
        )
        procs.append(xvfb)
        if not _wait_for_socket(socket_path, timeout_sec=12.0):
            raise RuntimeError(f"Xvfb socket did not appear at {socket_path}")
    else:
        _eprint(f"entrypoint: reusing existing X display at {display}")

    if _is_true(env.get("RELAYTV_HEADLESS_VNC_ENABLED"), True):
        listen = (env.get("RELAYTV_HEADLESS_VNC_LISTEN") or "127.0.0.1").strip()
        port = (env.get("RELAYTV_HEADLESS_VNC_PORT") or "5900").strip()
        pass_file = (env.get("RELAYTV_HEADLESS_VNC_PASSWORD_FILE") or "").strip()

        if not pass_file:
            raw_pass = (env.get("RELAYTV_HEADLESS_VNC_PASSWORD") or "").strip()
            if raw_pass:
                pass_file = "/tmp/relaytv-x11vnc.pass"
                subprocess.run(
                    ["x11vnc", "-storepasswd", raw_pass, pass_file],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                os.chmod(pass_file, 0o600)

        cmd = [
            "x11vnc",
            "-display",
            display,
            "-rfbport",
            port,
            "-listen",
            listen,
            "-forever",
            "-shared",
            "-xkb",
            "-noxrecord",
            "-noxfixes",
            "-noxdamage",
        ]
        if pass_file and Path(pass_file).is_file():
            cmd.extend(["-rfbauth", pass_file])
        else:
            cmd.append("-nopw")
        vnc_env = dict(env)
        vnc_env.pop("WAYLAND_DISPLAY", None)
        _eprint(f"entrypoint: starting x11vnc listen={listen} port={port}")
        vnc_log = open("/tmp/x11vnc.log", "ab")
        x11vnc = subprocess.Popen(cmd, stdout=vnc_log, stderr=subprocess.STDOUT, env=vnc_env)
        procs.append(x11vnc)

    return procs


def _terminate(proc: subprocess.Popen | None) -> None:
    if proc is None:
        return
    try:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=3.0)
    except Exception:
        try:
            if proc.poll() is None:
                proc.kill()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = ["uvicorn", "relaytv_app.main:app", "--host", "0.0.0.0", "--port", "8787"]
    if args and args[0] == "uvicorn" and (not access_logging_enabled()) and "--no-access-log" not in args:
        args.append("--no-access-log")

    env = dict(os.environ)
    helper_procs: list[subprocess.Popen] = []
    main_proc: subprocess.Popen | None = None

    try:
        _normalize_path_env(env)
        _normalize_runtime_defaults(env)
        _sync_legacy_brand_assets()
        _yt_dlp_auto_update(env)
        if _is_true(env.get("RELAYTV_HEADLESS_REMOTE_ENABLED"), False):
            helper_procs = _start_headless_remote(env)
        main_proc = subprocess.Popen(args, env=env)

        def _handle_signal(signum: int, _frame) -> None:
            _terminate(main_proc)
            for proc in reversed(helper_procs):
                _terminate(proc)
            raise SystemExit(128 + int(signum))

        signal.signal(signal.SIGTERM, _handle_signal)
        signal.signal(signal.SIGINT, _handle_signal)

        return int(main_proc.wait())
    finally:
        _terminate(main_proc)
        for proc in reversed(helper_procs):
            _terminate(proc)


if __name__ == "__main__":
    raise SystemExit(main())
