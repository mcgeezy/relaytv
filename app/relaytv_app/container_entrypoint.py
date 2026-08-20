# SPDX-License-Identifier: GPL-3.0-only
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

from . import config
from .debug import configure_logging, get_logger, access_logging_enabled
from .display_credentials import refresh_display_credentials


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


def _ytdlp_update_dir(env: dict[str, str]) -> Path:
    """Where auto-updated yt-dlp lives.

    Deliberately on the data volume, not ``$HOME/.local``: ``HOME`` is ``/tmp``
    and ``/tmp`` is tmpfs, so a user-site install evaporated on every container
    recreate. The update state file already lives on ``/data``, so the old
    split meant state said "checked recently" while the binary had silently
    reverted to the image's copy — and the interval gate then suppressed a
    re-check for hours.
    """
    raw = (env.get("RELAYTV_YTDLP_UPDATE_DIR") or "").strip() or "/data/ytdlp"
    return Path(raw)


def _normalize_path_env(env: dict[str, str]) -> None:
    """Make the persisted yt-dlp install callable by the server and its workers."""
    update_dir = _ytdlp_update_dir(env)
    # PYTHONUSERBASE steers `pip install --user` here, and the same variable
    # makes the interpreter import from it, so the console script and the
    # package always come from one place.
    env["PYTHONUSERBASE"] = str(update_dir)
    user_bin = str(update_dir / "bin")
    cur = env.get("PATH") or ""
    parts = [p for p in cur.split(":") if p]
    if user_bin not in parts:
        env["PATH"] = f"{user_bin}:{cur}" if cur else user_bin


def _version_key(raw: str) -> tuple:
    """Comparable form of a yt-dlp version, for "is the image newer?" checks."""
    text = str(raw or "").strip()
    if not text:
        return ()
    try:
        from pip._vendor.packaging.version import parse as _parse  # type: ignore

        return (1, _parse(text))
    except Exception:
        pass
    parts: list[int] = []
    for chunk in text.replace("-", ".").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return (0, tuple(parts))


def _path_without(env: dict[str, str], drop: str) -> str:
    return ":".join(p for p in (env.get("PATH") or "").split(":") if p and p != drop)


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


def _yt_dlp_version(env: dict[str, str], *, path: str | None = None, user_site: bool = True) -> str:
    run_env = dict(env)
    if path is not None:
        run_env["PATH"] = path
    if not user_site:
        # Dropping the persisted bin from PATH is not enough to see the image's
        # copy: PYTHONUSERBASE still steers the import, so the console script in
        # /usr/local/bin would load the persisted package and report its version.
        run_env.pop("PYTHONUSERBASE", None)
    try:
        p = subprocess.run(
            ["yt-dlp", "--version"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=run_env,
        )
    except Exception:
        return ""
    if p.returncode != 0:
        return ""
    return (p.stdout or "").strip().splitlines()[0] if (p.stdout or "").strip() else ""


def _discard_persisted_ytdlp(update_dir: Path, reason: str) -> None:
    try:
        shutil.rmtree(update_dir)
    except FileNotFoundError:
        return
    except Exception as exc:
        _eprint(f"entrypoint: yt-dlp persisted copy could not be removed ({reason}): {exc}")
        return
    _eprint(f"entrypoint: yt-dlp persisted copy discarded ({reason})")


def _prune_persisted_ytdlp(env: dict[str, str]) -> None:
    """Drop the persisted install when it is unusable or older than the image.

    A tree on ``/data`` outlives the image around it. Two ways that goes wrong:
    the console script's shebang names an interpreter a rebuilt image no longer
    ships, or a newer image ships a yt-dlp ahead of what we once installed. In
    both cases the persisted copy is first on PATH and would win, so it has to
    be removed rather than merely ignored.
    """
    update_dir = _ytdlp_update_dir(env)
    if not (update_dir / "bin" / "yt-dlp").exists():
        return

    persisted = _yt_dlp_version(env)
    if not persisted:
        _discard_persisted_ytdlp(update_dir, "not executable")
        return

    image = _yt_dlp_version(
        env, path=_path_without(env, str(update_dir / "bin")), user_site=False
    )
    if not image:
        return
    if _version_key(image) > _version_key(persisted):
        _discard_persisted_ytdlp(update_dir, f"image {image} is newer than persisted {persisted}")


def _yt_dlp_auto_update(env: dict[str, str]) -> None:
    if not _is_true(env.get("RELAYTV_YTDLP_AUTO_UPDATE"), False):
        return
    run_yt_dlp_update(env)


def run_yt_dlp_update(env: dict[str, str], *, force: bool = False) -> bool:
    """Run one yt-dlp pip-upgrade check; returns True when the check ran ok.

    Shared by the entrypoint startup check and the backend's settings-driven
    worker (relaytv_app.ytdlp_update); the interval gate lives in the state
    file so both callers share one schedule. ``force`` bypasses the interval
    (used when the settings toggle is switched on).
    """
    interval_hours = max(0.0, _parse_float_env(env, "RELAYTV_YTDLP_AUTO_UPDATE_INTERVAL_HOURS", 6.0))
    timeout_sec = max(10.0, _parse_float_env(env, "RELAYTV_YTDLP_AUTO_UPDATE_TIMEOUT_SEC", 180.0))
    state_path_raw = (env.get("RELAYTV_YTDLP_AUTO_UPDATE_STATE_FILE") or "/data/.relaytv-ytdlp-update.json").strip()
    state_path = Path(state_path_raw)
    if not state_path.is_absolute():
        state_path = Path("/data") / state_path

    _prune_persisted_ytdlp(env)

    now = float(time.time())
    state = _read_json_file(state_path)
    last_ts = float(state.get("last_check_ts") or 0.0)
    next_due_ts = last_ts + (interval_hours * 3600.0)
    before = _yt_dlp_version(env)
    # The interval alone is not a safe gate. The state file lives on /data and
    # survives anything, so if the installed copy has moved out from under it —
    # a rebuilt image, a discarded persisted tree — "checked recently" would
    # keep us on a stale yt-dlp for hours. Trust the gate only while the
    # version we are looking at is the one the state describes.
    channel = (env.get("RELAYTV_YTDLP_UPDATE_CHANNEL") or "nightly").strip().lower()
    if channel not in ("nightly", "stable"):
        channel = "nightly"

    stale_reason = ""
    if bool(state.get("after_version")) and before != str(state.get("after_version")):
        stale_reason = f"installed={before or 'unknown'} state={state.get('after_version')}"
    elif bool(state.get("last_check_ts")) and str(state.get("channel") or "stable") != channel:
        # The recorded check answered a different question. Switching to the
        # nightly channel must not wait out an interval that was satisfied by a
        # stable-only check — that is the whole reason for switching.
        stale_reason = f"channel={channel} state_channel={state.get('channel') or 'stable'}"

    if (not force) and (not stale_reason) and interval_hours > 0 and last_ts > 0 and now < next_due_ts:
        _eprint(
            f"entrypoint: yt-dlp auto-update skipped (next check in {int(next_due_ts - now)}s)"
        )
        return False
    if stale_reason:
        _eprint(f"entrypoint: yt-dlp auto-update forced ({stale_reason})")

    _eprint(f"entrypoint: yt-dlp auto-update check start (current={before or 'unknown'})")

    def _pip_install(pre: bool) -> tuple[int, str]:
        cmd = [sys.executable, "-m", "pip", "install", "--user", "--upgrade", "--no-cache-dir"]
        if pre:
            cmd.append("--pre")
        cmd.append("yt-dlp")
        try:
            proc = subprocess.run(
                cmd, check=False, capture_output=True, text=True, timeout=timeout_sec, env=env
            )
        except Exception as exc:
            return -1, str(exc)
        if proc.returncode != 0:
            return int(proc.returncode), (proc.stderr or proc.stdout or "").strip()[:600]
        return 0, ""

    used_channel = channel
    rc, err = _pip_install(pre=(channel == "nightly"))
    if rc != 0 and channel == "nightly":
        # A broken nightly must not leave the device worse off than the stable
        # channel it would otherwise have been running.
        _eprint(f"entrypoint: yt-dlp nightly install failed (rc={rc}); falling back to stable")
        used_channel = "stable"
        rc, err = _pip_install(pre=False)

    after = _yt_dlp_version(env)
    if rc == 0 and not after:
        # Installed but not runnable: almost always a console-script shebang
        # pointing at an interpreter this image no longer ships.
        _discard_persisted_ytdlp(_ytdlp_update_dir(env), "installed copy did not run")
        after = _yt_dlp_version(env)
        rc = 1
        err = err or "installed yt-dlp did not execute; reverted to the image copy"

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
            "channel": used_channel,
            "install_dir": str(_ytdlp_update_dir(env)),
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
    return ok


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


def _default_server_args() -> list[str]:
    """The stock uvicorn command, bound to the configured port.

    The bind port and every URL the app generates (mDNS, host URLs, the
    post-live relay loopback) must come from the same source —
    ``config.server_port()`` — or `RELAYTV_PORT` would move the generated
    URLs while the server keeps listening on the old port.
    """
    return [
        "uvicorn",
        "relaytv_app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(config.server_port()),
    ]


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        args = _default_server_args()
    if args and args[0] == "uvicorn" and (not access_logging_enabled()) and "--no-access-log" not in args:
        args.append("--no-access-log")

    env = refresh_display_credentials(os.environ)
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
