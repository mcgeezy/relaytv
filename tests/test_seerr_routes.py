# SPDX-License-Identifier: GPL-3.0-only
from fastapi.testclient import TestClient

from relaytv_app.config import runtime_config
from relaytv_app.integrations import seerr_service, seerr_sessions
from relaytv_app.integrations.seerr_client import SeerrBinaryResponse, SeerrError
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


def test_seerr_read_routes_delegate_bounded_semantic_inputs(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        seerr_service,
        "discover",
        lambda section, page, **kwargs: calls.append(
            ("discover", section, page, kwargs)
        )
        or {"results": []},
    )
    monkeypatch.setattr(
        seerr_service,
        "search",
        lambda query, page, **kwargs: calls.append(("search", query, page, kwargs))
        or {"results": []},
    )
    monkeypatch.setattr(
        seerr_service,
        "item_detail",
        lambda media_type, media_id, **kwargs: calls.append(
            ("item", media_type, media_id, kwargs)
        )
        or {"media_id": media_id},
    )
    monkeypatch.setattr(
        seerr_service,
        "list_requests",
        lambda **kwargs: calls.append(("requests", kwargs)) or {"results": []},
    )
    client = TestClient(create_app(testing=True))

    assert client.get("/seerr/discover?section=movies&page=2").status_code == 200
    assert client.get("/seerr/search?query=arrival&page=3").status_code == 200
    assert client.get("/seerr/item/movie/329865").status_code == 200
    assert client.get("/seerr/requests?take=25&skip=50&filter=pending").status_code == 200

    assert calls == [
        ("discover", "movies", 2, {"session_id": ""}),
        ("search", "arrival", 3, {"session_id": ""}),
        ("item", "movie", 329865, {"session_id": ""}),
        (
            "requests",
            {"take": 25, "skip": 50, "status_filter": "pending", "session_id": ""},
        ),
    ]


def test_seerr_image_route_preserves_only_cache_validators(monkeypatch) -> None:
    monkeypatch.setattr(
        seerr_service,
        "image",
        lambda size, path: SeerrBinaryResponse(
            content=b"jpeg",
            content_type="image/jpeg",
            cache_control="public, max-age=7200",
            etag='"abc"',
            last_modified="Wed, 21 Oct 2015 07:28:00 GMT",
        ),
    )

    response = TestClient(create_app(testing=True)).get("/seerr/image/w342/poster.jpg")

    assert response.status_code == 200
    assert response.content == b"jpeg"
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "public, max-age=7200"
    assert response.headers["etag"] == '"abc"'
    assert "set-cookie" not in response.headers


def test_seerr_read_error_contract_is_sanitized(monkeypatch) -> None:
    def _fail(*args, **kwargs):
        raise SeerrError(
            "seerr_invalid_request",
            "Unknown Seerr discovery section",
            status_code=400,
        )

    monkeypatch.setattr(seerr_service, "discover", _fail)

    response = TestClient(create_app(testing=True)).get("/seerr/discover?section=admin")

    assert response.status_code == 400
    assert response.json() == {
        "detail": {
            "code": "seerr_invalid_request",
            "message": "Unknown Seerr discovery section",
        }
    }


def test_seerr_users_route_returns_sanitized_selector_records(monkeypatch) -> None:
    monkeypatch.setattr(
        seerr_service,
        "list_users",
        lambda: [{"id": 3, "display_name": "Alex", "username": "alex"}],
    )

    response = TestClient(create_app(testing=True)).get("/integrations/seerr/users")

    assert response.status_code == 200
    assert response.json() == {
        "users": [{"id": 3, "display_name": "Alex", "username": "alex"}]
    }


def test_seerr_request_route_rejects_administrator_fields(monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        seerr_service,
        "create_request",
        lambda **kwargs: calls.append(kwargs) or {"created": True},
    )

    response = TestClient(create_app(testing=True)).post(
        "/seerr/requests",
        json={
            "media_type": "tv",
            "media_id": 44,
            "seasons": [1, 2],
            "is_4k": False,
            "user_id": 999,
            "ignore_quota": True,
            "root_folder": "/unsafe",
        },
    )

    assert response.status_code == 422
    assert calls == []

    response = TestClient(create_app(testing=True)).post(
        "/seerr/requests",
        json={"media_type": "tv", "media_id": 44, "seasons": [1, 2], "is_4k": False},
    )

    assert response.status_code == 200
    assert calls == [
        {
            "media_type": "tv",
            "media_id": 44,
            "seasons": [1, 2],
            "is_4k": False,
            "session_id": "",
        }
    ]


def test_quick_connect_sets_only_opaque_relaytv_cookie(monkeypatch) -> None:
    monkeypatch.setattr(
        seerr_sessions,
        "initiate",
        lambda: {"flow_id": "F" * 43, "code": "123456", "expires_in": 600},
    )
    monkeypatch.setattr(
        seerr_sessions,
        "complete",
        lambda flow_id: (
            "S" * 43,
            {
                "connected": True,
                "identity": {"id": 7, "display_name": "Alex", "username": "alex"},
            },
        ),
    )
    client = TestClient(create_app(testing=True))

    started = client.post("/integrations/seerr/session/quick-connect")
    completed = client.post(
        "/integrations/seerr/session/quick-connect/complete",
        json={"flow_id": started.json()["flow_id"]},
    )

    assert completed.status_code == 200
    cookie = completed.headers["set-cookie"]
    assert cookie.startswith("relaytv_seerr_session=" + "S" * 43)
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert "upstream" not in cookie


def test_caller_session_status_and_logout_use_browser_cookie(monkeypatch) -> None:
    observed = []
    monkeypatch.setattr(
        seerr_sessions,
        "status",
        lambda session_id: observed.append(("status", session_id))
        or {"connected": bool(session_id)},
    )
    monkeypatch.setattr(
        seerr_sessions,
        "retire",
        lambda session_id: observed.append(("retire", session_id)),
    )
    client = TestClient(create_app(testing=True))
    client.cookies.set(seerr_sessions.COOKIE_NAME, "S" * 43)

    status = client.get("/integrations/seerr/session")
    logout = client.post("/integrations/seerr/session/logout")

    assert status.json() == {"connected": True}
    assert logout.json() == {"connected": False}
    assert observed == [("status", "S" * 43), ("retire", "S" * 43)]
    assert "Max-Age=0" in logout.headers["set-cookie"]
