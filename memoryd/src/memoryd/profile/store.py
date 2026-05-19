"""DAO for ``profile_versions`` / ``profile_change_reports`` tables.

Pure SQLite — no LLM, no filesystem (identity.md disk writes live in
:mod:`memoryd.profile.identity`). Keeps versioning logic out of the
LLM module so it is easy to unit test.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


@dataclass
class ProfileVersion:
    """In-memory representation of one row in ``profile_versions``."""

    id: int
    version_num: int
    written_at: datetime
    trigger: str
    content_md: str
    diff_from_prev: str | None
    change_summary: str | None
    sources_count: int
    sources_window_start: datetime | None = None
    sources_window_end: datetime | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row | dict[str, Any]) -> "ProfileVersion":
        d = dict(row)
        return cls(
            id=d["id"],
            version_num=d["version_num"],
            written_at=_parse_iso(d["written_at"]) or datetime.now(timezone.utc),
            trigger=d["trigger"],
            content_md=d["content_md"],
            diff_from_prev=d.get("diff_from_prev"),
            change_summary=d.get("change_summary"),
            sources_count=d.get("sources_count") or 0,
            sources_window_start=_parse_iso(d.get("sources_window_start")),
            sources_window_end=_parse_iso(d.get("sources_window_end")),
        )


class ProfileStore:
    """Wrap a sqlite3 connection with typed read/write helpers.

    The connection must already have the profile tables; either obtain it
    from :func:`memoryd.index.open_index` (which auto-runs migration 004)
    or call :func:`memoryd.profile.migrations.ensure_profile_schema` on
    the connection yourself.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        # Be defensive: callers may pass a connection that hasn't set
        # row_factory, e.g. in tests that build a bare sqlite3.connect.
        if conn.row_factory is None:
            conn.row_factory = sqlite3.Row

    # ------------------------------------------------------------------
    # profile_versions
    # ------------------------------------------------------------------

    def latest_version(self) -> ProfileVersion | None:
        row = self.conn.execute(
            "SELECT * FROM profile_versions ORDER BY version_num DESC LIMIT 1"
        ).fetchone()
        return ProfileVersion.from_row(row) if row else None

    def list_versions(
        self,
        since: datetime | None = None,
        *,
        until: datetime | None = None,
        limit: int | None = None,
    ) -> list[ProfileVersion]:
        sql = "SELECT * FROM profile_versions"
        clauses: list[str] = []
        args: list[Any] = []
        if since is not None:
            clauses.append("written_at >= ?")
            args.append(since.isoformat())
        if until is not None:
            clauses.append("written_at < ?")
            args.append(until.isoformat())
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY version_num ASC"
        if limit:
            sql += " LIMIT ?"
            args.append(limit)
        return [
            ProfileVersion.from_row(r) for r in self.conn.execute(sql, args).fetchall()
        ]

    def save_version(
        self,
        content_md: str,
        *,
        trigger: str,
        prev_version: ProfileVersion | None = None,
        diff_from_prev: str | None = None,
        change_summary: str | None = None,
        sources_count: int = 0,
        sources_window_start: datetime | None = None,
        sources_window_end: datetime | None = None,
        written_at: datetime | None = None,
    ) -> ProfileVersion:
        """Insert a new ``profile_versions`` row.

        ``version_num`` is allocated as ``max(version_num)+1`` so it stays
        monotonic even if rows are deleted. ``prev_version`` is accepted
        for API symmetry — callers that compute their own diff pass it in
        via ``diff_from_prev``; we don't recompute here so the store stays
        LLM-free.
        """
        next_num = (
            self.conn.execute(
                "SELECT COALESCE(MAX(version_num), 0) FROM profile_versions"
            ).fetchone()[0]
            + 1
        )
        ts = (written_at or datetime.now(timezone.utc)).isoformat()
        cur = self.conn.execute(
            """
            INSERT INTO profile_versions
                (version_num, written_at, trigger, content_md, diff_from_prev,
                 change_summary, sources_count, sources_window_start,
                 sources_window_end)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                next_num,
                ts,
                trigger,
                content_md,
                diff_from_prev,
                change_summary,
                sources_count,
                sources_window_start.isoformat() if sources_window_start else None,
                sources_window_end.isoformat() if sources_window_end else None,
            ),
        )
        self.conn.commit()
        row_id = cur.lastrowid
        row = self.conn.execute(
            "SELECT * FROM profile_versions WHERE id = ?", (row_id,)
        ).fetchone()
        return ProfileVersion.from_row(row)

    # ------------------------------------------------------------------
    # profile_change_reports
    # ------------------------------------------------------------------

    def save_change_report(
        self,
        period: str,
        content_md: str,
        *,
        versions_count: int = 0,
        supersedes_count: int = 0,
        entities_added: int = 0,
        entities_dropped: int = 0,
        generated_at: datetime | None = None,
    ) -> None:
        """Upsert a monthly change report row keyed by ``period`` (``YYYY-MM``)."""
        ts = (generated_at or datetime.now(timezone.utc)).isoformat()
        self.conn.execute(
            """
            INSERT INTO profile_change_reports
                (period, generated_at, content_md, versions_count,
                 supersedes_count, entities_added, entities_dropped)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(period) DO UPDATE SET
                generated_at      = excluded.generated_at,
                content_md        = excluded.content_md,
                versions_count    = excluded.versions_count,
                supersedes_count  = excluded.supersedes_count,
                entities_added    = excluded.entities_added,
                entities_dropped  = excluded.entities_dropped
            """,
            (
                period,
                ts,
                content_md,
                versions_count,
                supersedes_count,
                entities_added,
                entities_dropped,
            ),
        )
        self.conn.commit()

    def get_change_report(self, period: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM profile_change_reports WHERE period = ?", (period,)
        ).fetchone()
        return dict(row) if row else None

    def list_change_reports(self) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM profile_change_reports ORDER BY period DESC"
        ).fetchall()
        return [dict(r) for r in rows]
