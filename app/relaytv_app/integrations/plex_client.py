# SPDX-License-Identifier: GPL-3.0-only
"""Secret-safe HTTP transport for Plex cloud and media-server APIs."""
from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from .. import device_identity


DEFAULT_TIMEOUT_SEC = 6.0
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
PLEX_PRODUCT = "RelayTV"
PLEX_PLATFORM = "Linux"


class PlexError(RuntimeError):
    """A sanitized Plex failure safe for API responses and logs."""

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


def normalize_server_url(value: object) -> str:
    """Return a canonical Plex connection URL without credentials or a path."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme.lower() not in {"http", "https"}:
            raise ValueError("Plex server URL must use http or https")
        if not parsed.hostname or parsed.username is not None or parsed.password is not None:
            raise ValueError("Plex server URL must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("Plex server URL must not contain a query or fragment")
        _ = parsed.port
    except ValueError as exc:
        if str(exc).startswith("Plex server URL"):
            raise
        raise ValueError("Plex server URL is invalid") from None

    path = (parsed.path or "").rstrip("/")
    if path not in {"", "/"}:
        raise ValueError("Plex server URL must not contain a path")
    hostname = str(parsed.hostname).lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    netloc = hostname
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    return urllib.parse.urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


def _origin(url: str) -> tuple[str, str, int]:
    parsed = urllib.parse.urlsplit(url)
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme.lower() == "https" else 80
    return parsed.scheme.lower(), str(parsed.hostname or "").lower(), port


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow Plex compatibility redirects only when credentials stay on-origin."""

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


@dataclass(frozen=True, slots=True)
class PlexClientIdentity:
    client_identifier: str
    product: str = PLEX_PRODUCT
    version: str = "local"
    platform: str = PLEX_PLATFORM
    device_name: str = "RelayTV"

    @classmethod
    def current(cls) -> "PlexClientIdentity":
        return cls(
            client_identifier=device_identity.device_id(),
            version=device_identity.app_version(),
            device_name=device_identity.device_name(),
        )

    def headers(self) -> dict[str, str]:
        return {
            "X-Plex-Client-Identifier": self.client_identifier,
            "X-Plex-Product": self.product,
            "X-Plex-Version": self.version,
            "X-Plex-Platform": self.platform,
            "X-Plex-Device-Name": self.device_name,
        }


class PlexClient:
    """One immutable Plex origin and credential snapshot per operation."""

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        identity: PlexClientIdentity | None = None,
        timeout_sec: float = DEFAULT_TIMEOUT_SEC,
        opener: Any = None,
    ) -> None:
        self.base_url = normalize_server_url(base_url)
        if not self.base_url:
            raise ValueError("Plex server URL is required")
        clean_token = str(token or "").strip()
        if "\r" in clean_token or "\n" in clean_token:
            raise ValueError("Invalid Plex token")
        self.token = clean_token
        self.identity = identity or PlexClientIdentity.current()
        self.timeout_sec = max(0.25, min(30.0, float(timeout_sec)))
        self._opener = opener or _OPENER

    def get(
        self,
        path: str,
        *,
        query: dict[str, object] | None = None,
        auth: bool = True,
    ) -> dict | list:
        return self.request_json("GET", path, query=query, auth=auth)

    def post(
        self,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        auth: bool = True,
    ) -> dict | list:
        return self.request_json("POST", path, query=query, body=body, auth=auth)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, object] | None = None,
        body: dict[str, object] | None = None,
        auth: bool = True,
    ) -> dict | list:
        if auth and not self.token:
            raise PlexError(
                "plex_not_authenticated",
                "Plex account is not linked",
                status_code=503,
            )
        route = str(path or "").strip()
        if not route.startswith("/") or route.startswith("//"):
            raise ValueError("Plex client paths must be absolute origin-relative paths")
        parsed_route = urllib.parse.urlsplit(route)
        if parsed_route.scheme or parsed_route.netloc or parsed_route.fragment:
            raise ValueError("Plex client paths must stay on the configured origin")

        url = f"{self.base_url}{parsed_route.path}"
        pairs = urllib.parse.parse_qsl(parsed_route.query, keep_blank_values=True)
        if any(
            key.strip().lower()
            in {"access_token", "auth_token", "token", "x-plex-token"}
            for key, _value in pairs
        ):
            raise ValueError("Plex client paths must not carry credentials")
        if query:
            pairs.extend(
                (
                    str(key),
                    str(value).lower() if isinstance(value, bool) else str(value),
                )
                for key, value in query.items()
                if value is not None
            )
        if pairs:
            url = f"{url}?{urllib.parse.urlencode(pairs)}"

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "RelayTV Plex Integration",
            **self.identity.headers(),
        }
        if auth:
            request_headers["X-Plex-Token"] = self.token
        payload = None
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
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise PlexError(
                        "plex_response_too_large",
                        "Plex returned an unexpectedly large response",
                        status_code=502,
                    )
        except PlexError:
            raise
        except urllib.error.HTTPError as exc:
            raise _http_error(int(exc.code)) from None
        except (TimeoutError, socket.timeout):
            raise PlexError(
                "plex_timeout",
                "Plex did not respond before the timeout",
                status_code=504,
            ) from None
        except (urllib.error.URLError, OSError):
            raise PlexError(
                "plex_unreachable",
                "Plex could not be reached",
                status_code=502,
            ) from None

        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise PlexError(
                "plex_invalid_response",
                "Plex returned an invalid JSON response",
                status_code=502,
            ) from None
        if not isinstance(parsed, (dict, list)):
            raise PlexError(
                "plex_invalid_response",
                "Plex returned an unexpected response shape",
                status_code=502,
            )
        return parsed


def _http_error(status: int) -> PlexError:
    if status in {401, 498}:
        return PlexError(
            "plex_auth_expired",
            "Plex authentication has expired; link the account again",
            status_code=401,
            upstream_status=status,
        )
    if status == 403:
        return PlexError(
            "plex_forbidden",
            "The linked Plex account does not have permission for this operation",
            status_code=403,
            upstream_status=status,
        )
    if status == 404:
        return PlexError(
            "plex_not_found",
            "The requested Plex resource was not found",
            status_code=404,
            upstream_status=status,
        )
    if status == 429:
        return PlexError(
            "plex_rate_limited",
            "Plex temporarily rate-limited this device",
            status_code=503,
            upstream_status=status,
        )
    return PlexError(
        "plex_upstream_error",
        "Plex returned an upstream error",
        status_code=502,
        upstream_status=status,
    )
