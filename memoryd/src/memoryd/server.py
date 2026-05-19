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
        from .governance.gate import check_or_raise
        sh = scope_hash if scope_hash is not None else _default_scope()
        if not sh:
            raise ValueError(
                "scope_hash required (pass argument or set MEMORYD_DEFAULT_SCOPE)"
            )
        check_or_raise(sh, "search_memory", memory_root=_data_root())
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
        from .governance.gate import check_or_raise

        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError("scope_hash required")
        check_or_raise(sh, "record_long_term", memory_root=_data_root())
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
        from .governance.gate import check_or_raise
        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError("scope_hash required")
        check_or_raise(sh, "list_by_type", memory_root=_data_root())
        idx = open_index(_data_root() / "index.db")
        try:
            return idx.list_by_type(type, scope_hash=sh, limit=limit)
        finally:
            idx.close()

    @mcp.tool()
    def get_memory(slug: str) -> dict | None:
        """Return one memory's full row (metadata + body_path)."""
        from .index import open_index
        from .governance.gate import check_or_raise
        idx = open_index(_data_root() / "index.db")
        try:
            row = idx.get_memory(slug)
            if row is None:
                return None
            # Gate check: look up scope_hash for this slug, then check_or_raise
            sh = row["scope_hash"]
            check_or_raise(sh, "get_memory", memory_root=_data_root())
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
        """List promotion candidates produced by analyze-session.

        When scope_hash is omitted all scopes are queried but rows belonging
        to sensitive scopes without a valid grant are silently filtered out.
        """
        from .index import open_index
        from .governance.gate import check_or_raise, AuthorizationRequired
        idx = open_index(_data_root() / "index.db")
        try:
            sql = "SELECT * FROM promotions WHERE status = ?"
            args: list = [status]
            if scope_hash is not None:
                # Explicit scope: gate check raises on deny
                check_or_raise(scope_hash, "list_promotions", memory_root=_data_root())
                sql += " AND scope_hash = ?"
                args.append(scope_hash)
            sql += " ORDER BY created_at DESC LIMIT 50"
            rows = [dict(r) for r in idx.conn.execute(sql, args).fetchall()]
        finally:
            idx.close()
        if scope_hash is not None:
            return rows
        # No explicit scope: filter out rows from sensitive scopes without grant
        allowed: list[dict] = []
        for row in rows:
            sh = row.get("scope_hash", "")
            try:
                check_or_raise(sh, "list_promotions", memory_root=_data_root())
                allowed.append(row)
            except AuthorizationRequired:
                pass  # silently skip sensitive rows without grant
        return allowed

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
        from .governance.gate import check_or_raise

        if type not in {"decision", "preference", "fact", "playbook", "warning"}:
            raise ValueError(f"type must be a long-term type, got {type!r}")
        idx = open_index(_data_root() / "index.db")
        try:
            row = idx.get_memory(session_slug)
            if row is None:
                raise ValueError(f"session_slug not found: {session_slug}")
            sh = row["scope_hash"]
            check_or_raise(sh, "promote_to_long_term", memory_root=_data_root())
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
                scope_hash=sh,
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
        """Merge `drop_slugs` into `keep_slug` (bodies appended, triggers unioned).

        If keep_slug belongs to a sensitive scope, a valid grant is required and
        all drop_slugs must belong to the same scope.
        """
        from .index import open_index
        from .governance.gate import check_or_raise, AuthorizationRequired
        from .governance.merge import merge_memories

        idx = open_index(_data_root() / "index.db")
        try:
            keep_row = idx.get_memory(keep_slug)
            if keep_row is None:
                raise ValueError(f"keep_slug not found: {keep_slug}")
            sh = keep_row["scope_hash"]
            check_or_raise(sh, "merge_duplicates", memory_root=_data_root())
            # If keep scope is sensitive, verify all drop_slugs are in the same scope
            if idx.is_scope_sensitive(sh):
                for drop_slug in drop_slugs:
                    drop_row = idx.get_memory(drop_slug)
                    if drop_row is not None and drop_row["scope_hash"] != sh:
                        raise ValueError(
                            f"merge_duplicates: drop_slug {drop_slug!r} is in a different "
                            f"scope than keep_slug {keep_slug!r} — cross-scope merges are "
                            "not permitted when the keep scope is sensitive"
                        )
        finally:
            idx.close()
        merge_memories(_data_root(), keep_slug=keep_slug, drop_slugs=drop_slugs)
        return {"kept": keep_slug, "dropped": drop_slugs}

    @mcp.tool()
    def request_sensitive_read(
        scope_path: str,
        query: str,
        duration: str = "once",
    ) -> dict:
        """Tell the user the agent wants to read a sensitive scope.

        The user must grant via `memoryd grant` in another terminal (or accept
        an interactive prompt if MEMORYD_AUTH_INTERACTIVE=1). Returns
        {granted: bool, scope_hash: str, ...}.

        Args:
            scope_path: Path to any file/dir inside the target scope.
            query: Human-readable description of what the agent wants to read.
            duration: Requested grant duration — "once" (90s), "session" (8h),
                      or "task" (permanent until revoke).

        Returns:
            {"granted": True, "scope_hash": ..., "scope_root": ...} on success, or
            {"granted": False, "scope_hash": ..., "reason": ...} when no grant.
        """
        from pathlib import Path as _Path
        from .scope import resolve_scope_root, scope_hash as _scope_hash
        from .governance.gate import AuthorizationRequired, check_or_raise

        scope_root = resolve_scope_root(_Path(scope_path))
        sh = _scope_hash(scope_root)
        try:
            check_or_raise(sh, "request_sensitive_read", memory_root=_data_root())
            return {"granted": True, "scope_hash": sh, "scope_root": str(scope_root)}
        except AuthorizationRequired as e:
            return {"granted": False, "scope_hash": sh, "reason": str(e)}

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
