# SPDX-License-Identifier: GPL-3.0-only
"""Settings-driven yt-dlp auto-update worker.

The container entrypoint runs one env-gated check at startup; this worker
makes the toggle a live setting (Settings -> YouTube) and keeps long-running
containers current without a restart. The pip-upgrade mechanics and the
interval gate (shared state file) live in container_entrypoint so both
callers follow one schedule.
"""
import os
import threading
import time

from . import container_entrypoint
from .config import runtime_config
from .debug import get_logger

logger = get_logger("ytdlp_update")

_WORKER_LOCK = threading.Lock()
_WORKER_STARTED = False
_RUN_LOCK = threading.Lock()


def _poll_interval_sec() -> float:
    raw = (os.getenv("RELAYTV_YTDLP_AUTO_UPDATE_POLL_SEC") or "3600").strip()
    try:
        return max(60.0, float(raw))
    except Exception:
        return 3600.0


def enabled() -> bool:
    return runtime_config.snapshot().flag("RELAYTV_YTDLP_AUTO_UPDATE", False)


def run_update_check(*, force: bool = False) -> bool:
    env = dict(os.environ)
    # The worker gate is the settings bus, not the process env; the shared
    # runner only needs the interval/timeout/state-file knobs from env.
    container_entrypoint._normalize_path_env(env)
    with _RUN_LOCK:
        return bool(container_entrypoint.run_yt_dlp_update(env, force=force))


def kick_async(*, force: bool = False) -> None:
    """Run one update check in the background (settings toggle flipped on)."""

    def _run() -> None:
        try:
            run_update_check(force=force)
        except Exception as exc:
            logger.warning("ytdlp_update_kick_failed error=%s", exc)

    threading.Thread(target=_run, daemon=True).start()


def _worker_loop() -> None:
    while True:
        try:
            if enabled():
                run_update_check(force=False)
        except Exception as exc:
            logger.warning("ytdlp_update_worker_failed error=%s", exc)
        time.sleep(_poll_interval_sec())


def start_worker() -> None:
    global _WORKER_STARTED
    with _WORKER_LOCK:
        if _WORKER_STARTED:
            return
        _WORKER_STARTED = True
    threading.Thread(target=_worker_loop, daemon=True).start()
