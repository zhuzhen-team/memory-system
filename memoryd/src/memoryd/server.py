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
        sh = scope_hash or _default_scope()
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

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
