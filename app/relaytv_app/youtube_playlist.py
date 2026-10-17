# SPDX-License-Identifier: GPL-3.0-only
"""Finite YouTube playlist snapshots and bounded, retry-safe queue imports."""

import json
import math
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4
from urllib.parse import parse_qs, urlencode, urlsplit

from fastapi import HTTPException

from . import playback_service, player, resolver, url_boundary
from .debug import get_logger

logger = get_logger("youtube_playlist")

MAX_VIDEOS = 1000
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
EXTRACT_TIMEOUT = 120
JOB_TTL = 3600
MAX_JOBS = 64
MAX_WORKERS = 2
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
_PLAYLIST_ID = re.compile(r"[A-Za-z0-9_-]{2,160}\Z")


def _normalize_playlist_input(raw: str) -> str:
    text = resolver.normalize_shared_url(resolver.extract_first_url(raw or ""))
    if text.lower().startswith("www."):
        text = "https://" + text
    return text


def shared_playlist_url(raw: str, mode: str = "auto") -> str | None:
    """Classify shared text without losing a playlist embedded in a video URL."""
    text = _normalize_playlist_input(raw)
    if not text:
        raise HTTPException(400, "Missing url")
    parsed = url_boundary.parse_url(text)
    if parsed is None:
        raise HTTPException(400, "Invalid url")

    query = parse_qs(parsed.query, keep_blank_values=True)
    has_list = resolver.is_youtube_url(text) and "list" in query
    has_video = resolver.youtube_id_from_url(text) not in (None, "videoseries")
    if mode == "video":
        if has_list and not has_video:
            raise HTTPException(400, "This link is a playlist; use auto or playlist mode")
        return None
    if mode == "auto" and has_list and has_video:
        list_ids = query.get("list", [])
        if len(list_ids) == 1 and list_ids[0].startswith("RD"):
            return None
    if mode == "playlist" or has_list:
        return playlist_url(text)
    return None


def playlist_url(raw: str) -> str:
    """Accept real YouTube list links; ignore the selected video and index."""
    url = resolver.validate_user_url(_normalize_playlist_input(raw))
    if not url_boundary.host_matches_any(
        resolver.url_hostname(url), ("youtube.com", "youtu.be", "youtube-nocookie.com")
    ):
        raise HTTPException(400, "Enter a YouTube playlist link")
    query = parse_qs(urlsplit(url).query)
    ids = query.get("list", [])
    if len(ids) != 1 or not _PLAYLIST_ID.fullmatch(ids[0]):
        raise HTTPException(400, "The link must contain one valid YouTube playlist ID")
    if ids[0].startswith("RD"):
        raise HTTPException(400, "YouTube mixes are dynamic; use a saved, finite playlist")
    return "https://www.youtube.com/playlist?" + urlencode({"list": ids[0]})



@dataclass
class Playlist:
    title: str
    items: list[dict]
    skipped: int


def extract_playlist(url: str) -> Playlist:
    # A separate enumeration command keeps --no-playlist in the playback path.
    # Ignore machine config so filters, archives, and playlist ranges cannot
    # silently turn "entire playlist" into a subset. Keep RelayTV's cookies/JS.
    base = [arg for arg in resolver.build_ytdlp_base_args() if arg != "--no-playlist"]
    args = [
        *base, "--ignore-config", "--flat-playlist", "--yes-playlist",
        "--dump-single-json", "--skip-download", "--no-abort-on-error",
        "--playlist-end", str(MAX_VIDEOS + 1), "--socket-timeout", "15",
        "--retries", "2", "--extractor-retries", "2", "--", url,
    ]
    # Files avoid retaining an unbounded subprocess stdout/stderr in memory.
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        try:
            result = subprocess.run(
                args, stdout=output, stderr=errors, timeout=EXTRACT_TIMEOUT, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise HTTPException(504, "Playlist lookup timed out; please try again") from exc
        except OSError as exc:
            raise HTTPException(503, "YouTube playlist lookup is unavailable") from exc
        if result.returncode:
            # Upstream stderr can contain cookie paths and URLs. Never publish it.
            raise HTTPException(
                400, "Could not read the playlist. Check the link and YouTube cookies for private lists"
            )
        if output.tell() > MAX_OUTPUT_BYTES:
            raise HTTPException(413, "Playlist metadata is too large; split the playlist")
        output.seek(0)
        try:
            data = json.load(output)
        except (ValueError, UnicodeError) as exc:
            raise HTTPException(502, "YouTube returned invalid playlist metadata") from exc
    return playlist_from_metadata(data)


def playlist_from_metadata(data: object) -> Playlist:
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise HTTPException(502, "YouTube did not return a playlist")
    entries = data["entries"]
    count = data.get("playlist_count")
    if len(entries) > MAX_VIDEOS or (
        isinstance(count, (int, float)) and count > MAX_VIDEOS
    ):
        raise HTTPException(413, f"Playlists are limited to {MAX_VIDEOS} videos; nothing was added")
    items = []
    skipped = 0
    for entry in entries:
        if not isinstance(entry, dict):
            skipped += 1
            continue
        video_id = str(entry.get("id") or "")
        title = str(entry.get("title") or "").strip()
        if not _VIDEO_ID.fullmatch(video_id) or title.lower() in {
            "[private video]", "[deleted video]",
        }:
            skipped += 1
            continue
        item = {
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "provider": "youtube",
            "title": title or f"YouTube video {video_id}",
            "thumbnail": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        }
        channel = entry.get("channel") or entry.get("uploader")
        if isinstance(channel, str) and channel.strip():
            item["channel"] = channel.strip()
        duration = entry.get("duration")
        if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
            item["duration"] = duration
        items.append(item)
    if not items:
        raise HTTPException(400, "The playlist has no available videos; nothing was added")
    return Playlist(str(data.get("title") or "YouTube playlist"), items, skipped)


@dataclass
class _Job:
    request_id: str
    url: str
    status: str = "loading"
    title: str = ""
    added: int = 0
    skipped: int = 0
    queue_length: int | None = None
    detail: str = "Reading playlist…"
    created_at: float = field(default_factory=time.time)
    action: str = "append"
    effective_action: str = "append"
    intent: int | None = None
    use_resolver: bool = True
    cec: bool = False
    preserve_current: bool = True
    legacy: bool = False
    outcome: str | None = None
    playing_url: str | None = None

    def public(self) -> dict:
        return {
            "job_id": self.request_id, "url": self.url, "status": self.status,
            "title": self.title, "added": self.added, "skipped": self.skipped,
            "queue_length": self.queue_length, "detail": self.detail,
            "expires_at": self.created_at + JOB_TTL,
            "action": self.action, "effective_action": self.effective_action,
            "outcome": self.outcome, "playing_url": self.playing_url,
        }


class PlaylistImports:
    """One-hour idempotency window; cancelled workers retain their resource slot."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, _Job] = {}
        self._workers: set[threading.Thread] = set()
        self._stop = threading.Event()

    def start(self) -> None:
        with self._lock:
            if self._stop.is_set():
                self._stop = threading.Event()

    def stop(self) -> None:
        with self._lock:
            self._stop.set()
            for job in self._jobs.values():
                if job.status in {"loading", "adding"}:
                    playback_service.retire_playlist_action(job.intent)
                    job.status = "cancelled"
                    job.detail = "Import cancelled during server shutdown; nothing was added"

    def submit(
        self, raw: str, request_id: str | None, on_complete: Callable, *,
        action: str = "append", use_resolver: bool = True, cec: bool = False,
        preserve_current: bool = True,
    ) -> dict:
        url = playlist_url(raw)
        options = (action, use_resolver, cec, preserve_current)
        with self._lock:
            now = time.time()
            self._jobs = {
                key: job for key, job in self._jobs.items()
                if job.status in {"loading", "adding"} or now < job.created_at + JOB_TTL
            }
            existing = self._jobs.get(request_id)
            if existing is not None:
                if existing.url != url or (
                    existing.action, existing.use_resolver, existing.cec, existing.preserve_current
                ) != options:
                    raise HTTPException(409, "This request ID belongs to a different playlist or action")
                return existing.public()
            if request_id is None:
                # Old phone builds send only a URL. Coalesce while in flight;
                # completed re-shares remain intentional new requests.
                for existing in self._jobs.values():
                    if existing.legacy and existing.status in {"loading", "adding"} and existing.url == url and (
                        existing.action, existing.use_resolver, existing.cec, existing.preserve_current
                    ) == options:
                        return existing.public()
            if self._stop.is_set():
                raise HTTPException(503, "Playlist imports are stopping")
            if len(self._jobs) >= MAX_JOBS or len(self._workers) >= MAX_WORKERS:
                raise HTTPException(429, "Playlist imports are busy; try again later")
            legacy = request_id is None
            request_id = request_id or uuid4().hex
            effective, intent = playback_service.reserve_playlist_action(action)
            job = _Job(
                request_id, url, action=action, effective_action=effective, intent=intent,
                use_resolver=use_resolver, cec=cec, preserve_current=preserve_current, legacy=legacy,
            )
            generation = self._stop
            worker = threading.Thread(
                target=self._run, args=(job, generation, on_complete),
                name="youtube-playlist-import", daemon=True,
            )
            self._jobs[request_id] = job
            self._workers.add(worker)
            try:
                worker.start()
            except Exception:
                self._workers.discard(worker)
                self._jobs.pop(request_id, None)
                raise
            return job.public()

    def clear_queue(self) -> None:
        # Serialize Clear with both the loading -> committing boundary and
        # failure rollback. Slow resolution never holds this lock.
        with self._lock:
            for job in self._jobs.values():
                if job.status in {"loading", "adding"}:
                    retired = playback_service.retire_playlist_action(job.intent, before_load_only=True)
                    if not retired and job.intent is not None and player.playback_intent_loading(job.intent):
                        # Loading the first video is already committed. Clear
                        # empties its tail, without orphaning its session publish.
                        continue
                    job.status = "cancelled"
                    job.detail = "Import cancelled by queue Clear"
            playback_service.clear_queue()

    def get(self, request_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(request_id)
            if job is None or (
                job.status not in {"loading", "adding"} and time.time() >= job.created_at + JOB_TTL
            ):
                raise HTTPException(404, "Import expired or the server restarted; check the queue before adding again")
            return job.public()

    def cancel(self, request_id: str) -> dict:
        with self._lock:
            job = self._jobs.get(request_id)
            if job is None:
                raise HTTPException(404, "Playlist import not found")
            if job.status == "adding":
                raise HTTPException(409, "The playlist is already being added; check its status")
            if job.status == "loading":
                playback_service.retire_playlist_action(job.intent)
                job.status = "cancelled"
                job.detail = "Import cancelled; nothing was added"
            return job.public()

    def _run(self, job: _Job, generation: threading.Event, on_complete: Callable) -> None:
        try:
            playlist = extract_playlist(job.url)
            preserved = None
            if (
                job.intent is not None
                and job.preserve_current
                and not generation.is_set()
                and job.status == "loading"
                and player.playback_intent_current(job.intent)
            ):
                preserved = playback_service.preserve_current_to_queue_front(
                    playlist.items[0], capture_only=True
                )
            with self._lock:
                # The commit boundary: cancellation and retired generations
                # cannot append after slow extraction returns.
                if generation.is_set() or job.status != "loading":
                    return
                staged = playback_service.stage_playlist(
                    playlist.items, intent=job.intent, preserved=preserved
                )
                if staged is None:
                    job.status = "cancelled"
                    job.detail = "Playlist start superseded by a newer playback command; nothing was added"
                    return
                job.status = "adding"
                job.title = playlist.title
                job.skipped = playlist.skipped
                job.detail = "Adding videos to queue…"
            result = playback_service.finish_playlist(
                playlist.items, staged, intent=job.intent, preserved=preserved,
                use_resolver=job.use_resolver, cec=job.cec, commit_lock=self._lock,
                still_current=lambda: not generation.is_set() and job.status == "adding",
            )
            with self._lock:
                if job.status == "cancelled":
                    return
                result = playback_service.refresh_playlist_result(playlist.items, result)
                job.status = "completed"
                job.added = result["added"]
                job.queue_length = result["queue_length"]
                job.outcome = result["outcome"]
                job.playing_url = result["playing_url"]
                job.detail = (
                    f"Playing first video; queued {job.added} remaining videos"
                    if job.outcome == "playing" else f"Added {job.added} videos to queue"
                )
                if job.outcome in {"playback_failed", "superseded"}:
                    job.detail += "; first video could not start" if job.outcome == "playback_failed" else "; playback superseded by a newer command"
                if job.skipped:
                    job.detail += f"; skipped {job.skipped} unavailable entries"
                if generation.is_set():
                    return
                # Serialize final publication with stop(), so retiring the
                # generation cannot race the ownership check above.
                try:
                    on_complete(job.public(), result["snapshot"])
                except Exception:
                    logger.warning("playlist_import_notification_failed")
        except Exception as exc:
            with self._lock:
                if job.status == "cancelled" or generation.is_set():
                    return
                job.status = "failed"
                job.detail = str(exc.detail) if isinstance(exc, HTTPException) else "Playlist import failed; check the queue before trying again"
                try:
                    on_complete(job.public(), [])
                except Exception:
                    logger.warning("playlist_import_notification_failed")
            logger.info("playlist_import_failed category=%s", type(exc).__name__)
        finally:
            with self._lock:
                self._workers.discard(threading.current_thread())


imports = PlaylistImports()
