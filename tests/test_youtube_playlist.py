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

from relaytv_app import playback_service, player, resolver, state, youtube_playlist as playlists
from relaytv_app.config import runtime_config
from relaytv_app.main import create_app
from test_playback_intent import harness as harness

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
    "www.youtube.com/playlist?list=PLreview123",
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
    monkeypatch.setattr(state, "SESSION_STATE", "idle")
    monkeypatch.setattr(state, "SESSION_POSITION", None)
    monkeypatch.setattr(state, "SESSION_PAUSE_REASON", None)
    monkeypatch.setattr(state, "AUTO_NEXT_SUPPRESS_UNTIL", 0.0)
    monkeypatch.setattr(state, "_persist_session_payload", lambda *a, **kw: True)
    monkeypatch.setattr(player, "is_playing", lambda: False)
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
def test_enqueue_aliases_automatically_expand_playlist_links(imports, monkeypatch, path, url):
    service, client, *_ = imports
    monkeypatch.setattr(queue_routes, "_smart_item_from_url", lambda url, **kw: {"url": url})
    monkeypatch.setattr(queue_routes, "_push_queue_added_toast_async", lambda *a: None)
    response = client.post(path, json={"url": url})
    assert response.status_code == 202
    job = wait_done(service, response.json()["job_id"])
    assert job["added"] == 3 and job["action"] == "append"
    assert len(state.QUEUE) == 3


def test_existing_mixed_video_link_still_queues_one_video(imports, monkeypatch):
    _, client, *_ = imports
    monkeypatch.setattr(queue_routes, "_smart_item_from_url", lambda url, **kw: {"url": url})
    monkeypatch.setattr(queue_routes, "_push_queue_added_toast_async", lambda *a: None)
    url = f"https://www.youtube.com/watch?v={VIDEO_A}&list=PLreview123"
    assert client.post("/enqueue", json={"url": url, "playlist_mode": "video"}).status_code == 200
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


@pytest.mark.parametrize("raw", [
    URL, URL.replace("https://www.", "https://m."),
    "www.youtube.com/playlist?list=PLreview123",
    f"https://youtu.be/{VIDEO_A}?list=PLreview123&index=8&t=99",
    f"Shared playlist: <https://youtube.com/watch?v={VIDEO_B}&list=PLreview123>",
])
def test_shared_playlist_detection_canonicalizes_phone_inputs(raw):
    assert playlists.shared_playlist_url(raw) == URL
    assert playlists.shared_playlist_url(f"https://youtu.be/{VIDEO_A}") is None


@pytest.mark.parametrize("path", [
    "/enqueue", "/queue/add", "/api/queue/add", "/v1/queue/add", "/smart", "/play_now",
])
@pytest.mark.parametrize("mode", ["auto", "video", "playlist"])
@pytest.mark.parametrize("url", [
    "https://[",
    "https://youtube.com\uff0f.evil.test/playlist?list=PLreview123",
])
def test_malformed_shared_links_return_400_without_side_effects(imports, monkeypatch, path, mode, url):
    service, _, persisted, warmed, events = imports
    client = TestClient(create_app(testing=True), raise_server_exceptions=False)
    current = {"url": "https://example.com/current.mp4", "title": "Current"}
    queued = {"url": "https://example.com/queued.mp4"}
    monkeypatch.setattr(state, "NOW_PLAYING", current)
    monkeypatch.setattr(state, "SESSION_STATE", "playing")
    state.QUEUE.append(queued)
    intent = player.current_playback_intent()
    body = {"url": url}
    if mode != "auto":
        body["playlist_mode"] = mode

    response = client.post(path, json=body)

    assert response.status_code == 400, response.text
    assert response.json() == {"detail": "Invalid url"}
    assert state.NOW_PLAYING is current and state.SESSION_STATE == "playing"
    assert state.QUEUE == [queued]
    assert player.current_playback_intent() == intent
    assert service._jobs == {} and not service._workers
    assert persisted == warmed == events == []


def test_video_only_override_requires_an_individual_video():
    assert playlists.shared_playlist_url(f"https://youtu.be/{VIDEO_A}?list=PLreview123", "video") is None
    with pytest.raises(HTTPException):
        playlists.shared_playlist_url(URL, "video")


@pytest.mark.parametrize("status", ["playing", "paused"])
def test_smart_busy_or_paused_appends_without_touching_playback(imports, monkeypatch, status):
    service, client, *_ = imports
    current = {"url": "https://example.com/current.mp4", "title": "Current"}
    monkeypatch.setattr(state, "NOW_PLAYING", current)
    monkeypatch.setattr(state, "SESSION_STATE", status)
    monkeypatch.setattr(player, "is_playing", lambda: status == "playing")
    monkeypatch.setattr(player, "play_item", lambda *a, **kw: pytest.fail("Smart interrupted playback"))
    state.QUEUE.append({"url": "https://example.com/queued.mp4"})
    response = client.post("/smart", json={"url": URL})
    assert response.status_code == 202
    job = wait_done(service, response.json()["job_id"])
    assert job["effective_action"] == "append" and job["added"] == 3
    assert state.NOW_PLAYING is current and state.SESSION_STATE == status
    assert state.QUEUE[0]["url"] == "https://example.com/queued.mp4"


@pytest.mark.parametrize("path", ["/smart", "/play_now"])
def test_phone_idle_starts_first_and_preserves_remaining_order(imports, harness, path):
    service, client, *_ = imports
    old = {"url": "https://example.com/already-queued.mp4"}
    state.QUEUE.append(old)
    harness["gate"].set()
    response = client.post(path, json={"url": URL, "request_id": uuid4().hex})
    assert response.status_code == 202
    job = wait_done(service, response.json()["job_id"])
    assert job["status"] == "completed" and job["outcome"] == "playing", job
    assert job["added"] == 2 and job["playing_url"].endswith(VIDEO_A)
    assert [item["url"].split("=")[-1] for item in state.QUEUE[:2]] == [VIDEO_B, VIDEO_A]
    assert state.QUEUE[2] is old
    assert state.NOW_PLAYING["url"].endswith(VIDEO_A)
    assert len(harness["loaded"]) == 1 and len(harness["history"]) == 1


def test_phone_play_preserves_interrupted_video_after_playlist(imports, harness, monkeypatch):
    service, client, *_ = imports
    current = {"url": "https://example.com/current.mp4", "title": "Current", "provider": "other"}
    monkeypatch.setattr(state, "NOW_PLAYING", current)
    monkeypatch.setattr(state, "SESSION_STATE", "playing")
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 42 if prop == "time-pos" else 300)
    state.QUEUE.append({"url": "https://example.com/old-queue.mp4"})
    harness["gate"].set()
    response = client.post("/play_now", json={"url": URL})
    job = wait_done(service, response.json()["job_id"])
    assert job["outcome"] == "playing", job
    assert [item["url"] for item in state.QUEUE] == [
        f"https://www.youtube.com/watch?v={VIDEO_B}",
        f"https://www.youtube.com/watch?v={VIDEO_A}",
        current["url"], "https://example.com/old-queue.mp4",
    ]
    assert state.QUEUE[2]["resume_pos"] == 42


def test_failed_start_keeps_all_videos_and_drops_redundant_preserved_copy(imports, monkeypatch):
    service, client, *_ = imports
    original = {"url": "https://example.com/current.mp4", "title": "Current"}
    monkeypatch.setattr(state, "NOW_PLAYING", original)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    monkeypatch.setattr(player, "mpv_get", lambda prop: 42)
    monkeypatch.setattr(player, "update_history_progress", lambda *a, **kw: None)

    def fail(*a, **kw):
        raise HTTPException(400, "upstream secret must not be returned")

    monkeypatch.setattr(player, "play_item", fail)
    response = client.post("/play_now", json={"url": URL})
    job = wait_done(service, response.json()["job_id"])
    assert job["outcome"] == "playback_failed" and job["added"] == 3, job
    assert "secret" not in job["detail"]
    assert [item["url"].split("=")[-1] for item in state.QUEUE] == [VIDEO_A, VIDEO_B, VIDEO_A]
    assert state.NOW_PLAYING is original


@pytest.mark.parametrize("operation", ["/stop", "/close", "/clear", "new_play"])
def test_newer_command_wins_while_phone_playlist_extracts(imports, harness, monkeypatch, operation):
    service, client, *_ = imports
    entered, release = blocked_extraction(monkeypatch)
    monkeypatch.setattr(player, "stop_mpv", lambda **kw: None)
    monkeypatch.setattr("relaytv_app.routes._ensure_notification_surface", lambda **kw: None)
    preserve_calls = []
    real_preserve = playback_service.preserve_current_to_queue_front
    monkeypatch.setattr(
        playback_service,
        "preserve_current_to_queue_front",
        lambda *a, **kw: preserve_calls.append((a, kw)) or real_preserve(*a, **kw),
    )
    try:
        response = client.post("/play_now", json={"url": URL})
        assert response.status_code == 202
        assert entered.wait(2)
        if operation == "new_play":
            harness["gate"].set()
            response2 = client.post("/play_now", json={"url": "https://example.com/new.mp4", "title": "New", "preserve_current": False})
            assert response2.status_code == 200
        else:
            assert client.post(operation).status_code == 200
    finally:
        release.set()
        harness["gate"].set()
    job = wait_done(service, response.json()["job_id"])
    assert job["status"] == "cancelled", job
    assert preserve_calls == [], "superseded playlist import captured preserved state from newer session"
    assert state.QUEUE == []
    assert len(harness["loaded"]) == (1 if operation == "new_play" else 0)


@pytest.mark.parametrize("operation", ["/stop", "/close", "/clear", "restart_service"])
def test_newer_command_wins_during_first_video_resolution(imports, harness, monkeypatch, operation):
    service, client, *_ = imports
    monkeypatch.setattr(player, "stop_mpv", lambda **kw: None)
    monkeypatch.setattr("relaytv_app.routes._ensure_notification_surface", lambda **kw: None)
    response = client.post("/play_now", json={"url": URL})
    try:
        assert harness["resolving"].wait(2), "first video never began resolving"
        assert client.get("/health").status_code == 200
        if operation == "restart_service":
            service.stop()
            service.start()
        else:
            assert client.post(operation).status_code == 200
        workers = list(service._workers)
    finally:
        harness["gate"].set()
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()
    assert harness["loaded"] == [], "an old playlist restarted playback"
    assert harness["history"] == []
    job = service.get(response.json()["job_id"])
    if operation in {"/clear", "restart_service"}:
        assert job["status"] == "cancelled"
    else:
        assert job["outcome"] == "superseded"
    if operation == "/clear":
        assert state.QUEUE == [], "failed first-video rollback undid Clear"


def test_legacy_phone_requests_coalesce_inflight_and_uuid_binds_action(imports, monkeypatch):
    service, client, *_ = imports
    entered, release = blocked_extraction(monkeypatch)
    monkeypatch.setattr(player, "is_playing", lambda: True)
    try:
        first = client.post("/smart", json={"url": URL}).json()
        assert entered.wait(2)
        duplicate = client.post("/smart", json={"url": URL.replace("playlist?", f"watch?v={VIDEO_A}&index=9&")}).json()
        assert duplicate["job_id"] == first["job_id"]
        assert len(service._workers) == 1
        key = uuid4().hex
        assert client.post("/smart", json={"url": URL, "request_id": key}).status_code == 202
        # Even if Smart selected append, it is a distinct requested action.
        assert client.post("/enqueue", json={"url": URL, "request_id": key}).status_code == 409
    finally:
        release.set()
    wait_done(service, first["job_id"])
    wait_done(service, key)
    again = client.post("/smart", json={"url": URL})
    assert again.json()["job_id"] != first["job_id"]


@pytest.mark.parametrize("path", ["/enqueue", "/smart", "/play_now"])
def test_phone_playlist_writes_require_auth_and_reject_dynamic_mixes(imports, monkeypatch, path):
    from relaytv_app.routes.playback import PlayReq, SmartPlayReq

    assert "playlist_mode" not in PlayReq.model_fields
    assert "playlist_mode" in SmartPlayReq.model_fields
    _, client, *_ = imports
    monkeypatch.setenv("RELAYTV_API_TOKEN", "phone-secret")
    runtime_config.refresh_from_env()
    assert client.post(path, json={"url": URL}).status_code == 401
    response = client.post(path, json={"url": URL.replace("PLreview123", "RDreview123")}, headers={"Authorization": "Bearer phone-secret"})
    assert response.status_code == 400 and "dynamic" in response.json()["detail"]
    assert state.QUEUE == []
    mix_video = f"https://www.youtube.com/watch?v={VIDEO_A}&list=RD{VIDEO_A}&start_radio=1"
    assert playlists.shared_playlist_url(mix_video, "auto") is None
    with pytest.raises(HTTPException) as exc:
        playlists.shared_playlist_url(mix_video, "playlist")
    assert exc.value.status_code == 400 and "dynamic" in str(exc.value.detail)



def test_phone_start_cannot_claim_a_late_intent_after_stop(imports, harness, monkeypatch):
    service, client, *_ = imports
    entered, release = threading.Event(), threading.Event()
    calls = []

    def blocked_persist():
        if threading.current_thread().name == "youtube-playlist-import" and not calls:
            calls.append(True)
            entered.set()
            assert release.wait(3)

    monkeypatch.setattr(state, "persist_queue", blocked_persist)
    monkeypatch.setattr(player, "stop_mpv", lambda **kw: None)
    harness["gate"].set()
    response = client.post("/play_now", json={"url": URL})
    try:
        assert entered.wait(2), "playlist never staged its tail"
        assert client.post("/stop").status_code == 200
    finally:
        release.set()
    job = wait_done(service, response.json()["job_id"])
    assert job["outcome"] == "superseded", job
    assert harness["loaded"] == [], "the player claimed a new intent after Stop"
    assert harness["history"] == []


@pytest.mark.parametrize("path", ["/enqueue", "/smart"])
def test_clear_cancels_pending_append_imports(imports, monkeypatch, path):
    service, client, *_ = imports
    monkeypatch.setattr(state, "SESSION_STATE", "playing")
    entered, release = blocked_extraction(monkeypatch)
    try:
        response = client.post(path, json={"url": URL})
        assert entered.wait(2)
        assert client.post("/clear").status_code == 200
        workers = list(service._workers)
    finally:
        release.set()
    for worker in workers:
        worker.join(3)
        assert not worker.is_alive()
    assert service.get(response.json()["job_id"])["status"] == "cancelled"
    assert state.QUEUE == []


def test_failed_start_never_restores_an_old_queue_snapshot(imports, harness, monkeypatch):
    service, client, *_ = imports

    def fail_resolve(url):
        harness["resolving"].set()
        assert harness["gate"].wait(3)
        raise HTTPException(400, "temporary resolver failure")

    monkeypatch.setattr(player, "resolve_streams", fail_resolve)
    response = client.post("/play_now", json={"url": URL})
    try:
        assert harness["resolving"].wait(2)
        assert client.post("/queue/import", json={"items": [{"url": "https://example.com/newer.mp4", "title": "Newer"}]}).status_code == 200
        before = [(state.queue_item_id(item), item["url"]) for item in state.QUEUE]
    finally:
        harness["gate"].set()
    job = wait_done(service, response.json()["job_id"])
    assert job["outcome"] == "playback_failed" and job["added"] == 2
    assert [(state.queue_item_id(item), item["url"]) for item in state.QUEUE] == before


def test_retiring_an_old_import_does_not_retire_a_newer_play(imports, harness, monkeypatch):
    service, client, *_ = imports
    entered, release = blocked_extraction(monkeypatch)
    try:
        client.post("/play_now", json={"url": URL})
        assert entered.wait(2)
        harness["gate"].set()
        assert client.post("/play_now", json={"url": "https://example.com/winner.mp4", "title": "Winner", "preserve_current": False}).status_code == 200
        winner = player.current_playback_intent()
        service.stop()
        assert player.playback_intent_current(winner)
    finally:
        release.set()
    assert state.NOW_PLAYING["url"] == "https://example.com/winner.mp4"


def test_failed_phone_extraction_notifies_the_tv_without_a_false_queue_event(imports, monkeypatch):
    service, client, _, _, events = imports
    toasts = []
    monkeypatch.setattr(queue_routes, "_push_overlay_toast", lambda **kw: toasts.append(kw))

    def fail(url):
        raise HTTPException(400, "Playlist is private; configure YouTube cookies")

    monkeypatch.setattr(playlists, "extract_playlist", fail)
    response = client.post("/smart", json={"url": URL})
    job = wait_done(service, response.json()["job_id"])
    assert job["status"] == "failed"
    assert events == [] and state.QUEUE == []
    assert len(toasts) == 1 and toasts[0]["level"] == "warn"
    assert "private" in toasts[0]["text"]


def test_clear_after_load_begins_keeps_first_video_and_clears_tail(imports, harness, monkeypatch):
    service, client, *_ = imports
    entered, release = threading.Event(), threading.Event()

    def blocked_load(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        harness["loaded"].append(args[0])
        return True

    monkeypatch.setattr(player, "_load_stream_in_existing_mpv", blocked_load)
    harness["gate"].set()
    response = client.post("/play_now", json={"url": URL})
    try:
        assert entered.wait(2)
        assert client.post("/clear").status_code == 200
        assert state.QUEUE == []
    finally:
        release.set()
    job = wait_done(service, response.json()["job_id"])
    assert job["status"] == "completed" and job["outcome"] == "playing", job
    assert job["added"] == 0
    assert len(harness["loaded"]) == len(harness["history"]) == 1
    assert state.NOW_PLAYING["url"].endswith(VIDEO_A)
    assert state.QUEUE == []


def test_clear_between_playback_and_completion_keeps_job_counts_current(imports, harness, monkeypatch):
    service, client, _, _, events = imports
    entered, release = threading.Event(), threading.Event()
    real_finish = playback_service.finish_playlist

    def blocked_finish(*a, **kw):
        result = real_finish(*a, **kw)
        entered.set()
        assert release.wait(3)
        return result

    monkeypatch.setattr(playback_service, "finish_playlist", blocked_finish)
    harness["gate"].set()
    response = client.post("/play_now", json={"url": URL})
    try:
        assert entered.wait(2)
        assert client.post("/clear").status_code == 200
    finally:
        release.set()
    job = wait_done(service, response.json()["job_id"])
    assert job["outcome"] == "playing" and job["added"] == 0, job
    assert job["queue_length"] == 0
    for worker in list(service._workers):
        worker.join(3)
    assert events[-1]["queue_length"] == 0 and events[-1]["queue"] == []
