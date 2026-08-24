# SPDX-License-Identifier: GPL-3.0-only
import pytest

from relaytv_app.integrations import seerr_sessions
from relaytv_app.integrations.seerr_client import SeerrError, SeerrJsonResponse


class _Config:
    enabled = True
    configured = True
    configuration_error = ""
    server_url = "https://seerr.example"
    api_key = ""
    request_mode = "caller_session"


@pytest.fixture(autouse=True)
def _clear_sessions():
    seerr_sessions.retire_all()
    yield
    seerr_sessions.retire_all()


def test_quick_connect_keeps_upstream_secret_and_cookie_server_side(monkeypatch) -> None:
    calls = []

    class _Client:
        def __init__(self, config, **kwargs):
            calls.append(("init", kwargs))

        def request_json_response(self, method, path, **kwargs):
            calls.append((method, path, kwargs))
            if path.endswith("/initiate"):
                return SeerrJsonResponse(
                    data={"code": "123456", "secret": "abcdef1234567890"}, status=200
                )
            assert path.endswith("/authenticate")
            return SeerrJsonResponse(
                data={
                    "id": 7,
                    "displayName": "Alex",
                    "username": "alex",
                    "email": "private@example.com",
                    "permissions": 999,
                },
                status=200,
                set_cookies=(
                    "connect.sid=s%3Aupstream-secret.signature; Path=/; HttpOnly; SameSite=Lax",
                ),
            )

        def get(self, path, **kwargs):
            calls.append(("GET", path, kwargs))
            return {"authenticated": True}

    monkeypatch.setattr(seerr_sessions.SeerrConfig, "current", lambda: _Config())
    monkeypatch.setattr(seerr_sessions, "SeerrClient", _Client)

    started = seerr_sessions.initiate()
    assert started["code"] == "123456"
    assert "abcdef1234567890" not in str(started)

    session_id, completed = seerr_sessions.complete(started["flow_id"])

    assert session_id
    assert "upstream-secret" not in str(completed)
    assert completed["identity"] == {
        "id": 7,
        "display_name": "Alex",
        "username": "alex",
    }
    stored = seerr_sessions.resolve(session_id)
    assert stored is not None
    assert stored.cookie == "connect.sid=s%3Aupstream-secret.signature"
    assert "private@example.com" not in str(completed)
    assert calls[-1][2]["auth"] is False


def test_quick_connect_pending_flow_can_be_polled(monkeypatch) -> None:
    class _Client:
        def __init__(self, config, **kwargs):
            pass

        def request_json_response(self, method, path, **kwargs):
            return SeerrJsonResponse(
                data={"code": "654321", "secret": "abcdef1234567890"}, status=200
            )

        def get(self, path, **kwargs):
            return {"authenticated": False}

    monkeypatch.setattr(seerr_sessions.SeerrConfig, "current", lambda: _Config())
    monkeypatch.setattr(seerr_sessions, "SeerrClient", _Client)

    started = seerr_sessions.initiate()
    session_id, result = seerr_sessions.complete(started["flow_id"])

    assert session_id is None
    assert result == {"connected": False, "pending": True}


def test_session_is_retired_when_server_or_mode_changes(monkeypatch) -> None:
    now = [100.0]
    monkeypatch.setattr(seerr_sessions.time, "monotonic", lambda: now[0])
    config = _Config()
    monkeypatch.setattr(seerr_sessions.SeerrConfig, "current", lambda: config)
    session_id = "A" * 43
    seerr_sessions._SESSIONS[session_id] = seerr_sessions.CallerSession(
        cookie="connect.sid=value",
        server_url=config.server_url,
        identity={"id": 1, "display_name": "A", "username": "a"},
        expires_at=200.0,
    )

    assert seerr_sessions.resolve(session_id) is not None
    now[0] = 201.0
    assert seerr_sessions.resolve(session_id) is None

    now[0] = 100.0
    seerr_sessions._SESSIONS[session_id] = seerr_sessions.CallerSession(
        cookie="connect.sid=value",
        server_url="https://old.example",
        identity={"id": 1},
        expires_at=200.0,
    )
    assert seerr_sessions.resolve(session_id) is None


def test_quick_connect_store_rejects_unbounded_flow_growth(monkeypatch) -> None:
    monkeypatch.setattr(seerr_sessions.SeerrConfig, "current", lambda: _Config())
    now = 100.0
    monkeypatch.setattr(seerr_sessions.time, "monotonic", lambda: now)
    for index in range(seerr_sessions.MAX_FLOWS):
        seerr_sessions._FLOWS[f"flow-{index}"] = seerr_sessions._Flow(
            secret="abcdef1234567890",
            server_url="https://seerr.example",
            expires_at=200.0,
        )

    with pytest.raises(SeerrError) as exc_info:
        seerr_sessions.initiate()

    assert getattr(exc_info.value, "code", "") == "seerr_session_capacity"
    assert len(seerr_sessions._FLOWS) == seerr_sessions.MAX_FLOWS
