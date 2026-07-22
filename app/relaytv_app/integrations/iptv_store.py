# SPDX-License-Identifier: GPL-3.0-only
"""SQLite persistence for the IPTV catalog.

This store owns versioned catalog state. It intentionally stays separate from
``state.py`` because playlist refreshes can touch tens of thousands of rows and
must not rewrite or lock playback queue/session JSON.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
RANK_STEP = 1024


class IptvStore:
    def __init__(self, path: str) -> None:
        self.path = str(path)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 15000")
        return conn

    def _initialize(self) -> None:
        with self._lock:
            parent = Path(self.path).parent
            parent.mkdir(parents=True, exist_ok=True)
            conn = self._connect()
            try:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS iptv_schema (
                        version INTEGER NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS iptv_sources (
                        id TEXT PRIMARY KEY,
                        name TEXT NOT NULL,
                        kind TEXT NOT NULL DEFAULT 'url',
                        location TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        preset_id TEXT NOT NULL DEFAULT '',
                        enabled INTEGER NOT NULL DEFAULT 1,
                        refresh_interval_sec INTEGER NOT NULL DEFAULT 21600,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        last_attempt_at REAL,
                        last_success_at REAL,
                        etag TEXT NOT NULL DEFAULT '',
                        last_modified TEXT NOT NULL DEFAULT '',
                        last_error TEXT NOT NULL DEFAULT '',
                        channel_count INTEGER NOT NULL DEFAULT 0
                    );

                    CREATE TABLE IF NOT EXISTS iptv_channels (
                        source_id TEXT NOT NULL,
                        channel_id TEXT NOT NULL,
                        identity_key TEXT NOT NULL,
                        tvg_id TEXT NOT NULL DEFAULT '',
                        tvg_name TEXT NOT NULL DEFAULT '',
                        name TEXT NOT NULL,
                        group_title TEXT NOT NULL DEFAULT '',
                        logo_url TEXT NOT NULL DEFAULT '',
                        stream_url TEXT NOT NULL,
                        user_agent TEXT NOT NULL DEFAULT '',
                        referrer TEXT NOT NULL DEFAULT '',
                        upstream_index INTEGER NOT NULL,
                        manual_rank INTEGER NOT NULL,
                        hidden INTEGER NOT NULL DEFAULT 0,
                        favorite INTEGER NOT NULL DEFAULT 0,
                        added INTEGER NOT NULL DEFAULT 0,
                        active INTEGER NOT NULL DEFAULT 1,
                        availability TEXT NOT NULL DEFAULT 'unknown',
                        consecutive_failures INTEGER NOT NULL DEFAULT 0,
                        last_checked_at REAL,
                        last_available_at REAL,
                        first_seen_at REAL NOT NULL,
                        last_seen_at REAL NOT NULL,
                        PRIMARY KEY (source_id, channel_id),
                        FOREIGN KEY (source_id) REFERENCES iptv_sources(id) ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_iptv_channels_source_rank
                        ON iptv_channels(source_id, manual_rank);
                    CREATE INDEX IF NOT EXISTS idx_iptv_channels_source_upstream
                        ON iptv_channels(source_id, upstream_index);
                    CREATE INDEX IF NOT EXISTS idx_iptv_channels_source_name
                        ON iptv_channels(source_id, name COLLATE NOCASE);
                    CREATE INDEX IF NOT EXISTS idx_iptv_channels_source_group
                        ON iptv_channels(source_id, group_title COLLATE NOCASE);
                    CREATE INDEX IF NOT EXISTS idx_iptv_channels_visibility
                        ON iptv_channels(source_id, active, hidden, favorite, availability);
                    """
                )
                # Backward-compatible column add: "added" (My Channels membership)
                # arrived after the initial schema; ALTER any pre-existing table.
                channel_cols = {r["name"] for r in conn.execute("PRAGMA table_info(iptv_channels)").fetchall()}
                if "added" not in channel_cols:
                    conn.execute("ALTER TABLE iptv_channels ADD COLUMN added INTEGER NOT NULL DEFAULT 0")
                row = conn.execute("SELECT version FROM iptv_schema LIMIT 1").fetchone()
                if row is None:
                    conn.execute("INSERT INTO iptv_schema(version) VALUES (?)", (SCHEMA_VERSION,))
                elif int(row["version"]) != SCHEMA_VERSION:
                    raise RuntimeError(f"unsupported IPTV schema version {row['version']}")
                conn.commit()
            finally:
                conn.close()
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    @staticmethod
    def _source_public(row: sqlite3.Row | dict) -> dict[str, object]:
        data = dict(row)
        location = str(data.pop("location", "") or "")
        data.pop("content", None)
        host = ""
        if location:
            try:
                host = str(urlsplit(location).hostname or "")
            except Exception:
                host = ""
        data["location_configured"] = bool(location or data.get("kind") == "upload")
        data["location_host"] = host
        data["enabled"] = bool(data.get("enabled"))
        return data

    @staticmethod
    def _channel_public(row: sqlite3.Row | dict) -> dict[str, object]:
        data = dict(row)
        data.pop("stream_url", None)
        data.pop("user_agent", None)
        data.pop("referrer", None)
        data.pop("identity_key", None)
        for key in ("hidden", "favorite", "added", "active"):
            data[key] = bool(data.get(key))
        return data

    def create_source(
        self,
        *,
        source_id: str,
        name: str,
        kind: str,
        location: str = "",
        content: str = "",
        preset_id: str = "",
        refresh_interval_sec: int = 21600,
    ) -> dict[str, object]:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO iptv_sources(
                    id, name, kind, location, content, preset_id, enabled,
                    refresh_interval_sec, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                """,
                (
                    source_id,
                    name,
                    kind,
                    location,
                    content,
                    preset_id,
                    refresh_interval_sec,
                    now,
                    now,
                ),
            )
            row = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (source_id,)).fetchone()
        assert row is not None
        return self._source_public(row)

    def list_sources(self) -> list[dict[str, object]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM iptv_sources ORDER BY name COLLATE NOCASE, created_at"
            ).fetchall()
        return [self._source_public(row) for row in rows]

    def get_source(self, source_id: str, *, redacted: bool = False) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (source_id,)).fetchone()
        if row is None:
            return None
        return self._source_public(row) if redacted else dict(row)

    def update_source(self, source_id: str, patch: dict[str, object]) -> dict[str, object] | None:
        allowed = {
            "name",
            "kind",
            "enabled",
            "location",
            "content",
            "etag",
            "last_modified",
            "refresh_interval_sec",
        }
        fields = {key: value for key, value in patch.items() if key in allowed}
        if not fields:
            return self.get_source(source_id, redacted=True)
        fields["updated_at"] = time.time()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        values = list(fields.values()) + [source_id]
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE iptv_sources SET {assignments} WHERE id = ?",  # noqa: S608
                values,
            )
            if cur.rowcount <= 0:
                return None
            row = conn.execute("SELECT * FROM iptv_sources WHERE id = ?", (source_id,)).fetchone()
        assert row is not None
        return self._source_public(row)

    def delete_source(self, source_id: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM iptv_sources WHERE id = ?", (source_id,))
            return cur.rowcount > 0

    def mark_refresh_attempt(self, source_id: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE iptv_sources SET last_attempt_at = ?, last_error = '' WHERE id = ?",
                (time.time(), source_id),
            )

    def mark_refresh_error(self, source_id: str, error: str) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE iptv_sources SET last_error = ?, updated_at = ? WHERE id = ?",
                (str(error or "")[:1000], time.time(), source_id),
            )

    def replace_catalog(
        self,
        source_id: str,
        channels: list[dict[str, object]],
        *,
        etag: str = "",
        last_modified: str = "",
    ) -> dict[str, int]:
        now = time.time()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            before = conn.execute(
                "SELECT COUNT(*) AS n FROM iptv_channels WHERE source_id = ? AND active = 1",
                (source_id,),
            ).fetchone()
            before_count = int(before["n"] if before else 0)
            max_row = conn.execute(
                "SELECT COALESCE(MAX(manual_rank), 0) AS n FROM iptv_channels WHERE source_id = ?",
                (source_id,),
            ).fetchone()
            next_rank = int(max_row["n"] if max_row else 0)
            conn.execute("UPDATE iptv_channels SET active = 0 WHERE source_id = ?", (source_id,))
            inserted = 0
            for entry in channels:
                existing = conn.execute(
                    "SELECT manual_rank FROM iptv_channels WHERE source_id = ? AND channel_id = ?",
                    (source_id, entry["channel_id"]),
                ).fetchone()
                if existing is None:
                    next_rank += RANK_STEP
                    rank = next_rank
                    inserted += 1
                else:
                    rank = int(existing["manual_rank"])
                conn.execute(
                    """
                    INSERT INTO iptv_channels(
                        source_id, channel_id, identity_key, tvg_id, tvg_name,
                        name, group_title, logo_url, stream_url, user_agent,
                        referrer, upstream_index, manual_rank, active,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(source_id, channel_id) DO UPDATE SET
                        identity_key = excluded.identity_key,
                        tvg_id = excluded.tvg_id,
                        tvg_name = excluded.tvg_name,
                        name = excluded.name,
                        group_title = excluded.group_title,
                        logo_url = excluded.logo_url,
                        stream_url = excluded.stream_url,
                        user_agent = excluded.user_agent,
                        referrer = excluded.referrer,
                        upstream_index = excluded.upstream_index,
                        active = 1,
                        last_seen_at = excluded.last_seen_at
                    """,
                    (
                        source_id,
                        entry["channel_id"],
                        entry["identity_key"],
                        entry.get("tvg_id", ""),
                        entry.get("tvg_name", ""),
                        entry["name"],
                        entry.get("group_title", ""),
                        entry.get("logo_url", ""),
                        entry["stream_url"],
                        entry.get("user_agent", ""),
                        entry.get("referrer", ""),
                        int(entry.get("upstream_index", 0)),
                        rank,
                        now,
                        now,
                    ),
                )
            count = len(channels)
            conn.execute(
                """
                UPDATE iptv_sources
                SET last_success_at = ?, last_error = '', etag = ?,
                    last_modified = ?, channel_count = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, etag, last_modified, count, now, source_id),
            )
            conn.commit()
        return {
            "active": count,
            "inserted": inserted,
            "inactive": max(0, before_count - (count - inserted)),
        }

    def query_channels(
        self,
        *,
        source_id: str = "",
        query: str = "",
        group: str = "",
        visibility: str = "visible",
        include_unavailable: bool = False,
        favorites_only: bool = False,
        added_only: bool = False,
        availability: str = "",
        sort: str = "manual",
        offset: int = 0,
        limit: int = 100,
    ) -> dict[str, object]:
        clauses: list[str] = []
        args: list[object] = []
        if source_id:
            clauses.append("c.source_id = ?")
            args.append(source_id)
        if query:
            clauses.append("(c.name LIKE ? ESCAPE '\\' OR c.group_title LIKE ? ESCAPE '\\')")
            escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.extend([f"%{escaped}%", f"%{escaped}%"])
        if group:
            clauses.append("c.group_title = ?")
            args.append(group)
        if visibility == "hidden":
            clauses.append("c.hidden = 1")
        elif visibility == "all":
            pass
        else:
            clauses.extend(["c.hidden = 0", "c.active = 1"])
            if not include_unavailable:
                clauses.append("c.availability != 'unavailable'")
        if favorites_only:
            clauses.append("c.favorite = 1")
        if added_only:
            clauses.append("c.added = 1")
        if availability:
            clauses.append("c.availability = ?")
            args.append(availability)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        order_by = {
            "name": "c.name COLLATE NOCASE, c.channel_id",
            "group": "c.group_title COLLATE NOCASE, c.name COLLATE NOCASE, c.channel_id",
            "playlist": "c.source_id, c.upstream_index, c.channel_id",
            "manual": "c.source_id, c.manual_rank, c.channel_id",
        }.get(sort, "c.source_id, c.manual_rank, c.channel_id")
        with self._lock, self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS n FROM iptv_channels c{where}",  # noqa: S608
                args,
            ).fetchone()
            rows = conn.execute(
                f"""
                SELECT c.*, s.name AS source_name
                FROM iptv_channels c
                JOIN iptv_sources s ON s.id = c.source_id
                {where}
                ORDER BY {order_by}
                LIMIT ? OFFSET ?
                """,  # noqa: S608
                [*args, limit, offset],
            ).fetchall()
            groups = conn.execute(
                """
                SELECT DISTINCT group_title FROM iptv_channels
                WHERE (? = '' OR source_id = ?) AND active = 1 AND group_title != ''
                ORDER BY group_title COLLATE NOCASE
                """,
                (source_id, source_id),
            ).fetchall()
        total = int(total_row["n"] if total_row else 0)
        return {
            "items": [self._channel_public(row) for row in rows],
            "count": len(rows),
            "total": total,
            "offset": offset,
            "limit": limit,
            "has_more": (offset + len(rows)) < total,
            "groups": [str(row["group_title"]) for row in groups],
        }

    def get_channel(
        self, source_id: str, channel_id: str, *, redacted: bool = False
    ) -> dict[str, object] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM iptv_channels WHERE source_id = ? AND channel_id = ?",
                (source_id, channel_id),
            ).fetchone()
        if row is None:
            return None
        return self._channel_public(row) if redacted else dict(row)

    def update_channel(
        self, source_id: str, channel_id: str, patch: dict[str, object]
    ) -> dict[str, object] | None:
        fields: dict[str, object] = {}
        if "hidden" in patch:
            fields["hidden"] = 1 if bool(patch["hidden"]) else 0
        if "favorite" in patch:
            fields["favorite"] = 1 if bool(patch["favorite"]) else 0
        if "added" in patch:
            fields["added"] = 1 if bool(patch["added"]) else 0
        if not fields:
            return self.get_channel(source_id, channel_id, redacted=True)
        assignments = ", ".join(f"{key} = ?" for key in fields)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"UPDATE iptv_channels SET {assignments} WHERE source_id = ? AND channel_id = ?",  # noqa: S608
                [*fields.values(), source_id, channel_id],
            )
            if cur.rowcount <= 0:
                return None
            row = conn.execute(
                "SELECT * FROM iptv_channels WHERE source_id = ? AND channel_id = ?",
                (source_id, channel_id),
            ).fetchone()
        assert row is not None
        return self._channel_public(row)

    def set_group_hidden(self, source_id: str, group: str, hidden: bool) -> int:
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE iptv_channels SET hidden = ? WHERE source_id = ? AND group_title = ?",
                (1 if hidden else 0, source_id, group),
            )
            return int(cur.rowcount)

    def channels_due_for_check(self, *, before: float, limit: int) -> list[dict[str, object]]:
        """Return internal favorite channels due for a bounded availability check."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT c.* FROM iptv_channels c
                JOIN iptv_sources s ON s.id = c.source_id
                WHERE c.active = 1 AND c.favorite = 1 AND s.enabled = 1
                  AND (c.last_checked_at IS NULL OR c.last_checked_at < ?)
                ORDER BY
                  CASE c.availability WHEN 'suspect' THEN 0 WHEN 'unavailable' THEN 1 ELSE 2 END,
                  COALESCE(c.last_checked_at, 0), c.source_id, c.manual_rank
                LIMIT ?
                """,
                (before, max(1, int(limit))),
            ).fetchall()
        return [dict(row) for row in rows]

    def channel_identities(self, source_id: str) -> list[dict[str, object]]:
        """Existing identity rows for a source, for identity reconciliation."""
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT identity_key, tvg_name, name, group_title "
                "FROM iptv_channels WHERE source_id = ?",
                (source_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def remove_unavailable(self, *, source_id: str = "") -> int:
        """Physically remove explicitly selected inactive/unavailable entries."""
        clauses = ["(active = 0 OR availability = 'unavailable')"]
        args: list[object] = []
        if source_id:
            clauses.append("source_id = ?")
            args.append(source_id)
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                f"DELETE FROM iptv_channels WHERE {' AND '.join(clauses)}",  # noqa: S608
                args,
            )
            removed = int(cur.rowcount)
            if removed:
                # Keep iptv_sources.channel_count (active total) consistent so
                # status and the Sources UI do not report a stale total.
                if source_id:
                    conn.execute(
                        "UPDATE iptv_sources SET channel_count = "
                        "(SELECT COUNT(*) FROM iptv_channels WHERE source_id = ? AND active = 1) "
                        "WHERE id = ?",
                        (source_id, source_id),
                    )
                else:
                    conn.execute(
                        "UPDATE iptv_sources SET channel_count = "
                        "(SELECT COUNT(*) FROM iptv_channels WHERE source_id = iptv_sources.id AND active = 1)"
                    )
            return removed

    def reorder_channel(
        self,
        source_id: str,
        channel_id: str,
        *,
        before_channel_id: str = "",
        after_channel_id: str = "",
    ) -> bool:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT channel_id FROM iptv_channels
                WHERE source_id = ? ORDER BY manual_rank, channel_id
                """,
                (source_id,),
            ).fetchall()
            ordered = [str(row["channel_id"]) for row in rows]
            if channel_id not in ordered:
                return False
            ordered.remove(channel_id)
            if before_channel_id:
                if before_channel_id not in ordered:
                    return False
                index = ordered.index(before_channel_id)
            elif after_channel_id:
                if after_channel_id not in ordered:
                    return False
                index = ordered.index(after_channel_id) + 1
            else:
                index = len(ordered)
            ordered.insert(index, channel_id)
            conn.execute("BEGIN IMMEDIATE")
            for idx, item_id in enumerate(ordered, 1):
                conn.execute(
                    """
                    UPDATE iptv_channels SET manual_rank = ?
                    WHERE source_id = ? AND channel_id = ?
                    """,
                    (idx * RANK_STEP, source_id, item_id),
                )
            conn.commit()
        return True

    def mark_channel_check(
        self, source_id: str, channel_id: str, *, available: bool
    ) -> dict[str, object] | None:
        now = time.time()
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT consecutive_failures FROM iptv_channels
                WHERE source_id = ? AND channel_id = ?
                """,
                (source_id, channel_id),
            ).fetchone()
            if row is None:
                return None
            if available:
                failures = 0
                state = "available"
                last_available = now
            else:
                failures = int(row["consecutive_failures"] or 0) + 1
                state = "unavailable" if failures >= 3 else "suspect"
                last_available = None
            conn.execute(
                """
                UPDATE iptv_channels
                SET availability = ?, consecutive_failures = ?,
                    last_checked_at = ?,
                    last_available_at = COALESCE(?, last_available_at)
                WHERE source_id = ? AND channel_id = ?
                """,
                (state, failures, now, last_available, source_id, channel_id),
            )
            updated = conn.execute(
                "SELECT * FROM iptv_channels WHERE source_id = ? AND channel_id = ?",
                (source_id, channel_id),
            ).fetchone()
        assert updated is not None
        return self._channel_public(updated)
