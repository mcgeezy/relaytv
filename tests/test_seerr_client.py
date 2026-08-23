# SPDX-License-Identifier: GPL-3.0-only
import email.message
import io
import json
import urllib.error
import urllib.request

import pytest

from relaytv_app.config import SettingsSnapshot
from relaytv_app.integrations import seerr_client


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()


class _RecordingOpener:
    def __init__(self, payload: object):
        self.payload = payload
        self.requests: list[urllib.request.Request] = []

    def open(self, request, timeout):
        self.requests.append(request)
        return _Response(json.dumps(self.payload).encode("utf-8"))


def _config(**overrides) -> seerr_client.SeerrConfig:
    values = {
        "enabled": True,
        "server_url": "https://seerr.example/base",
        "api_key": "super-secret",
        "shared_requests_enabled": False,
        "request_user_id": None,
    }
    values.update(overrides)
    return seerr_client.SeerrConfig(**values)


def test_normalize_server_url_preserves_prefix_and_strips_api_suffix() -> None:
    assert (
        seerr_client.normalize_server_url(" HTTPS://Seerr.Example:443/base/api/v1/ ")
        == "https://seerr.example:443/base"
    )
    with pytest.raises(ValueError):
        seerr_client.normalize_server_url("https://admin:secret@seerr.example")
    with pytest.raises(ValueError):
        seerr_client.normalize_server_url("file:///data/seerr")


def test_config_snapshot_is_immutable_and_normalized() -> None:
    source = {
        "RELAYTV_SEERR_ENABLED": "1",
        "RELAYTV_SEERR_SERVER_URL": "https://seerr.example/api/v1",
        "RELAYTV_SEERR_API_KEY": " secret ",
        "RELAYTV_SEERR_REQUEST_USER_ID": "9",
    }
    config = seerr_client.SeerrConfig.from_snapshot(SettingsSnapshot(source))
    source["RELAYTV_SEERR_SERVER_URL"] = "https://replacement.example"

    assert config.server_url == "https://seerr.example"
    assert config.api_key == "secret"
    assert config.request_user_id == 9


def test_client_keeps_api_key_in_header_and_out_of_url() -> None:
    opener = _RecordingOpener({"version": "3.4.1"})
    client = seerr_client.SeerrClient(_config(), opener=opener)

    assert client.get("/status", query={"checkUpdateAvailable": False}) == {
        "version": "3.4.1"
    }
    request = opener.requests[0]
    assert request.full_url == (
        "https://seerr.example/base/api/v1/status?checkUpdateAvailable=false"
    )
    assert request.get_header("X-api-key") == "super-secret"
    assert "super-secret" not in request.full_url


def test_cross_origin_redirect_is_rejected_before_secret_can_move() -> None:
    handler = seerr_client._SameOriginRedirectHandler()
    request = urllib.request.Request(
        "https://seerr.example/api/v1/status",
        headers={"X-Api-Key": "super-secret"},
    )

    with pytest.raises(urllib.error.HTTPError) as exc_info:
        handler.redirect_request(
            request,
            io.BytesIO(),
            302,
            "Found",
            email.message.Message(),
            "https://attacker.example/capture",
        )

    assert exc_info.value.code == 502
    assert "super-secret" not in str(exc_info.value)


def test_upstream_auth_error_is_sanitized() -> None:
    class _FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "response mentioned super-secret",
                email.message.Message(),
                io.BytesIO(b'{"apiKey":"super-secret"}'),
            )

    client = seerr_client.SeerrClient(_config(), opener=_FailingOpener())
    with pytest.raises(seerr_client.SeerrError) as exc_info:
        client.get("/auth/me")

    assert exc_info.value.code == "seerr_auth_failed"
    assert "super-secret" not in str(exc_info.value)
