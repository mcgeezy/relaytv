# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app.config import runtime_config
from relaytv_app.integrations import seerr_service
from relaytv_app.integrations.seerr_client import SeerrError
from relaytv_app.main import create_app


def test_seerr_status_reports_disabled_without_network() -> None:
    runtime_config.set_value("RELAYTV_SEERR_ENABLED", "0")
    runtime_config.set_value("RELAYTV_SEERR_SERVER_URL", "")
    runtime_config.set_value("RELAYTV_SEERR_API_KEY", "")

    response = TestClient(create_app(testing=True)).get("/integrations/seerr/status")

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["configured"] is False
    assert response.json()["reachable"] is False


def test_seerr_test_route_returns_only_sanitized_identity(monkeypatch) -> None:
    class _Config:
        enabled = True
        configured = True
        configuration_error = ""
        server_url = "https://seerr.example"
        api_key = "secret"
        shared_requests_enabled = False
        request_user_id = None

    class _Client:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            if path == "/status":
                return {"version": "3.4.1"}
            if path == "/settings/main":
                return {"applicationTitle": "Requests", "mediaServerType": 2}
            assert path == "/auth/me"
            return {
                "id": 7,
                "displayName": "Operator",
                "username": "mark",
                "email": "private@example.com",
                "permissions": 2,
                "apiKey": "never-return-this",
            }

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: _Config())
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    response = TestClient(create_app(testing=True)).post("/integrations/seerr/test")

    assert response.status_code == 200
    body = response.json()
    assert body["identity"] == {"id": 7, "display_name": "Operator", "username": "mark"}
    assert "private@example.com" not in response.text
    assert "never-return-this" not in response.text


def test_seerr_test_route_preserves_safe_timeout_status(monkeypatch) -> None:
    class _Config:
        enabled = True
        configured = True
        configuration_error = ""
        server_url = "https://seerr.example"
        api_key = "secret"
        shared_requests_enabled = False
        request_user_id = None

    class _Client:
        def __init__(self, config):
            pass

        def get(self, path, **kwargs):
            raise SeerrError("seerr_timeout", "Seerr timed out", status_code=504)

    monkeypatch.setattr(seerr_service.SeerrConfig, "current", lambda: _Config())
    monkeypatch.setattr(seerr_service, "SeerrClient", _Client)

    response = TestClient(create_app(testing=True)).post("/integrations/seerr/test")

    assert response.status_code == 504
    assert response.json()["detail"]["code"] == "seerr_timeout"
