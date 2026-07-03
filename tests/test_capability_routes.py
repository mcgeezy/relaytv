# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app import routes
from relaytv_app.main import create_app


def test_notifications_capabilities_route_uses_runtime_helper(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "_notification_capabilities",
        lambda: {
            "visual_runtime_mode": "x11_display",
            "notification_strategy": "overlay",
            "native_qt_toasts_deprecated": True,
        },
    )

    client = TestClient(create_app(testing=True))
    response = client.get("/notifications/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "visual_runtime_mode": "x11_display",
        "notification_strategy": "overlay",
        "native_qt_toasts_deprecated": True,
    }


def test_runtime_capabilities_route_uses_runtime_helper(monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "_runtime_capabilities",
        lambda: {
            "player_backend": "qt",
            "backend_ready": True,
            "notifications_available": True,
        },
    )

    client = TestClient(create_app(testing=True))
    response = client.get("/runtime/capabilities")

    assert response.status_code == 200
    assert response.json() == {
        "player_backend": "qt",
        "backend_ready": True,
        "notifications_available": True,
    }
