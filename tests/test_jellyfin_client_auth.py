# SPDX-License-Identifier: GPL-3.0-only
"""Client login and shared casting must work at the same time."""
import json

import pytest

from relaytv_app import state
from relaytv_app.config import runtime_config
from relaytv_app.integrations import jellyfin_receiver as receiver


@pytest.fixture
def shared_client(monkeypatch):
    monkeypatch.setattr(receiver, "_STATUS", {
        **receiver._STATUS,
        "enabled": True, "running": True, "connected": True,
        "server_url": "http://jf.example", "device_id": "tv-device",
        "auth_mode": "shared_api_key", "api_key_configured": True,
        "auth_user_configured": True, "authenticated": False,
        "auth_user_id": "", "last_auth_ok": None,
        "last_register_ok": True,
    })
    for name, value in {
        "_AUTH_MODE": "shared_api_key", "_API_KEY": "cast-key",
        "_AUTH_USERNAME": "viewer", "_AUTH_PASSWORD": "password",
        "_ACCESS_TOKEN": "", "_AUTH_USER_ID": "", "_AUTH_SESSION_ID": "",
    }.items():
        monkeypatch.setattr(receiver, name, value)
    monkeypatch.setattr(state, "get_settings", lambda: {})
    monkeypatch.setattr(receiver, "_preferred_catalog_user_id", lambda: "")
    monkeypatch.setattr(receiver, "_control_socket_status", lambda: {})
    runtime_config.set_value("RELAYTV_JELLYFIN_AUTH_ENABLED", "1")
    receiver._catalog_cache_clear()
    requests = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps(self.body).encode()

    def urlopen(req, timeout=5):
        requests.append(req)
        if req.full_url.endswith("AuthenticateByName"):
            assert json.loads(req.data) == {"Username": "viewer", "Pw": "password"}
            return Response({"AccessToken": "client-token", "User": {"Id": "viewer-id"}})
        return Response({"Id": "movie", "Name": "Movie", "Type": "Movie"})

    monkeypatch.setattr(receiver._urlrequest, "urlopen", urlopen)
    yield requests
    receiver._catalog_cache_clear()


def test_shared_cast_authenticates_client_and_keeps_request_roles_separate(shared_client):
    receiver._ensure_authentication()
    assert receiver.status()["authenticated"] is True
    assert receiver.status()["catalog_auth_source"] == "user_session"
    assert receiver.status()["cast_target_scope"] == "shared"
    assert receiver.status()["catalog_user_id"] == "viewer-id"
    assert receiver.control_token() == "cast-key"
    assert receiver.catalog_token() == "client-token"
    assert 'DeviceId="tv-device-client"' in shared_client[0].get_header("X-emby-authorization")

    assert receiver.get_item_metadata("movie")["title"] == "Movie"
    metadata = shared_client[-1]
    assert metadata.full_url == "http://jf.example/Users/viewer-id/Items/movie"
    assert metadata.get_header("X-emby-token") == "client-token"
    assert 'DeviceId="tv-device-client"' in metadata.get_header("X-emby-authorization")
    # Catalog detail/playback-info share this HTTP helper.
    receiver._get_json("http://jf.example/Users/viewer-id/Items", token="client-token")
    assert 'DeviceId="tv-device-client"' in shared_client[-1].get_header("X-emby-authorization")

    assert receiver.send_playback_start_once({"ItemId": "movie"})["ok"] is True
    control = shared_client[-1]
    assert control.get_header("X-emby-token") == "cast-key"
    assert 'DeviceId="tv-device"' in control.get_header("X-emby-authorization")


def test_pending_and_failed_client_login_never_fall_back_to_admin_key(shared_client, monkeypatch):
    assert receiver.catalog_token() == ""
    assert receiver._request_context().catalog_token == ""
    assert receiver.status()["catalog_ready"] is False

    # An explicit retry that fails must also retire an earlier login token.
    receiver._ensure_authentication()
    receiver.get_item_metadata("movie")
    assert receiver._CATALOG_CACHE
    monkeypatch.setattr(receiver._urlrequest, "urlopen", lambda *a, **kw: (_ for _ in ()).throw(OSError("login rejected")))
    assert receiver.authenticate_once()["reason"] == "auth_failed"
    assert receiver.catalog_token() == ""
    assert receiver.session_token() == ""
    assert not receiver._CATALOG_CACHE
    assert receiver.status()["catalog_ready"] is False
    assert receiver.status()["control_auth_source"] == "api_key"
    assert receiver.control_token() == "cast-key"
    assert receiver.status()["sync_health"] == "ok"


def test_api_key_only_setup_remains_functional(shared_client, monkeypatch):
    monkeypatch.setattr(receiver, "_AUTH_USERNAME", "")
    monkeypatch.setattr(receiver, "_AUTH_PASSWORD", "")
    monkeypatch.setitem(receiver._STATUS, "auth_user_configured", False)
    receiver._ensure_authentication()
    assert shared_client == []
    assert receiver.catalog_token() == "cast-key"
    assert receiver._request_context().catalog_token == "cast-key"
    assert receiver.status()["catalog_auth_source"] == "api_key"
    assert receiver.status()["catalog_ready"] is True


def test_shared_cast_media_is_independent_of_concurrent_client_browsing(shared_client, monkeypatch):
    import threading

    receiver._ensure_authentication()
    entered = threading.Event()
    release = threading.Event()
    original_open = receiver._urlrequest.urlopen
    failures = []

    def urlopen(req, timeout=5):
        if req.get_header("X-emby-token") == "cast-key":
            entered.set()
            assert release.wait(5), "test did not release the cast lookup"
        return original_open(req, timeout=timeout)

    def sink(action, payload, *, guard=None):
        assert receiver.catalog_token() == "cast-key"
        return receiver.get_item_detail("movie", user_id_override=payload["ControllingUserId"])

    monkeypatch.setattr(receiver._urlrequest, "urlopen", urlopen)
    monkeypatch.setattr(receiver, "_COMMAND_SINK", sink)

    def cast():
        try:
            assert receiver.dispatch_command("play", {"ControllingUserId": "viewer-id"})["title"] == "Movie"
            assert receiver.catalog_token() == "client-token"
        except BaseException as exc:
            failures.append(exc)

    thread = threading.Thread(target=cast)
    thread.start()
    try:
        assert entered.wait(5), "cast lookup never reached HTTP"
        assert receiver.catalog_token() == "client-token"
        assert receiver.get_item_detail("movie")["title"] == "Movie"
        assert shared_client[-1].get_header("X-emby-token") == "client-token"
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert not failures
    assert shared_client[-1].get_header("X-emby-token") == "cast-key"
    assert 'DeviceId="tv-device"' in shared_client[-1].get_header("X-emby-authorization")
    # Cache hits in either role must retain that role's media/image credential.
    client_detail = receiver.get_item_detail("movie")
    cast_detail = receiver.dispatch_command("play", {"ControllingUserId": "viewer-id"})
    assert "client-token" in client_detail["thumbnail"]
    assert "cast-key" in cast_detail["thumbnail"]


def test_cast_credential_scope_is_reset_on_failure(shared_client, monkeypatch):
    receiver._ensure_authentication()

    def fail(*args, **kwargs):
        assert receiver.catalog_token() == "cast-key"
        raise RuntimeError("command failed")

    monkeypatch.setattr(receiver, "_COMMAND_SINK", fail)
    with pytest.raises(RuntimeError, match="command failed"):
        receiver.dispatch_command("play", {})
    assert receiver.catalog_token() == "client-token"
    assert receiver._request_context().catalog_token == "client-token"
