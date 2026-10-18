# SPDX-License-Identifier: GPL-3.0-only
"""Media caches must be bounded and de-duplicated (F10).

The thumbnail queue was unbounded with no de-duplication, so every /status
poll re-enqueued ids for anything that had not landed yet: a history page of
items whose provider was down grew the queue without limit and re-requested
the same failing URLs forever. The yt-dlp metadata cache was an unlocked dict
with a TTL check but no eviction, so it grew for the life of the process.
"""

import queue
import threading
import time

import pytest

from relaytv_app import resolver, thumb_cache


@pytest.fixture(autouse=True)
def _clean_caches():
    thumb_cache.reset_thumb_tracking_for_tests()
    resolver.reset_ytdlp_info_cache_for_tests()
    yield
    thumb_cache.reset_thumb_tracking_for_tests()
    resolver.reset_ytdlp_info_cache_for_tests()


@pytest.fixture
def thumb_env(tmp_path, monkeypatch):
    monkeypatch.setattr(thumb_cache, "THUMB_DIR", str(tmp_path), raising=False)
    monkeypatch.setattr(thumb_cache, "start_worker", lambda: None)
    # Drain into a list instead of running the real worker.
    drained: "queue.Queue[tuple[str, str]]" = queue.Queue(maxsize=thumb_cache.THUMB_QUEUE_MAX)
    monkeypatch.setattr(thumb_cache, "_Q", drained)
    return drained


# --- thumbnail de-duplication ------------------------------------------------


def test_the_same_thumbnail_is_queued_once(thumb_env) -> None:
    item = {"thumbnail": "https://cdn.test/a.jpg"}

    for _ in range(10):
        thumb_cache.attach_local_thumbnail(dict(item))

    assert thumb_env.qsize() == 1


def test_distinct_thumbnails_are_all_queued(thumb_env) -> None:
    for index in range(5):
        thumb_cache.attach_local_thumbnail({"thumbnail": f"https://cdn.test/{index}.jpg"})

    assert thumb_env.qsize() == 5


def test_queue_saturation_drops_rather_than_grows(thumb_env) -> None:
    for index in range(thumb_cache.THUMB_QUEUE_MAX + 50):
        thumb_cache.attach_local_thumbnail({"thumbnail": f"https://cdn.test/{index}.jpg"})

    assert thumb_env.qsize() <= thumb_cache.THUMB_QUEUE_MAX


def test_a_dropped_request_can_be_offered_again(thumb_env, monkeypatch) -> None:
    """Saturation must not permanently blacklist an id."""
    monkeypatch.setattr(thumb_env, "put_nowait", lambda item: (_ for _ in ()).throw(queue.Full()))
    thumb_cache.attach_local_thumbnail({"thumbnail": "https://cdn.test/x.jpg"})

    # Not left marked in-flight, so a later poll can retry it.
    tid = thumb_cache.thumb_id("https://cdn.test/x.jpg")
    assert thumb_cache._claim_thumb(tid) is True


# --- failure backoff ---------------------------------------------------------


def test_a_failed_thumbnail_is_not_retried_immediately(thumb_env) -> None:
    url = "https://cdn.test/dead.jpg"
    tid = thumb_cache.thumb_id(url)

    thumb_cache.attach_local_thumbnail({"thumbnail": url})
    assert thumb_env.qsize() == 1
    # The worker reports the fetch failed.
    thumb_cache._release_thumb(tid, failed=True)

    for _ in range(20):
        thumb_cache.attach_local_thumbnail({"thumbnail": url})

    assert thumb_env.qsize() == 1, "a failing provider was re-requested on every poll"


def test_backoff_expires(thumb_env, monkeypatch) -> None:
    url = "https://cdn.test/slow.jpg"
    tid = thumb_cache.thumb_id(url)
    thumb_cache._release_thumb(tid, failed=True)
    assert thumb_cache._claim_thumb(tid) is False

    monkeypatch.setattr(thumb_cache, "THUMB_FAILURE_BACKOFF_SEC", 0)
    assert thumb_cache._claim_thumb(tid) is True


def test_a_success_clears_a_previous_failure(thumb_env) -> None:
    tid = thumb_cache.thumb_id("https://cdn.test/flaky.jpg")
    thumb_cache._release_thumb(tid, failed=True)
    assert thumb_cache._claim_thumb(tid) is False

    thumb_cache._release_thumb(tid, failed=False)
    assert thumb_cache._claim_thumb(tid) is True


def test_a_failed_thumbnail_commit_enters_backoff(thumb_env, monkeypatch) -> None:
    """Download success is not success when the cache file cannot be published."""
    url = "https://cdn.test/disk-full.jpg"
    tid = thumb_cache.thumb_id(url)
    monkeypatch.setattr(thumb_cache, "_download_to", lambda source, path: True)
    monkeypatch.setattr(thumb_cache, "_normalize_to_jpg", lambda source, path: False)
    monkeypatch.setattr(thumb_cache, "_commit_file", lambda source, path: False)
    monkeypatch.setattr(thumb_cache, "_prune_thumb_dir", lambda *args, **kwargs: None)
    assert thumb_cache._claim_thumb(tid) is True

    worker = threading.Thread(target=thumb_cache._worker, daemon=True)
    worker.start()
    thumb_env.put((url, tid))
    thumb_env.join()

    assert tid in thumb_cache._FAILED_AT
    assert thumb_cache._claim_thumb(tid) is False


def test_failure_map_is_bounded(thumb_env) -> None:
    for index in range(thumb_cache.THUMB_FAILURE_MAP_MAX + 200):
        thumb_cache._release_thumb(f"tid{index}", failed=True)

    assert len(thumb_cache._FAILED_AT) <= thumb_cache.THUMB_FAILURE_MAP_MAX


def test_sync_thumbnail_path_respects_failure_backoff(thumb_env, monkeypatch) -> None:
    url = "https://cdn.test/dead-sync.jpg"
    tid = thumb_cache.thumb_id(url)
    thumb_cache._remember_src(tid, url)
    thumb_cache._release_thumb(tid, failed=True)
    attempts: list[str] = []
    monkeypatch.setattr(
        thumb_cache,
        "_download_to",
        lambda source, path: attempts.append(source) or False,
    )

    assert thumb_cache.ensure_cached_sync(tid, timeout_s=0) is False
    assert thumb_cache.ensure_cached_sync(tid, timeout_s=0) is False
    assert attempts == []


def test_sync_thumbnail_path_does_not_duplicate_inflight_work(thumb_env, monkeypatch) -> None:
    url = "https://cdn.test/inflight.jpg"
    tid = thumb_cache.thumb_id(url)
    thumb_cache._remember_src(tid, url)
    assert thumb_cache._claim_thumb(tid) is True
    attempts: list[str] = []
    monkeypatch.setattr(
        thumb_cache,
        "_download_to",
        lambda source, path: attempts.append(source) or False,
    )

    assert thumb_cache.ensure_cached_sync(tid, timeout_s=0) is False
    assert attempts == []


def test_sync_thumbnail_failure_enters_backoff(thumb_env, monkeypatch) -> None:
    url = "https://cdn.test/new-failure.jpg"
    tid = thumb_cache.thumb_id(url)
    thumb_cache._remember_src(tid, url)
    monkeypatch.setattr(thumb_cache, "_download_to", lambda source, path: False)

    assert thumb_cache.ensure_cached_sync(tid, timeout_s=0) is False
    assert tid in thumb_cache._FAILED_AT
    assert thumb_cache._claim_thumb(tid) is False


# --- yt-dlp metadata cache ---------------------------------------------------


def test_metadata_cache_evicts_by_size() -> None:
    now = time.time()
    for index in range(resolver._YTDLP_INFO_CACHE_MAX + 40):
        resolver._ytdlp_info_store(f"https://x.test/{index}", now, {"title": str(index)})

    assert len(resolver._YTDLP_INFO_CACHE) <= resolver._YTDLP_INFO_CACHE_MAX


def test_metadata_cache_evicts_least_recently_used() -> None:
    now = time.time()
    for index in range(resolver._YTDLP_INFO_CACHE_MAX):
        resolver._ytdlp_info_store(f"https://x.test/{index}", now, {"title": str(index)})

    # Touch the oldest so it is no longer the eviction candidate.
    assert resolver._ytdlp_info_cached("https://x.test/0", now) is not None
    resolver._ytdlp_info_store("https://x.test/new", now, {"title": "new"})

    assert resolver._ytdlp_info_cached("https://x.test/0", now) is not None
    assert resolver._ytdlp_info_cached("https://x.test/1", now) is None


def test_metadata_cache_expires_by_ttl() -> None:
    now = time.time()
    resolver._ytdlp_info_store("https://x.test/a", now, {"title": "a"})

    assert resolver._ytdlp_info_cached("https://x.test/a", now) is not None
    stale = now + resolver._YTDLP_INFO_TTL_SEC + 1
    assert resolver._ytdlp_info_cached("https://x.test/a", stale) is None
    # And it is gone, not merely reported missing.
    assert "https://x.test/a" not in resolver._YTDLP_INFO_CACHE


def test_expired_entries_are_swept_on_write() -> None:
    """A URL looked up once and never revisited was never released."""
    old = time.time() - resolver._YTDLP_INFO_TTL_SEC - 10
    resolver._ytdlp_info_store("https://x.test/forgotten", old, {"title": "old"})
    resolver._ytdlp_info_store("https://x.test/fresh", time.time(), {"title": "new"})

    assert "https://x.test/forgotten" not in resolver._YTDLP_INFO_CACHE


def test_concurrent_lookups_run_one_subprocess(monkeypatch) -> None:
    """Duplicates used to each spawn their own yt-dlp."""
    calls: list[str] = []
    release = threading.Event()

    class _Proc:
        returncode = 0
        stdout = '{"title": "shared"}'

    def _fake_run(url, base_args, extra, timeout):
        calls.append(url)
        release.wait(5.0)
        return _Proc(), (), False

    monkeypatch.setattr(resolver, "_run_ytdlp_provider_command", _fake_run)
    monkeypatch.setattr(resolver, "build_ytdlp_base_args", lambda: ())

    results: list[object] = []

    def _lookup():
        results.append(resolver.ytdlp_info("https://x.test/same"))

    threads = [threading.Thread(target=_lookup, daemon=True) for _ in range(4)]
    for thread in threads:
        thread.start()
    time.sleep(0.2)
    release.set()
    for thread in threads:
        thread.join(timeout=10.0)

    assert len(calls) == 1, f"spawned {len(calls)} subprocesses for one URL"
    assert all(r == {"title": "shared"} for r in results), results


def test_cache_is_rechecked_before_claiming_a_new_lookup(monkeypatch) -> None:
    """An owner can finish between the optimistic read and inflight lock."""
    url = "https://x.test/completed-in-gap"
    expected = {"title": "already done"}
    original_cached = resolver._ytdlp_info_cached
    first = True

    def _complete_during_first_read(key, now):
        nonlocal first
        if first:
            first = False
            resolver._ytdlp_info_store(key, now, expected)
            return None
        return original_cached(key, now)

    subprocess_calls: list[str] = []
    monkeypatch.setattr(resolver, "_ytdlp_info_cached", _complete_during_first_read)
    monkeypatch.setattr(
        resolver,
        "_run_ytdlp_provider_command",
        lambda requested, *args, **kwargs: subprocess_calls.append(requested),
    )

    assert resolver.ytdlp_info(url) == expected
    assert subprocess_calls == []


def test_a_failed_lookup_releases_its_inflight_slot(monkeypatch) -> None:
    """A failure must not leave later callers waiting on a fetch nobody runs."""

    def _boom(*args, **kwargs):
        raise RuntimeError("yt-dlp exploded")

    monkeypatch.setattr(resolver, "_run_ytdlp_provider_command", _boom)
    monkeypatch.setattr(resolver, "build_ytdlp_base_args", lambda: ())

    assert resolver.ytdlp_info("https://x.test/boom") is None
    assert "https://x.test/boom" not in resolver._YTDLP_INFO_INFLIGHT
    # A second attempt runs rather than blocking on a phantom fetch.
    assert resolver.ytdlp_info("https://x.test/boom") is None


def test_ytdlp_metadata_lookups_pass_no_check_formats(monkeypatch) -> None:
    """Metadata lookups must skip stream format HEAD probes (--no-check-formats)."""
    seen_extra: list[list[str]] = []

    class _Proc:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout

    def _fake_run(url, base_args, extra, timeout):
        seen_extra.append(list(extra))
        if "-J" in extra:
            return (
                _Proc(
                    '{"title": "BitChute Clip", "thumbnail": "https://static-3.bitchute.com/cover.jpg", "channel": "Channel"}'
                ),
                (),
                False,
            )
        return _Proc("BitChute Clip\n"), (), False

    monkeypatch.setattr(resolver, "_run_ytdlp_provider_command", _fake_run)
    monkeypatch.setattr(resolver, "build_ytdlp_base_args", lambda: ())

    info = resolver.ytdlp_info("https://www.bitchute.com/video/wPdrOYMYZsnn/")
    title = resolver.title_from_ytdlp("https://www.bitchute.com/video/wPdrOYMYZsnn/")

    assert info is not None
    assert info["thumbnail"] == "https://static-3.bitchute.com/cover.jpg"
    assert title == "BitChute Clip"
    assert len(seen_extra) == 2
    assert "--no-check-formats" in seen_extra[0]
    assert "--no-check-formats" in seen_extra[1]


def test_sync_local_thumbnail_registers_src_and_attaches_when_cached(thumb_env) -> None:
    url = "https://static-3.bitchute.com/live/cover_images/rzUd1ovwJXug/wPdrOYMYZsnn_640x360.jpg"
    tid = thumb_cache.thumb_id(url)
    expected_rel = thumb_cache.local_rel_path(tid)

    # Stale/mismatched local path is cleared while the file is not on disk yet.
    item = {"thumbnail": url, "thumbnail_local": "/thumbs/stale.jpg"}
    thumb_cache.attach_local_thumbnail(item)
    assert "thumbnail_local" not in item

    # After a restart (tracking cleared), sync_local_thumbnail re-registers the
    # source URL without enqueuing work, and attaches thumbnail_local once the
    # file lands on disk.
    thumb_cache.reset_thumb_tracking_for_tests()
    assert thumb_cache.get_thumb_src(tid) is None

    synced = thumb_cache.sync_local_thumbnail({"thumbnail": url, "thumbnail_local": "/thumbs/wrong.jpg"})
    assert thumb_cache.get_thumb_src(tid) == url
    assert "thumbnail_local" not in synced
    assert thumb_env.qsize() == 1  # No new queue entry was added by sync_local_thumbnail.

    dst = thumb_cache.local_abs_path(tid)
    with open(dst, "wb") as f:
        f.write(b"\xff\xd8\xff\xd9")

    ready = thumb_cache.sync_local_thumbnail({"thumbnail": url})
    assert ready["thumbnail_local"] == expected_rel


def test_now_playing_metadata_prefetch_enriches_active_playback_and_history(
    thumb_env, monkeypatch
) -> None:
    from relaytv_app import player, state

    url = "https://www.bitchute.com/video/wPdrOYMYZsnn/"
    thumb_url = "https://static-3.bitchute.com/live/cover_images/rzUd1ovwJXug/wPdrOYMYZsnn_640x360.jpg"
    tid = thumb_cache.thumb_id(thumb_url)
    dst = thumb_cache.local_abs_path(tid)
    with open(dst, "wb") as f:
        f.write(b"\xff\xd8\xff\xd9")

    now = {
        "url": url,
        "title": "IT'S UNSTOPPABLE",
        "provider": "bitchute",
        "thumbnail": None,
        "thumbnail_local": None,
        "channel": None,
        "history_id": "hist-bitchute-1",
    }
    history_entry = {
        "history_id": "hist-bitchute-1",
        "ts": 1000,
        "mode": "startup_resume",
        "url": url,
        "title": "IT'S UNSTOPPABLE",
        "provider": "bitchute",
        "resume_pos": 12.0,
    }
    monkeypatch.setattr(state, "NOW_PLAYING", dict(now), raising=False)
    monkeypatch.setattr(state, "HISTORY", [dict(history_entry)], raising=False)
    monkeypatch.setattr(state, "persist_session", lambda: True)
    monkeypatch.setattr(state, "_persist_history", lambda: True)
    monkeypatch.setattr(
        state,
        "set_now_playing",
        lambda updated: setattr(state, "NOW_PLAYING", updated),
    )
    monkeypatch.setattr(
        resolver,
        "ytdlp_info",
        lambda requested_url: {
            "title": "IT'S UNSTOPPABLE: FULL TITLE",
            "thumbnail": thumb_url,
            "channel": "The Reese Report",
        },
    )

    player._prefetch_queue_item_metadata_worker(dict(now), url, False)

    assert state.NOW_PLAYING is not None
    assert state.NOW_PLAYING["thumbnail"] == thumb_url
    assert state.NOW_PLAYING["thumbnail_local"] == thumb_cache.local_rel_path(tid)
    assert state.NOW_PLAYING["channel"] == "The Reese Report"
    assert state.HISTORY[0]["thumbnail"] == thumb_url
    assert state.HISTORY[0]["thumbnail_local"] == thumb_cache.local_rel_path(tid)
    assert state.HISTORY[0]["channel"] == "The Reese Report"
