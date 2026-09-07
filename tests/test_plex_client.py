# SPDX-License-Identifier: GPL-3.0-only
import email.message
import http.client
import io
import json
import urllib.error
import urllib.request

import pytest

from relaytv_app.integrations import plex_client


class _Response(io.BytesIO):
    headers = email.message.Message()

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


def _identity() -> plex_client.PlexClientIdentity:
    return plex_client.PlexClientIdentity(
        client_identifier="device-123",
        version="0.10.3",
        device_name="Living Room",
    )


def test_normalize_server_url_accepts_only_an_origin() -> None:
    assert plex_client.normalize_server_url(" HTTPS://Plex.Example:32400/ ") == (
        "https://plex.example:32400"
    )
    with pytest.raises(ValueError):
        plex_client.normalize_server_url("https://owner:secret@plex.example:32400")
    with pytest.raises(ValueError):
        plex_client.normalize_server_url("https://plex.example:32400/web")
    with pytest.raises(ValueError):
        plex_client.normalize_server_url("file:///data/plex")


def test_client_keeps_token_in_header_and_sends_stable_identity() -> None:
    opener = _RecordingOpener({"MediaContainer": {"size": 0}})
    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=opener,
    )

    assert client.get(
        "/library/sections?includeDetails=1",
        query={"X-Plex-Container-Size": 20},
    ) == {"MediaContainer": {"size": 0}}
    request = opener.requests[0]
    assert request.full_url == (
        "https://plex.example:32400/library/sections?includeDetails=1&"
        "X-Plex-Container-Size=20"
    )
    assert request.get_header("X-plex-token") == "server-secret"
    assert request.get_header("X-plex-client-identifier") == "device-123"
    assert request.get_header("X-plex-device-name") == "Living Room"
    assert "server-secret" not in request.full_url


def test_no_content_request_keeps_timeline_credentials_in_headers() -> None:
    opener = _RecordingOpener({})
    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=opener,
    )

    client.request_no_content(
        "POST",
        "/:/timeline",
        query={"ratingKey": "10", "state": "playing", "time": 12000},
    )

    request = opener.requests[0]
    assert request.method == "POST"
    assert "ratingKey=10" in request.full_url
    assert "state=playing" in request.full_url
    assert request.get_header("X-plex-token") == "server-secret"
    assert "server-secret" not in request.full_url


def test_unauthenticated_identity_request_omits_token() -> None:
    opener = _RecordingOpener({"MediaContainer": {"machineIdentifier": "server-id"}})
    client = plex_client.PlexClient(
        "http://127.0.0.1:32400",
        identity=_identity(),
        opener=opener,
    )

    client.get("/identity", auth=False)

    assert opener.requests[0].get_header("X-plex-token") is None


def test_cross_origin_redirect_is_rejected_before_token_can_move() -> None:
    handler = plex_client._SameOriginRedirectHandler()
    request = urllib.request.Request(
        "https://plex.example:32400/library/sections",
        headers={"X-Plex-Token": "server-secret"},
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
    assert "server-secret" not in str(exc_info.value)


def test_absolute_or_cross_origin_paths_are_rejected() -> None:
    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=_RecordingOpener({}),
    )

    with pytest.raises(ValueError):
        client.get("https://attacker.example/capture")
    with pytest.raises(ValueError):
        client.get("//attacker.example/capture")
    with pytest.raises(ValueError):
        client.get("/library/sections?X-Plex-Token=path-secret")


def test_upstream_auth_error_is_sanitized() -> None:
    class _FailingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url,
                401,
                "response mentioned server-secret",
                email.message.Message(),
                io.BytesIO(b'{"authToken":"server-secret"}'),
            )

    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=_FailingOpener(),
    )

    with pytest.raises(plex_client.PlexError) as exc_info:
        client.get("/library/sections")

    assert exc_info.value.code == "plex_auth_expired"
    assert "server-secret" not in str(exc_info.value)


def test_binary_artwork_keeps_auth_in_headers_and_checks_media_type() -> None:
    class _ArtworkOpener:
        def __init__(self, content_type: str):
            self.content_type = content_type
            self.request = None

        def open(self, request, timeout):
            self.request = request
            response = _Response(b"image-data")
            response.headers = email.message.Message()
            response.headers["Content-Type"] = self.content_type
            return response

    opener = _ArtworkOpener("image/jpeg; charset=binary")
    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=opener,
    )

    result = client.get_bytes("/library/metadata/10/thumb/1", query={"width": 800})

    assert result.body == b"image-data"
    assert result.content_type == "image/jpeg"
    assert opener.request.get_header("X-plex-token") == "server-secret"
    assert "server-secret" not in opener.request.full_url

    invalid = _ArtworkOpener("text/html")
    with pytest.raises(plex_client.PlexError) as exc_info:
        plex_client.PlexClient(
            "https://plex.example:32400",
            token="server-secret",
            identity=_identity(),
            opener=invalid,
        ).get_bytes("/library/metadata/10/thumb/1")
    assert exc_info.value.code == "plex_invalid_response"

    active = _ArtworkOpener("image/svg+xml")
    with pytest.raises(plex_client.PlexError):
        plex_client.PlexClient(
            "https://plex.example:32400",
            token="server-secret",
            identity=_identity(),
            opener=active,
        ).get_bytes("/library/metadata/10/thumb/1")


def test_media_stream_forwards_one_range_without_exposing_token() -> None:
    class _StreamResponse(_Response):
        status = 206

        def __init__(self):
            super().__init__(b"first-second")
            self.headers = email.message.Message()
            self.headers["Content-Type"] = "video/mp4"
            self.headers["Content-Length"] = "12"
            self.headers["Content-Range"] = "bytes 10-21/100"

        def getcode(self):
            return self.status

    class _StreamOpener:
        def __init__(self):
            self.request = None

        def open(self, request, timeout):
            self.request = request
            return _StreamResponse()

    opener = _StreamOpener()
    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=opener,
    )

    stream = client.open_stream(
        "/library/parts/10/file.mp4",
        range_header="bytes=10-21",
    )

    assert stream.status_code == 206
    assert stream.headers["Content-Range"] == "bytes 10-21/100"
    assert b"".join(stream.iter_bytes(chunk_size=5)) == b"first-second"
    assert opener.request.get_header("Range") == "bytes=10-21"
    assert opener.request.get_header("X-plex-token") == "server-secret"
    assert "server-secret" not in opener.request.full_url

    with pytest.raises(plex_client.PlexError) as exc_info:
        client.open_stream(
            "/library/parts/10/file.mp4",
            range_header="bytes=0-1,10-11",
        )
    assert exc_info.value.code == "plex_invalid_range"


def test_media_stream_close_runs_transcode_cleanup_once() -> None:
    class _StreamResponse(_Response):
        status = 200

        def __init__(self):
            super().__init__(b"transcoded")
            self.headers = email.message.Message()
            self.headers["Content-Type"] = "video/x-matroska"

        def getcode(self):
            return self.status

    opener = _RecordingOpener({})
    opener.open = lambda request, timeout: (
        opener.requests.append(request) or _StreamResponse()
    )
    closed = []
    client = plex_client.PlexClient(
        "https://plex.example:32400",
        token="server-secret",
        identity=_identity(),
        opener=opener,
    )

    stream = client.open_stream(
        "/video/:/transcode/universal/start.mkv",
        query={"session": "session-1", "path": "/library/metadata/10"},
        on_close=lambda: closed.append(True),
    )
    assert b"".join(stream.iter_bytes()) == b"transcoded"
    stream.close()

    assert closed == [True]
    assert "session=session-1" in opener.requests[0].full_url
    assert "path=%2Flibrary%2Fmetadata%2F10" in opener.requests[0].full_url
    assert opener.requests[0].get_header("X-plex-token") == "server-secret"


def test_conversion_stream_tolerates_pms_chunk_termination() -> None:
    class _InterruptedResponse:
        def __init__(self) -> None:
            self.closed = False

        def read(self, _size):
            raise http.client.IncompleteRead(b"final-transcode-bytes")

        def close(self):
            self.closed = True

    response = _InterruptedResponse()
    closed = []
    stream = plex_client.PlexStreamResponse(
        status_code=200,
        headers={"Content-Type": "video/x-matroska"},
        _response=response,
        _on_close=lambda: closed.append(True),
        _allow_incomplete_read=True,
    )

    assert b"".join(stream.iter_bytes()) == b"final-transcode-bytes"
    assert response.closed is True
    assert closed == [True]


def test_direct_stream_preserves_incomplete_read_failure() -> None:
    class _InterruptedResponse:
        def read(self, _size):
            raise http.client.IncompleteRead(b"partial-file")

        def close(self):
            pass

    stream = plex_client.PlexStreamResponse(
        status_code=206,
        headers={"Content-Type": "video/mp4"},
        _response=_InterruptedResponse(),
    )

    with pytest.raises(http.client.IncompleteRead):
        b"".join(stream.iter_bytes())
