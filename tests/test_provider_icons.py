# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from relaytv_app.main import create_app


PROVIDERS = (
    "dailymotion",
    "facebook",
    "instagram",
    "kick",
    "odysee",
    "peertube",
    "rumble",
    "tiktok",
    "twitch",
    "vimeo",
    "x",
    "youtube",
)


@pytest.mark.parametrize("provider", PROVIDERS)
def test_bundled_provider_icon_is_served_locally(provider: str) -> None:
    client = TestClient(create_app(testing=True))

    response = client.get(f"/pwa/providers/{provider}.svg")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["cache-control"] == "public, max-age=86400"
    assert "<title>" in response.text
    assert "<script" not in response.text
    assert "<image" not in response.text
    assert "href=" not in response.text


def test_provider_icon_inventory_matches_bundled_assets() -> None:
    root = Path(__file__).resolve().parents[1] / "app" / "relaytv_app" / "static" / "providers"
    assert tuple(path.stem for path in sorted(root.glob("*.svg"))) == PROVIDERS
