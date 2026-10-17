# SPDX-License-Identifier: GPL-3.0-only
import json
import importlib
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from relaytv_app import player, resolver, state, youtube_playlist as playlists
from relaytv_app.config import runtime_config
from relaytv_app.main import create_app

queue_routes = importlib.import_module("relaytv_app.routes.queue")
URL = "https://www.youtube.com/playlist?list=PLreview123"
VIDEO_A = "jNQXAC9IVRw"
VIDEO_B = "aqz-KE-bpKQ"


def metadata():
    return {
        "title": "Review playlist", "playlist_count": 4,
        "entries": [
            {"id": VIDEO_A, "title": "First", "channel": "Channel", "duration": 19},
            {"id": VIDEO_B, "title": "Second", "duration": 596},
            {"id": "private0000", "title": "[Private video]"},
            {"id": VIDEO_A, "title": "First again"},
        ],
    }


@pytest.mark.parametrize("url", [
    URL,
    "https://m.youtube.com/watch?v=jNQXAC9IVRw&list=PLreview123&index=3&t=12",
    "https://youtu.be/jNQXAC9IVRw?list=PLreview123&index=8",
    "https://music.youtube.com/playlist?list=PLreview123",
    "shared link <https://www.youtube.com/playlist?list=PLreview123>",
])
def test_playlist_url_removes_video_and_index_context(url):
    assert playlists.playlist_url(url) == URL


@pytest.mark.parametrize("url", [
    "https://youtube.com.evil.test/playlist?list=PLreview123",
    "https://evil-youtube.com/playlist?list=PLreview123",
    "https://www.youtube.com/watch?v=jNQXAC9IVRw",
    "https://youtube.com/playlist?list=bad%20id",
    "https://youtube.com/playlist?list=PLone&list=PLtwo",
    "https://youtube.com/playlist?list=RDjNQXAC9IVRw",
    "file:///tmp/playlist?list=PLreview123",
])
def test_playlist_url_rejects_unsafe_or_dynamic_inputs(url):
    with pytest.raises(HTTPException):
        playlists.playlist_url(url)


def test_flat_entries_preserve_order_repeats_and_safe_metadata(monkeypatch):
    monkeypatch.setattr(resolver, "make_item", lambda *a, **kw: pytest.fail("per-video lookup"))
    data = metadata()
    data["entries"][0].update(
        url="https://evil.test/token", thumbnail="http://private.test/secret", headers={"cookie": "secret"}
    )
    data["entries"] += [None, {"id": "bad"}, {"id": VIDEO_B, "duration": float("nan")}]
    result = playlists.playlist_from_metadata(data)
    assert result.title == "Review playlist"
    assert result.skipped == 3
    assert [item["url"].split("=")[-1] for item in result.items] == [VIDEO_A, VIDEO_B, VIDEO_A, VIDEO_B]
    assert result.items[0] == {
        "url": f"https://www.youtube.com/watch?v={VIDEO_A}", "provider": "youtube",
        "title": "First", "thumbnail": f"https://i.ytimg.com/vi/{VIDEO_A}/hqdefault.jpg",
        "channel": "Channel", "duration": 19,
    }
    assert "duration" not in result.items[-1]


@pytest.mark.parametrize("data,code", [
    ({}, 502), ({"entries": []}, 400),
    ({"entries": [None, {"id": VIDEO_A, "title": "[Deleted video]"}]}, 400),
    ({"entries": [None] * (playlists.MAX_VIDEOS + 1)}, 413),
    ({"entries": metadata()["entries"], "playlist_count": playlists.MAX_VIDEOS + 1}, 413),
])
def test_invalid_empty_or_oversized_metadata_adds_nothing(data, code):
    with pytest.raises(HTTPException) as exc:
        playlists.playlist_from_metadata(data)
    assert exc.value.status_code == code


def test_extraction_uses_flat_command_existing_cookies_and_a_finite_limit(monkeypatch):
    commands = []
    base = ["yt-dlp", "--cookies", "/data/private-cookies.txt", "--js-runtimes", "node", "--no-playlist"]
    monkeypatch.setattr(resolver, "build_ytdlp_base_args", lambda: base.copy())

    def run(args, **kwargs):
        commands.append((args, kwargs))
        kwargs["stdout"].write(json.dumps(metadata()).encode())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(playlists.subprocess, "run", run)
    assert len(playlists.extract_playlist(URL).items) == 3
    args, kwargs = commands[0]
    assert "--no-playlist" not in args
    assert args[args.index("--cookies") + 1] == "/data/private-cookies.txt"
    assert all(flag in args for flag in ["--ignore-config", "--flat-playlist", "--yes-playlist", "--dump-single-json"])
    assert args[args.index("--playlist-end") + 1] == str(playlists.MAX_VIDEOS + 1)
    assert args[-2:] == ["--", URL]
    assert kwargs["timeout"] == playlists.EXTRACT_TIMEOUT
    assert base[-1] == "--no-playlist"


@pytest.mark.parametrize("failure,code", [("timeout", 504), ("missing", 503), ("upstream", 400), ("json", 502), ("large", 413)])
def test_extraction_errors_are_bounded_and_do_not_leak_secrets(monkeypatch, failure, code):
    secret = "private-cookie-path-and-token"

    def run(args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(secret, 120)
        if failure == "missing":
            raise FileNotFoundError(secret)
        if failure == "large":
            kwargs["stdout"].seek(playlists.MAX_OUTPUT_BYTES)
        kwargs["stdout"].write(b"invalid")
        kwargs["stderr"].write(secret.encode())
        return subprocess.CompletedProcess(args, 1 if failure == "upstream" else 0)

    monkeypatch.setattr(playlists.subprocess, "run", run)
    with pytest.raises(HTTPException) as exc:
        playlists.extract_playlist(URL)
    assert exc.value.status_code == code
    assert secret not in str(exc.value.detail)


@pytest.fixture
def imports(monkeypatch):
    service = playlists.PlaylistImports()
    monkeypatch.setattr(playlists, "imports", service)
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    monkeypatch.setattr(state, "QUEUE", state._RevisionedQueue())
    monkeypatch.setattr(state, "NOW_PLAYING", None)
    persisted, warmed, events = [], [], []
    monkeypatch.setattr(state, "persist_queue", lambda: persisted.append(list(state.QUEUE)))
    monkeypatch.setattr(player, "prefetch_queue_item_stream", lambda item: warmed.append(item))
    monkeypatch.setattr(player, "prime_mpv_up_next_from_queue", lambda **kw: None)
    monkeypatch.setattr(queue_routes, "_ui_event_push_queue", lambda *a, **kw: events.append(kw))
    monkeypatch.setattr(queue_routes, "_push_overlay_toast", lambda **kw: None)
    monkeypatch.setattr(playlists, "extract_playlist", lambda url: playlists.playlist_from_metadata(metadata()))
    yield service, TestClient(create_app(testing=True)), persisted, warmed, events
    service.stop()
    for worker in list(service._workers):
        worker.join(timeout=3)
        assert not worker.is_alive(), "test left a playlist worker in flight"


def wait_done(service, job_id):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        status = service.get(job_id)
        if status["status"] not in {"loading", "adding"}:
            return status
        time.sleep(0.005)
    pytest.fail("import did not finish")


def test_api_imports_a_batch_once_and_retries_return_same_result(imports):
    service, client, persisted, warmed, events = imports
    request_id = uuid4().hex
    body = {"url": URL, "request_id": request_id}
    assert client.post("/queue/playlists", json=body).status_code == 202
    result = wait_done(service, request_id)
    assert result["status"] == "completed"
    assert result["added"] == 3 and result["skipped"] == 1
    response = client.post("/queue/playlists", json=body)
    assert response.json() == result
    assert client.get(f"/queue/playlists/{request_id}").json() == result
    assert len(state.QUEUE) == 3
    assert len({state.queue_item_id(item) for item in state.QUEUE}) == 3
    assert len(persisted) == len(warmed) == 1
    # Completion notification follows commit and can finish just after polling.
    for worker in list(service._workers):
        worker.join(timeout=2)
    assert len(events) == 1 and events[0]["source"] == "youtube_playlist"


def test_api_request_id_conflict_and_validation(imports):
    service, client, *_ = imports
    request_id = uuid4().hex
    assert client.post("/queue/playlists", json={"url": URL, "request_id": request_id}).status_code == 202
    wait_done(service, request_id)
    assert client.post("/queue/playlists", json={"url": URL + "different", "request_id": request_id}).status_code == 409
    assert client.post("/queue/playlists", json={"url": URL}).status_code == 422
    assert client.post("/queue/playlists", json={"url": URL, "request_id": "bad"}).status_code == 422
    assert client.get(f"/queue/playlists/{uuid4().hex}").status_code == 404


def test_api_auth_protects_start_and_cancel_but_leaves_status_readable(imports, monkeypatch):
    service, client, *_ = imports
    monkeypatch.setenv("RELAYTV_API_TOKEN", "playlist-test-secret")
    runtime_config.refresh_from_env()
    request_id = uuid4().hex
    body = {"url": URL, "request_id": request_id}
    assert client.post("/queue/playlists", json=body).status_code == 401
    assert client.delete(f"/queue/playlists/{request_id}").status_code == 401
    headers = {"Authorization": "Bearer playlist-test-secret"}
    assert client.post("/queue/playlists", json=body, headers=headers).status_code == 202
    wait_done(service, request_id)
    assert client.get(f"/queue/playlists/{request_id}").status_code == 200


@pytest.mark.parametrize("path", ["/enqueue", "/queue/add", "/api/queue/add", "/v1/queue/add"])
@pytest.mark.parametrize("url", [URL, URL.replace("/playlist?", "/embed/videoseries?")])
def test_single_enqueue_rejects_playlist_only_without_inserting_an_unplayable_item(imports, monkeypatch, path, url):
    _, client, *_ = imports
    monkeypatch.setattr(queue_routes, "_smart_item_from_url", lambda url, **kw: {"url": url})
    monkeypatch.setattr(queue_routes, "_push_queue_added_toast_async", lambda *a: None)
    response = client.post(path, json={"url": url})
    assert response.status_code == 400 and "entire playlist" in response.json()["detail"]
    assert state.QUEUE == []


def test_existing_mixed_video_link_still_queues_one_video(imports, monkeypatch):
    _, client, *_ = imports
    monkeypatch.setattr(queue_routes, "_smart_item_from_url", lambda url, **kw: {"url": url})
    monkeypatch.setattr(queue_routes, "_push_queue_added_toast_async", lambda *a: None)
    url = f"https://www.youtube.com/watch?v={VIDEO_A}&list=PLreview123"
    assert client.post("/enqueue", json={"url": url}).status_code == 200
    assert [item["url"] for item in state.QUEUE] == [url]


def blocked_extraction(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_run = subprocess.run

    def run(args, **kwargs):
        if args[0] != "yt-dlp":
            return real_run(args, **kwargs)
        entered.set()
        assert release.wait(3), "test never released extraction"
        kwargs["stdout"].write(json.dumps(metadata()).encode())
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(playlists.subprocess, "run", run)
    monkeypatch.setattr(playlists, "extract_playlist", _REAL_EXTRACT)
    return entered, release


_REAL_EXTRACT = playlists.extract_playlist


def test_slow_real_extractor_path_returns_202_and_keeps_other_requests_responsive(imports, monkeypatch):
    service, client, *_ = imports
    entered, release = blocked_extraction(monkeypatch)
    request_id = uuid4().hex
    try:
        with ThreadPoolExecutor() as pool:
            request = pool.submit(client.post, "/queue/playlists", json={"url": URL, "request_id": request_id})
            assert entered.wait(2)
            assert request.result(timeout=1).status_code == 202
            assert client.get("/health").status_code == 200
            assert client.get(f"/queue/playlists/{request_id}").json()["status"] == "loading"
            # A real competing queue edit survives the eventual batch append.
            assert client.post("/queue/import", json={"items": [{"url": "https://example.com/other.mp4", "title": "Other"}]}).status_code == 200
    finally:
        release.set()
    assert wait_done(service, request_id)["added"] == 3
    assert state.QUEUE[0]["title"] == "Other"
    assert len(state.QUEUE) == 4


@pytest.mark.parametrize("action", ["cancel", "shutdown"])
def test_cancel_or_shutdown_wins_over_inflight_extraction(imports, monkeypatch, action):
    service, client, persisted, warmed, events = imports
    entered, release = blocked_extraction(monkeypatch)
    request_id = uuid4().hex
    try:
        assert client.post("/queue/playlists", json={"url": URL, "request_id": request_id}).status_code == 202
        assert entered.wait(2)
        if action == "cancel":
            assert client.delete(f"/queue/playlists/{request_id}").json()["status"] == "cancelled"
        else:
            service.stop()
            service.start()  # A new generation cannot revive a retired result.
        workers = list(service._workers)
    finally:
        release.set()
    for worker in workers:
        worker.join(timeout=2)
        assert not worker.is_alive()
    assert service.get(request_id)["status"] == "cancelled"
    assert state.QUEUE == [] and persisted == [] and warmed == [] and events == []


def test_identical_inflight_requests_share_one_worker_and_cancelled_workers_keep_slots(imports, monkeypatch):
    service, client, *_ = imports
    entered, release = blocked_extraction(monkeypatch)
    first, second = uuid4().hex, uuid4().hex
    try:
        body = {"url": URL, "request_id": first}
        assert client.post("/queue/playlists", json=body).status_code == 202
        assert entered.wait(2)
        assert client.post("/queue/playlists", json=body).status_code == 202
        assert len(service._workers) == 1
        assert client.post("/queue/playlists", json={"url": URL, "request_id": second}).status_code == 202
        for key in [first, second]:
            client.delete(f"/queue/playlists/{key}")
        assert client.post("/queue/playlists", json={"url": URL, "request_id": uuid4().hex}).status_code == 429
    finally:
        release.set()


def test_failed_import_and_expired_status_are_explicit(imports, monkeypatch):
    service, client, *_ = imports

    def fail(url):
        raise HTTPException(400, "Playlist is private; configure cookies")

    monkeypatch.setattr(playlists, "extract_playlist", fail)
    key = uuid4().hex
    client.post("/queue/playlists", json={"url": URL, "request_id": key})
    result = wait_done(service, key)
    assert result["status"] == "failed" and "private" in result["detail"]
    assert state.QUEUE == []
    with service._lock:
        service._jobs[key].created_at -= playlists.JOB_TTL + 1
    assert client.get(f"/queue/playlists/{key}").status_code == 404
