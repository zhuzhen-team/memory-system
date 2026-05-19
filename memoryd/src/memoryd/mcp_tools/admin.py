"""Admin handlers (6 tools, gated by ``MEMORYD_MCP_ADMIN=1``).

Admin tools are *not* exposed to agents by default — they touch
destructive state (merge_projects), expose sensitive aggregates (stats),
or trigger LLM calls that aren't part of normal recall (suggest_topic_key).
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any

from ..scope import resolve_scope_root, scope_hash as _scope_hash
from . import util


# --- mem_stats ---------------------------------------------------------------


async def stats(*, scope: str | None = None) -> dict[str, Any]:
    """Aggregate counts across the memoryd index.

    Returns: total / by-type / by-scope / by-decay-state / top-entities.
    When ``scope`` is supplied, all aggregates are scoped to that one scope.
    """
    conn = util.open_db()
    try:
        where = ""
        args: list[Any] = []
        if scope:
            where = " WHERE scope_hash = ?"
            args.append(scope)

        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM memories{where}", args
        ).fetchone()["n"]

        by_type_rows = conn.execute(
            f"SELECT type, COUNT(*) AS n FROM memories{where} GROUP BY type",
            args,
        ).fetchall()
        by_type = {r["type"]: int(r["n"]) for r in by_type_rows}

        by_scope_rows = conn.execute(
            "SELECT scope_hash, COUNT(*) AS n FROM memories "
            "GROUP BY scope_hash ORDER BY n DESC LIMIT 50"
        ).fetchall() if not scope else []
        by_scope = {r["scope_hash"]: int(r["n"]) for r in by_scope_rows}

        by_decay_rows = conn.execute(
            f"SELECT decay_state, COUNT(*) AS n FROM memories{where} GROUP BY decay_state",
            args,
        ).fetchall()
        by_decay = {r["decay_state"]: int(r["n"]) for r in by_decay_rows}

        # Top entities — best-effort; entities table may be empty.
        try:
            ent_sql = (
                "SELECT name, type, mention_count FROM entities "
                + ("WHERE scope_hash = ? " if scope else "")
                + "ORDER BY mention_count DESC LIMIT 10"
            )
            ent_args = [scope] if scope else []
            top_entities = [dict(r) for r in conn.execute(ent_sql, ent_args).fetchall()]
        except sqlite3.OperationalError:
            top_entities = []
    finally:
        conn.close()

    return util.ok(
        total=int(total),
        by_type=by_type,
        by_scope=by_scope,
        by_decay=by_decay,
        top_entities=top_entities,
    )


# --- mem_merge_projects ------------------------------------------------------


async def merge_projects(
    scope_a: str,
    scope_b: str,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Preview / execute a project merge.

    "Merge" here means: re-tag all memories in ``scope_b`` so that their
    ``scope_hash`` becomes ``scope_a``. The markdown files are *not* moved
    on disk; only the SQLite index is rewritten. Use sparingly — the
    inverse operation is impossible without backups.
    """
    if not scope_a or not scope_b:
        return util.fail("scope_a and scope_b required", code="invalid_argument")
    if scope_a == scope_b:
        return util.fail("scope_a and scope_b must differ", code="invalid_argument")

    conn = util.open_db()
    try:
        count_b = conn.execute(
            "SELECT COUNT(*) AS n FROM memories WHERE scope_hash = ?", (scope_b,)
        ).fetchone()["n"]
        if dry_run:
            preview = [
                dict(r) for r in conn.execute(
                    "SELECT slug, type, title FROM memories WHERE scope_hash = ? LIMIT 20",
                    (scope_b,),
                ).fetchall()
            ]
            return util.ok(
                dry_run=True,
                from_scope=scope_b,
                to_scope=scope_a,
                affected=int(count_b),
                preview=preview,
            )
        conn.execute(
            "UPDATE memories SET scope_hash = ? WHERE scope_hash = ?",
            (scope_a, scope_b),
        )
        conn.commit()
    finally:
        conn.close()
    return util.ok(
        dry_run=False,
        from_scope=scope_b,
        to_scope=scope_a,
        affected=int(count_b),
    )


# --- mem_current_project -----------------------------------------------------


async def current_project(*, cwd: str | None = None) -> dict[str, Any]:
    """Detect the scope for the current working directory.

    Returns ``{scope_hash, scope_root, source}`` where ``source`` is one of
    ``git`` (cwd is inside a git repo), ``cwd`` (fallback), or ``env``
    (MEMORYD_DEFAULT_SCOPE override).
    """
    start = Path(cwd) if cwd else Path.cwd()
    env_default = util.default_scope()
    try:
        root = resolve_scope_root(start)
    except Exception as e:  # pragma: no cover
        return util.fail(f"failed to resolve scope: {e}", code="scope_error")
    has_git = (root / ".git").exists()
    if not has_git and env_default:
        return util.ok(
            scope_hash=env_default,
            scope_root=str(start.resolve()),
            source="env",
        )
    return util.ok(
        scope_hash=_scope_hash(root),
        scope_root=str(root),
        source="git" if has_git else "cwd",
    )


# --- mem_doctor --------------------------------------------------------------


async def doctor() -> dict[str, Any]:
    """Health check across memoryd subsystems.

    Returns a dict of per-component statuses + a top-level ``healthy`` bool.
    Does not raise — every probe is wrapped so a broken subsystem still
    reports a status instead of taking down the tool.
    """
    checks: dict[str, dict[str, Any]] = {}
    root = util.data_root()

    # 1) data root exists / writeable
    checks["data_root"] = _probe_path(root, "data root")

    # 2) index DB
    db = root / "index.db"
    if not db.exists():
        checks["index_db"] = {"ok": False, "detail": "index.db missing"}
    else:
        try:
            conn = sqlite3.connect(db)
            n = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
            conn.close()
            checks["index_db"] = {"ok": True, "memories": int(n)}
        except sqlite3.Error as e:
            checks["index_db"] = {"ok": False, "detail": str(e)}

    # 3) embeddings / vector module availability
    try:
        from ..embeddings import get_embedder  # noqa: F401
        checks["embeddings"] = {"ok": True}
    except Exception as e:
        checks["embeddings"] = {"ok": False, "detail": str(e)}

    # 4) LLM provider
    try:
        from ..llm import get_llm
        provider = get_llm()
        checks["llm"] = {"ok": True, "provider": getattr(provider, "name", "unknown"),
                          "model": getattr(provider, "model", "unknown")}
    except Exception as e:
        checks["llm"] = {"ok": False, "detail": str(e)}

    # 5) knowledge graph schema
    try:
        from ..knowledge_graph import ensure_kg_schema
        if db.exists():
            conn = sqlite3.connect(db)
            ensure_kg_schema(conn)
            n = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            conn.close()
            checks["knowledge_graph"] = {"ok": True, "entities": int(n)}
        else:
            checks["knowledge_graph"] = {"ok": False, "detail": "no index.db"}
    except Exception as e:
        checks["knowledge_graph"] = {"ok": False, "detail": str(e)}

    # 6) sync dir (optional)
    try:
        from ..config import load_config
        cfg = load_config()
        if cfg.sync.enabled and cfg.sync.dir:
            checks["sync"] = _probe_path(Path(cfg.sync.dir).expanduser(), "sync dir")
        else:
            checks["sync"] = {"ok": True, "enabled": False}
    except Exception as e:
        checks["sync"] = {"ok": False, "detail": str(e)}

    healthy = all(c.get("ok", False) for c in checks.values())
    return util.ok(healthy=healthy, checks=checks, data_root=str(root))


def _probe_path(p: Path, label: str) -> dict[str, Any]:
    """Best-effort dir-probe used by ``doctor``."""
    try:
        if not p.exists():
            return {"ok": False, "detail": f"{label} does not exist: {p}"}
        if not p.is_dir():
            return {"ok": False, "detail": f"{label} is not a directory: {p}"}
        # quick writeability test — create + remove a temp file
        marker = p / ".doctor-write-test"
        marker.write_text("ok", encoding="utf-8")
        marker.unlink(missing_ok=True)
        return {"ok": True, "path": str(p)}
    except Exception as e:
        return {"ok": False, "detail": f"{label}: {e}"}


# --- mem_save_prompt ---------------------------------------------------------


async def save_prompt(name: str, content: str) -> dict[str, Any]:
    """Persist a high-quality user prompt under ``<data_root>/prompts/<name>.md``.

    Idempotent: re-saving the same name overwrites the file. Name is
    sanitized to ``[A-Za-z0-9_-]`` to keep it filesystem-safe.
    """
    if not name or not name.strip():
        return util.fail("name required", code="invalid_argument")
    if not content or not content.strip():
        return util.fail("content required", code="invalid_argument")
    import re
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name.strip())[:60] or "untitled"
    prompts_dir = util.data_root() / "prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)
    path = prompts_dir / f"{safe}.md"
    try:
        path.write_text(content, encoding="utf-8")
    except OSError as e:  # pragma: no cover
        return util.fail(f"save_prompt failed: {e}", code="storage_error")
    return util.ok(name=safe, path=str(path), bytes=len(content.encode("utf-8")))


# --- mem_suggest_topic_key ---------------------------------------------------


async def suggest_topic_key(content: str) -> dict[str, Any]:
    """Ask the LLM for a stable ``topic_key`` for a piece of text.

    Falls back to a simple "first significant word(s) of content" heuristic
    when the LLM is unreachable, so the tool still returns something
    actionable in offline tests.
    """
    if not content or not content.strip():
        return util.fail("content required", code="invalid_argument")
    text = content.strip()

    # Heuristic fallback used both when no LLM available and as a safety net.
    fallback_key = _heuristic_topic_key(text)

    try:
        from ..llm import get_llm
        from ..llm.base import LLMMessage
        provider = get_llm()
    except Exception as e:
        return util.ok(topic_key=fallback_key, source="heuristic", reason=f"no llm: {e}")

    sys_msg = (
        "你是一个 topic-key 生成器。读用户提供的一段文字，输出一个 ≤32 字符、"
        "snake_case、纯 ASCII 的稳定主题 key（用于持久化文件命名），"
        "只输出 key 本身，不要任何 markdown / 解释。"
    )
    user_msg = text[:2000]
    try:
        raw = await provider.generate(
            [LLMMessage(role="system", content=sys_msg),
             LLMMessage(role="user", content=user_msg)],
            max_tokens=64,
            temperature=0.0,
        )
    except Exception as e:
        return util.ok(topic_key=fallback_key, source="heuristic", reason=f"llm error: {e}")

    key = (raw or "").strip().splitlines()[0].strip(' "\'`') if raw else ""
    key = "".join(c for c in key.lower() if c.isalnum() or c in "_-")[:32] or fallback_key
    return util.ok(topic_key=key, source="llm")


def _heuristic_topic_key(text: str) -> str:
    """Pick the first 3 significant words and snake_case them.

    "Significant" = non-empty, non-punctuation. Falls back to ``memory`` if
    nothing usable remains.
    """
    import re
    tokens = [t.lower() for t in re.findall(r"[A-Za-z0-9]+", text) if t]
    if not tokens:
        return "memory"
    return "_".join(tokens[:3])[:32]


__all__ = [
    "current_project",
    "doctor",
    "merge_projects",
    "save_prompt",
    "stats",
    "suggest_topic_key",
]
