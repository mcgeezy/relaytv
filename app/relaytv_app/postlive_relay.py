# SPDX-License-Identifier: GPL-3.0-only
"""Relay post-live YouTube replays that mpv cannot play natively.

While YouTube processes a finished live stream (``live_status=post_live``),
the only available formats are ``http_dash_segments`` whose fragments carry
no durations. mpv's yt-dlp hook cannot build a timeline from those and
falls back to a live-edge-only URL, so playback joins seconds before the
end and dies. yt-dlp itself downloads these manifests fine, so RelayTV
relays: one yt-dlp process per selected format streams to stdout, ffmpeg
muxes them (``-c copy``, no transcode) into matroska, and the
``/postlive/{token}.mkv`` route serves ffmpeg's stdout to mpv as an
ordinary progressive HTTP stream. mpv gets no timeline, so seeking is
unavailable and duration is unknown — accepted limitations while the
replay processes. See docs/POSTLIVE_REPLAY_RELAY_ROADMAP.md.

Sessions are capped at one (single-player appliance): creating a session
supersedes any existing one. Teardown triggers are the route reader
disconnecting, supersession, and a reaper for sessions whose reader never
attached or has detached. OS pipes provide backpressure end to end: when
mpv stops reading, ffmpeg's writes block, then yt-dlp's writes block.
"""
from __future__ import annotations

import collections
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field

from . import config
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

_LOCK = threading.Lock()
_SESSIONS: dict[str, "RelaySession"] = {}
_REAPER_STARTED = False


class RelayError(Exception):
    """Session creation failed; callers fall back to the skip+toast path."""


def relay_enabled() -> bool:
    return config.env_bool("RELAYTV_POSTLIVE_RELAY", True)


@dataclass
class RelaySession:
    token: str
    page_url: str
    video_format: str
    audio_format: str | None
    ytdlp_procs: list[subprocess.Popen]
    ffmpeg_proc: subprocess.Popen
    created_at: float
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
    return args


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

    # Single-player appliance: at most one pipeline at a time.
    with _LOCK:
        stale_tokens = list(_SESSIONS.keys())
    for stale_token in stale_tokens:
        close_session(stale_token, reason="superseded")

    ytdlp_procs: list[subprocess.Popen] = []
    ffmpeg_proc: subprocess.Popen | None = None
    workdirs: list[str] = []
    try:
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
        ffmpeg_cmd += ["-c", "copy", "-f", "matroska", "pipe:1"]
        ffmpeg_proc = subprocess.Popen(
            ffmpeg_cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
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

    session = RelaySession(
        token=token,
        page_url=url,
        video_format=video_fmt,
        audio_format=audio_fmt,
        ytdlp_procs=ytdlp_procs,
        ffmpeg_proc=ffmpeg_proc,
        created_at=time.time(),
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


def iter_stream(token: str, chunk_size: int = 65536):
    """Yield muxed output for the route; exactly one reader per session.

    The relay is progressive-only — bytes already yielded are gone — so a
    second attach (e.g. an mpv reconnect) cannot be served and the session
    closes with the stream.
    """
    with _LOCK:
        session = _SESSIONS.get(str(token or ""))
        if session is None or session.closed or session.reader_attached:
            return None
        session.reader_attached = True

    def gen():
        stdout = session.ffmpeg_proc.stdout
        try:
            while True:
                chunk = stdout.read(chunk_size)
                if not chunk:
                    break
                yield chunk
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
    try:
        if session.ffmpeg_proc.stdout is not None:
            session.ffmpeg_proc.stdout.close()
    except Exception:
        pass
    _remove_workdirs(session.workdirs)
    tails = {
        name: " | ".join(list(tail)[-3:])
        for name, tail in session.stderr_tails.items()
        if tail
    }
    logger.info(
        "postlive_relay_session_closed token=%s reason=%s stderr_tails=%s",
        session.token,
        reason,
        tails or "{}",
    )


def close_all(*, reason: str = "shutdown") -> None:
    with _LOCK:
        tokens = list(_SESSIONS.keys())
    for token in tokens:
        close_session(token, reason=reason)


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
