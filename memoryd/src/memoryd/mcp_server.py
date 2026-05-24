"""memoryd MCP server — exposes 19 ``mem_*`` tools over stdio / http.

This is the **isolated** MCP entry point. It does not share code with
``server.py`` (the legacy ``search_memory`` server) so that adding tools
here does not risk regressing the existing surface, and so admin gating
can be enforced cleanly via ``MEMORYD_MCP_ADMIN``.

Tool tier:

- **agent** (11 tools, exposed by default to Claude Code / Codex / OpenClaw):
  ``mem_save``, ``mem_update``, ``mem_delete``, ``mem_get``, ``mem_search``,
  ``mem_context``, ``mem_timeline``, ``mem_session_start``, ``mem_session_end``,
  ``mem_session_summary``, ``mem_capture_passive``.

  *Note*: ``mem_judge`` and ``mem_compare`` are agent-callable but counted
  separately as "judge" tools below — 11+2 = 13 agent-visible tools total.

- **admin** (6 tools, hidden unless ``MEMORYD_MCP_ADMIN=1``):
  ``mem_stats``, ``mem_merge_projects``, ``mem_current_project``,
  ``mem_doctor``, ``mem_save_prompt``, ``mem_suggest_topic_key``.

CLI usage::

    memoryd-mcp                       # stdio transport (default)
    memoryd-mcp --transport http --port 8766
    MEMORYD_MCP_ADMIN=1 memoryd-mcp   # enable admin tools

The handlers themselves live in :mod:`memoryd.mcp_tools` so this file can
stay focused on schema declaration + transport boilerplate.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from typing import Any

from fastmcp import FastMCP

from .mcp_tools import admin as admin_tools
from .mcp_tools import judge as judge_tools
from .mcp_tools import memory as memory_tools
from .mcp_tools import promotions as promotions_tools
from .mcp_tools import session as session_tools


log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Admin gating
# ---------------------------------------------------------------------------

# Names exposed only when MEMORYD_MCP_ADMIN=1.
ADMIN_TOOL_NAMES = (
    "mem_stats",
    "mem_merge_projects",
    "mem_current_project",
    "mem_doctor",
    "mem_save_prompt",
    "mem_suggest_topic_key",
)


def is_admin_enabled() -> bool:
    """Whether to register admin-tier tools.

    Read at call-time (not import-time) so tests can monkeypatch the env
    var between server builds.
    """
    return os.environ.get("MEMORYD_MCP_ADMIN", "0") == "1"


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def build_server(*, include_admin: bool | None = None) -> FastMCP:
    """Build the FastMCP server with all 19 tools registered.

    ``include_admin`` defaults to ``is_admin_enabled()``. Pass ``True`` /
    ``False`` from tests to force a specific layout regardless of env.
    """
    if include_admin is None:
        include_admin = is_admin_enabled()

    mcp = FastMCP("memoryd")

    # ----------------------------------------------------------------------
    # Memory tools (7) — agent tier
    # ----------------------------------------------------------------------

    @mcp.tool(
        name="mem_save",
        description=(
            "Save a memory to memoryd. Type is one of session / decision / "
            "preference / fact / playbook / warning. scope='auto' derives the "
            "scope from cwd (.git root); pass an explicit scope_hash to bypass."
        ),
    )
    async def mem_save(
        content: str,
        type: str = "session",  # noqa: A002 - matches MCP arg name
        scope: str = "auto",
        tags: list[str] | None = None,
        triggers: list[str] | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        return await memory_tools.save(
            content=content,
            type=type,
            scope=scope,
            tags=tags,
            triggers=triggers,
            title=title,
        )

    @mcp.tool(
        name="mem_update",
        description=(
            "Patch a memory's body, tags, triggers, or title in place. "
            "Reuse this when you have an exact memory_id and want to correct it."
        ),
    )
    async def mem_update(
        memory_id: str,
        content: str | None = None,
        tags: list[str] | None = None,
        triggers: list[str] | None = None,
        title: str | None = None,
    ) -> dict[str, Any]:
        return await memory_tools.update(
            memory_id,
            content=content,
            tags=tags,
            triggers=triggers,
            title=title,
        )

    @mcp.tool(
        name="mem_delete",
        description="Delete a memory by id. Removes both the markdown file and the index row.",
    )
    async def mem_delete(memory_id: str) -> dict[str, Any]:
        return await memory_tools.delete(memory_id)

    @mcp.tool(
        name="mem_get",
        description="Return the full memory (row + raw markdown body) for a memory_id.",
    )
    async def mem_get(memory_id: str) -> dict[str, Any]:
        return await memory_tools.get(memory_id)

    @mcp.tool(
        name="mem_search",
        description=(
            "Hybrid (ripgrep + Milvus) search across a scope. types is an "
            "optional post-filter, entity_ids gives matched memories an "
            "additive boost."
        ),
    )
    async def mem_search(
        query: str,
        scope: str = "auto",
        top_k: int = 10,
        types: list[str] | None = None,
        entity_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        return await memory_tools.search(
            query,
            scope=scope,
            top_k=top_k,
            types=types,
            entity_ids=entity_ids,
        )

    @mcp.tool(
        name="mem_context",
        description=(
            "Return memories temporally adjacent to memory_id in the same "
            "scope (before + after). Use this to surface 'what happened "
            "around this point in time'."
        ),
    )
    async def mem_context(memory_id: str, neighbors: int = 3) -> dict[str, Any]:
        return await memory_tools.context(memory_id, neighbors=neighbors)

    @mcp.tool(
        name="mem_timeline",
        description=(
            "Chronological list of memories in a scope. since='30d' / '2w' / "
            "'6m' / '1y'. Excludes soft-forgotten memories."
        ),
    )
    async def mem_timeline(
        scope: str = "auto",
        since: str = "30d",
        types: list[str] | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        return await memory_tools.timeline(
            scope=scope,
            since=since,
            types=types,
            limit=limit,
        )

    # ----------------------------------------------------------------------
    # Session tools (4) — agent tier
    # ----------------------------------------------------------------------

    @mcp.tool(
        name="mem_session_start",
        description=(
            "Open a new session memory and return its id. Call once per "
            "Claude / Codex session to anchor downstream session_end / "
            "session_summary."
        ),
    )
    async def mem_session_start(
        scope: str = "auto",
        source: str = "manual",
        title: str | None = None,
    ) -> dict[str, Any]:
        return await session_tools.session_start(
            scope=scope, source=source, title=title
        )

    @mcp.tool(
        name="mem_session_end",
        description="Append a summary to a session and mark it closed. Idempotent.",
    )
    async def mem_session_end(session_id: str, summary: str = "") -> dict[str, Any]:
        return await session_tools.session_end(session_id, summary=summary)

    @mcp.tool(
        name="mem_session_summary",
        description="Return the body of a session as raw markdown.",
    )
    async def mem_session_summary(session_id: str) -> dict[str, Any]:
        return await session_tools.session_summary(session_id)

    @mcp.tool(
        name="mem_capture_passive",
        description=(
            "Write a long-term memory (fact/decision/preference/playbook/"
            "warning) directly, bypassing the DURA promotion gate. Used by "
            "harness mirrors to persist observed user behaviour without LLM scoring."
        ),
    )
    async def mem_capture_passive(
        content: str,
        source: str,
        scope: str = "auto",
        type: str = "fact",  # noqa: A002
        tags: list[str] | None = None,
        triggers: list[str] | None = None,
    ) -> dict[str, Any]:
        return await session_tools.capture_passive(
            content,
            source,
            scope=scope,
            type=type,
            tags=tags,
            triggers=triggers,
        )

    # ----------------------------------------------------------------------
    # Judge tools (2) — agent tier
    # ----------------------------------------------------------------------

    @mcp.tool(
        name="mem_judge",
        description=(
            "Ask the LLM whether new_text supersedes the memory old_memory_id. "
            "Returns a SupersedeJudgment + band (auto/review/ignore). The "
            "judgment is *not* auto-applied — record the verdict yourself "
            "via mem_save if needed."
        ),
    )
    async def mem_judge(new_text: str, old_memory_id: str) -> dict[str, Any]:
        return await judge_tools.judge(new_text, old_memory_id)

    @mcp.tool(
        name="mem_compare",
        description=(
            "Diff two memories + ask the LLM whether they conflict. Returns "
            "{a, b, diff_lines, judgment, band}."
        ),
    )
    async def mem_compare(memory_id_a: str, memory_id_b: str) -> dict[str, Any]:
        return await judge_tools.compare(memory_id_a, memory_id_b)

    # ----------------------------------------------------------------------
    # Promotion-review tools (3) — agent tier, for in-conversation triage
    # ----------------------------------------------------------------------

    @mcp.tool(
        name="mem_review_pending",
        description=(
            "List pending promotions (LLM-scored candidates awaiting human "
            "judgment). Defaults: scope=global, sorted by DURA avg ascending "
            "(most uncertain first). Use min_score/max_score to focus the "
            "grey zone (e.g. 0.5..0.85)."
        ),
    )
    async def mem_review_pending(
        scope: str = "global",
        limit: int = 10,
        min_score: float = 0.0,
        max_score: float = 1.0,
        types: list[str] | None = None,
    ) -> dict[str, Any]:
        return await promotions_tools.review_pending(
            scope=scope,
            limit=limit,
            min_score=min_score,
            max_score=max_score,
            types=types,
        )

    @mcp.tool(
        name="mem_promote",
        description=(
            "Approve pending promotion(s). Pass promotion_ids=[..] for "
            "explicit batch, or auto_high=True to approve every row with "
            "DURA avg >= threshold (default 0.85)."
        ),
    )
    async def mem_promote(
        promotion_ids: list[int] | None = None,
        auto_high: bool = False,
        threshold: float = 0.85,
        scope: str = "global",
    ) -> dict[str, Any]:
        return await promotions_tools.promote(
            promotion_ids=promotion_ids,
            auto_high=auto_high,
            threshold=threshold,
            scope=scope,
        )

    @mcp.tool(
        name="mem_reject",
        description=(
            "Reject pending promotion(s) — flips status to 'rejected'. They "
            "stay in the table for audit but no .md is written. Use this "
            "when the user explicitly says 'drop' / 'never mind' / etc."
        ),
    )
    async def mem_reject(promotion_ids: list[int]) -> dict[str, Any]:
        return await promotions_tools.reject(promotion_ids=promotion_ids)

    # ----------------------------------------------------------------------
    # Admin tools (6) — registered only when MEMORYD_MCP_ADMIN=1
    # ----------------------------------------------------------------------

    if include_admin:

        @mcp.tool(
            name="mem_stats",
            description="Aggregate counts (total / by-type / by-scope / by-decay / top-entities).",
        )
        async def mem_stats(scope: str | None = None) -> dict[str, Any]:
            return await admin_tools.stats(scope=scope)

        @mcp.tool(
            name="mem_merge_projects",
            description=(
                "Merge scope_b's memories into scope_a. dry_run=True (default) "
                "returns a preview. Use with care — inverse is not implemented."
            ),
        )
        async def mem_merge_projects(
            scope_a: str,
            scope_b: str,
            dry_run: bool = True,
        ) -> dict[str, Any]:
            return await admin_tools.merge_projects(
                scope_a, scope_b, dry_run=dry_run
            )

        @mcp.tool(
            name="mem_current_project",
            description="Detect the scope for the current working directory.",
        )
        async def mem_current_project(cwd: str | None = None) -> dict[str, Any]:
            return await admin_tools.current_project(cwd=cwd)

        @mcp.tool(
            name="mem_doctor",
            description=(
                "Health check across memoryd subsystems (data root, index DB, "
                "embeddings, LLM, knowledge graph, sync)."
            ),
        )
        async def mem_doctor() -> dict[str, Any]:
            return await admin_tools.doctor()

        @mcp.tool(
            name="mem_save_prompt",
            description="Persist a high-quality user prompt under <data_root>/prompts/<name>.md.",
        )
        async def mem_save_prompt(name: str, content: str) -> dict[str, Any]:
            return await admin_tools.save_prompt(name, content)

        @mcp.tool(
            name="mem_suggest_topic_key",
            description=(
                "Ask the LLM (or heuristic fallback) for a stable topic_key "
                "for a piece of text. Returns {topic_key, source}."
            ),
        )
        async def mem_suggest_topic_key(content: str) -> dict[str, Any]:
            return await admin_tools.suggest_topic_key(content)

    return mcp


# ---------------------------------------------------------------------------
# Introspection helpers (used by tests)
# ---------------------------------------------------------------------------


async def list_tool_names(mcp: FastMCP) -> list[str]:
    """Return all registered tool names — works regardless of fastmcp version."""
    tools = await mcp.list_tools()
    return [t.name for t in tools]


async def list_tool_summaries(mcp: FastMCP) -> list[dict[str, Any]]:
    """Return ``[{name, description, parameters}, ...]`` for every registered tool."""
    tools = await mcp.list_tools()
    out: list[dict[str, Any]] = []
    for t in tools:
        out.append(
            {
                "name": t.name,
                "description": t.description or "",
                "parameters": dict(t.parameters or {}),
            }
        )
    return out


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="memoryd-mcp",
        description="memoryd MCP server (19 mem_* tools)",
    )
    p.add_argument(
        "--transport",
        choices=("stdio", "http"),
        default=os.environ.get("MEMORYD_MCP_TRANSPORT", "stdio"),
        help="MCP transport (default: stdio)",
    )
    p.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("MEMORYD_MCP_PORT", "8766")),
        help="HTTP port when --transport=http (default: 8766)",
    )
    p.add_argument(
        "--host",
        default=os.environ.get("MEMORYD_MCP_HOST", "127.0.0.1"),
        help="HTTP bind host when --transport=http (default: 127.0.0.1)",
    )
    p.add_argument(
        "--admin",
        action="store_true",
        help="Force-enable admin tools (overrides MEMORYD_MCP_ADMIN).",
    )
    p.add_argument(
        "-v", "--verbose", action="store_true", help="Verbose logging to stderr.",
    )
    return p.parse_args(argv)


def _disable_fastmcp_version_check() -> None:
    """Prevent FastMCP's startup banner from blocking on PyPI lookups.

    FastMCP queries pypi.org for the latest version at boot. Behind a strict
    firewall the network error escapes its narrow ``(httpx.HTTPError, ...)``
    except clause and crashes the whole MCP transport. Outdated-fastmcp
    notices aren't actionable from inside an installed app, so we silence it.
    """
    try:
        from fastmcp.utilities import version_check

        version_check.check_for_newer_version = lambda: None  # type: ignore[attr-defined]
        version_check.get_latest_version = (  # type: ignore[attr-defined]
            lambda include_prereleases=False: None
        )
        version_check._fetch_latest_version = (  # type: ignore[attr-defined]
            lambda include_prereleases=False: None
        )
    except Exception:  # noqa: BLE001
        pass


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``memoryd-mcp`` console script."""
    _disable_fastmcp_version_check()
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    include_admin = True if args.admin else is_admin_enabled()
    mcp = build_server(include_admin=include_admin)

    n_tools = len(asyncio.run(list_tool_names(mcp)))
    log.info(
        "memoryd-mcp ready: transport=%s tools=%d admin=%s",
        args.transport, n_tools, include_admin,
    )

    try:
        if args.transport == "stdio":
            mcp.run()
        else:
            # FastMCP v3 uses "http" / "streamable-http"; "sse" is legacy.
            mcp.run(transport="http", host=args.host, port=args.port)
    except (EOFError, KeyboardInterrupt, BrokenPipeError) as exc:
        log.info("memoryd-mcp shutdown: %s", type(exc).__name__)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
