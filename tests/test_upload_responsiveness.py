# SPDX-License-Identifier: GPL-3.0-only
"""Upload ingestion must not stall the event loop (F08).

Both ingest handlers ran their file writes, fsyncs, JSON session writes, and
cleanup scans directly on the loop. A 4 GiB upload issued roughly 4000 fsyncs
and 4000 session rewrites there, so HTTP controls and realtime subscribers
stopped making progress for the duration.
"""
import asyncio
import inspect
import os
import time

import pytest
from fastapi.testclient import TestClient

from relaytv_app import upload_store
from relaytv_app.main import create_app
from relaytv_app.routes import uploads


@pytest.fixture
def uploads_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(root))
    return root


# --- the loop stays free -----------------------------------------------------


def test_no_blocking_io_remains_on_the_event_loop() -> None:
    """Every disk call in an async handler must go through the threadpool."""
    offenders: list[str] = []
    for name in ("ingest_media", "ingest_media_play", "ingest_media_enqueue"):
        handler = getattr(uploads, name)
        if not inspect.iscoroutinefunction(handler):
            continue
        source = inspect.getsource(handler)
        for line in source.splitlines():
            stripped = line.strip()
            if stripped.startswith("#") or "run_in_threadpool" in stripped:
                continue
            for call in (
                "os.fsync(",
                "os.makedirs(",
                "os.replace(",
                "upload_store.write_session(",
                "upload_store.write_metadata(",
                "upload_store.cleanup_uploads(",
                "upload_store.delete_upload(",
            ):
                if call in stripped:
                    offenders.append(f"{name}: {stripped}")
    assert not offenders, offenders


@pytest.mark.anyio
async def test_health_stays_responsive_while_the_disk_is_slow(uploads_root, monkeypatch) -> None:
    """Run a real upload against a deliberately slow disk and poll /health.

    The write is made genuinely blocking (time.sleep, not asyncio.sleep), so
    this fails by timing out if the write ever runs on the event loop again.
    """
    import httpx

    real_write = uploads._write_chunk

    def _slow_write(handle, chunk, *, sync):
        time.sleep(0.15)
        return real_write(handle, chunk, sync=sync)

    monkeypatch.setattr(uploads, "_write_chunk", _slow_write)

    app = create_app(testing=True)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://tv.local") as client:
        # 6 MiB in 1 MiB chunks: ~0.9s of blocking disk time.
        upload = asyncio.create_task(
            client.post(
                "/ingest/media",
                files={"file": ("clip.mp4", b"q" * (6 * 1024 * 1024), "video/mp4")},
                data={"title": "Clip"},
            )
        )
        await asyncio.sleep(0.2)

        health_latencies: list[float] = []
        for _ in range(5):
            started = time.monotonic()
            response = await asyncio.wait_for(client.get("/health"), timeout=2.0)
            health_latencies.append(time.monotonic() - started)
            assert response.status_code == 200
            await asyncio.sleep(0.05)

        result = await asyncio.wait_for(upload, timeout=30.0)

    assert result.status_code == 200, result.text
    # Each health check must return promptly; a blocked loop would make these
    # wait out the whole upload.
    assert max(health_latencies) < 0.15, health_latencies


@pytest.fixture
def anyio_backend():
    return "asyncio"


# --- durability policy -------------------------------------------------------


def test_sync_cadence_is_batched_not_per_chunk() -> None:
    assert uploads._DURABLE_SYNC_BYTES >= 8 * 1024 * 1024
    assert uploads._DURABLE_SYNC_SEC >= 1.0


def test_write_chunk_only_syncs_when_asked(tmp_path) -> None:
    synced: list[int] = []
    real_fsync = os.fsync
    path = tmp_path / "f.bin"

    def _counting_fsync(fd):
        synced.append(fd)
        return real_fsync(fd)

    with open(path, "wb") as handle:
        uploads.os.fsync = _counting_fsync
        try:
            uploads._write_chunk(handle, b"abc", sync=False)
            assert synced == []
            uploads._write_chunk(handle, b"def", sync=True)
            assert len(synced) == 1
        finally:
            uploads.os.fsync = real_fsync

    assert path.read_bytes() == b"abcdef"


def test_empty_final_chunk_still_syncs(tmp_path) -> None:
    """The end-of-upload sync is written as a zero-length flush."""
    synced: list[int] = []
    real_fsync = os.fsync

    with open(tmp_path / "f.bin", "wb") as handle:
        uploads.os.fsync = lambda fd: synced.append(fd) or real_fsync(fd)
        try:
            uploads._write_chunk(handle, b"", sync=True)
        finally:
            uploads.os.fsync = real_fsync

    assert len(synced) == 1


# --- behavior preserved ------------------------------------------------------


def _upload(client, payload: bytes, endpoint: str = "/ingest/media"):
    return client.post(
        endpoint,
        files={"file": ("clip.mp4", payload, "video/mp4")},
        data={"title": "Clip"},
    )


def test_upload_still_stores_the_file(uploads_root) -> None:
    client = TestClient(create_app(testing=True))
    payload = b"\x00\x01" * 4096

    response = _upload(client, payload)

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    served = client.get(body["url"].replace("http://testserver", ""))
    assert served.status_code == 200
    assert served.content == payload


def test_size_limit_is_still_enforced(uploads_root, monkeypatch) -> None:
    monkeypatch.setattr(upload_store, "max_upload_bytes", lambda settings: 1024)
    client = TestClient(create_app(testing=True))

    response = _upload(client, b"x" * 4096)

    assert response.status_code == 413


def test_partial_file_is_removed_when_the_upload_fails(uploads_root, monkeypatch) -> None:
    monkeypatch.setattr(upload_store, "max_upload_bytes", lambda settings: 1024)
    client = TestClient(create_app(testing=True))

    _upload(client, b"x" * 4096)

    leftovers = [p for p in uploads_root.rglob("*.part")]
    assert leftovers == [], leftovers


def test_empty_upload_is_rejected(uploads_root) -> None:
    client = TestClient(create_app(testing=True))
    assert _upload(client, b"").status_code == 400


def test_unsupported_type_is_rejected(uploads_root) -> None:
    client = TestClient(create_app(testing=True))
    response = client.post(
        "/ingest/media",
        files={"file": ("x.txt", b"hello", "text/plain")},
        data={"title": "x"},
    )
    assert response.status_code == 400


def test_large_upload_does_not_sync_per_chunk(uploads_root, monkeypatch) -> None:
    """The cost this PR removes, measured rather than asserted in prose."""
    syncs: list[int] = []
    real = uploads._write_chunk

    def _counting(handle, chunk, *, sync):
        if sync:
            syncs.append(len(chunk))
        return real(handle, chunk, sync=sync)

    monkeypatch.setattr(uploads, "_write_chunk", _counting)
    client = TestClient(create_app(testing=True))

    # 8 MiB in 1 MiB chunks: eight chunks, well under the batch threshold.
    _upload(client, b"z" * (8 * 1024 * 1024))

    # Only the final flush syncs, instead of one sync per chunk.
    assert len(syncs) == 1, f"expected a single durable sync, got {len(syncs)}"


def test_progressive_session_still_written_on_state_changes() -> None:
    """Batching the heartbeat must not batch the state transitions."""
    source = inspect.getsource(uploads.ingest_media_play)
    # Each transition writes the session immediately, off the loop.
    for marker in (
        "mark_session_progressive_started",
        "mark_session_fallback",
        "mark_session_complete",
    ):
        index = source.index(marker)
        following = source[index : index + 400]
        assert "write_session" in following, f"{marker} does not persist immediately"
