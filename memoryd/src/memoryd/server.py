"""memoryd MCP server.

Exposes one tool in v1.0-α:
  search_memory(query, scope_hash=None) — substring/regex search over session
                                            markdowns in a scope; returns hits

Plan 3 will add: list_promotions, promote_to_long_term, merge_duplicates,
                 list_decisions, get_decision, etc.
Plan 4 adds: request_sensitive_read.
Total tools must stay ≤ 12 per spec § 3.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from .search import search_sessions


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "memoryd"


def _data_root() -> Path:
    return Path(os.environ.get("MEMORYD_DATA_ROOT") or DEFAULT_DATA_ROOT)


def _default_scope() -> str | None:
    return os.environ.get("MEMORYD_DEFAULT_SCOPE")


class SearchResult(BaseModel):
    """A single search hit, JSON-serializable."""

    title: str
    slug: str
    triggers: list[str]
    excerpt: str
    path: str


def build_server() -> FastMCP:
    """Build and return the FastMCP server (split for testability)."""
    mcp = FastMCP("memoryd")

    @mcp.tool()
    def search_memory(query: str, scope_hash: str | None = None) -> list[SearchResult]:
        """Search session memories in a scope for `query` (substring/regex).

        Args:
            query: The text to search for. Case-insensitive.
            scope_hash: Hash identifying the scope. If omitted, uses
                MEMORYD_DEFAULT_SCOPE env var. Must be set somewhere.

        Returns:
            Up to 20 hits, each with title, slug, triggers, excerpt, and file path.
        """
        sh = scope_hash if scope_hash is not None else _default_scope()
        if not sh:
            raise ValueError(
                "scope_hash required (pass argument or set MEMORYD_DEFAULT_SCOPE)"
            )
        hits = search_sessions(_data_root(), scope_hash=sh, query=query)
        return [
            SearchResult(
                title=h.title,
                slug=h.slug,
                triggers=list(h.triggers),  # SearchHit.triggers is tuple[str, ...]; coerce to list
                excerpt=h.excerpt,
                path=str(h.path),
            )
            for h in hits
        ]

    @mcp.tool()
    def record_long_term(
        type: str,
        title: str,
        body: str,
        triggers: list[str],
        scope_hash: str | None = None,
    ) -> dict:
        """Write a new long-term memory directly (no DURA promotion).

        type must be one of: decision / preference / fact / playbook / warning.
        Use this when the user explicitly says 'remember this as a decision' etc.
        """
        import re as _re
        from datetime import datetime, timezone
        from .schema import Frontmatter, SessionMemory
        from .storage import save_memory

        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError("scope_hash required")
        if type not in {"decision", "preference", "fact", "playbook", "warning"}:
            raise ValueError(f"type must be a long-term type, got {type!r}")
        now = datetime.now(timezone.utc)
        safe_title = _re.sub(r"[^A-Za-z0-9_-]", "_", title)[:40]
        slug = f"{now:%Y-%m-%d}-{safe_title}-{int(now.timestamp())}"
        mem = SessionMemory(
            frontmatter=Frontmatter(
                title=title,
                slug=slug,
                type=type,
                scope_hash=sh,
                triggers=triggers,
                source="manual",
                created_at=now,
                ttl_days=None,
            ),
            body=body,
        )
        save_memory(_data_root(), mem)
        return {"slug": slug, "type": type, "title": title}

    @mcp.tool()
    def list_by_type(type: str, scope_hash: str | None = None, limit: int = 20) -> list[dict]:
        """List up to `limit` memories of a given type in a scope."""
        from .index import open_index
        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError("scope_hash required")
        idx = open_index(_data_root() / "index.db")
        try:
            return idx.list_by_type(type, scope_hash=sh, limit=limit)
        finally:
            idx.close()

    @mcp.tool()
    def get_memory(slug: str) -> dict | None:
        """Return one memory's full row (metadata + body_path)."""
        from .index import open_index
        idx = open_index(_data_root() / "index.db")
        try:
            row = idx.get_memory(slug)
            if row is None:
                return None
            # also include body
            body_path = _data_root() / row["body_path"]
            try:
                row["body"] = body_path.read_text(encoding="utf-8")
            except OSError:
                row["body"] = ""
            return row
        finally:
            idx.close()

    @mcp.tool()
    def list_promotions(scope_hash: str | None = None, status: str = "pending") -> list[dict]:
        """List promotion candidates produced by analyze-session."""
        from .index import open_index
        idx = open_index(_data_root() / "index.db")
        try:
            sql = "SELECT * FROM promotions WHERE status = ?"
            args: list = [status]
            if scope_hash is not None:
                sql += " AND scope_hash = ?"
                args.append(scope_hash)
            sql += " ORDER BY created_at DESC LIMIT 50"
            return [dict(r) for r in idx.conn.execute(sql, args).fetchall()]
        finally:
            idx.close()

    @mcp.tool()
    def promote_to_long_term(
        session_slug: str,
        type: str,
        title: str,
        body: str | None = None,
        triggers: list[str] | None = None,
        reason: str | None = None,
    ) -> dict:
        """Promote a slice of a captured session into a typed long-term memory.

        If body / triggers omitted, the session body and triggers are reused.
        """
        from datetime import datetime, timezone
        import re as _re
        from .index import open_index
        from .schema import Frontmatter, SessionMemory
        from .storage import load_session, save_memory

        if type not in {"decision", "preference", "fact", "playbook", "warning"}:
            raise ValueError(f"type must be a long-term type, got {type!r}")
        idx = open_index(_data_root() / "index.db")
        try:
            row = idx.get_memory(session_slug)
            if row is None:
                raise ValueError(f"session_slug not found: {session_slug}")
            sess_path = _data_root() / row["body_path"]
            sess = load_session(sess_path)
        finally:
            idx.close()
        now = datetime.now(timezone.utc)
        safe_title = _re.sub(r"[^A-Za-z0-9_-]", "_", title)[:40]
        slug = f"{now:%Y-%m-%d}-{safe_title}-{int(now.timestamp())}"
        new_body = body if body is not None else sess.body[:5000]
        new_triggers = triggers if triggers is not None else sess.frontmatter.triggers
        mem = SessionMemory(
            frontmatter=Frontmatter(
                title=title,
                slug=slug,
                type=type,
                scope_hash=row["scope_hash"],
                triggers=new_triggers,
                source="manual",
                created_at=now,
                ttl_days=None,
                promoted_from=session_slug,
            ),
            body=new_body,
        )
        save_memory(_data_root(), mem)
        return {"slug": slug, "promoted_from": session_slug, "reason": reason or ""}

    @mcp.tool()
    def merge_duplicates(keep_slug: str, drop_slugs: list[str]) -> dict:
        """Merge `drop_slugs` into `keep_slug` (bodies appended, triggers unioned)."""
        from .governance.merge import merge_memories
        merge_memories(_data_root(), keep_slug=keep_slug, drop_slugs=drop_slugs)
        return {"kept": keep_slug, "dropped": drop_slugs}

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
