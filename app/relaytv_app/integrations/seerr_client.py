# SPDX-License-Identifier: GPL-3.0-only
"""Secret-safe HTTP transport for the allowlisted Seerr product adapter."""
from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from ..config import (
    SettingsSnapshot,
    normalize_optional_positive_int,
    normalize_seerr_request_mode,
    runtime_config,
)

DEFAULT_TIMEOUT_SEC = 5.0
MAX_RESPONSE_BYTES = 4 * 1024 * 1024
MAX_IMAGE_BYTES = 12 * 1024 * 1024


class SeerrError(RuntimeError):
    """A sanitized upstream/configuration failure safe for routes and logs."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int,
        upstream_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code)
        self.message = str(message)
        self.status_code = int(status_code)
        self.upstream_status = upstream_status


@dataclass(frozen=True, slots=True)
class SeerrBinaryResponse:
    content: bytes
    content_type: str
    cache_control: str
    etag: str
    last_modified: str


@dataclass(frozen=True, slots=True)
class SeerrJsonResponse:
    data: dict | list
    status: int
    set_cookies: tuple[str, ...] = ()


def normalize_server_url(value: object) -> str:
    """Return a canonical Seerr base URL without an accidental API suffix."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Seerr server URL must use http or https")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Seerr server URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Seerr server URL must not contain a query or fragment")
        # Accessing .port validates malformed/out-of-range ports.
        _ = parsed.port
    except ValueError as exc:
        if str(exc).startswith("Seerr server URL"):
            raise
        raise ValueError("Seerr server URL is invalid") from None

    path = (parsed.path or "").rstrip("/")
    if path.lower().endswith("/api/v1"):
        path = path[: -len("/api/v1")].rstrip("/")
    hostname = parsed.hostname.lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, path, "", ""))


@dataclass(frozen=True, slots=True)
class SeerrConfig:
    enabled: bool
    server_url: str
    api_key: str
    shared_requests_enabled: bool
    request_user_id: int | None
    request_mode: str = "disabled"
    configuration_error: str = ""

    @classmethod
    def from_snapshot(cls, snapshot: SettingsSnapshot) -> "SeerrConfig":
        raw_url = snapshot.text("RELAYTV_SEERR_SERVER_URL")
        try:
            server_url = normalize_server_url(raw_url)
            configuration_error = ""
        except ValueError as exc:
            server_url = ""
            configuration_error = str(exc)
        request_mode = normalize_seerr_request_mode(
            snapshot.raw("RELAYTV_SEERR_REQUEST_MODE"),
            shared_requests_enabled=snapshot.flag(
                "RELAYTV_SEERR_SHARED_REQUESTS_ENABLED", False
            ),
        )
        return cls(
            enabled=snapshot.flag("RELAYTV_SEERR_ENABLED", False),
            server_url=server_url,
            api_key=snapshot.text("RELAYTV_SEERR_API_KEY"),
            shared_requests_enabled=request_mode == "shared_admin",
            request_user_id=normalize_optional_positive_int(
                snapshot.raw("RELAYTV_SEERR_REQUEST_USER_ID")
            ),
            request_mode=request_mode,
            configuration_error=configuration_error,
        )

    @classmethod
    def current(cls) -> "SeerrConfig":
        return cls.from_snapshot(runtime_config.snapshot())

    @property
    def configured(self) -> bool:
        if self.request_mode == "caller_session":
            return bool(self.server_url)
        return bool(self.server_url and self.api_key)

    @property
    def api_base_url(self) -> str:
        return f"{self.server_url}/api/v1"


def _origin(url: str) -> tuple[str, str, int | None]:
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), str(parsed.hostname or "").lower(), port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Allow compatibility redirects without forwarding secrets cross-origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        absolute = urllib.parse.urljoin(req.full_url, str(newurl or ""))
        if _origin(absolute) != _origin(req.full_url):
            raise urllib.error.HTTPError(
                req.full_url,
                502,
                "cross-origin redirect rejected",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, absolute)


_OPENER = urllib.request.build_opener(_SameOriginRedirectHandler())


class SeerrClient:
    """One immutable Seerr configuration snapshot per operation."""

    def __init__(
        self,
        config: SeerrConfig,
        *,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        opener: Any = None,
        session_cookie: str = "",
    ) -> None:
        self.config = config
        self.timeout_sec = max(0.25, min(30.0, float(timeout_sec)))
        self._opener = opener or _OPENER
        cookie = str(session_cookie or "").strip()
        if "\r" in cookie or "\n" in cookie:
            raise ValueError("Invalid Seerr session cookie")
        self._session_cookie = cookie

    def get(self, path: str, *, query: dict[str, object] | None = None, auth: bool = True):
        return self.request_json("GET", path, query=query, auth=auth)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        auth: bool = True,
    ) -> dict | list:
        return self.request_json_response(
            method,
            path,
            query=query,
            body=body,
            auth=auth,
        ).data

    def request_json_response(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        auth: bool = True,
    ) -> SeerrJsonResponse:
        raw, _headers, status = self._request(
            method,
            path,
            query=query,
            body=body,
            auth=auth,
            accept="application/json",
            max_bytes=MAX_RESPONSE_BYTES,
            api_path=True,
        )
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise SeerrError(
                "seerr_invalid_response",
                "Seerr returned an invalid JSON response",
                status_code=502,
            ) from None
        if not isinstance(parsed, (dict, list)):
            raise SeerrError(
                "seerr_invalid_response",
                "Seerr returned an unexpected response shape",
                status_code=502,
            )
        get_all = getattr(_headers, "get_all", None)
        set_cookies = tuple(str(value) for value in (get_all("Set-Cookie") or [])) if get_all else ()
        return SeerrJsonResponse(data=parsed, status=status, set_cookies=set_cookies)

    def get_binary(
        self,
        path: str,
        *,
        query: dict[str, object] | None = None,
        auth: bool = True,
    ) -> SeerrBinaryResponse:
        raw, headers, _status = self._request(
            "GET",
            path,
            query=query,
            auth=auth,
            accept="image/*",
            max_bytes=MAX_IMAGE_BYTES,
            api_path=False,
        )
        return SeerrBinaryResponse(
            content=raw,
            content_type=str(headers.get("Content-Type") or "").split(";", 1)[0].strip(),
            cache_control=str(headers.get("Cache-Control") or "").strip(),
            etag=str(headers.get("ETag") or "").strip(),
            last_modified=str(headers.get("Last-Modified") or "").strip(),
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        auth: bool = True,
        accept: str,
        max_bytes: int,
        api_path: bool,
    ) -> tuple[bytes, object, int]:
        if not self.config.server_url:
            raise SeerrError(
                "seerr_not_configured",
                "Seerr server URL is not configured",
                status_code=503,
            )
        if auth and not (self._session_cookie or self.config.api_key):
            raise SeerrError(
                "seerr_not_configured",
                "Seerr API key is not configured",
                status_code=503,
            )
        route = str(path or "").strip()
        if not route.startswith("/") or route.startswith("//"):
            raise ValueError("Seerr client paths must be absolute API paths")
        base_url = self.config.api_base_url if api_path else self.config.server_url
        url = f"{base_url}{route}"
        if query:
            params = {
                str(key): str(value).lower() if isinstance(value, bool) else str(value)
                for key, value in query.items()
                if value is not None
            }
            if params:
                url = f"{url}?{urllib.parse.urlencode(params)}"

        payload = None
        request_headers = {
            "Accept": accept,
            "User-Agent": "RelayTV Seerr Integration",
        }
        if auth and self._session_cookie:
            request_headers["Cookie"] = self._session_cookie
        elif auth:
            request_headers["X-Api-Key"] = self.config.api_key
        if body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode("utf-8")
            request_headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=payload,
            headers=request_headers,
            method=str(method or "GET").upper(),
        )
        try:
            with self._opener.open(request, timeout=self.timeout_sec) as response:
                raw = response.read(max_bytes + 1)
                if len(raw) > max_bytes:
                    raise SeerrError(
                        "seerr_response_too_large",
                        "Seerr returned an unexpectedly large response",
                        status_code=502,
                    )
                headers = getattr(response, "headers", {})
                status = int(getattr(response, "status", 200) or 200)
        except SeerrError:
            raise
        except urllib.error.HTTPError as exc:
            raise _http_error(
                int(exc.code), session_auth=bool(auth and self._session_cookie)
            ) from None
        except (TimeoutError, socket.timeout):
            raise SeerrError(
                "seerr_timeout",
                "Seerr did not respond before the timeout",
                status_code=504,
            ) from None
        except (urllib.error.URLError, OSError):
            raise SeerrError(
                "seerr_unreachable",
                "Seerr could not be reached",
                status_code=502,
            ) from None
        return raw, headers, status


def _http_error(status: int, *, session_auth: bool = False) -> SeerrError:
    if status == 401:
        if session_auth:
            return SeerrError(
                "seerr_session_expired",
                "Your Seerr session has expired; connect again",
                status_code=401,
                upstream_status=status,
            )
        return SeerrError(
            "seerr_auth_failed",
            "Seerr rejected the configured API key",
            status_code=502,
            upstream_status=status,
        )
    if status == 403:
        return SeerrError(
            "seerr_forbidden",
            "The Seerr identity does not have permission for this operation",
            status_code=403,
            upstream_status=status,
        )
    if status == 404:
        return SeerrError(
            "seerr_not_found",
            "The requested Seerr resource was not found",
            status_code=404,
            upstream_status=status,
        )
    if status == 409:
        return SeerrError(
            "seerr_duplicate_request",
            "This media request already exists",
            status_code=409,
            upstream_status=status,
        )
    return SeerrError(
        "seerr_upstream_error",
        "Seerr returned an upstream error",
        status_code=502,
        upstream_status=status,
    )
