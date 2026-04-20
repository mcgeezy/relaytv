# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import shutil
import subprocess

import pytest

from fastapi.testclient import TestClient

from relaytv_app.main import create_app
from relaytv_app import player
from relaytv_app import resolver
from relaytv_app import routes
from relaytv_app import upload_store
from relaytv_app.qt_shell_app import (
    _embedded_web_overlay_enabled,
    _libmpv_enabled,
    _native_idle_overlay_enabled,
    _native_overlay_toasts_enabled,
    _overlay_software_mode_enabled,
    _native_idle_weather_layout,
)
from relaytv_app.routes import _notification_capabilities, _overlay_prefers_native_qt_toast

pytestmark = pytest.mark.native
ROOT_DIR = Path(__file__).resolve().parents[1]


def test_ui_smoke() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/ui')

    assert response.status_code == 200
    assert 'text/html' in response.headers['content-type']
    assert 'RelayTV' in response.text
    assert 'id="jfActionStatus"' in response.text
    assert 'id="jellyfinOpenBtn"' in response.text
    assert 'id="jellyfinShell"' in response.text
    assert 'id="jfSearchInput"' in response.text
    assert 'id="nowLangBtn"' in response.text
    assert 'id="aboutBtn"' in response.text
    assert 'id="aboutBackdrop"' in response.text
    assert 'id="aboutGithubLink"' in response.text
    assert 'https://github.com/mcgeezy/relaytv' in response.text
    assert 'id="aboutSupportLink"' in response.text
    assert 'https://buymeacoffee.com/relaytv' in response.text
    assert 'img.buymeacoffee.com/button-api' in response.text
    assert 'function openAbout' in response.text
    assert 'bindAboutUi();' in response.text
    assert 'class="nowSubRow"' in response.text
    assert 'id="langBackdrop"' in response.text
    assert 'role="tablist"' in response.text
    assert 'role="tab"' in response.text
    assert 'id="jfDetailBackdrop"' in response.text
    assert 'id="jfSortSelect"' in response.text
    assert 'id="jfAlphaIndicator"' in response.text
    assert 'id="remoteVolSlider"' in response.text
    assert 'id="remoteVolValue"' in response.text
    assert 'id="setUploadMaxSize"' in response.text
    assert 'id="setUploadRetentionHours"' in response.text
    assert 'function _uploadBadge(item)' in response.text
    assert 'function _uploadSummary(item)' in response.text
    assert 'function _formatUploadSize(bytes)' in response.text
    assert 'mediaBadge' in response.text
    assert 'isUnavailable' in response.text
    assert 'Playback unavailable: stored upload was removed' in response.text
    assert 'onclick="post(\'/resume/clear\')"' in response.text
    assert 'id="jfSearchBtn"' not in response.text
    assert 'id="jfRefreshBtn"' not in response.text
    assert 'id="jfReconnectBtn"' not in response.text
    assert 'function _jfSetActionStatus' in response.text
    assert 'function _jfSetLaunchVisible' in response.text
    assert 'function _jfCloseDetailPanel' in response.text
    assert 'const shellRect = shell.getBoundingClientRect();' in response.text
    assert 'const rawTop = gutter - (gridRect.top - shellRect.top);' in response.text
    assert 'function loadJellyfinMovies' in response.text
    assert 'function loadJellyfinTvSeries' in response.text
    assert 'function _jfPlayAllSeries' in response.text
    assert 'function _jfSyncTabControls' in response.text
    assert 'function _jfScheduleSearch' in response.text
    assert 'function _jfBuildRowItemCard' in response.text
    assert 'const __JF_REQ_TIMEOUT_MS' in response.text
    assert 'function _jfFetchWithTimeout' in response.text
    assert 'function _applyQueueSnapshot' in response.text
    assert 'touch-action: none;' in response.text
    assert "_applyQueueSnapshot(payload);" in response.text
    assert "await post('/play_now', {url, preserve_current:true, preserve_to:'queue_front', resume_current:true, reason:'add_menu'});" in response.text
    assert "play.disabled = !available;" in response.text
    assert "queue.disabled = !available;" in response.text
    assert 'id="setAboutGithubLink"' not in response.text
    assert 'id="setAboutSupportLink"' not in response.text


def test_health_endpoint() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/health')

    assert response.status_code == 200
    assert response.json() == {'ok': True}


def test_release_compose_uses_published_image_without_source_build() -> None:
    text = (ROOT_DIR / "docker-compose.release.yml").read_text()

    assert "image: \"${RELAYTV_IMAGE_REF:-ghcr.io/mcgeezy/relaytv:latest}\"" in text
    assert "build:" not in text
    assert "context: ./app" not in text
    assert "./data:/data" in text


def test_root_bootstrap_installer_downloads_release_bundle() -> None:
    text = (ROOT_DIR / "install.sh").read_text()

    assert "docker-compose.release.yml" in text
    assert "scripts/install.sh" in text
    assert "scripts/doctor.sh" in text
    assert "docker compose pull" in text
    assert "docker compose up -d" in text
    assert "RELAYTV_CEC_ENABLED" in text
    assert "--enable-cec" in text
    assert "monitor standby/source changes" in text
    assert "--force" in text
    assert "INSTALL_DIR=\"$(default_install_dir)\"" in text
    assert "confirm_current_directory_install" in text
    assert "RelayTV will be installed in the current directory" in text


def test_install_scripts_have_valid_bash_syntax() -> None:
    subprocess.run(["bash", "-n", str(ROOT_DIR / "install.sh")], check=True)
    subprocess.run(["bash", "-n", str(ROOT_DIR / "scripts/install.sh")], check=True)


def test_repo_installer_generates_host_device_override_for_cec() -> None:
    text = (ROOT_DIR / "scripts/install.sh").read_text()

    assert "RELAYTV_CEC_ENABLED" in text
    assert "RELAYTV_CEC_MONITOR" in text
    assert "RELAYTV_CEC" in text
    assert "Optional HDMI-CEC control" in text
    assert "detect_cec_device_nodes" in text
    assert "host-device-overrides" in text
    assert "/dev/cec*" in text


def test_cec_send_uses_running_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    writes: list[str] = []

    class FakeStdin:
        def write(self, value: str) -> None:
            writes.append(value)

        def flush(self) -> None:
            writes.append("<flush>")

    class FakeProc:
        pid = 1234
        stdin = FakeStdin()

        def poll(self):
            return None

    def fail_run(*args, **kwargs):
        raise AssertionError("one-shot cec-client should not run when controller is alive")

    monkeypatch.setattr(player, "CEC_MONITOR_ENV", "1")
    monkeypatch.setattr(player, "_CEC_CONTROLLER_PROC", FakeProc())
    monkeypatch.setattr(player.subprocess, "run", fail_run)

    player.cec_send("on 0\nas\n")

    assert writes == ["on 0\nas\n", "<flush>"]
    status = player.cec_controller_status()
    assert status["last_command"] == "on 0\nas"
    assert status["last_command_ok"] is True


def test_cec_send_falls_back_to_one_shot_without_controller(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    class Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(args, **kwargs):
        calls.append({"args": args, **kwargs})
        return Result()

    monkeypatch.setattr(player, "CEC_MONITOR_ENV", "0")
    monkeypatch.setattr(player, "_CEC_CONTROLLER_PROC", None)
    monkeypatch.setattr(player.subprocess, "run", fake_run)

    player.cec_send("pow 0\n")

    assert calls
    assert calls[0]["args"] == ["cec-client", "-s", "-d", "1"]
    assert calls[0]["input"] == "pow 0\n"


def test_public_image_docs_and_ci_only_offer_latest() -> None:
    text = "\n".join(
        [
            (ROOT_DIR / "README.md").read_text(),
            (ROOT_DIR / "docs/INSTALL.md").read_text(),
            (ROOT_DIR / ".github/workflows/ci.yml").read_text(),
        ]
    )

    assert "ghcr.io/mcgeezy/relaytv:latest" in text
    assert ":full" not in text
    assert "docker-image-full" not in text
    assert "suffix=-full" not in text


def test_release_image_traceability_metadata_is_documented() -> None:
    dockerfile = (ROOT_DIR / "app/Dockerfile").read_text()
    compose = (ROOT_DIR / "docker-compose.yml").read_text()
    workflow = (ROOT_DIR / ".github/workflows/ci.yml").read_text()
    release_doc = (ROOT_DIR / "docs/RELEASE.md").read_text()
    pyproject = (ROOT_DIR / "pyproject.toml").read_text()

    assert "python:3.13-slim@sha256:" in dockerfile
    assert 'org.opencontainers.image.source="${RELAYTV_IMAGE_SOURCE}"' in dockerfile
    assert 'org.opencontainers.image.revision="${RELAYTV_IMAGE_REVISION}"' in dockerfile
    assert 'org.opencontainers.image.licenses="GPL-3.0-only"' in dockerfile
    assert "COPY LICENSE COPYING THIRD_PARTY_LICENSES.md ASSETS.md /usr/share/doc/relaytv/" in dockerfile
    assert "context: ." in compose
    assert "dockerfile: app/Dockerfile" in compose
    assert "context: ." in workflow
    assert "file: ./app/Dockerfile" in workflow
    assert "RELAYTV_IMAGE_REVISION=${{ github.sha }}" in workflow
    assert "RELAYTV_YTDLP_AUTO_UPDATE=0" in release_doc
    assert "GPL-3.0-only" in pyproject


def test_first_party_source_files_have_spdx_headers() -> None:
    checked: list[Path] = []
    checked.extend((ROOT_DIR / "app/relaytv_app").glob("*.py"))
    checked.extend((ROOT_DIR / "app/relaytv_app/integrations").glob("*.py"))
    checked.extend((ROOT_DIR / "tests").glob("*.py"))
    checked.extend((ROOT_DIR / "scripts").glob("*.sh"))
    checked.append(ROOT_DIR / "install.sh")

    assert checked
    for path in checked:
        head = "\n".join(path.read_text().splitlines()[:3])
        assert "SPDX-License-Identifier: GPL-3.0-only" in head, str(path)


def test_api_docs_include_uploaded_media_endpoints() -> None:
    text = (ROOT_DIR / "docs/API.md").read_text()

    assert "POST /ingest/media" in text
    assert "POST /ingest/media/enqueue" in text
    assert "POST /ingest/media/play" in text
    assert "GET /media/uploads/{upload_id}/{filename}" in text
    assert "multipart/form-data" in text
    assert "uploads.max_size_gb" in text
    assert "uploads.retention_hours" in text


def test_ingest_media_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["title"] == "Shared Clip"
    assert body["item"]["size_bytes"] == len(b"video-bytes")
    assert body["url"].endswith(".mp4")

    fetch = client.get(body["media_path"])
    assert fetch.status_code == 200
    assert fetch.content == b"video-bytes"

    queued = client.post("/enqueue", json={"url": body["url"]})
    assert queued.status_code == 200
    queued_body = queued.json()
    assert queued_body["item"]["provider"] == "upload"
    assert queued_body["item"]["title"] == "Shared Clip"
    assert queued_body["queue_length"] == 1


def test_ingest_audio_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared Audio"},
        files={"file": ("clip.mp3", b"audio-bytes", "audio/mpeg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["title"] == "Shared Audio"
    assert body["item"]["mime_type"] == "audio/mpeg"
    assert body["item"]["size_bytes"] == len(b"audio-bytes")
    assert body["url"].endswith(".mp3")

    fetch = client.get(body["media_path"])
    assert fetch.status_code == 200
    assert fetch.content == b"audio-bytes"

    queued = client.post("/enqueue", json={"url": body["url"]})
    assert queued.status_code == 200
    queued_body = queued.json()
    assert queued_body["item"]["provider"] == "upload"
    assert queued_body["item"]["mime_type"] == "audio/mpeg"
    assert queued_body["queue_length"] == 1


def test_ingest_m4a_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared M4A"},
        files={"file": ("clip.m4a", b"audio-bytes", "audio/m4a")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["title"] == "Shared M4A"
    assert body["item"]["mime_type"] == "audio/m4a"
    assert body["url"].endswith(".m4a")

    queued = client.post("/enqueue", json={"url": body["url"]})
    assert queued.status_code == 200
    queued_body = queued.json()
    assert queued_body["item"]["provider"] == "upload"
    assert queued_body["item"]["mime_type"] == "audio/m4a"
    assert queued_body["queue_length"] == 1


def test_ingest_audio_ogg_round_trip_and_enqueue(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Shared OGG"},
        files={"file": ("clip.ogg", b"audio-bytes", "audio/ogg")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["mime_type"] == "audio/ogg"
    assert body["url"].endswith(".ogg")


def test_ingest_audio_octet_stream_uses_allowed_extension(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        data={"title": "Generic M4A"},
        files={"file": ("clip.m4a", b"audio-bytes", "application/octet-stream")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["item"]["provider"] == "upload"
    assert body["item"]["mime_type"] == "application/octet-stream"
    assert body["url"].endswith(".m4a")


def test_ingest_media_enqueue_single_call(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media/enqueue",
        data={"title": "Queued Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["action"] == "enqueue"
    assert body["item"]["provider"] == "upload"
    assert body["result"]["status"] == "queued"
    assert body["result"]["item"]["provider"] == "upload"
    assert body["result"]["queue_length"] == 1


def test_ingest_media_rejects_unsupported_type(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        files={"file": ("clip.mov", b"video-bytes", "video/quicktime")},
    )

    assert response.status_code == 400
    assert "Unsupported media type" in response.json()["detail"]


def test_ingest_media_rejects_empty_upload(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post(
        "/ingest/media",
        files={"file": ("clip.mp4", b"", "video/mp4")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Uploaded media is empty"


def test_play_now_accepts_uploaded_media_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: None)

    captured: dict[str, object] = {}

    def fake_play_item(url, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["url"] = url
        return {"url": url, "provider": "upload", "title": "Shared Clip"}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    ).json()

    response = client.post("/play_now", json={"url": created["url"]})

    assert response.status_code == 200
    assert captured["url"] == created["url"]
    assert response.json()["now_playing"]["provider"] == "upload"


def test_play_now_accepts_uploaded_audio_url(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: None)

    captured: dict[str, object] = {}

    def fake_play_item(url, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["url"] = url
        return {"url": url, "provider": "upload", "title": "Shared Audio", "mime_type": "audio/mpeg"}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Shared Audio"},
        files={"file": ("clip.mp3", b"audio-bytes", "audio/mpeg")},
    ).json()

    response = client.post("/play_now", json={"url": created["url"]})

    assert response.status_code == 200
    assert captured["url"] == created["url"]
    assert response.json()["now_playing"]["provider"] == "upload"


def test_upload_items_mark_unavailable_after_removal(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)
    monkeypatch.setattr(routes.state, "persist_queue", lambda: None)
    monkeypatch.setattr(routes.state, "QUEUE", [], raising=False)
    monkeypatch.setattr(routes.state, "HISTORY", [], raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    ).json()
    item = dict(created["item"])
    routes.state.QUEUE[:] = [dict(item)]
    routes.state.HISTORY[:] = [dict(item)]

    shutil.rmtree(upload_store.upload_dir(created["media_id"]))

    queue_response = client.get("/queue")
    assert queue_response.status_code == 200
    assert queue_response.json()["queue"][0]["available"] is False

    history_response = client.get("/history")
    assert history_response.status_code == 200
    assert history_response.json()["history"][0]["available"] is False


def test_enqueue_stale_uploaded_media_returns_gone(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    app = create_app(testing=True)
    client = TestClient(app)

    created = client.post(
        "/ingest/media",
        data={"title": "Clip"},
        files={"file": ("clip.mp4", b"video-bytes", "video/mp4")},
    ).json()

    shutil.rmtree(upload_store.upload_dir(created["media_id"]))

    response = client.post("/enqueue", json={"url": created["url"]})

    assert response.status_code == 410
    assert response.json()["detail"] == "Uploaded media expired or removed"


def test_ingest_media_play_starts_progressively(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB", "1")
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS", "1")
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    captured: dict[str, object] = {}
    toasts: list[dict[str, object]] = []

    def fake_play_item(item, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["item"] = dict(item)
        captured["mode"] = mode
        return {"url": item["url"], "provider": "upload", "title": item["title"]}

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: toasts.append(dict(kwargs)))

    app = create_app(testing=True)
    client = TestClient(app)

    payload = (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + (b"x" * 1024) + b"moov" + (b"y" * (2 * 1024 * 1024)))
    response = client.post(
        "/ingest/media/play",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["playback_mode"] == "progressive"
    assert body["now_playing"]["provider"] == "upload"
    assert body["fallback_reason"] == ""
    assert captured["mode"] == "ingest_media_play"
    assert str(captured["item"]["_local_stream_path"]).endswith(".mp4")
    assert toasts == []


def test_ingest_media_play_falls_back_to_full_upload_with_toast(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    uploads_dir = tmp_path / "uploads"
    monkeypatch.setenv("RELAYTV_UPLOADS_DIR", str(uploads_dir))
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MP4_READY_MB", "1")
    monkeypatch.setenv("RELAYTV_UPLOAD_PROGRESSIVE_MIN_THROUGHPUT_KBPS", "1")
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(uploads_dir), raising=False)

    captured: dict[str, object] = {}
    toasts: list[dict[str, object]] = []

    def fake_play_item(item, use_resolver=True, cec=False, clear_queue=False, mode="play_now"):
        captured["item"] = dict(item)
        captured["mode"] = mode
        return {"url": item["url"], "provider": "upload", "title": item["title"]}

    def fake_progressive_start_ready(meta: dict, session: dict) -> tuple[bool, str]:
        if int(session.get("bytes_received") or 0) >= int(session.get("ready_threshold_bytes") or 0):
            return False, "probe_failed"
        return False, "buffering"

    monkeypatch.setattr(routes.player, "play_item", fake_play_item)
    monkeypatch.setattr(routes, "_push_overlay_toast", lambda **kwargs: toasts.append(dict(kwargs)))
    monkeypatch.setattr(upload_store, "progressive_start_ready", fake_progressive_start_ready)

    app = create_app(testing=True)
    client = TestClient(app)

    payload = (b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + (b"x" * (2 * 1024 * 1024)))
    response = client.post(
        "/ingest/media/play",
        data={"title": "Shared Clip"},
        files={"file": ("clip.mp4", payload, "video/mp4")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["playback_mode"] == "full_upload"
    assert body["fallback_reason"] == "probe_failed"
    assert body["now_playing"]["provider"] == "upload"
    assert captured["mode"] == "ingest_media_play"
    assert len(toasts) == 1
    assert "Waiting for full file" in str(toasts[0].get("text") or "")


def test_idle_page_uses_banner_brand_asset() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/idle')

    assert response.status_code == 200
    assert '/pwa/brand/banner.png' in response.text
    assert 'html{font-size:clamp(12px,1.4815vmin,32px)}' in response.text
    assert '.time{font-size:8rem' in response.text
    assert 'width:min(100%,70rem)' in response.text
    assert "root.style.setProperty('--idleQrSizePx', `${size / 16}rem`);" in response.text


def test_overlay_playback_visibility_prefers_session_and_transition_signals() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/x11/overlay')

    assert response.status_code == 200
    assert 'function overlayPlaybackVisible(state)' in response.text
    assert "j.native_qt_mpv_runtime_stream_loaded === true" in response.text
    assert "j.transition_in_progress === true" in response.text
    assert "return qtRuntimeActive || sessionActive;" in response.text


def test_pwa_brand_banner_png_asset_resolves_with_logo_fallback() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/pwa/brand/banner.png')

    assert response.status_code == 200
    assert response.headers['content-type'].startswith(('image/png', 'image/svg+xml'))


def test_jellyfin_plugin_ingress_is_deprecated() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.post('/integrations/jellyfin/push', json={'item_id': '123', 'play_command': 'PlayNow'})

    assert response.status_code == 410
    assert response.json() == {
        'detail': 'jellyfin plugin ingress deprecated; use RelayTV native Jellyfin client or /integrations/jellyfin/command'
    }


def test_native_idle_weather_layout_normalizes_to_supported_values() -> None:
    assert _native_idle_weather_layout({}) == 'split'
    assert _native_idle_weather_layout({'idle_panels': {'weather': {'layout': 'minimal'}}}) == 'minimal'
    assert _native_idle_weather_layout({'idle_panels': {'weather': {'layout': 'hourly'}}}) == 'split'
    assert _native_idle_weather_layout({'idle_panels': {'weather': {'layout': 'unexpected'}}}) == 'split'


def test_qt_idle_defaults_prefer_browser_overlay(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_ENABLED', raising=False)
    monkeypatch.delenv('RELAYTV_QT_NATIVE_IDLE', raising=False)

    assert _embedded_web_overlay_enabled() is True
    assert _native_idle_overlay_enabled() is False


def test_qt_runtime_defaults_prefer_libmpv_and_overlay_toasts_on_x86(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_LIBMPV', raising=False)
    monkeypatch.delenv('RELAYTV_QT_NATIVE_TOASTS', raising=False)
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_SOFTWARE', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'x86_64')

    assert _libmpv_enabled() is True
    assert _native_overlay_toasts_enabled() is False
    assert _overlay_software_mode_enabled() is False


def test_qt_overlay_software_mode_defaults_on_for_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_SOFTWARE', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'aarch64')

    assert _overlay_software_mode_enabled() is True


def test_qt_runtime_defaults_disable_libmpv_on_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_LIBMPV', raising=False)
    monkeypatch.setattr('relaytv_app.qt_shell_app.platform.machine', lambda: 'aarch64')

    assert _libmpv_enabled() is False


def test_resolver_playback_transition_window_sec_defaults_and_clamps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC', raising=False)
    assert player._resolver_playback_transition_window_sec() == 20.0

    monkeypatch.setenv('RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC', '2')
    assert player._resolver_playback_transition_window_sec() == 5.0

    monkeypatch.setenv('RELAYTV_RESOLVE_PLAYBACK_TRANSITION_SEC', '120')
    assert player._resolver_playback_transition_window_sec() == 60.0


def test_mark_playback_transition_allows_longer_resolve_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.time, 'time', lambda: 1000.0)
    monkeypatch.setattr(player, '_PLAYBACK_TRANSITION_UNTIL', 0.0)

    player._mark_playback_transition(window_sec=20.0)

    assert player._PLAYBACK_TRANSITION_UNTIL == 1020.0


def test_youtube_arm_safe_strategies_prefer_quality_retries_before_plain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, '_preferred_js_runtime_spec', lambda: 'node')

    strategies = resolver._build_youtube_arm_safe_strategies(
        ['yt-dlp', '--cookies', '/data/cookies.txt', '--no-playlist'],
        ['fmt1', 'best'],
    )

    assert '--cookies' in strategies[0][0]
    assert '--remote-components' in strategies[0][0]
    assert strategies[0][1] == ['fmt1', 'best']
    assert strategies[-1][0] == ['yt-dlp', '--no-playlist']
    assert strategies[-1][1] == ['fmt1', 'best']


def test_youtube_strategies_prefer_quality_retries_before_plain_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, '_preferred_js_runtime_spec', lambda: 'node')

    strategies = resolver._build_youtube_strategies(
        ['yt-dlp', '--cookies', '/data/cookies.txt', '--js-runtimes', 'node', '--no-playlist'],
        ['fmt1', 'best'],
    )

    assert '--cookies' in strategies[0][0]
    assert '--remote-components' in strategies[0][0]
    assert strategies[0][1] == ['', 'best']
    assert (['yt-dlp', '--no-playlist'], ['fmt1', 'best']) in strategies


def test_repair_orphan_runtime_playback_ignores_idle_core_with_stale_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None)
    monkeypatch.setattr(player.state, 'QUEUE', [])

    assert player._repair_orphan_runtime_playback(
        {
            'path': 'https://example.com/stale.m3u8',
            'core-idle': True,
            'eof-reached': False,
        }
    ) is False


def test_repair_orphan_runtime_playback_ignores_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None)
    monkeypatch.setattr(player.state, 'QUEUE', [])
    monkeypatch.setattr(player.state, 'AUTO_NEXT_SUPPRESS_UNTIL', player.time.time() + 3600.0)

    assert player._repair_orphan_runtime_playback(
        {
            'path': 'https://example.com/stale-after-close.m3u8',
            'core-idle': False,
            'eof-reached': False,
        }
    ) is False


def test_repair_orphan_runtime_playback_ignores_natural_idle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'idle')
    monkeypatch.setattr(player.state, 'NOW_PLAYING', None)
    monkeypatch.setattr(player.state, 'QUEUE', [])
    monkeypatch.setattr(player, 'natural_idle_reset_holding', lambda: True)

    assert player._repair_orphan_runtime_playback(
        {
            'path': 'https://example.com/stale-after-queue-end.m3u8',
            'core-idle': False,
            'eof-reached': False,
        }
    ) is False


def test_playback_runtime_idle_or_ended_ignores_active_play_transition(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, '_is_playing', lambda: False)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: True)
    monkeypatch.setattr(player, 'auto_next_transitioning', lambda: False)

    assert player._playback_runtime_idle_or_ended() is False


def test_is_playing_ignores_idle_qt_socket_with_stale_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: True)
    monkeypatch.setattr(
        player,
        'mpv_get_many',
        lambda props: {
            'core-idle': True,
            'eof-reached': False,
            'path': 'https://example.com/stale.m3u8',
        },
    )
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=True: False)

    assert player._is_playing() is False


def test_qt_toasts_follow_overlay_by_default_on_pi(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_NATIVE_TOASTS', raising=False)
    monkeypatch.delenv('RELAYTV_QT_OVERLAY_ENABLED', raising=False)
    monkeypatch.setattr('relaytv_app.routes._qt_shell_runtime_running', lambda: True)
    monkeypatch.setattr('relaytv_app.routes.video_profile.get_profile', lambda: {'decode_profile': 'arm_safe'})

    assert _overlay_prefers_native_qt_toast() is False


def test_status_keeps_closed_session_non_playing_during_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'stopped', 'closed': True}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', routes.time.time() + 3600.0, raising=False)
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})

    payload = routes.status()

    assert payload['state'] == 'closed'
    assert payload['playing'] is False
    assert payload['resume_available'] is True


def test_playback_state_keeps_closed_session_non_playing_during_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'closed', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'stopped', 'closed': True}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', routes.time.time() + 3600.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(
        routes.player,
        'qt_shell_runtime_telemetry',
        lambda **_: {'selected': True, 'available': True, 'freshness': 'fresh', 'mpv_runtime_playback_active': True},
    )
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
            'playback_runtime_previous_state': 'playing',
            'playback_runtime_previous_reason': 'runtime_active',
            'playback_runtime_state_since_unix': 1000.0,
            'playback_runtime_last_transition_unix': 1000.0,
            'playback_runtime_time_in_state_sec': 0.0,
        },
    )

    payload = routes.playback_state()

    assert payload['state'] == 'closed'
    assert payload['playing'] is False
    assert payload['has_now_playing'] is True


def test_status_keeps_idle_non_playing_during_natural_idle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.player, 'is_playing', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})

    payload = routes.status()

    assert payload['state'] == 'idle'
    assert payload['playing'] is False
    assert payload['resume_available'] is False


def test_status_preserves_paused_session_during_runtime_dropout(monkeypatch: pytest.MonkeyPatch) -> None:
    session_sets: list[str] = []

    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes.player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(routes.player, '_qt_runtime_active', lambda **_: False)
    monkeypatch.setattr(routes.player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, '_effective_ytdl_format', lambda s=None: '')
    monkeypatch.setattr(routes.player, 'get_mpv_log_tail', lambda lines=40: [])
    monkeypatch.setattr(routes.player, 'IPC_PATH', '/tmp/test-mpv.sock', raising=False)
    monkeypatch.setattr(routes.os.path, 'exists', lambda p: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', {'title': 'sample'}, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda val: session_sets.append(val))
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {})
    monkeypatch.setattr(
        routes,
        '_runtime_capabilities',
        lambda playing=None: {
            'native_qt_mpv_runtime_paused': True,
            'native_qt_mpv_runtime_stream_loaded': True,
            'native_qt_mpv_runtime_path': 'https://example.com/current.mp4',
        },
    )

    payload = routes.status()

    assert payload['state'] == 'paused'
    assert payload['playing'] is True
    assert payload['paused'] is True
    assert session_sets == ['paused']


def test_playback_toggle_resumes_paused_session_without_reloading(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.player, 'is_playing', lambda: False)
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(
        routes.state,
        'NOW_PLAYING',
        {
            'url': 'https://example.com/current',
            'stream': 'https://example.com/stream.mp4',
            'resume_pos': 42.0,
        },
        raising=False,
    )
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda _v: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda _v: None)
    monkeypatch.setattr(routes.state, 'set_pause_reason', lambda _v: None)
    monkeypatch.setattr(
        routes.player,
        '_load_stream_in_existing_mpv',
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('paused resume should not reload stream')),
    )
    monkeypatch.setattr(
        routes.player,
        'start_mpv',
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError('paused resume should not restart mpv')),
    )
    calls: list[tuple[str, object]] = []
    monkeypatch.setattr(
        routes.player,
        'mpv_set_result',
        lambda prop, value: calls.append((prop, value)) or {
            'error': 'success',
            'request_id': 'qtctl-toggle-resume',
            'ack_observed': True,
            'ack_reason': 'control_acknowledged',
        },
    )

    resp = routes.playback_toggle()

    assert resp['ok'] is True
    assert resp['action'] == 'resume'
    assert resp['paused'] is False
    assert resp['request_id'] == 'qtctl-toggle-resume'
    assert calls == [('pause', False)]


def test_playback_state_keeps_idle_non_playing_during_natural_idle_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: True)
    monkeypatch.setattr(
        routes.player,
        'qt_shell_runtime_telemetry',
        lambda **_: {'selected': True, 'available': True, 'freshness': 'fresh', 'mpv_runtime_playback_active': True},
    )
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
            'playback_runtime_previous_state': 'playing',
            'playback_runtime_previous_reason': 'runtime_active',
            'playback_runtime_state_since_unix': 1000.0,
            'playback_runtime_last_transition_unix': 1000.0,
            'playback_runtime_time_in_state_sec': 0.0,
        },
    )

    payload = routes.playback_state()

    assert payload['state'] == 'idle'
    assert payload['playing'] is False
    assert payload['has_now_playing'] is False


def test_playback_state_exposes_transition_during_manual_play_handoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(routes.state, 'SESSION_STATE', 'idle', raising=False)
    monkeypatch.setattr(routes.state, 'NOW_PLAYING', None, raising=False)
    monkeypatch.setattr(routes.state, 'QUEUE', [], raising=False)
    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'playback_transitioning', lambda: True)
    monkeypatch.setattr(routes.player, 'auto_next_transitioning', lambda: False)
    monkeypatch.setattr(routes.player, 'natural_idle_reset_holding', lambda: False)
    monkeypatch.setattr(routes.player, 'qt_shell_runtime_telemetry', lambda **_: {'selected': False})
    monkeypatch.setattr(
        routes.state,
        'update_playback_runtime_state',
        lambda next_state, reason='': {
            'playback_runtime_state': next_state,
            'playback_runtime_state_reason': reason,
        },
    )

    payload = routes.playback_state()

    assert payload['transition_in_progress'] is True
    assert payload['transitioning_between_items'] is True
    assert payload['playback_runtime_state'] == 'buffering'


def test_resume_clear_sets_explicit_stop_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    stop_calls: list[str] = []
    persisted: list[str] = []

    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, 'stop_mpv', lambda: stop_calls.append('stop'))
    monkeypatch.setattr(routes.state, 'set_now_playing', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_position', lambda value: None)
    monkeypatch.setattr(routes.state, 'set_session_state', lambda value: None)
    monkeypatch.setattr(routes.state, 'persist_queue', lambda: persisted.append('persist'))

    response = routes.clear_resumable_session()

    assert response == {'status': 'cleared', 'resume_available': False}
    assert stop_calls == ['stop']
    assert persisted == ['persist']
    assert routes.state.AUTO_NEXT_SUPPRESS_UNTIL > routes.time.time() + 3600.0


def test_seek_routes_set_extended_transition_hold(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list[float] = []

    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, '_mark_playback_transition', lambda sec=None: marked.append(float(sec or 0.0)))
    monkeypatch.setattr(routes.player, '_qt_shell_runtime_accepts_mpv_commands', lambda: False)
    monkeypatch.setattr(routes.player, 'mpv_command', lambda cmd: {'error': 'success', 'request_id': 'seek-ok'})

    seek_resp = routes.seek(routes.SeekReq(sec=30))
    seek_abs_resp = routes.seek_abs(routes.SeekAbsReq(sec=120))

    assert seek_resp['ok'] is True
    assert seek_abs_resp['ok'] is True
    assert marked == [6.0, 6.0]
    assert routes.state.AUTO_NEXT_SUPPRESS_UNTIL > routes.time.time() + 5.0


def test_seek_routes_use_time_pos_setter_for_qt_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    marked: list[float] = []
    mpv_commands: list[list[object]] = []
    set_calls: list[tuple[str, float]] = []

    monkeypatch.setattr(routes.state, 'AUTO_NEXT_SUPPRESS_UNTIL', 0.0, raising=False)
    monkeypatch.setattr(routes.player, '_mark_playback_transition', lambda sec=None: marked.append(float(sec or 0.0)))
    monkeypatch.setattr(routes.player, '_qt_shell_runtime_accepts_mpv_commands', lambda: True)
    monkeypatch.setattr(routes.player, 'mpv_get_many', lambda props: {'time-pos': 90.0, 'duration': 120.0})
    monkeypatch.setattr(
        routes.player,
        'mpv_set_result',
        lambda prop, value: set_calls.append((prop, float(value))) or {'error': 'success', 'request_id': 'seek-set'},
    )
    monkeypatch.setattr(routes.player, 'mpv_command', lambda cmd: mpv_commands.append(list(cmd)) or {'error': 'success'})

    seek_resp = routes.seek(routes.SeekReq(sec=30))
    seek_abs_resp = routes.seek_abs(routes.SeekAbsReq(sec=200))

    assert seek_resp['ok'] is True
    assert seek_abs_resp['ok'] is True
    assert marked == [6.0, 6.0]
    assert set_calls == [('time-pos', 120.0), ('time-pos', 120.0)]
    assert mpv_commands == []


def test_qt_runtime_seek_uses_extended_ack_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[float | None] = []

    monkeypatch.setattr(player, '_qt_shell_runtime_accepts_mpv_commands', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_preferred', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_command', lambda cmd: {'error': 'success', 'request_id': 'seek-ok'})

    def fake_finalize(result, *, timeout_sec=None):
        captured.append(timeout_sec)
        return {'error': 'success', 'request_id': 'seek-ok', 'ack_observed': True, 'ack_reason': 'control_acknowledged'}

    monkeypatch.setattr(player, '_qt_shell_runtime_finalize_control_result', fake_finalize)

    result = player.mpv_command(['seek', 180.0, 'absolute'])

    assert result['error'] == 'success'
    assert captured == [3.5]


def test_pause_timeout_is_tolerated_when_runtime_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        player,
        'mpv_command',
        lambda cmd_list: {
            'error': 'timeout_or_unavailable',
            'request_id': 'qtctl-pause-timeout',
            'ack_observed': False,
            'ack_reason': 'timeout_or_unavailable',
        },
    )
    monkeypatch.setattr(
        player,
        'qt_shell_runtime_telemetry',
        lambda max_age_sec=3.0: {'alive': True},
    )
    monkeypatch.setattr(player, '_MPV_PROP_CACHE', {}, raising=False)
    monkeypatch.setattr(player, '_MPV_PROP_CACHE_TS', 0.0, raising=False)

    result = player.mpv_set_result('pause', True)

    assert result['error'] == 'success'
    assert result['request_id'] == 'qtctl-pause-timeout'
    assert result['ack_observed'] is False
    assert result['ack_reason'] == 'control_pending'
    assert player._MPV_PROP_CACHE['pause'] is True


def test_qt_toast_override_can_force_native_toasts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('RELAYTV_QT_NATIVE_TOASTS', '1')
    monkeypatch.setenv('RELAYTV_QT_OVERLAY_ENABLED', '1')
    monkeypatch.setattr('relaytv_app.routes._qt_shell_runtime_running', lambda: True)

    assert _overlay_prefers_native_qt_toast() is True


def test_notification_capabilities_expose_native_qt_deprecation_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('RELAYTV_QT_NATIVE_IDLE', raising=False)
    monkeypatch.delenv('RELAYTV_QT_NATIVE_TOASTS', raising=False)
    monkeypatch.setattr('relaytv_app.routes._qt_shell_runtime_running', lambda: True)

    caps = _notification_capabilities()

    assert caps['native_qt_idle_deprecated'] is True
    assert caps['native_qt_idle_status'] == 'override_only'
    assert caps['native_qt_idle_override_enabled'] is False
    assert caps['native_qt_toasts_deprecated'] is True
    assert caps['native_qt_toasts_status'] == 'override_only'
    assert caps['native_qt_toasts_override_enabled'] is False


def test_notifications_capabilities_endpoint_includes_native_qt_deprecation_metadata() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/notifications/capabilities')

    assert response.status_code == 200
    payload = response.json()
    assert payload['native_qt_idle_deprecated'] is True
    assert payload['native_qt_idle_status'] == 'override_only'
    assert payload['native_qt_toasts_deprecated'] is True
    assert payload['native_qt_toasts_status'] == 'override_only'


def test_status_endpoint_includes_native_qt_deprecation_metadata() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/status')

    assert response.status_code == 200
    payload = response.json()
    assert payload['native_qt_idle_deprecated'] is True
    assert payload['native_qt_idle_status'] == 'override_only'
    assert payload['native_qt_toasts_deprecated'] is True
    assert payload['native_qt_toasts_status'] == 'override_only'


def test_stop_mpv_persists_live_runtime_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(player, 'mpv_get', lambda prop: 73.0 if prop == 'volume' else None)
    monkeypatch.setattr(player, '_stop_qt_shell', lambda: observed.setdefault('stop_called', True))
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset_called', True))
    monkeypatch.setattr(player, '_cleanup_ipc_socket', lambda: observed.setdefault('cleanup_called', True))
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(player, 'start_splash_screen', lambda: observed.setdefault('splash_called', True))
    monkeypatch.setattr(player.state, 'update_settings', lambda patch: observed.setdefault('patch', dict(patch)))
    monkeypatch.setattr(player, 'MPV_PROC', None)

    player.stop_mpv()

    assert observed['patch'] == {'volume': 73.0}
    assert observed['stop_called'] is True


def test_stop_mpv_ignores_invalid_runtime_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setattr(player, 'mpv_get', lambda prop: 'not-a-number')
    monkeypatch.setattr(player, '_stop_qt_shell', lambda: observed.setdefault('stop_called', True))
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: None)
    monkeypatch.setattr(player, '_cleanup_ipc_socket', lambda: None)
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: False)
    monkeypatch.setattr(player, 'start_splash_screen', lambda: None)
    monkeypatch.setattr(player.state, 'update_settings', lambda patch: observed.setdefault('patch', dict(patch)))
    monkeypatch.setattr(player, 'MPV_PROC', None)

    player.stop_mpv()

    assert 'patch' not in observed
    assert observed['stop_called'] is True


def test_idle_qt_shell_can_be_reused_for_stream_load(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'control_file': '/tmp/relaytv-qt-runtime-control.json',
            'mpv_runtime_core_idle': True,
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': False,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_error': '',
            'mpv_runtime_sample_detail': 'heartbeat',
        },
    )
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=False: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: False)

    def fake_load(stream_url: str, audio_url: str | None = None):
        observed['load'] = {'stream': stream_url, 'audio': audio_url}
        return {'error': 'success'}

    monkeypatch.setattr(player, '_qt_shell_runtime_load_stream', fake_load)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset', True))

    assert player._load_stream_in_existing_mpv(
        'https://example.com/stream.m3u8',
        audio_url='https://example.com/audio.m4a',
    ) is True
    assert observed['load'] == {
        'stream': 'https://example.com/stream.m3u8',
        'audio': 'https://example.com/audio.m4a',
    }
    assert observed['reset'] is True


def test_idle_qt_shell_can_be_reused_for_video_only_stream_load(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'alive': True,
            'control_file': '/tmp/relaytv-qt-runtime-control.json',
            'mpv_runtime_core_idle': True,
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': False,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_error': '',
            'mpv_runtime_sample_detail': '',
        },
    )
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=False: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: False)

    def fake_load(stream_url: str, audio_url: str | None = None):
        observed['load'] = {'stream': stream_url, 'audio': audio_url}
        return {'error': 'success'}

    monkeypatch.setattr(player, '_qt_shell_runtime_load_stream', fake_load)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset', True))

    assert player._load_stream_in_existing_mpv('https://example.com/stream.m3u8') is True
    assert observed['load'] == {
        'stream': 'https://example.com/stream.m3u8',
        'audio': None,
    }
    assert observed['reset'] is True


def test_idle_qt_shell_can_be_reused_without_fresh_snapshot_control_file(monkeypatch: pytest.MonkeyPatch) -> None:
    observed: dict[str, object] = {}

    monkeypatch.setenv('RELAYTV_MPV_SEAMLESS_REPLACE', '1')
    monkeypatch.setattr(player, '_qt_shell_backend_enabled', lambda: True)
    monkeypatch.setattr(player, '_qt_runtime_uses_external_mpv', lambda: False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(player, '_qt_shell_runtime_snapshot', lambda max_age_sec=3.0: {})
    monkeypatch.setattr(player, '_qt_shell_runtime_control_file', lambda: '/tmp/relaytv-qt-runtime-control.json')
    monkeypatch.setattr(player, '_qt_runtime_active', lambda require_active_session=False: False)
    monkeypatch.setattr(player.os.path, 'exists', lambda path: False)

    def fake_load(stream_url: str, audio_url: str | None = None):
        observed['load'] = {'stream': stream_url, 'audio': audio_url}
        return {'error': 'success'}

    monkeypatch.setattr(player, '_qt_shell_runtime_load_stream', fake_load)
    monkeypatch.setattr(player, '_reset_mpv_up_next_state', lambda: observed.setdefault('reset', True))

    assert player._load_stream_in_existing_mpv('https://example.com/stream.m3u8') is True
    assert observed['load'] == {
        'stream': 'https://example.com/stream.m3u8',
        'audio': None,
    }
    assert observed['reset'] is True


def test_qt_runtime_active_treats_paused_loaded_stream_as_active(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(player.state, 'SESSION_STATE', 'paused', raising=False)
    monkeypatch.setattr(player.state, 'NOW_PLAYING', {'url': 'https://example.com/video.mp4'}, raising=False)
    monkeypatch.setattr(player, '_QT_RUNTIME_ACTIVE_LAST_TS', 0.0, raising=False)
    monkeypatch.setattr(player, '_qt_shell_running', lambda: True)
    monkeypatch.setattr(
        player,
        '_qt_shell_runtime_snapshot',
        lambda max_age_sec=3.0: {
            'mpv_runtime_playback_active': False,
            'mpv_runtime_stream_loaded': True,
            'mpv_runtime_playback_started': False,
            'mpv_runtime_paused': True,
            'mpv_runtime_core_idle': True,
            'mpv_runtime_eof_reached': False,
        },
    )

    assert player._qt_runtime_active(require_active_session=True) is True


def test_pwa_weather_asset_resolves_google_icon_aliases() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/pwa/weather/partly_cloudy_day.svg?theme=dark')

    assert response.status_code == 200
    assert 'image/svg+xml' in response.headers['content-type']


def test_pwa_weather_asset_uses_theme_directory_when_available() -> None:
    app = create_app(testing=True)
    client = TestClient(app)

    response = client.get('/pwa/weather/clear_day.svg?theme=light')

    assert response.status_code == 200
    assert 'image/svg+xml' in response.headers['content-type']
