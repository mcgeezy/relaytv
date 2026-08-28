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


def test_failure_map_is_bounded(thumb_env) -> None:
    for index in range(thumb_cache.THUMB_FAILURE_MAP_MAX + 200):
        thumb_cache._release_thumb(f"tid{index}", failed=True)

    assert len(thumb_cache._FAILED_AT) <= thumb_cache.THUMB_FAILURE_MAP_MAX


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
