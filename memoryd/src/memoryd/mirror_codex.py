"""Mirror Codex.app rollout_summary markdown into memoryd SessionMemory.

Codex.app writes a session summary per session to:
    ~/.codex/memories/rollout_summaries/<ISO-ts>-<short-id>-<topic-slug>.md

Header is plain text key:value (NOT YAML frontmatter — no `---`):
    thread_id: <uuid>
    updated_at: <ISO-8601>
    rollout_path: /Users/.../rollout-<ts>-<uuid>.jsonl
    cwd: /Users/.../<project>
    git_branch: <branch>

    # <one-line title>
    <body>

We read `cwd` directly for scope resolution; no content reverse-lookup needed.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from .mirror import save_to_scope_or_unscoped
from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash


_HEADER_LINE = re.compile(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*:\s*(.*?)\s*$")


def parse_rollout_header(path: Path) -> tuple[dict[str, str], str]:
    """Parse Codex rollout_summary into (header dict, body string).

    Stops on first blank line OR first line that doesn't match KEY: VALUE.
    Returns ({}, full_text) when no header is detected.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)

    header: dict[str, str] = {}
    body_start = 0
    for idx, raw in enumerate(lines):
        stripped = raw.strip()
        if stripped == "":
            body_start = idx + 1
            break
        m = _HEADER_LINE.match(raw.rstrip("\n"))
        if not m:
            body_start = idx
            break
        header[m.group(1)] = m.group(2)
    else:
        # file is all header, no body
        body_start = len(lines)

    body = "".join(lines[body_start:])
    return header, body


def _slug_from_filename(path: Path, updated_at: datetime | None) -> str:
    """Derive a memoryd slug from a Codex rollout filename + updated_at date.

    Filename pattern: 2026-05-13T08-56-13-xoIz-<topic-slug>.md
    Memoryd slug pattern: <YYYY-MM-DD>-<stem>
    """
    date_str = (updated_at or datetime.now(timezone.utc)).strftime("%Y-%m-%d")
    stem = path.stem
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", stem)[:80]
    return f"{date_str}-{safe}"


def _parse_iso(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc)


def _resolve_scope_from_cwd(cwd: str | None) -> str | None:
    if not cwd:
        return None
    p = Path(cwd)
    if not p.exists():
        # cwd may point to a project on a machine no longer accessible;
        # still try to resolve as a path-only scope hash (no .git walk)
        return scope_hash(p)
    return scope_hash(resolve_scope_root(p))


def transcode_rollout(path: Path) -> tuple[SessionMemory, str | None]:
    """Read a rollout_summary .md and produce SessionMemory + resolved scope hash.

    Returns (session, None) if scope can't be resolved → caller routes to
    _unscoped bucket via save_to_scope_or_unscoped.
    """
    header, body = parse_rollout_header(path)
    updated_at = _parse_iso(header.get("updated_at"))
    resolved_hash = _resolve_scope_from_cwd(header.get("cwd"))

    slug = _slug_from_filename(path, updated_at)
    # Title: first H1 in body, fallback to filename stem
    title_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    # Body keeps the original header as a fenced block so future readers
    # can see thread_id / rollout_path / git_branch.
    header_block = "\n".join(f"{k}: {v}" for k, v in header.items())
    full_body = (
        f"```codex-rollout-header\n{header_block}\n```\n\n{body}"
        if header
        else body
    )

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=title[:200],
            slug=slug,
            type="session",
            scope_hash=resolved_hash or "_unscoped",
            triggers=[],
            source="codex-rollout",
            created_at=updated_at,
        ),
        body=full_body,
    )
    return session, resolved_hash


class CodexRolloutHandler:
    """Callable that mirrors a single rollout_summary .md to memoryd data root."""

    def __init__(self, memory_root: Path) -> None:
        self.memory_root = memory_root

    def __call__(self, path: Path) -> None:
        if path.suffix.lower() != ".md":
            return
        try:
            session, resolved_hash = transcode_rollout(path)
        except Exception:
            # Never crash the daemon on a single bad file.
            return
        save_to_scope_or_unscoped(
            self.memory_root,
            session,
            resolved_scope_hash=resolved_hash,
        )
