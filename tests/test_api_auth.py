# SPDX-License-Identifier: GPL-3.0-only
"""Optional API token guard contract (docs/ARCHITECTURE.md, API Trust Boundary).

With RELAYTV_API_TOKEN unset the API must behave exactly as before: every
write endpoint open. With a token configured, write requests require
``Authorization: Bearer <token>`` while reads, /health, /ui, and static
assets stay open.
"""
from urllib.parse import unquote

from fastapi.testclient import TestClient

from relaytv_app import api_auth, playback_service
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
    assert (
        client.post(
            "/seerr/playback",
            json={"media_type": "movie", "media_id": 11, "command": "play_now"},
        ).status_code
        == 401
    )
    assert client.post("/integrations/seerr/session/quick-connect").status_code == 401
    assert (
        client.post(
            "/integrations/seerr/session/quick-connect/complete",
            json={"flow_id": "F" * 43},
        ).status_code
        == 401
    )
    assert client.post("/integrations/seerr/session/logout").status_code == 401


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


# --- token on: mutating GETs are classified as writes -------------------------


def test_is_write_request_classifies_mutating_gets() -> None:
    assert api_auth.is_write_request("POST", "/anything") is True
    assert api_auth.is_write_request("GET", "/status") is False
    assert api_auth.is_write_request("HEAD", "/snapshot") is False
    # The compatibility alias mutates despite the method.
    assert api_auth.is_write_request("GET", "/snapshot") is True
    assert api_auth.is_write_request("get", "/snapshot/") is True
    # /share redirects now, so it is a genuine read again.
    assert api_auth.is_write_request("GET", "/share") is False


def test_mutating_get_requires_token(monkeypatch) -> None:
    _enable_token(monkeypatch)
    client = _client()

    unauthenticated = client.get("/snapshot")
    assert unauthenticated.status_code == 401
    assert unauthenticated.headers.get("WWW-Authenticate") == "Bearer"

    wrong = client.get("/snapshot", headers={"Authorization": "Bearer nope"})
    assert wrong.status_code == 401

    # The correct token gets past the guard and reaches the handler, which
    # rejects with 409 because nothing is playing. Anything but 401 proves the
    # middleware let it through.
    authorized = client.get("/snapshot", headers={"Authorization": f"Bearer {TOKEN}"})
    assert authorized.status_code != 401


def test_mutating_get_open_when_token_unset(monkeypatch) -> None:
    """Local-first default is unchanged: no token, no guard."""
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    client = _client()
    assert client.get("/snapshot").status_code != 401


def test_cross_site_mutating_get_is_rejected_without_a_token(monkeypatch) -> None:
    """A hostile page must not turn an open local API into a browser CSRF path."""
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    client = _client()

    response = client.get("/snapshot", headers={"Sec-Fetch-Site": "cross-site"})

    assert response.status_code == 403
    assert response.json()["detail"] == "cross-site write request rejected"


def test_referer_fallback_rejects_cross_origin_snapshot(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    client = _client()

    response = client.get(
        "/snapshot",
        headers={"Referer": "https://attacker.example/page", "Host": "relaytv.lan:8787"},
    )

    assert response.status_code == 403


def test_same_origin_and_headerless_snapshot_clients_remain_compatible(monkeypatch) -> None:
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()
    client = _client()

    same_origin = client.get(
        "/snapshot",
        headers={
            "Sec-Fetch-Site": "same-origin",
            "Referer": "http://testserver/ui",
            "Host": "testserver",
        },
    )
    headerless = client.get("/snapshot")

    # No playback is active, so reaching the handler yields 409. The important
    # contract is that neither legitimate shape is rejected by the CSRF guard.
    assert same_origin.status_code == 409
    assert headerless.status_code == 409


def test_share_target_redirects_without_playing(monkeypatch) -> None:
    """The share target must not be a control path, with or without a token."""
    monkeypatch.delenv("RELAYTV_API_TOKEN", raising=False)
    runtime_config.refresh_from_env()

    played: list[object] = []
    monkeypatch.setattr(
        playback_service, "play_now", lambda *a, **k: played.append((a, k)) or {}
    )

    client = _client()
    response = client.get(
        "/share", params={"url": "https://youtu.be/abc123"}, follow_redirects=False
    )
    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/ui?share=")
    assert "youtu.be" in unquote(location)
    assert played == []


def test_share_target_redirect_survives_token(monkeypatch) -> None:
    """A browser navigation carries no bearer header; it must still work."""
    _enable_token(monkeypatch)

    client = _client()
    response = client.get(
        "/share", params={"url": "https://youtu.be/abc123"}, follow_redirects=False
    )
    assert response.status_code == 303


def test_share_post_is_guarded_and_plays(monkeypatch) -> None:
    _enable_token(monkeypatch)

    played: list[dict] = []

    def _fake_play_now(item, **kwargs):
        played.append({"item": item, **kwargs})
        return {"title": "shared"}

    monkeypatch.setattr(playback_service, "play_now", _fake_play_now)

    client = _client()
    assert client.post("/share", json={"url": "https://youtu.be/abc123"}).status_code == 401
    assert played == []

    ok = client.post(
        "/share",
        json={"url": "https://youtu.be/abc123"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert ok.status_code == 200
    assert ok.json()["source"] == "share_target"
    assert len(played) == 1
    assert played[0]["clear_queue"] is True
