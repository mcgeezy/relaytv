# SPDX-License-Identifier: GPL-3.0-only
"""Plex catalog normalization and opaque public references."""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Any
import urllib.parse

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCMSIV

from . import plex_auth
from .plex_client import PlexBinaryResponse, PlexError


REFERENCE_VERSION = 1
REFERENCE_AAD = b"relaytv-plex-reference-v1"
MAX_REFERENCE_LENGTH = 4096
VIDEO_TYPES = {"movie", "show", "season", "episode"}
SECTION_TYPES = {"movie", "show"}
SORTS = {
    "title": "titleSort:asc",
    "added": "addedAt:desc",
    "year": "year:desc",
    "rating": "rating:desc",
}


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + ("=" * (-len(value) % 4)))


def _integer(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _number(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_upstream_path(value: object) -> str:
    path = str(value or "").strip()
    if not path.startswith("/") or path.startswith("//"):
        return ""
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        return ""
    if any(
        key.strip().lower() in {"access_token", "auth_token", "token", "x-plex-token"}
        for key, _value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    ):
        return ""
    return path


class PlexCatalogService:
    def __init__(self, auth_manager: plex_auth.PlexAuthManager | None = None) -> None:
        self.auth_manager = auth_manager or plex_auth.auth_manager

    def home(self, *, limit: int = 20) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        payload = session.client.get(
            "/hubs",
            query={
                "includeGuids": 1,
                "includeCollections": 0,
                "includeExternalMedia": 0,
            },
        )
        container = self._container(payload)
        rows: list[dict[str, object]] = []
        for hub in self._records(container, "Hub"):
            items = [
                item
                for raw in self._records(hub, "Metadata")
                if (item := self._item(session, raw)) is not None
            ][:limit]
            if not items:
                continue
            rows.append(
                {
                    "id": str(hub.get("hubIdentifier") or hub.get("key") or ""),
                    "title": str(hub.get("title") or "Plex"),
                    "items": items,
                    "more": bool(hub.get("more")),
                }
            )
        self.auth_manager.assert_server_session_current(session)
        return {"rows": rows, "server": session.machine_id}

    def libraries(self) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        payload = session.client.get("/library/sections")
        container = self._container(payload)
        libraries: list[dict[str, object]] = []
        for section in self._records(container, "Directory"):
            section_type = str(section.get("type") or "").strip().lower()
            section_key = str(section.get("key") or "").strip()
            if section_type not in SECTION_TYPES or not section_key:
                continue
            path = f"/library/sections/{urllib.parse.quote(section_key, safe='')}/all"
            libraries.append(
                {
                    "id": self._reference(session, "section", path, section_type),
                    "title": str(section.get("title") or "Plex Library"),
                    "type": section_type,
                    "updated_at": _integer(section.get("updatedAt")) or None,
                }
            )
        self.auth_manager.assert_server_session_current(session)
        return {"libraries": libraries, "server": session.machine_id}

    def library_items(
        self,
        library_id: str,
        *,
        start: int,
        limit: int,
        sort: str,
    ) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, library_id, expected_kind="section")
        sort_name = str(sort or "title").strip().lower()
        if sort_name not in SORTS:
            raise PlexError(
                "plex_invalid_sort",
                "Choose a supported Plex library sort",
                status_code=400,
            )
        payload = session.client.get(
            reference["path"],
            query={
                "sort": SORTS[sort_name],
                "includeGuids": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": limit,
            },
        )
        container = self._container(payload)
        items = [
            item
            for raw in self._records(container, "Metadata")
            if (item := self._item(session, raw)) is not None
        ]
        total = max(len(items), _integer(container.get("totalSize"), len(items)))
        offset = max(0, _integer(container.get("offset"), start))
        next_start = offset + len(items)
        self.auth_manager.assert_server_session_current(session)
        return {
            "library_id": library_id,
            "library_type": reference["media_type"],
            "sort": sort_name,
            "items": items,
            "count": total,
            "start": offset,
            "limit": limit,
            "next_start": next_start if next_start < total else None,
        }

    def search(self, query: str, *, limit: int) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        payload = session.client.get(
            "/hubs/search",
            query={"query": query, "limit": limit, "includeGuids": 1},
        )
        container = self._container(payload)
        items: list[dict[str, object]] = []
        seen: set[str] = set()
        for hub in self._records(container, "Hub"):
            for raw in self._records(hub, "Metadata"):
                identity = f"{raw.get('type')}:{_safe_upstream_path(raw.get('key'))}"
                if identity in seen:
                    continue
                item = self._item(session, raw)
                if item is None:
                    continue
                seen.add(identity)
                items.append(item)
                if len(items) >= limit:
                    break
            if len(items) >= limit:
                break
        self.auth_manager.assert_server_session_current(session)
        return {"query": query, "items": items, "count": len(items)}

    def item_detail(self, item_id: str) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        payload = session.client.get(
            reference["path"],
            query={"includeGuids": 1, "includeExtras": 0},
        )
        container = self._container(payload)
        records = self._records(container, "Metadata")
        item = self._item(session, records[0], detail=True) if records else None
        if item is None:
            raise PlexError(
                "plex_not_found",
                "The requested Plex item was not found",
                status_code=404,
            )
        self.auth_manager.assert_server_session_current(session)
        return {"item": item}

    def children(
        self,
        item_id: str,
        *,
        start: int,
        limit: int,
    ) -> dict[str, object]:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, item_id, expected_kind="item")
        if reference["media_type"] not in {"show", "season"}:
            raise PlexError(
                "plex_children_unavailable",
                "This Plex item does not contain browsable episodes or seasons",
                status_code=400,
            )
        base_path = str(reference["path"]).partition("?")[0].rstrip("/")
        if re.fullmatch(r"/library/metadata/[^/?]+", base_path) is None:
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is no longer valid",
                status_code=404,
            )
        payload = session.client.get(
            f"{base_path}/children",
            query={
                "includeGuids": 1,
                "X-Plex-Container-Start": start,
                "X-Plex-Container-Size": limit,
            },
        )
        container = self._container(payload)
        records = self._records(container, "Metadata") + self._records(
            container, "Directory"
        )
        items = [
            item
            for raw in records
            if (item := self._item(session, raw)) is not None
        ]
        total = max(len(items), _integer(container.get("totalSize"), len(items)))
        offset = max(0, _integer(container.get("offset"), start))
        next_start = offset + len(items)
        self.auth_manager.assert_server_session_current(session)
        return {
            "parent_id": item_id,
            "items": items,
            "count": total,
            "start": offset,
            "limit": limit,
            "next_start": next_start if next_start < total else None,
        }

    def artwork(self, asset_id: str) -> PlexBinaryResponse:
        session = self.auth_manager.selected_server_session()
        reference = self._resolve(session, asset_id, expected_kind="asset")
        result = session.client.get_bytes(
            reference["path"],
            query={"width": 1000, "height": 1500, "minSize": 1, "upscale": 0},
        )
        self.auth_manager.assert_server_session_current(session)
        return result

    @staticmethod
    def _container(payload: object) -> dict[str, Any]:
        container = payload.get("MediaContainer") if isinstance(payload, dict) else None
        if not isinstance(container, dict):
            raise PlexError(
                "plex_invalid_response",
                "Plex returned an unexpected catalog response",
                status_code=502,
            )
        return container

    @staticmethod
    def _records(container: object, field: str) -> list[dict[str, Any]]:
        records = container.get(field) if isinstance(container, dict) else None
        if not isinstance(records, list):
            return []
        return [record for record in records if isinstance(record, dict)]

    def _item(
        self,
        session: plex_auth.PlexServerSession,
        raw: dict[str, Any],
        *,
        detail: bool = False,
    ) -> dict[str, object] | None:
        media_type = str(raw.get("type") or "").strip().lower()
        path = _safe_upstream_path(raw.get("key"))
        if media_type not in VIDEO_TYPES or not path:
            return None
        duration = max(0, _integer(raw.get("duration")))
        view_offset = max(0, _integer(raw.get("viewOffset")))
        progress = round(min(100.0, (view_offset / duration) * 100.0), 1) if duration else 0.0
        subtitle_parts: list[str] = []
        if media_type == "episode":
            if raw.get("grandparentTitle"):
                subtitle_parts.append(str(raw["grandparentTitle"]))
            season = _integer(raw.get("parentIndex"), -1)
            episode = _integer(raw.get("index"), -1)
            if season >= 0 and episode >= 0:
                subtitle_parts.append(f"S{season:02d} E{episode:02d}")
        elif media_type == "season" and raw.get("parentTitle"):
            subtitle_parts.append(str(raw["parentTitle"]))
        elif raw.get("year"):
            subtitle_parts.append(str(raw["year"]))

        item: dict[str, object] = {
            "id": self._reference(session, "item", path, media_type),
            "type": media_type,
            "title": str(raw.get("title") or "Untitled"),
            "subtitle": " · ".join(subtitle_parts),
            "year": _integer(raw.get("year")) or None,
            "duration_ms": duration or None,
            "view_offset_ms": view_offset,
            "progress": progress,
            "watched": _integer(raw.get("viewCount")) > 0,
            "index": _integer(raw.get("index")) or None,
            "parent_index": _integer(raw.get("parentIndex")) or None,
            "leaf_count": _integer(raw.get("leafCount")) or None,
            "viewed_leaf_count": _integer(raw.get("viewedLeafCount")),
            "poster_url": self._asset_url(session, raw.get("thumb")),
            "backdrop_url": self._asset_url(session, raw.get("art")),
            "children_available": media_type in {"show", "season"},
        }
        if detail:
            genres = [
                str(value.get("tag") or "").strip()
                for value in (raw.get("Genre") or [])
                if isinstance(value, dict) and str(value.get("tag") or "").strip()
            ]
            item.update(
                {
                    "summary": str(raw.get("summary") or ""),
                    "tagline": str(raw.get("tagline") or ""),
                    "content_rating": str(raw.get("contentRating") or ""),
                    "audience_rating": _number(raw.get("audienceRating")),
                    "studio": str(raw.get("studio") or ""),
                    "originally_available_at": str(raw.get("originallyAvailableAt") or ""),
                    "genres": genres,
                }
            )
        return item

    def _asset_url(
        self,
        session: plex_auth.PlexServerSession,
        value: object,
    ) -> str:
        path = _safe_upstream_path(value)
        if not path:
            return ""
        asset_id = self._reference(session, "asset", path, "image")
        return f"/plex/artwork/{asset_id}"

    @staticmethod
    def _reference(
        session: plex_auth.PlexServerSession,
        kind: str,
        path: str,
        media_type: str,
    ) -> str:
        document = {
            "v": REFERENCE_VERSION,
            "a": session.account_id,
            "m": session.machine_id,
            "k": kind,
            "p": path,
            "t": media_type,
        }
        raw = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")
        nonce = os.urandom(12)
        encrypted = AESGCMSIV(session.reference_key).encrypt(
            nonce,
            raw,
            REFERENCE_AAD,
        )
        return _b64encode(nonce + encrypted)

    @staticmethod
    def _resolve(
        session: plex_auth.PlexServerSession,
        value: str,
        *,
        expected_kind: str,
    ) -> dict[str, str]:
        reference = str(value or "").strip()
        if not reference or len(reference) > MAX_REFERENCE_LENGTH:
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is invalid",
                status_code=404,
            )
        try:
            encrypted = _b64decode(reference)
            nonce, ciphertext = encrypted[:12], encrypted[12:]
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError
            raw = AESGCMSIV(session.reference_key).decrypt(
                nonce,
                ciphertext,
                REFERENCE_AAD,
            )
            document = json.loads(raw.decode("utf-8"))
        except (InvalidTag, ValueError, UnicodeDecodeError, json.JSONDecodeError):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is invalid",
                status_code=404,
            ) from None
        if not isinstance(document, dict):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is invalid",
                status_code=404,
            )
        path = _safe_upstream_path(document.get("p"))
        if (
            document.get("v") != REFERENCE_VERSION
            or str(document.get("a") or "") != session.account_id
            or str(document.get("m") or "") != session.machine_id
            or str(document.get("k") or "") != expected_kind
            or not path
        ):
            raise PlexError(
                "plex_invalid_reference",
                "The Plex item reference is no longer valid",
                status_code=404,
            )
        return {
            "path": path,
            "media_type": str(document.get("t") or ""),
        }


catalog_service = PlexCatalogService()
