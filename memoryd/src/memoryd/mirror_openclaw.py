"""Mirror OpenClaw session jsonl files into memoryd SessionMemory.

OpenClaw writes per-session jsonl logs to:
    ~/.openclaw/agents/<agent-id>/sessions/<session-id>.jsonl

Each line is a JSON record; shapes vary but typically contain a `role` /
`author` and `content` (string or array of {type:"text", text:...}).
No explicit cwd field, so we reverse-lookup scope from path mentions in
the concatenated message content.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .mirror import save_to_scope_or_unscoped
from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash


_PATH_PATTERN = re.compile(r"(/[^\s\"`'()<>]{2,})")


def reverse_lookup_scope_from_content(
    content: str,
    *,
    known_roots: list[Path],
) -> Path | None:
    """Find the deepest known root that any path in `content` lies under.

    Returns None if:
    - no path is mentioned
    - multiple unrelated roots (not nested) match
    """
    if not known_roots:
        return None
    resolved_roots = [r.resolve() for r in known_roots]
    candidates = _PATH_PATTERN.findall(content)
    if not candidates:
        return None

    matched: set[Path] = set()
    for cand in candidates:
        cand_path = Path(cand).resolve()
        # find any root that is an ancestor of (or equal to) cand_path
        for root in resolved_roots:
            try:
                cand_path.relative_to(root)
                matched.add(root)
            except ValueError:
                continue

    if not matched:
        return None

    # If matched roots are nested, pick the deepest.
    # If matched roots are siblings / unrelated, ambiguity → None.
    matched_list = sorted(matched, key=lambda p: len(str(p)), reverse=True)
    deepest = matched_list[0]
    for other in matched_list[1:]:
        try:
            deepest.relative_to(other)  # other is ancestor of deepest → OK
        except ValueError:
            try:
                other.relative_to(deepest)  # deepest ancestor of other → impossible here since sorted by length
                # If this somehow passes, treat as ambiguous
                return None
            except ValueError:
                # truly unrelated
                return None
    return deepest


def _extract_text(content_field) -> str | None:
    if isinstance(content_field, str):
        return content_field
    if isinstance(content_field, list):
        parts = []
        for c in content_field:
            if isinstance(c, str):
                parts.append(c)
            elif isinstance(c, dict):
                txt = c.get("text") or c.get("value")
                if isinstance(txt, str):
                    parts.append(txt)
        return "\n".join(parts) if parts else None
    return None


def transcode_session_jsonl(
    path: Path,
    *,
    known_roots: list[Path],
) -> tuple[SessionMemory, Path | None]:
    """Read OpenClaw session jsonl, return (SessionMemory, resolved_root)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    body_parts: list[str] = []
    for raw in lines:
        if not raw.strip():
            continue
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            continue
        role = obj.get("role") or obj.get("author") or "?"
        text = _extract_text(obj.get("content"))
        if text:
            body_parts.append(f"**{role}**: {text}")

    body = "\n\n".join(body_parts) if body_parts else "(empty session)"

    # Reverse-lookup scope
    resolved_root = reverse_lookup_scope_from_content(body, known_roots=known_roots)
    resolved_hash: str | None = None
    if resolved_root is not None:
        resolved_hash = scope_hash(resolve_scope_root(resolved_root))

    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc) if path.exists() else datetime.now(timezone.utc)
    slug = f"{mtime:%Y-%m-%d}-{path.stem}"

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=f"OpenClaw session {path.stem[:20]}",
            slug=slug,
            type="session",
            scope_hash=resolved_hash or "_unscoped",
            triggers=[],
            source="openclaw-fs",
            created_at=mtime,
        ),
        body=body[:8000],  # cap body; Plan 3 LLM-summarizes
    )
    return session, resolved_root


class OpenClawSessionHandler:
    """Callable that mirrors a single OpenClaw session.jsonl to memoryd."""

    def __init__(self, memory_root: Path, known_roots: list[Path]) -> None:
        self.memory_root = memory_root
        self.known_roots = known_roots

    def __call__(self, path: Path) -> None:
        if path.suffix.lower() != ".jsonl":
            return
        try:
            session, resolved_root = transcode_session_jsonl(
                path, known_roots=self.known_roots
            )
        except Exception:
            return
        resolved_hash: str | None = None
        if resolved_root is not None:
            resolved_hash = scope_hash(resolve_scope_root(resolved_root))
        save_to_scope_or_unscoped(
            self.memory_root,
            session,
            resolved_scope_hash=resolved_hash,
        )
