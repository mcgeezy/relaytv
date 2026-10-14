# SPDX-License-Identifier: GPL-3.0-only
"""Exercise resolver classification together with automatic queue advancement."""
import subprocess

import pytest
from fastapi import HTTPException

from relaytv_app import player, resolver, state


@pytest.fixture
def resolver_queue(monkeypatch):
    items = [
        {"url": f"https://www.youtube.com/watch?v=review{i:05}", "title": f"Queued {i}"}
        for i in range(3)
    ]
    persisted = []
    toasted = []
    calls = []
    monkeypatch.setattr(state, "QUEUE", items.copy())
    monkeypatch.setattr(state, "NOW_PLAYING", None)
    monkeypatch.setattr(state, "SESSION_STATE", "playing")
    monkeypatch.setattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0)
    monkeypatch.setattr(state, "persist_queue_payload", lambda payload: persisted.append(payload))
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **kw: None)
    monkeypatch.setattr(player, "_emit_jellyfin_stopped_from_now", lambda *a: None)
    monkeypatch.setattr(player, "_notify_unplayable_skip", lambda item: toasted.append(item))
    monkeypatch.setattr(resolver, "build_ytdlp_base_args", lambda: ["yt-dlp"])

    def play(item, **kwargs):
        calls.append(item)
        # Resolve through the real extractor path; stop before starting mpv.
        resolver.resolve_streams_ytdlp(item["url"])
        return item

    monkeypatch.setattr(player, "play_item", play)
    return items, persisted, toasted, calls


@pytest.mark.parametrize("error", [
    "ERROR: [youtube] review00000: Unable to download webpage: timed out",
    "ERROR: [youtube] review00000: Temporary failure in name resolution",
    "ERROR: [youtube] review00000: HTTP Error 429: Too Many Requests",
    "ERROR: [youtube] review00000: HTTP Error 503: Service Unavailable",
    "ERROR: [youtube] review00000: HTTP Error 404: Not Found",
    "ERROR: [youtube] review00000: HTTP Error 403: Forbidden",
    "ERROR: [youtube] review00000: Video unavailable",
    "ERROR: [youtube] review00000: Requested format is not available",
    "WARNING: [youtube] Private video\nERROR: [youtube] review00000: timed out",
])
def test_auto_next_preserves_queue_on_unclassified_resolver_failure(
    monkeypatch, resolver_queue, error,
):
    items, persisted, toasted, calls = resolver_queue
    monkeypatch.setattr(
        resolver, "run",
        lambda args, **kwargs: subprocess.CompletedProcess(args, 1, "", error),
    )

    with pytest.raises(HTTPException) as raised:
        player.advance_queue_playback(mode="auto_next")

    assert raised.value.status_code == 400
    assert not isinstance(raised.value, resolver.YouTubeUnavailableError)
    assert state.QUEUE == items
    assert persisted[-1]["queue"] == items
    assert calls == [items[0]]
    assert toasted == []


@pytest.mark.parametrize("reason", [
    "Join this channel to get access to members-only content like this video",
    "This video is available to this channel's members on level: Supporter",
    "Private video. Sign in if you've been granted access to this video",
    "This video is private",
    "This video has been removed by the uploader",
    "This video is no longer available because the associated account has been terminated",
])
def test_auto_next_skips_explicit_unavailable_reason_and_plays_next(
    monkeypatch, resolver_queue, reason,
):
    items, persisted, toasted, calls = resolver_queue

    def run(args, **kwargs):
        if args[-1] == items[0]["url"]:
            return subprocess.CompletedProcess(args, 1, "", f"ERROR: [youtube] review00000: {reason}")
        return subprocess.CompletedProcess(args, 0, "https://example.com/ready.mp4\n", "")

    monkeypatch.setattr(resolver, "run", run)
    result = player.advance_queue_playback(mode="auto_next")

    assert result["now_playing"] == items[1]
    assert result["skipped_unplayable"] == 1
    assert state.QUEUE == items[2:]
    assert persisted[-1]["queue"] == items[2:]
    assert calls == items[:2]
    assert toasted == [items[0]]
