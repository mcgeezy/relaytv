# SPDX-License-Identifier: GPL-3.0-only
"""Relay post-live YouTube replays that mpv cannot play natively.

While YouTube processes a finished live stream (``live_status=post_live``),
the only available formats are ``http_dash_segments`` whose fragments carry
no durations. mpv's yt-dlp hook cannot build a timeline from those and
falls back to a live-edge-only URL, so playback joins seconds before the
end and dies. yt-dlp itself downloads these manifests fine, so RelayTV
relays: one yt-dlp process per selected format streams to stdout and
ffmpeg muxes them (``-c copy``, no transcode) into a matroska **spool
file** under the state dir. The ``/postlive/{token}.mkv`` route tail-follows
the growing file, so playback still starts within seconds of the resolve.

The spool file is the whole point of muxing to disk instead of a pipe:
the download runs at network speed (no realtime coupling, so fragment-URL
expiry stops mattering for all but the longest replays), and when ffmpeg
finishes it finalizes the file with duration and cues — a fully seekable
mkv. The player watches for that moment and seamlessly reloads mpv from
the local file at the current position, upgrading the session from
"progressive, no timeline" to "local file, full seeking". Until then
seeking is unavailable and duration unknown; an info toast sets the
expectation. See docs/POSTLIVE_REPLAY.md.

Sessions are capped at one (single-player appliance): creating a session
supersedes any existing one. Teardown triggers are the route reader
disconnecting, supersession, and a reaper for sessions whose reader never
attached or has detached. A completed spool (ffmpeg exit 0) outlives its
session so the in-place upgrade can attach to it; completed spools are
pruned to the most recent one, expired by the reaper, swept at startup,
and removed on shutdown. Disk, not pipe backpressure, buffers the
download; a full disk fails the mux, which degrades to EOF + queue
advance.
"""
from __future__ import annotations

import collections
import os
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

from . import config, state
from .debug import get_logger

logger = get_logger("postlive_relay")

# A reader (mpv via the /postlive route) should attach within seconds of
# session creation; the playback-start watchdog gives up at 45s. Sessions
# nobody is reading are reaped after this grace so abandoned pipelines
# never outlive playback intent.
_READERLESS_GRACE_SEC = 60.0
_REAPER_INTERVAL_SEC = 5.0
_TERMINATE_WAIT_SEC = 3.0
_STDERR_TAIL_LINES = 30
# A completed spool only matters while its replay could still be watched;
# YouTube's own processing finishes well inside this window anyway.
_SPOOL_TTL_SEC = 6 * 3600.0
_SPOOL_POLL_SEC = 0.05

_LOCK = threading.Lock()
_SESSIONS: dict[str, "RelaySession"] = {}
# token -> (spool path, completion timestamp) for sessions whose mux
# finished cleanly; kept so the player can upgrade onto the local file
# after the session itself has closed.
_COMPLETED_SPOOLS: dict[str, tuple[str, float]] = {}
_REAPER_STARTED = False


class RelayError(Exception):
    """Session creation failed; callers fall back to the skip+toast path."""


def relay_enabled() -> bool:
    return config.env_bool("RELAYTV_POSTLIVE_RELAY", True)


def _spool_root() -> str:
    return os.path.join(state.STATE_DIR, "cache", "postlive")


@dataclass
class RelaySession:
    token: str
    page_url: str
    video_format: str
    audio_format: str | None
    ytdlp_procs: list[subprocess.Popen]
    ffmpeg_proc: subprocess.Popen
    created_at: float
    spool_path: str = ""
    workdirs: tuple[str, ...] = ()
    reader_attached: bool = False
    reader_detached: bool = False
    closed: bool = False
    close_reason: str = ""
    stderr_tails: dict[str, collections.deque] = field(default_factory=dict)


def split_format_expression(fmt: str) -> tuple[str, str | None]:
    """Split a yt-dlp merge expression into per-process selections.

    yt-dlp cannot merge two formats to stdout, so ``bestvideo[...]+bestaudio``
    becomes one video and one audio download. The split is the first ``+`` at
    bracket depth zero; everything after it (including ``/`` fallbacks like
    ``bestaudio/best``) stays a single audio selection. No top-level ``+``
    means a single download.

    An empty expression means the resolver won without ``-f`` — yt-dlp's
    default ``bv*+ba/b`` — so it splits to video+audio downloads. It must not
    map to ``best``: that selects a muxed format, and post_live videos only
    serve adaptive (split) formats, so ``-f best`` fails outright.
    """
    text = (fmt or "").strip()
    if not text:
        return "bv*", "ba"
    depth = 0
    for idx, ch in enumerate(text):
        if ch in "[(":
            depth += 1
        elif ch in "])":
            depth = max(0, depth - 1)
        elif ch == "+" and depth == 0:
            video = text[:idx].strip()
            audio = text[idx + 1 :].strip()
            if video and audio:
                return video, audio
            break
    return text, None


def _ytdlp_base_args(ytdlp_args) -> list[str]:
    args = [str(a) for a in (ytdlp_args or []) if str(a).strip()]
    if not args or args[0].startswith("-"):
        args = ["yt-dlp", *args]
    return _absolutize_path_args(args)


# yt-dlp options whose values are filesystem paths. The relay children run
# from per-session temp workdirs (fragment staging needs a writable cwd), so
# a relative path that resolved fine from the server's cwd — e.g. an
# operator's RELAYTV_YTDLP_COOKIES=cookies.txt — would silently miss there.
_PATH_ARG_FLAGS = (
    "--cookies",
    "--cache-dir",
    "--config-location",
    "--config-locations",
    "--netrc-location",
    "--download-archive",
)


def _absolutize_path_args(args: list[str]) -> list[str]:
    out: list[str] = []
    expect_path = False
    for token in args:
        if expect_path:
            out.append(os.path.abspath(token))
            expect_path = False
            continue
        if token in _PATH_ARG_FLAGS:
            expect_path = True
            out.append(token)
            continue
        flag, sep, value = token.partition("=")
        if sep and flag in _PATH_ARG_FLAGS and value:
            out.append(f"{flag}={os.path.abspath(value)}")
            continue
        out.append(token)
    return out


def _drain_stderr(name: str, proc: subprocess.Popen, tail: collections.deque) -> None:
    """Keep the pipeline's stderr pipes from filling (a full pipe blocks the
    writer and deadlocks the whole relay); retain a tail for diagnostics."""
    stream = proc.stderr
    if stream is None:
        return
    try:
        for line in iter(stream.readline, b""):
            text = line.decode("utf-8", "replace").strip()
            if text:
                tail.append(text)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass


def _start_stderr_drain(session: RelaySession, name: str, proc: subprocess.Popen) -> None:
    tail: collections.deque = collections.deque(maxlen=_STDERR_TAIL_LINES)
    session.stderr_tails[name] = tail
    threading.Thread(
        target=_drain_stderr,
        args=(name, proc, tail),
        name=f"relaytv-postlive-stderr-{name}",
        daemon=True,
    ).start()


def create_session(page_url: str, ytdl_format: str, ytdlp_args=()) -> RelaySession:
    """Spawn the yt-dlp → ffmpeg pipeline for one post-live replay.

    ``ytdlp_args`` is the resolver's winning base argv
    (``ResolvedStreams.ytdlp_args``) so the relay runs the exact strategy
    that produced the resolve. Raises RelayError on any spawn failure with
    everything already spawned torn back down.
    """
    if not relay_enabled():
        raise RelayError("post-live relay is disabled (RELAYTV_POSTLIVE_RELAY=0)")
    url = str(page_url or "").strip()
    if not url:
        raise RelayError("post-live relay needs a page URL")

    video_fmt, audio_fmt = split_format_expression(ytdl_format)
    base = _ytdlp_base_args(ytdlp_args)
    token = secrets.token_urlsafe(16)

    ytdlp_procs: list[subprocess.Popen] = []
    ffmpeg_proc: subprocess.Popen | None = None
    workdirs: list[str] = []
    try:
        os.makedirs(_spool_root(), exist_ok=True)
        spool_path = os.path.join(_spool_root(), f"{token}.mkv")
        for fmt in [video_fmt] + ([audio_fmt] if audio_fmt else []):
            # Post-live formats are dash fragments; yt-dlp stages each
            # fragment as a .part file in its cwd even when writing to
            # stdout, and the server's own cwd may be read-only. One dir
            # per child — they would collide on the same '--FragN.part'
            # names in a shared one.
            workdir = tempfile.mkdtemp(prefix=f"postlive-{token}-")
            workdirs.append(workdir)
            ytdlp_procs.append(
                subprocess.Popen(
                    [*base, "-f", fmt, "--no-progress", "-o", "-", url],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    cwd=workdir,
                )
            )
        input_fds = [p.stdout.fileno() for p in ytdlp_procs]
        ffmpeg_cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error"]
        for fd in input_fds:
            ffmpeg_cmd += ["-i", f"pipe:{fd}"]
        # Mux to the spool file: ffmpeg finalizes it (duration + cues) on
        # clean exit, which is what makes the seek upgrade possible.
        ffmpeg_cmd += ["-c", "copy", "-f", "matroska", spool_path]
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            pass_fds=input_fds,
        )
    except Exception as exc:
        for proc in ytdlp_procs + ([ffmpeg_proc] if ffmpeg_proc else []):
            _end_process(proc)
        _remove_workdirs(workdirs)
        raise RelayError(f"post-live relay pipeline failed to start: {exc}") from exc

    # ffmpeg owns inherited copies of the yt-dlp stdout read ends; drop ours
    # so the pipeline's fds die with its processes.
    for proc in ytdlp_procs:
        try:
            proc.stdout.close()
        except Exception:
            pass

    # Single-player appliance: at most one session, and a new play
    # supersedes any completed spool held for an upgrade. Superseding only
    # AFTER the new pipeline spawned means a spawn failure leaves whatever
    # is currently playing untouched (restart-in-place relies on this).
    with _LOCK:
        stale_tokens = list(_SESSIONS.keys())
    for stale_token in stale_tokens:
        close_session(stale_token, reason="superseded")
    _prune_completed_spools(keep=0)

    session = RelaySession(
        token=token,
        page_url=url,
        video_format=video_fmt,
        audio_format=audio_fmt,
        ytdlp_procs=ytdlp_procs,
        ffmpeg_proc=ffmpeg_proc,
        created_at=time.time(),
        spool_path=spool_path,
        workdirs=tuple(workdirs),
    )
    for idx, proc in enumerate(ytdlp_procs):
        _start_stderr_drain(session, f"ytdlp{idx}", proc)
    _start_stderr_drain(session, "ffmpeg", ffmpeg_proc)

    with _LOCK:
        _SESSIONS[token] = session
    _ensure_reaper()
    logger.info(
        "postlive_relay_session_created token=%s video_format=%s audio_format=%s url=%s",
        token,
        video_fmt,
        audio_fmt or "",
        url,
    )
    return session


def get_session(token: str) -> RelaySession | None:
    with _LOCK:
        return _SESSIONS.get(str(token or ""))


def relay_url(token: str) -> str:
    return f"http://127.0.0.1:{config.server_port()}/postlive/{token}.mkv"


def _mux_finished(session: RelaySession) -> bool:
    try:
        return session.ffmpeg_proc.poll() == 0
    except Exception:
        return False


def spool_ready_path(token: str) -> str | None:
    """The finalized, seekable spool file for a token, or None.

    Ready means ffmpeg exited cleanly (0): natural completion finalizes the
    matroska with duration and cues. A terminated mux never reports 0, so a
    torn-down session can't present a truncated file as seekable. Consults
    live sessions and the completed-spool registry, so the answer survives
    the session's own close (reader detach races the upgrade).
    """
    key = str(token or "")
    with _LOCK:
        session = _SESSIONS.get(key)
        completed = _COMPLETED_SPOOLS.get(key)
    if session is not None and _mux_finished(session) and os.path.exists(session.spool_path):
        return session.spool_path
    if completed and os.path.exists(completed[0]):
        return completed[0]
    return None


def iter_stream(token: str, chunk_size: int = 65536):
    """Yield muxed output for the route; exactly one reader per session.

    Tail-follows the growing spool file: yields what exists, waits for more
    while the mux is running, and drains the remainder once it exits. One
    reader per session — the token is single-use and a reconnect closes the
    session rather than replaying it.
    """
    with _LOCK:
        session = _SESSIONS.get(str(token or ""))
        if session is None or session.closed or session.reader_attached:
            return None
        session.reader_attached = True

    def gen():
        try:
            # ffmpeg creates the spool only after both input headers parse,
            # so the file can lag the reader by a moment.
            while not os.path.exists(session.spool_path):
                if session.closed or session.ffmpeg_proc.poll() is not None:
                    return
                time.sleep(_SPOOL_POLL_SEC)
            with open(session.spool_path, "rb") as spool:
                while True:
                    chunk = spool.read(chunk_size)
                    if chunk:
                        yield chunk
                        continue
                    if session.closed:
                        return
                    if session.ffmpeg_proc.poll() is not None:
                        # The mux exited; drain whatever it flushed last.
                        tail = spool.read(chunk_size)
                        while tail:
                            yield tail
                            tail = spool.read(chunk_size)
                        return
                    time.sleep(_SPOOL_POLL_SEC)
        finally:
            session.reader_detached = True
            close_session(session.token, reason="reader closed")

    return gen()


def _end_process(proc: subprocess.Popen | None) -> None:
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=_TERMINATE_WAIT_SEC)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def _remove_workdirs(workdirs) -> None:
    for workdir in workdirs:
        shutil.rmtree(workdir, ignore_errors=True)


def _remove_file(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    except Exception:
        logger.warning("postlive_relay_spool_remove_failed path=%s", path)


def _prune_completed_spools(*, keep: int, max_age_sec: float | None = None) -> None:
    """Drop completed spools beyond the ``keep`` newest; ``max_age_sec``
    additionally expires the kept ones."""
    now = time.time()
    with _LOCK:
        entries = sorted(_COMPLETED_SPOOLS.items(), key=lambda kv: kv[1][1], reverse=True)
        doomed = list(entries[keep:])
        if max_age_sec is not None:
            doomed += [
                (token, meta)
                for token, meta in entries[:keep]
                if now - meta[1] > max_age_sec
            ]
        for token, _meta in doomed:
            _COMPLETED_SPOOLS.pop(token, None)
    for _token, (path, _ts) in doomed:
        _remove_file(path)


def close_session(token: str, *, reason: str) -> None:
    with _LOCK:
        session = _SESSIONS.pop(str(token or ""), None)
    if session is None or session.closed:
        return
    session.closed = True
    session.close_reason = reason
    # ffmpeg first so its inherited read fds close and the yt-dlp children
    # see EPIPE instead of blocking on a full pipe while we wait on them.
    _end_process(session.ffmpeg_proc)
    for proc in session.ytdlp_procs:
        _end_process(proc)
    _remove_workdirs(session.workdirs)
    spool_kept = False
    if _mux_finished(session) and os.path.exists(session.spool_path):
        # Finalized spool: keep it (newest only) so the player's seek
        # upgrade can attach even though the session is gone.
        with _LOCK:
            _COMPLETED_SPOOLS[session.token] = (session.spool_path, time.time())
        _prune_completed_spools(keep=1)
        spool_kept = True
    else:
        _remove_file(session.spool_path)
    tails = {
        name: " | ".join(list(tail)[-3:])
        for name, tail in session.stderr_tails.items()
        if tail
    }
    logger.info(
        "postlive_relay_session_closed token=%s reason=%s spool_kept=%s stderr_tails=%s",
        session.token,
        reason,
        spool_kept,
        tails or "{}",
    )


def close_all(*, reason: str = "shutdown") -> None:
    with _LOCK:
        tokens = list(_SESSIONS.keys())
    for token in tokens:
        close_session(token, reason=reason)
    _prune_completed_spools(keep=0)


def sweep_spool_root() -> None:
    """Startup sweep: nothing in the spool dir predating this process is
    attachable (sessions and the completed registry are process-local)."""
    root = _spool_root()
    try:
        entries = os.listdir(root)
    except FileNotFoundError:
        return
    except Exception:
        return
    for name in entries:
        path = os.path.join(root, name)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except Exception:
            pass
    if entries:
        logger.info("postlive_relay_spool_swept count=%d", len(entries))


def _session_expired(session: RelaySession, now: float) -> str:
    """Return the reap reason for a dead-weight session, or ""."""
    if session.reader_detached:
        return "reader detached"
    if not session.reader_attached and now - session.created_at > _READERLESS_GRACE_SEC:
        return "no reader attached"
    if not session.reader_attached and session.ffmpeg_proc.poll() is not None:
        return "pipeline exited before reader attached"
    return ""


def _reaper_loop() -> None:
    while True:
        time.sleep(_REAPER_INTERVAL_SEC)
        try:
            now = time.time()
            with _LOCK:
                sessions = list(_SESSIONS.values())
            for session in sessions:
                reason = _session_expired(session, now)
                if reason:
                    close_session(session.token, reason=reason)
            _prune_completed_spools(keep=1, max_age_sec=_SPOOL_TTL_SEC)
        except Exception:
            logger.exception("postlive_relay_reaper_error")


def _ensure_reaper() -> None:
    global _REAPER_STARTED
    with _LOCK:
        if _REAPER_STARTED:
            return
        _REAPER_STARTED = True
    threading.Thread(
        target=_reaper_loop, name="relaytv-postlive-reaper", daemon=True
    ).start()
