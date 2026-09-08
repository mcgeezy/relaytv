# SPDX-License-Identifier: GPL-3.0-only
import os
import threading
import time

import jwt
import pytest

from relaytv_app.integrations import plex_auth, plex_service


class _FakeClient:
    def __init__(self, factory, base_url: str, token: str):
        self.factory = factory
        self.base_url = base_url
        self.token = token

    def get(self, path, *, query=None, auth=True):
        self.factory.calls.append(("GET", self.base_url, path, query, auth, self.token))
        return self.factory.response("GET", self.base_url, path, query, self.token)

    def post(self, path, *, query=None, body=None, auth=True):
        self.factory.calls.append(("POST", self.base_url, path, body, auth, self.token))
        return self.factory.response("POST", self.base_url, path, body, self.token)


class _FakeClientFactory:
    def __init__(self, account_token: str):
        self.account_token = account_token
        self.pin_claimed = False
        self.calls = []
        self.server_resources = [
            {
                "name": "Home Plex",
                "clientIdentifier": "server-123",
                "provides": "server",
                "owned": True,
                "presence": True,
                "accessToken": "server-secret",
                "connections": [
                    {
                        "uri": "https://server-123-168-1-20.example.plex.direct:32400",
                        "local": True,
                        "relay": False,
                    },
                    {
                        "uri": "https://relay.example:443",
                        "local": False,
                        "relay": True,
                    },
                ],
            },
            {
                "name": "Photos only",
                "clientIdentifier": "photos-1",
                "provides": "photos",
                "accessToken": "unrelated-secret",
                "connections": [],
            },
        ]

    def __call__(self, base_url: str, token: str):
        return _FakeClient(self, base_url, token)

    def response(self, method, base_url, path, payload, token):
        if method == "POST" and path == "/api/v2/pins":
            assert token == ""
            assert payload["jwk"]["kty"] == "OKP"
            assert payload["jwk"]["crv"] == "Ed25519"
            assert payload["strong"] is True
            return {"id": 99, "code": "long-pin-code", "expiresIn": 1800}
        if method == "GET" and path == "/api/v2/pins/99":
            assert payload["deviceJWT"]
            return {"authToken": self.account_token if self.pin_claimed else None}
        if method == "GET" and path == "/api/v2/user":
            assert token == self.account_token
            return {"id": 7, "username": "mark", "friendlyName": "Mark"}
        if method == "GET" and path == "/api/v2/resources":
            assert token == self.account_token
            return self.server_resources
        if method == "GET" and path == "/api/v2/devices":
            assert token == self.account_token
            return [
                {
                    "clientIdentifier": "server-123",
                    "provides": "server",
                    "token": "server-secret",
                }
            ]
        if method == "GET" and path == "/identity":
            assert token == "server-secret"
            return {
                "MediaContainer": {
                    "machineIdentifier": "server-123",
                    "version": "1.43.3.10828",
                }
            }
        raise AssertionError((method, base_url, path, payload, token))


@pytest.fixture
def account_token() -> str:
    return jwt.encode(
        {"sub": "7", "username": "mark", "exp": int(time.time()) + 7 * 86400},
        "test-only-key",
        algorithm="HS256",
    )


@pytest.fixture
def manager(tmp_path, monkeypatch, account_token):
    factory = _FakeClientFactory(account_token)
    monkeypatch.setattr(plex_auth.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(
        plex_auth.state,
        "get_settings",
        lambda: {"plex_enabled": True, "plex_server_machine_id": ""},
    )
    value = plex_auth.PlexAuthManager(
        store=plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json")),
        client_identity=plex_auth.PlexClientIdentity(
            client_identifier="relaytv-device",
            version="0.10.3",
            device_name="Living Room",
        ),
        client_factory=factory,
    )
    return value, factory


def test_private_store_generates_stable_mode_0600_device_key(tmp_path) -> None:
    path = tmp_path / "plex_auth.json"
    store = plex_auth.PlexAuthStore(str(path))

    first = store.ensure_device()
    second = store.ensure_device()

    assert first["device"] == second["device"]
    assert first["device"]["private_key"]
    assert first["device"]["public_key"]
    assert first["device"]["kid"]
    assert os.stat(path).st_mode & 0o777 == 0o600


def test_link_flow_is_browser_bound_and_never_returns_tokens(manager, account_token) -> None:
    auth, factory = manager
    started = auth.start_link("browser-a")

    assert started["link_url"].startswith("https://app.plex.tv/auth#?")
    assert "long-pin-code" in started["link_url"]
    with pytest.raises(plex_auth.PlexAuthError) as exc_info:
        auth.poll_link(started["flow_id"], "browser-b")
    assert exc_info.value.code == "plex_flow_not_bound"

    pending = auth.poll_link(started["flow_id"], "browser-a")
    assert pending["pending"] is True
    factory.pin_claimed = True
    linked = auth.poll_link(started["flow_id"], "browser-a")

    assert linked["linked"] is True
    assert linked["account"] == {
        "id": "7",
        "username": "mark",
        "friendly_name": "Mark",
    }
    assert linked["servers"][0]["machine_id"] == "server-123"
    assert account_token not in repr(linked)
    assert "server-secret" not in repr(linked)
    persisted = auth.store.load()
    assert persisted["account"]["auth_token"] == account_token


def test_select_server_prefers_local_non_relay_and_verifies_identity(manager) -> None:
    auth, factory = manager
    started = auth.start_link("browser-a")
    factory.pin_claimed = True
    auth.poll_link(started["flow_id"], "browser-a")

    selected = auth.select_server("server-123")
    tested = auth.test_selected_server()

    assert selected == {
        "machine_id": "server-123",
        "name": "Home Plex",
        "server_url": "https://server-123-168-1-20.example.plex.direct:32400",
        "local": True,
        "relay": False,
        "secure": True,
    }
    assert tested["reachable"] is True
    assert tested["version"] == "1.43.3.10828"
    assert "server-secret" not in repr(auth.status())


def test_select_server_exchanges_jwt_resource_token_for_server_token(
    manager,
    account_token,
) -> None:
    auth, factory = manager
    started = auth.start_link("browser-a")
    factory.pin_claimed = True
    auth.poll_link(started["flow_id"], "browser-a")
    factory.server_resources[0]["accessToken"] = account_token

    auth.select_server("server-123")

    assert auth.store.load()["server"]["access_token"] == "server-secret"
    assert any(call[2] == "/api/v2/devices" for call in factory.calls)


def test_selected_server_session_upgrades_persisted_jwt_server_token(
    manager,
    account_token,
) -> None:
    auth, factory = manager
    started = auth.start_link("browser-a")
    factory.pin_claimed = True
    auth.poll_link(started["flow_id"], "browser-a")
    auth.select_server("server-123")
    payload = auth.store.load()
    payload["server"]["access_token"] = account_token
    auth.store.save(payload)

    session = auth.selected_server_session()

    assert session.client.token == "server-secret"
    assert auth.store.load()["server"]["access_token"] == "server-secret"


def test_server_token_upgrade_cannot_restore_a_disconnected_account(
    manager,
    account_token,
) -> None:
    auth, factory = manager
    started = auth.start_link("browser-a")
    factory.pin_claimed = True
    auth.poll_link(started["flow_id"], "browser-a")
    auth.select_server("server-123")
    payload = auth.store.load()
    payload["server"]["access_token"] = account_token
    auth.store.save(payload)
    entered = threading.Event()
    release = threading.Event()
    original_response = factory.response

    def blocked_response(method, base_url, path, payload, token):
        if method == "GET" and path == "/api/v2/devices":
            entered.set()
            assert release.wait(2)
        return original_response(method, base_url, path, payload, token)

    factory.response = blocked_response
    failures = []

    def load_session():
        try:
            auth.selected_server_session()
        except Exception as exc:  # noqa: BLE001 - captured for thread assertion
            failures.append(exc)

    worker = threading.Thread(target=load_session)
    worker.start()
    assert entered.wait(2)
    auth.disconnect()
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], plex_auth.PlexAuthError)
    assert failures[0].code == "plex_credentials_changed"
    assert auth.store.load()["account"] == {}
    assert auth.store.load()["server"] == {}


def test_server_reselection_rejects_a_blocked_catalog_result(manager) -> None:
    auth, factory = manager
    started = auth.start_link("browser-a")
    factory.pin_claimed = True
    auth.poll_link(started["flow_id"], "browser-a")
    auth.select_server("server-123")
    entered = threading.Event()
    release = threading.Event()
    original_response = factory.response

    def blocked_response(method, base_url, path, payload, token):
        if method == "GET" and path == "/hubs":
            entered.set()
            assert release.wait(2)
            return {
                "MediaContainer": {
                    "Hub": [
                        {
                            "title": "Recently Added",
                            "Metadata": [
                                {
                                    "key": "/library/metadata/10",
                                    "type": "movie",
                                    "title": "Stale Movie",
                                }
                            ],
                        }
                    ]
                }
            }
        return original_response(method, base_url, path, payload, token)

    factory.response = blocked_response
    failures = []

    def browse():
        try:
            plex_service.PlexCatalogService(auth).home()
        except Exception as exc:  # noqa: BLE001 - captured for thread assertion
            failures.append(exc)

    worker = threading.Thread(target=browse)
    worker.start()
    assert entered.wait(2)
    auth.select_server("server-123")
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], plex_auth.PlexAuthError)
    assert failures[0].code == "plex_credentials_changed"


def test_disconnect_invalidates_a_blocked_link_completion(
    tmp_path,
    monkeypatch,
    account_token,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    factory = _FakeClientFactory(account_token)
    original_response = factory.response

    def blocked_response(method, base_url, path, payload, token):
        if method == "GET" and path == "/api/v2/user":
            entered.set()
            assert release.wait(2)
        return original_response(method, base_url, path, payload, token)

    factory.response = blocked_response
    monkeypatch.setattr(plex_auth.state, "update_settings", lambda patch: dict(patch))
    monkeypatch.setattr(plex_auth.state, "get_settings", lambda: {})
    auth = plex_auth.PlexAuthManager(
        store=plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json")),
        client_identity=plex_auth.PlexClientIdentity("relaytv-device"),
        client_factory=factory,
    )
    started = auth.start_link("browser-a")
    factory.pin_claimed = True
    failures = []

    def finish_link():
        try:
            auth.poll_link(started["flow_id"], "browser-a")
        except Exception as exc:  # noqa: BLE001 - captured for the thread assertion
            failures.append(exc)

    worker = threading.Thread(target=finish_link)
    worker.start()
    assert entered.wait(2)
    auth.disconnect()
    release.set()
    worker.join(2)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], plex_auth.PlexAuthError)
    assert failures[0].code == "plex_flow_cancelled"
    assert auth.status()["linked"] is False


def test_concurrent_refresh_is_serialized_and_reuses_the_rotated_token(
    tmp_path,
    account_token,
) -> None:
    entered = threading.Event()
    release = threading.Event()
    expiring_token = jwt.encode(
        {"sub": "7", "exp": int(time.time()) + 60},
        "test-only-key",
        algorithm="HS256",
    )

    class _RefreshFactory(_FakeClientFactory):
        def response(self, method, base_url, path, payload, token):
            if method == "GET" and path == "/api/v2/auth/nonce":
                entered.set()
                assert release.wait(2)
                return {"nonce": "nonce-1"}
            if method == "POST" and path == "/api/v2/auth/token":
                assert payload["jwt"]
                return {"auth_token": self.account_token}
            return super().response(method, base_url, path, payload, token)

    factory = _RefreshFactory(account_token)
    store = plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json"))
    payload = store.ensure_device()
    payload["account"] = {
        "auth_token": expiring_token,
        "expires_at": int(time.time()) + 60,
        "id": "7",
        "username": "mark",
    }
    store.save(payload)
    auth = plex_auth.PlexAuthManager(
        store=store,
        client_identity=plex_auth.PlexClientIdentity("relaytv-device"),
        client_factory=factory,
    )
    results = []

    first = threading.Thread(target=lambda: results.append(auth.refresh_token()))
    second = threading.Thread(target=lambda: results.append(auth.refresh_token()))
    first.start()
    assert entered.wait(2)
    second.start()
    time.sleep(0.05)
    release.set()
    first.join(2)
    second.join(2)

    assert sorted(results) == [False, True]
    assert sum(call[2] == "/api/v2/auth/nonce" for call in factory.calls) == 1
    assert store.load()["account"]["auth_token"] == account_token


def test_corrupt_auth_state_fails_closed(tmp_path) -> None:
    path = tmp_path / "plex_auth.json"
    path.write_text('{"version":999,"account":{"auth_token":"secret"}}')

    with pytest.raises(plex_auth.PlexAuthError) as exc_info:
        plex_auth.PlexAuthStore(str(path)).load()

    assert exc_info.value.code == "plex_auth_state_invalid"
    assert "secret" not in str(exc_info.value)


def test_disconnect_recovers_a_corrupt_auth_state(tmp_path, monkeypatch) -> None:
    path = tmp_path / "plex_auth.json"
    path.write_text('{"version":999,"account":{"auth_token":"secret"}}')
    monkeypatch.setattr(plex_auth.state, "update_settings", lambda patch: dict(patch))
    auth = plex_auth.PlexAuthManager(
        store=plex_auth.PlexAuthStore(str(path)),
        client_identity=plex_auth.PlexClientIdentity("relaytv-device"),
    )

    assert auth.disconnect() == {"linked": False}
    assert auth.store.load()["account"] == {}
    assert "secret" not in path.read_text()


# --- shared (non-owner) account credentials ---------------------------------


def _jwt_like() -> str:
    return "header.payload.signature"


def test_status_reports_a_server_credential_that_never_resolved(tmp_path) -> None:
    """A JWT left in the server slot means the upgrade could not run.

    `/api/v2/devices` lists the account's own devices, so a server shared with
    this account is never in it. Browsing still works on the JWT; playback
    fails at PMS with an unexplained 400, so status has to say so.
    """
    store = plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json"))
    store.save({
        "device": {},
        "account": {"auth_token": "account-token", "id": "1", "username": "gavin"},
        "server": {"machine_id": "m1", "access_token": _jwt_like(), "server_url": "https://pms:32400"},
    })
    manager = plex_auth.PlexAuthManager(store=store)

    status = manager.status()

    assert status["server_selected"] is True
    assert status["server_token_unresolved"] is True


def test_status_is_quiet_once_the_server_credential_is_a_real_token(tmp_path) -> None:
    store = plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json"))
    store.save({
        "device": {},
        "account": {"auth_token": "account-token", "id": "1", "username": "gavin"},
        "server": {"machine_id": "m1", "access_token": "plain-server-token", "server_url": "https://pms:32400"},
    })
    manager = plex_auth.PlexAuthManager(store=store)

    assert manager.status()["server_token_unresolved"] is False


def test_an_unlinked_account_reports_no_unresolved_credential(tmp_path) -> None:
    store = plex_auth.PlexAuthStore(str(tmp_path / "plex_auth.json"))
    store.save({"device": {}, "account": {}, "server": {}})

    assert plex_auth.PlexAuthManager(store=store).status()["server_token_unresolved"] is False
