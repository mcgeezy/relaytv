# SPDX-License-Identifier: GPL-3.0-only
"""Tests for thumbnails embedded by cross-origin Home Assistant pages."""

from fastapi.testclient import TestClient

from relaytv_app.main import create_app
from relaytv_app.routes import assets


def test_thumbnail_allows_cross_origin_image_processing(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(assets, "THUMB_DIR", str(tmp_path))
    (tmp_path / "sample.jpg").write_bytes(b"jpeg-test")
    client = TestClient(create_app(testing=True))

    response = client.get("/thumbs/sample.jpg", headers={"Origin": "http://homeassistant.local:8123"})

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cross-origin-resource-policy"] == "cross-origin"
    assert response.headers["cache-control"] == "public, max-age=86400"
