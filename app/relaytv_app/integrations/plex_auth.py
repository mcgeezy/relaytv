# SPDX-License-Identifier: GPL-3.0-only
"""Plex account linking, renewable device credentials, and server selection."""
from __future__ import annotations

import base64
import copy
from dataclasses import dataclass
import hashlib
import os
import secrets
import threading
import time
from typing import Callable
import urllib.parse

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from .. import state
from .plex_client import PlexClient, PlexClientIdentity, PlexError, normalize_server_url


AUTH_STATE_FILE = "plex_auth.json"
AUTH_STATE_VERSION = 1
PLEX_CLOUD_URL = "https://clients.plex.tv"
FLOW_MAX_AGE_SEC = 30 * 60
REFRESH_WINDOW_SEC = 24 * 60 * 60
JWT_SCOPE = "username,email,friendly_name,restricted,anonymous,joinedAt"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64url_decode(value: object) -> bytes:
    text = str(value or "").strip()
    if not text:
        raise ValueError("missing key material")
    return base64.urlsafe_b64decode(text + ("=" * (-len(text) % 4)))


class PlexAuthError(PlexError):
    pass


class PlexAuthStore:
    """Versioned, private Plex credential storage outside settings.json."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path or state._state_path(AUTH_STATE_FILE)
        self._lock = threading.RLock()

    def load(self) -> dict:
        with self._lock:
            if not os.path.exists(self.path):
                return self._empty()
            raw = state._load_json(self.path, None)
            if not isinstance(raw, dict) or raw.get("version") != AUTH_STATE_VERSION:
                raise PlexAuthError(
                    "plex_auth_state_invalid",
                    "Plex account state could not be read",
                    status_code=500,
                )
            return copy.deepcopy(raw)

    def save(self, payload: dict) -> None:
        with self._lock:
            document = copy.deepcopy(payload)
            document["version"] = AUTH_STATE_VERSION
            if not state._atomic_write_json(self.path, document):
                raise PlexAuthError(
                    "plex_auth_state_write_failed",
                    "Plex account state could not be saved",
                    status_code=500,
                )
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                raise PlexAuthError(
                    "plex_auth_state_write_failed",
                    "Plex account state could not be protected",
                    status_code=500,
                ) from None

    def ensure_device(self) -> dict:
        with self._lock:
            payload = self.load()
            device = payload.get("device")
            if isinstance(device, dict) and device.get("private_key") and device.get("kid"):
                return payload
            private_key = Ed25519PrivateKey.generate()
            private_raw = private_key.private_bytes(
                serialization.Encoding.Raw,
                serialization.PrivateFormat.Raw,
                serialization.NoEncryption(),
            )
            public_raw = private_key.public_key().public_bytes(
                serialization.Encoding.Raw,
                serialization.PublicFormat.Raw,
            )
            payload["device"] = {
                "private_key": _b64url(private_raw),
                "public_key": _b64url(public_raw),
                "kid": _b64url(hashlib.sha256(public_raw).digest()),
            }
            self.save(payload)
            return payload

    @staticmethod
    def _empty() -> dict:
        return {
            "version": AUTH_STATE_VERSION,
            "device": {},
            "account": {},
            "server": {},
        }


@dataclass(slots=True)
class _LinkFlow:
    flow_id: str
    browser_secret: str
    pin_id: int
    code: str
    expires_at: float
    generation: int


class PlexAuthManager:
    def __init__(
        self,
        *,
        store: PlexAuthStore | None = None,
        client_identity: PlexClientIdentity | None = None,
        client_factory: Callable[[str, str], PlexClient] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store or PlexAuthStore()
        self._identity = client_identity
        self._client_factory = client_factory or self._default_client_factory
        self._clock = clock
        self._lock = threading.RLock()
        self._refresh_lock = threading.Lock()
        self._flows: dict[str, _LinkFlow] = {}
        self._generation = 0
        self._last_server_test: dict[str, object] = {}

    @property
    def identity(self) -> PlexClientIdentity:
        with self._lock:
            if self._identity is None:
                self._identity = PlexClientIdentity.current()
            return self._identity

    def _default_client_factory(self, base_url: str, token: str) -> PlexClient:
        return PlexClient(base_url, token=token, identity=self.identity)

    def _cloud(self, token: str = "") -> PlexClient:
        return self._client_factory(PLEX_CLOUD_URL, token)

    def start_link(self, browser_secret: str) -> dict[str, object]:
        secret = str(browser_secret or "").strip()
        if not secret:
            raise PlexAuthError(
                "plex_flow_not_bound",
                "Plex account linking must be started from this browser",
                status_code=400,
            )
        with self._lock:
            self._prune_flows_locked()
            generation = self._generation
            auth_state = self.store.ensure_device()
            device = auth_state["device"]
            jwk = {
                "kty": "OKP",
                "crv": "Ed25519",
                "x": str(device["public_key"]),
                "kid": str(device["kid"]),
                "alg": "EdDSA",
            }

        response = self._cloud().post(
            "/api/v2/pins",
            body={"jwk": jwk, "strong": True},
            auth=False,
        )
        if not isinstance(response, dict):
            raise PlexAuthError(
                "plex_invalid_response",
                "Plex returned an unexpected account-link response",
                status_code=502,
            )
        try:
            pin_id = int(response.get("id"))
        except (TypeError, ValueError):
            pin_id = 0
        code = str(response.get("code") or "").strip()
        try:
            expires_in = int(response.get("expiresIn") or FLOW_MAX_AGE_SEC)
        except (TypeError, ValueError):
            expires_in = FLOW_MAX_AGE_SEC
        if pin_id <= 0 or not code:
            raise PlexAuthError(
                "plex_invalid_response",
                "Plex returned an incomplete account-link response",
                status_code=502,
            )
        expires_in = max(1, min(FLOW_MAX_AGE_SEC, expires_in))
        flow = _LinkFlow(
            flow_id=secrets.token_urlsafe(24),
            browser_secret=secret,
            pin_id=pin_id,
            code=code,
            expires_at=self._clock() + expires_in,
            generation=generation,
        )
        with self._lock:
            if generation != self._generation:
                raise PlexAuthError(
                    "plex_flow_cancelled",
                    "Plex account linking was cancelled",
                    status_code=409,
                )
            self._flows[flow.flow_id] = flow
        query = urllib.parse.urlencode(
            {
                "clientID": self.identity.client_identifier,
                "code": code,
                "context[device][product]": self.identity.product,
            }
        )
        return {
            "flow_id": flow.flow_id,
            "link_url": f"https://app.plex.tv/auth#?{query}",
            "expires_in": expires_in,
        }

    def poll_link(self, flow_id: str, browser_secret: str) -> dict[str, object]:
        flow = self._flow(flow_id, browser_secret)
        auth_state = self.store.load()
        device_jwt = self._signed_device_jwt(auth_state["device"], nonce=None)
        response = self._cloud().get(
            f"/api/v2/pins/{flow.pin_id}",
            query={"deviceJWT": device_jwt},
            auth=False,
        )
        if not isinstance(response, dict):
            raise PlexAuthError(
                "plex_invalid_response",
                "Plex returned an unexpected account-link response",
                status_code=502,
            )
        token = str(response.get("authToken") or response.get("auth_token") or "").strip()
        if not token:
            return {
                "linked": False,
                "pending": True,
                "expires_in": max(0, int(flow.expires_at - self._clock())),
            }

        user = self._cloud(token).get("/api/v2/user")
        account = self._account_record(token, user)
        with self._lock:
            current = self._flows.get(flow.flow_id)
            if current is not flow or flow.generation != self._generation:
                raise PlexAuthError(
                    "plex_flow_cancelled",
                    "Plex account linking was cancelled",
                    status_code=409,
                )
            latest = self.store.load()
            latest["account"] = account
            latest["server"] = {}
            self.store.save(latest)
            self._flows.pop(flow.flow_id, None)
            self._generation += 1
            self._last_server_test = {}
        return {
            "linked": True,
            "pending": False,
            "account": self._public_account(account),
            "servers": self.list_servers(),
        }

    def cancel_link(self, flow_id: str, browser_secret: str) -> dict[str, bool]:
        flow = self._flow(flow_id, browser_secret)
        with self._lock:
            self._flows.pop(flow.flow_id, None)
        return {"cancelled": True}

    def disconnect(self) -> dict[str, bool]:
        with self._lock:
            try:
                payload = self.store.load()
            except PlexAuthError as exc:
                if exc.code != "plex_auth_state_invalid":
                    raise
                payload = self.store._empty()
            payload["account"] = {}
            payload["server"] = {}
            self.store.save(payload)
            self._generation += 1
            self._flows.clear()
            self._last_server_test = {}
        state.update_settings({"plex_server_machine_id": ""})
        return {"linked": False}

    def refresh_token(self, *, force: bool = False) -> bool:
        with self._refresh_lock:
            return self._refresh_token_serialized(force=force)

    def _refresh_token_serialized(self, *, force: bool) -> bool:
        with self._lock:
            generation = self._generation
            payload = self.store.load()
            account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
            token = str(account.get("auth_token") or "").strip()
            expires_at = float(account.get("expires_at") or 0)
            if not token:
                raise PlexAuthError(
                    "plex_not_authenticated",
                    "Plex account is not linked",
                    status_code=503,
                )
            if not force and expires_at > self._clock() + REFRESH_WINDOW_SEC:
                return False
            device = payload.get("device") if isinstance(payload.get("device"), dict) else {}

        nonce_response = self._cloud().get("/api/v2/auth/nonce", auth=False)
        nonce = str(nonce_response.get("nonce") if isinstance(nonce_response, dict) else "")
        if not nonce:
            raise PlexAuthError(
                "plex_invalid_response",
                "Plex returned an incomplete token-refresh response",
                status_code=502,
            )
        device_jwt = self._signed_device_jwt(device, nonce=nonce)
        token_response = self._cloud().post(
            "/api/v2/auth/token",
            body={"jwt": device_jwt},
            auth=False,
        )
        refreshed = str(
            token_response.get("auth_token") if isinstance(token_response, dict) else ""
        ).strip()
        if not refreshed:
            raise PlexAuthError(
                "plex_invalid_response",
                "Plex returned an incomplete token-refresh response",
                status_code=502,
            )
        user = self._cloud(refreshed).get("/api/v2/user")
        account_record = self._account_record(refreshed, user)
        with self._lock:
            if generation != self._generation:
                raise PlexAuthError(
                    "plex_credentials_changed",
                    "Plex account changed while credentials were refreshing",
                    status_code=409,
                )
            latest = self.store.load()
            latest["account"] = account_record
            self.store.save(latest)
        return True

    def list_servers(self) -> list[dict[str, object]]:
        self.refresh_token(force=False)
        payload = self.store.load()
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        token = str(account.get("auth_token") or "")
        resources = self._cloud(token).get(
            "/api/v2/resources",
            query={"includeHttps": 1, "includeRelay": 1, "includeIPv6": 1},
        )
        return [self._public_server(resource) for resource in self._server_resources(resources)]

    def select_server(self, machine_id: str) -> dict[str, object]:
        requested = str(machine_id or "").strip()
        if not requested:
            raise PlexAuthError(
                "plex_server_required",
                "Choose a Plex Media Server",
                status_code=400,
            )
        self.refresh_token(force=False)
        with self._lock:
            generation = self._generation
            payload = self.store.load()
            account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
            account_token = str(account.get("auth_token") or "")
        resources = self._cloud(account_token).get(
            "/api/v2/resources",
            query={"includeHttps": 1, "includeRelay": 1, "includeIPv6": 1},
        )
        resource = next(
            (
                item
                for item in self._server_resources(resources)
                if str(item.get("clientIdentifier") or "") == requested
            ),
            None,
        )
        if resource is None:
            raise PlexAuthError(
                "plex_server_not_found",
                "The selected Plex Media Server is no longer available to this account",
                status_code=404,
            )
        server_token = str(resource.get("accessToken") or "").strip()
        if not server_token:
            raise PlexAuthError(
                "plex_server_unauthorized",
                "Plex did not provide access to the selected server",
                status_code=403,
            )

        last_error: PlexError | None = None
        for connection in self._connection_candidates(resource):
            try:
                client = self._client_factory(str(connection["uri"]), server_token)
                identity = client.get("/identity")
                container = identity.get("MediaContainer") if isinstance(identity, dict) else None
                actual = str(container.get("machineIdentifier") if isinstance(container, dict) else "")
                if actual != requested:
                    continue
            except (PlexError, ValueError) as exc:
                if isinstance(exc, PlexError):
                    last_error = exc
                continue
            server_record = {
                "machine_id": requested,
                "name": str(resource.get("name") or "Plex Media Server"),
                "server_url": str(connection["uri"]),
                "access_token": server_token,
                "local": bool(connection.get("local")),
                "relay": bool(connection.get("relay")),
                "secure": str(connection["uri"]).lower().startswith("https://"),
            }
            with self._lock:
                if generation != self._generation:
                    raise PlexAuthError(
                        "plex_credentials_changed",
                        "Plex account changed while the server was being selected",
                        status_code=409,
                    )
                latest = self.store.load()
                latest["server"] = server_record
                self.store.save(latest)
                self._last_server_test = {
                    "reachable": True,
                    "version": str(container.get("version") or ""),
                    "tested_at": int(self._clock()),
                }
            state.update_settings({"plex_server_machine_id": requested})
            return self._public_selected_server(server_record)
        if last_error is not None:
            raise last_error
        raise PlexAuthError(
            "plex_server_identity_mismatch",
            "No advertised connection matched the selected Plex server",
            status_code=502,
        )

    def test_selected_server(self) -> dict[str, object]:
        with self._lock:
            generation = self._generation
            payload = self.store.load()
            server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
            url = str(server.get("server_url") or "")
            token = str(server.get("access_token") or "")
            machine_id = str(server.get("machine_id") or "")
        if not url or not token or not machine_id:
            raise PlexAuthError(
                "plex_server_not_selected",
                "Choose a Plex Media Server first",
                status_code=503,
            )
        identity = self._client_factory(url, token).get("/identity")
        container = identity.get("MediaContainer") if isinstance(identity, dict) else None
        actual = str(container.get("machineIdentifier") if isinstance(container, dict) else "")
        if actual != machine_id:
            raise PlexAuthError(
                "plex_server_identity_mismatch",
                "The configured address belongs to a different Plex server",
                status_code=502,
            )
        result = {
            "reachable": True,
            "version": str(container.get("version") or ""),
            "tested_at": int(self._clock()),
        }
        with self._lock:
            if generation != self._generation:
                raise PlexAuthError(
                    "plex_credentials_changed",
                    "Plex account changed while the server was being tested",
                    status_code=409,
                )
            self._last_server_test = result
        return dict(result)

    def status(self) -> dict[str, object]:
        payload = self.store.load()
        account = payload.get("account") if isinstance(payload.get("account"), dict) else {}
        server = payload.get("server") if isinstance(payload.get("server"), dict) else {}
        linked = bool(account.get("auth_token"))
        selected = bool(server.get("machine_id") and server.get("access_token"))
        settings = state.get_settings()
        with self._lock:
            active_flows = sum(
                1 for flow in self._flows.values() if flow.expires_at > self._clock()
            )
            last_test = dict(self._last_server_test)
        return {
            "enabled": bool(settings.get("plex_enabled", False)),
            "linked": linked,
            "account": self._public_account(account) if linked else None,
            "token_expires_at": int(float(account.get("expires_at") or 0)) or None,
            "server_selected": selected,
            "server": self._public_selected_server(server) if selected else None,
            "link_in_progress": active_flows > 0,
            "last_server_test": last_test or None,
        }

    def _flow(self, flow_id: str, browser_secret: str) -> _LinkFlow:
        with self._lock:
            self._prune_flows_locked()
            flow = self._flows.get(str(flow_id or ""))
            if flow is None:
                raise PlexAuthError(
                    "plex_flow_expired",
                    "Plex account linking expired; start again",
                    status_code=410,
                )
            if not secrets.compare_digest(
                flow.browser_secret,
                str(browser_secret or ""),
            ):
                raise PlexAuthError(
                    "plex_flow_not_bound",
                    "Plex account linking must finish in the browser that started it",
                    status_code=403,
                )
            return flow

    def _prune_flows_locked(self) -> None:
        now = self._clock()
        self._flows = {
            flow_id: flow
            for flow_id, flow in self._flows.items()
            if flow.expires_at > now
        }

    def _signed_device_jwt(self, device: dict, *, nonce: str | None) -> str:
        try:
            private = Ed25519PrivateKey.from_private_bytes(
                _b64url_decode(device.get("private_key"))
            )
            kid = str(device["kid"])
        except (KeyError, TypeError, ValueError):
            raise PlexAuthError(
                "plex_auth_state_invalid",
                "Plex device credentials could not be read",
                status_code=500,
            ) from None
        now = int(self._clock())
        claims: dict[str, object] = {
            "aud": "plex.tv",
            "iss": self.identity.client_identifier,
            "iat": now,
            "exp": now + 5 * 60,
        }
        if nonce:
            claims["nonce"] = nonce
            claims["scope"] = JWT_SCOPE
        return jwt.encode(
            claims,
            private,
            algorithm="EdDSA",
            headers={"kid": kid, "alg": "EdDSA", "typ": "JWT"},
        )

    @staticmethod
    def _account_record(token: str, user: object) -> dict[str, object]:
        details = user if isinstance(user, dict) else {}
        try:
            claims = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            claims = {}
        try:
            expires_at = int(claims.get("exp") or 0)
        except (TypeError, ValueError):
            expires_at = 0
        return {
            "auth_token": token,
            "expires_at": expires_at,
            "id": str(details.get("id") or claims.get("sub") or ""),
            "username": str(details.get("username") or claims.get("username") or ""),
            "friendly_name": str(
                details.get("friendlyName")
                or details.get("title")
                or claims.get("friendly_name")
                or ""
            ),
        }

    @staticmethod
    def _public_account(account: dict) -> dict[str, object]:
        return {
            "id": str(account.get("id") or ""),
            "username": str(account.get("username") or ""),
            "friendly_name": str(account.get("friendly_name") or ""),
        }

    @staticmethod
    def _server_resources(resources: object) -> list[dict]:
        if not isinstance(resources, list):
            raise PlexAuthError(
                "plex_invalid_response",
                "Plex returned an unexpected server list",
                status_code=502,
            )
        out: list[dict] = []
        for item in resources:
            if not isinstance(item, dict):
                continue
            provides = {
                part.strip().lower()
                for part in str(item.get("provides") or "").split(",")
                if part.strip()
            }
            if "server" in provides:
                out.append(item)
        return out

    @staticmethod
    def _connection_candidates(resource: dict) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        for raw in resource.get("connections") or []:
            if not isinstance(raw, dict) or bool(raw.get("relay")):
                continue
            try:
                uri = normalize_server_url(raw.get("uri"))
            except ValueError:
                continue
            if not uri:
                continue
            candidates.append(
                {
                    "uri": uri,
                    "local": bool(raw.get("local")),
                    "relay": bool(raw.get("relay")),
                }
            )
        candidates.sort(
            key=lambda item: (
                not bool(item["local"]),
                not str(item["uri"]).lower().startswith("https://"),
            )
        )
        return candidates

    @classmethod
    def _public_server(cls, resource: dict) -> dict[str, object]:
        connections = cls._connection_candidates(resource)
        return {
            "machine_id": str(resource.get("clientIdentifier") or ""),
            "name": str(resource.get("name") or "Plex Media Server"),
            "owned": bool(resource.get("owned")),
            "presence": bool(resource.get("presence")),
            "connections": [
                {
                    "uri": str(connection["uri"]),
                    "local": bool(connection["local"]),
                    "secure": str(connection["uri"]).lower().startswith("https://"),
                }
                for connection in connections
            ],
        }

    @staticmethod
    def _public_selected_server(server: dict) -> dict[str, object]:
        return {
            "machine_id": str(server.get("machine_id") or ""),
            "name": str(server.get("name") or "Plex Media Server"),
            "server_url": str(server.get("server_url") or ""),
            "local": bool(server.get("local")),
            "relay": bool(server.get("relay")),
            "secure": bool(server.get("secure")),
        }


auth_manager = PlexAuthManager()
