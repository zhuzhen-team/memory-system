"""HANDOFF.md generator.

Pulls recent ``decision`` / ``warning`` / ``session`` rows + identity
snippet + top entities for a given scope, hands them to the configured
LLM provider, and returns a markdown HANDOFF document.

Two operating modes:
- ``with_llm=True`` (default): LLM rewrites raw signals into the 6-block
  HANDOFF structure with the anti-pattern guards from ``prompt.py``.
- ``with_llm=False``: deterministic fallback that just renders the raw
  signals as a markdown digest. Used when no LLM is available, or for
  preview / dry-run before paying the LLM round-trip.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .prompt import HANDOFF_MAX_CHARS, render_handoff_prompt


def _data_root() -> Path:
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return Path.home() / ".local" / "share" / "memoryd"


def _read_identity_snippet(max_chars: int = 800) -> str:
    """Best-effort wrapper around profile.identity.read_current_identity."""
    try:
        from ..profile.identity import read_current_identity
        return (read_current_identity(max_chars=max_chars) or "").strip()
    except Exception:  # noqa: BLE001 — best-effort
        return ""


def _open_index_conn(data_root: Path) -> sqlite3.Connection | None:
    db = data_root / "index.db"
    if not db.exists():
        return None
    try:
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.DatabaseError:
        return None


def _fetch_recent_typed(
    conn: sqlite3.Connection,
    *,
    type_: str,
    scope_hash: str | None,
    window_days: int,
    limit: int,
    data_root: Path,
) -> list[dict[str, Any]]:
    """Pull recent rows of a given type from a scope (or all scopes).

    For each row also tries to read the body file so the LLM has context
    beyond just the title (clipped per-row by ``prompt.py``).
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sql = (
            "SELECT slug, type, title, body_path, scope_hash, created_at "
            "FROM memories WHERE type = ? AND created_at >= ? "
            "AND decay_state != 'soft-forgotten' "
            "AND COALESCE(scope_sensitive, 0) = 0"
        )
        args: list[Any] = [type_, cutoff]
        if scope_hash is not None:
            sql += " AND scope_hash = ?"
            args.append(scope_hash)
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []

    # Enrich with body text (best-effort, clipped at the prompt layer)
    for r in rows:
        body_path = r.get("body_path")
        if body_path:
            try:
                full = (data_root / body_path).read_text(encoding="utf-8")
                # Strip leading frontmatter for prompt readability
                if full.startswith("---"):
                    parts = full.split("---", 2)
                    if len(parts) >= 3:
                        full = parts[2].lstrip()
                r["body"] = full
            except OSError:
                r["body"] = ""
    return rows


def _fetch_top_entities(
    conn: sqlite3.Connection,
    *,
    scope_hash: str | None,
    window_days: int,
    limit: int,
) -> list[dict[str, Any]]:
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sql = (
            "SELECT name, type, mention_count FROM entities "
            "WHERE last_seen_at >= ?"
        )
        args: list[Any] = [cutoff]
        if scope_hash is not None:
            sql += " AND scope_hash = ?"
            args.append(scope_hash)
        sql += " ORDER BY mention_count DESC, last_seen_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in conn.execute(sql, args).fetchall()]
    except sqlite3.OperationalError:
        return []


def gather_signals(
    data_root: Path,
    *,
    scope_hash: str | None,
    window_days: int,
    decisions_limit: int = 12,
    warnings_limit: int = 8,
    sessions_limit: int = 5,
    entities_limit: int = 10,
) -> dict[str, Any]:
    """Collect raw inputs for HANDOFF generation. Never raises.

    Returns a dict ready to feed into ``render_handoff_prompt`` or
    ``_render_fallback_markdown``.
    """
    identity = _read_identity_snippet()
    conn = _open_index_conn(data_root)
    if conn is None:
        return {
            "identity": identity,
            "decisions": [],
            "warnings": [],
            "sessions": [],
            "entities": [],
            "window_days": window_days,
        }
    try:
        decisions = _fetch_recent_typed(
            conn, type_="decision", scope_hash=scope_hash,
            window_days=window_days, limit=decisions_limit, data_root=data_root,
        )
        warnings = _fetch_recent_typed(
            conn, type_="warning", scope_hash=scope_hash,
            window_days=window_days, limit=warnings_limit, data_root=data_root,
        )
        sessions = _fetch_recent_typed(
            conn, type_="session", scope_hash=scope_hash,
            window_days=window_days, limit=sessions_limit, data_root=data_root,
        )
        entities = _fetch_top_entities(
            conn, scope_hash=scope_hash, window_days=window_days,
            limit=entities_limit,
        )
    finally:
        conn.close()

    return {
        "identity": identity,
        "decisions": decisions,
        "warnings": warnings,
        "sessions": sessions,
        "entities": entities,
        "window_days": window_days,
    }


def _render_fallback_markdown(
    *,
    project_name: str,
    today_iso: str,
    signals: dict[str, Any],
) -> str:
    """Deterministic non-LLM fallback. Renders raw signals as a digest.

    Used when LLM unavailable (no API key, network down) or for dry-run.
    Doesn't follow the 6-block structure as strictly as the LLM output —
    explicitly labels itself as a fallback so the reader knows to revise.
    """
    # Fallback intentionally drops the 1./2./.../6. numbering: without LLM
    # synthesis we cannot guarantee all six blocks are populated, so
    # half-numbered output (1, 4, 6) would just look broken. We label each
    # section by name and let the reader (or a follow-up LLM pass) re-shape.
    parts = [
        f"# HANDOFF — {project_name} ({today_iso})",
        "",
        "> ⚠️ 这是 **fallback 模板**（LLM 不可用或 --no-llm 模式）。",
        "> 它把素材原样列出，没有经过 LLM 凝练。装好 LLM 后用 `memoryd handoff write` 重新生成 6 区块版。",
        "> 提示：用 `memoryd doctor` 看 LLM provider 状态；新装环境可直接走 `claude-code` provider（复用 CC 订阅，零 API key）。",
        "",
        "## TL;DR",
        "（fallback 模板未生成；请手填或重跑带 LLM 的 `handoff write`）",
        "",
    ]

    if signals.get("identity"):
        parts += [
            "## 用户画像摘要",
            signals["identity"],
            "",
        ]

    decisions = signals.get("decisions", [])
    if decisions:
        parts.append("## 关键决策记录（原始 decisions）")
        for d in decisions:
            date = (d.get("created_at") or "")[:10]
            title = d.get("title") or d.get("slug") or "?"
            parts.append(f"- [{date}] {title}")
        parts.append("")

    warnings = signals.get("warnings", [])
    if warnings:
        parts.append("## 已知坑 / 待办（原始 warnings）")
        for w in warnings:
            date = (w.get("created_at") or "")[:10]
            title = w.get("title") or w.get("slug") or "?"
            parts.append(f"- ⚠️ [{date}] {title}")
        parts.append("")

    sessions = signals.get("sessions", [])
    if sessions:
        parts.append(f"## 最近 session（{signals.get('window_days', 7)} 天）")
        for s in sessions:
            date = (s.get("created_at") or "")[:10]
            title = s.get("title") or s.get("slug") or "?"
            parts.append(f"- [{date}] {title}")
        parts.append("")

    entities = signals.get("entities", [])
    if entities:
        chips = [
            f"{e.get('name')} ({e.get('mention_count', 0)})"
            for e in entities[:10]
        ]
        parts.append("## 高频实体")
        parts.append("- " + " · ".join(chips))
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def _derive_project_name(cwd: Path) -> str:
    """Project label for the HANDOFF header.

    Prefers the basename of cwd. Special-cases the repo root case where
    cwd is already a meaningful project name.
    """
    name = cwd.resolve().name
    return name or "project"


def generate_handoff(
    *,
    cwd: Path,
    scope_hash: str | None,
    data_root: Path | None = None,
    window_days: int = 7,
    with_llm: bool = True,
    llm: Any = None,
    today: datetime | None = None,
) -> dict[str, Any]:
    """Produce a HANDOFF.md body. Returns a dict (no file IO here).

    Returns
    -------
    {
        "content": "<markdown>",
        "project_name": str,
        "today_iso": str,
        "used_llm": bool,
        "signals_summary": {"decisions": N, "warnings": N, ...},
    }

    The caller is responsible for writing the content to disk (so we can
    support --dry-run, --out, --snapshot variants cleanly in the CLI).
    """
    root = data_root or _data_root()
    today = today or datetime.now(timezone.utc)
    today_iso = today.date().isoformat()
    project_name = _derive_project_name(cwd)

    signals = gather_signals(
        root,
        scope_hash=scope_hash,
        window_days=window_days,
    )

    summary = {
        "decisions": len(signals["decisions"]),
        "warnings": len(signals["warnings"]),
        "sessions": len(signals["sessions"]),
        "entities": len(signals["entities"]),
        "identity_len": len(signals["identity"]),
    }

    if not with_llm:
        return {
            "content": _render_fallback_markdown(
                project_name=project_name,
                today_iso=today_iso,
                signals=signals,
            ),
            "project_name": project_name,
            "today_iso": today_iso,
            "used_llm": False,
            "signals_summary": summary,
        }

    # LLM path
    provider = llm
    if provider is None:
        try:
            from ..llm import get_provider
            provider = get_provider()
        except Exception:  # noqa: BLE001 — fall back to deterministic
            return {
                "content": _render_fallback_markdown(
                    project_name=project_name,
                    today_iso=today_iso,
                    signals=signals,
                ),
                "project_name": project_name,
                "today_iso": today_iso,
                "used_llm": False,
                "signals_summary": summary,
            }

    messages = render_handoff_prompt(
        project_name=project_name,
        today_iso=today_iso,
        identity_snippet=signals["identity"],
        decisions=signals["decisions"],
        warnings=signals["warnings"],
        sessions=signals["sessions"],
        entities=signals["entities"],
    )
    system_msg = messages[0].content
    user_msg = messages[1].content
    try:
        raw = provider.complete(system=system_msg, user=user_msg)
    except Exception:  # noqa: BLE001 — fall back to deterministic
        return {
            "content": _render_fallback_markdown(
                project_name=project_name,
                today_iso=today_iso,
                signals=signals,
            ),
            "project_name": project_name,
            "today_iso": today_iso,
            "used_llm": False,
            "signals_summary": summary,
        }

    content = (raw or "").strip()
    # Empty / whitespace-only LLM response is treated as a failure path so we
    # don't silently overwrite an existing HANDOFF.md with nothing. Triggers:
    # quota-throttled responses, safety-filtered outputs, network race.
    if not content:
        return {
            "content": _render_fallback_markdown(
                project_name=project_name,
                today_iso=today_iso,
                signals=signals,
            ),
            "project_name": project_name,
            "today_iso": today_iso,
            "used_llm": False,
            "signals_summary": summary,
        }

    # Strip ```markdown wrappers if the LLM added them despite instructions
    if content.startswith("```"):
        lines = content.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        content = "\n".join(lines).strip()

    # Soft cap on length (prompt says ≤ 4000 chars but LLMs sometimes overshoot)
    if len(content) > HANDOFF_MAX_CHARS * 2:
        content = content[: HANDOFF_MAX_CHARS * 2] + "\n\n_(truncated)_\n"

    if not content.endswith("\n"):
        content += "\n"

    return {
        "content": content,
        "project_name": project_name,
        "today_iso": today_iso,
        "used_llm": True,
        "signals_summary": summary,
    }
