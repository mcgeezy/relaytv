# SPDX-License-Identifier: GPL-3.0-only
"""Upload ids must stay inside the upload root (F13).

upload_ref_from_url decoded percent-escapes *after* splitting the URL path, so
an encoded separator survived inside a single component: "..%2F..%2Fdata"
arrived as one part and unquoted into "../../data". Only the filename was ever
basename'd, so the id went straight into os.path.join with the upload root.
"""
import os

import pytest
from fastapi.testclient import TestClient

from relaytv_app import upload_store
from relaytv_app.main import create_app


@pytest.fixture
def uploads_root(tmp_path, monkeypatch):
    root = tmp_path / "uploads"
    root.mkdir()
    monkeypatch.setattr(upload_store, "_UPLOADS_ROOT", str(root))
    return root


# --- the id format -----------------------------------------------------------


@pytest.mark.parametrize(
    "upload_id",
    [
        "../../data",
        "../../etc",
        "..",
        "/etc",
        "u_short",
        "u_" + "a" * 19,          # one too few
        "u_" + "a" * 21,          # one too many
        "u_" + "g" * 20,          # not hex
        "U_" + "a" * 20,          # wrong case
        "u_" + "a" * 20 + "/x",   # separator smuggled in
        "",
        None,
    ],
)
def test_invalid_ids_are_rejected(upload_id) -> None:
    assert upload_store.is_valid_upload_id(upload_id) is False
    assert upload_store.upload_dir(upload_id) == ""


def test_generated_ids_are_valid() -> None:
    for _ in range(25):
        assert upload_store.is_valid_upload_id(upload_store.new_upload_id()) is True


# --- derived paths must propagate the rejection ------------------------------


@pytest.mark.parametrize("upload_id", ["../../data", "u_short", ""])
def test_derived_paths_are_empty_not_relative(upload_id, uploads_root) -> None:
    """os.path.join("", "meta.json") is a *relative* path.

    Returning "" from upload_dir without handling it downstream would read
    ./meta.json out of the working directory instead of the upload store.
    """
    assert upload_store.metadata_path(upload_id) == ""
    assert upload_store.session_path(upload_id) == ""
    assert upload_store.stored_file_path({"id": upload_id, "stored_name": "a.mp4"}) is None


def test_valid_id_still_resolves_every_path(uploads_root) -> None:
    upload_id = upload_store.new_upload_id()
    expected = os.path.join(str(uploads_root), upload_id)

    assert upload_store.upload_dir(upload_id) == expected
    assert upload_store.metadata_path(upload_id) == os.path.join(expected, "meta.json")
    assert upload_store.session_path(upload_id) == os.path.join(expected, "session.json")
    assert upload_store.stored_file_path(
        {"id": upload_id, "stored_name": "clip.mp4"}
    ) == os.path.join(expected, "clip.mp4")


# --- the URL boundary --------------------------------------------------------


@pytest.mark.parametrize(
    "url",
    [
        "http://tv.local/media/uploads/..%2F..%2Fdata/x.mp4",
        "http://tv.local/media/uploads/../../etc/passwd.mp4",
        "http://tv.local/media/uploads/..%2f..%2fdata/x.mp4",
        "http://tv.local/media/uploads/%2e%2e%2F%2e%2e/x.mp4",
        "http://tv.local/media/uploads/u_short/x.mp4",
    ],
)
def test_encoded_traversal_does_not_survive_url_parsing(url: str) -> None:
    assert upload_store.upload_ref_from_url(url) is None


def test_valid_upload_url_still_resolves() -> None:
    upload_id = upload_store.new_upload_id()
    ref = upload_store.upload_ref_from_url(f"http://tv.local/media/uploads/{upload_id}/clip.mp4")
    assert ref == (upload_id, "clip.mp4")


# --- the escape, against a real filesystem -----------------------------------


def test_traversal_cannot_read_a_file_outside_the_root(uploads_root, tmp_path) -> None:
    """The audited escape, with a matching file actually in place.

    /data is the parent of /data/uploads on a device, so "../../data" reached a
    real directory holding settings.json, history.json, and peers.json.
    """
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "meta.json").write_text('{"id": "secret", "stored_name": "loot.bin"}')

    escape = f"..{os.sep}..{os.sep}{outside.name}"
    assert upload_store.load_metadata(escape) is None
    assert upload_store.metadata_path(escape) == ""


def test_delete_upload_never_rmtrees_an_unvalidated_path(uploads_root, tmp_path) -> None:
    """delete_upload is recursive deletion; it must not act on a bad id."""
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "settings.json").write_text("{}")

    upload_store.delete_upload(f"..{os.sep}..{os.sep}{victim.name}")

    assert victim.exists()
    assert (victim / "settings.json").exists()


def test_delete_upload_still_removes_a_real_upload(uploads_root) -> None:
    upload_id = upload_store.new_upload_id()
    target = uploads_root / upload_id
    target.mkdir()
    (target / "clip.mp4").write_bytes(b"data")

    upload_store.delete_upload(upload_id)

    assert not target.exists()


# --- the route ---------------------------------------------------------------


def test_media_route_rejects_a_traversal_id(uploads_root, tmp_path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "meta.json").write_text('{"id":"x","stored_name":"a.mp4","public_name":"a.mp4"}')

    client = TestClient(create_app(testing=True))
    response = client.get("/media/uploads/..%2F..%2Foutside/a.mp4")

    assert response.status_code in (404, 410, 400)


def test_media_route_still_serves_a_real_upload(uploads_root) -> None:
    upload_id = upload_store.new_upload_id()
    target = uploads_root / upload_id
    target.mkdir()
    (target / "clip.mp4").write_bytes(b"movie-bytes")
    upload_store.write_metadata(
        upload_id,
        {
            "id": upload_id,
            "filename": "clip.mp4",
            "public_name": "clip.mp4",
            "stored_name": "clip.mp4",
            "mime_type": "video/mp4",
        },
    )

    client = TestClient(create_app(testing=True))
    response = client.get(f"/media/uploads/{upload_id}/clip.mp4")

    assert response.status_code == 200
    assert response.content == b"movie-bytes"
