# SPDX-License-Identifier: GPL-3.0-only
"""Optional API token guard contract (docs/ARCHITECTURE.md, API Trust Boundary).

With RELAYTV_API_TOKEN unset the API must behave exactly as before: every
write endpoint open. With a token configured, write requests require
``Authorization: Bearer <token>`` while reads, /health, /ui, and static
assets stay open.
"""
from fastapi.testclient import TestClient

from relaytv_app import api_auth
from relaytv_app.config import runtime_config
from relaytv_app.main import create_app


TOKEN = "sekrit-token-123"


def _client() -> TestClient:
    return TestClient(create_app(testing=True))


def _enable_token(monkeypatch, value: str = TOKEN) -> None:
    monkeypatch.setenv("RELAYTV_API_TOKEN", value)
    runtime_config.refresh_from_env()


# --- helper contract ---------------------------------------------------------


def test_bearer_token_from_header_parses_scheme() -> None:
    assert api_auth.bearer_token_from_header(f"Bearer {TOKEN}") == TOKEN
    assert api_auth.bearer_token_from_header(f"bearer {TOKEN}") == TOKEN
    assert api_auth.bearer_token_from_header(f"Basic {TOKEN}") == ""
    assert api_auth.bearer_token_from_header(TOKEN) == ""
    assert api_auth.bearer_token_from_header("") == ""
    assert api_auth.bearer_token_from_header(None) == ""


def test_write_request_allowed_policy(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    assert api_auth.write_request_allowed("POST", None) is True

    _enable_token(monkeypatch)
    assert api_auth.write_request_allowed("GET", None) is True
    assert api_auth.write_request_allowed("HEAD", None) is True
    assert api_auth.write_request_allowed("OPTIONS", None) is True
    assert api_auth.write_request_allowed("POST", None) is False
    assert api_auth.write_request_allowed("POST", "Bearer wrong") is False
    assert api_auth.write_request_allowed("POST", f"Bearer {TOKEN}") is True
    assert api_auth.write_request_allowed("PUT", None) is False
    assert api_auth.write_request_allowed("PATCH", None) is False
    assert api_auth.write_request_allowed("DELETE", None) is False


# --- default off: behavior unchanged -----------------------------------------


def test_writes_open_when_token_unset(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    client = _client()
    response = client.post("/clear")
    assert response.status_code == 200
    assert response.json() == {"status": "cleared"}


def test_blank_token_means_disabled(monkeypatch) -> None:
    _enable_token(monkeypatch, "   ")

    client = _client()
    response = client.post("/clear")
    assert response.status_code == 200


# --- token on: write guard ----------------------------------------------------


def test_write_without_token_rejected(monkeypatch) -> None:
    _enable_token(monkeypatch)

    client = _client()
    response = client.post("/clear")
    assert response.status_code == 401
    assert response.json() == {"detail": "api token required"}
    assert response.headers.get("www-authenticate") == "Bearer"
    seerr = client.post(
        "/seerr/requests",
        json={"media_type": "movie", "media_id": 11},
    )
    assert seerr.status_code == 401


def test_write_with_wrong_token_rejected(monkeypatch) -> None:
    _enable_token(monkeypatch)

    client = _client()
    response = client.post("/clear", headers={"Authorization": "Bearer nope"})
    assert response.status_code == 401


def test_write_with_wrong_scheme_rejected(monkeypatch) -> None:
    _enable_token(monkeypatch)

    client = _client()
    response = client.post("/clear", headers={"Authorization": TOKEN})
    assert response.status_code == 401


def test_write_with_correct_token_accepted(monkeypatch) -> None:
    _enable_token(monkeypatch)

    client = _client()
    response = client.post("/clear", headers={"Authorization": f"Bearer {TOKEN}"})
    assert response.status_code == 200
    assert response.json() == {"status": "cleared"}


def test_auth_check_validates_without_mutating_state(monkeypatch) -> None:
    _enable_token(monkeypatch)
    client = _client()

    assert client.post("/auth/check").status_code == 401
    accepted = client.post("/auth/check", headers={"Authorization": f"Bearer {TOKEN}"})

    assert accepted.status_code == 200
    assert accepted.json() == {"ok": True, "token_required": True}


def test_auth_check_reports_unprotected_server(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    response = _client().post("/auth/check")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "token_required": False}


# --- token on: reads stay open -------------------------------------------------


def test_reads_stay_open_with_token(monkeypatch) -> None:
    _enable_token(monkeypatch)

    client = _client()
    assert client.get("/health").status_code == 200
    assert client.get("/status").status_code == 200
    assert client.get("/settings").status_code == 200
    ui = client.get("/ui")
    assert ui.status_code == 200
    css = client.get("/static/ui/app.css")
    assert css.status_code == 200


def test_settings_response_never_exposes_token(monkeypatch) -> None:
    _enable_token(monkeypatch)

    client = _client()
    response = client.get("/settings")
    assert response.status_code == 200
    assert TOKEN not in response.text
    assert "api_token" not in response.json()
