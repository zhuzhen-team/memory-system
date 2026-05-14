"""CLI entry points.

v1.0-α subcommands:
  memoryd capture   — invoked by tool hooks; reads JSON payload from stdin
                       and writes a session markdown to the data root
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash
from .storage import save_session


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "memoryd"


def _data_root() -> Path:
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return DEFAULT_DATA_ROOT


def _read_transcript_text(transcript_path: str) -> str | None:
    """Read up to last 50 message contents from a Claude Code transcript JSONL.

    Returns None if file missing/unreadable.
    """
    try:
        path = Path(transcript_path)
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        # Take last 50 lines for v1.0-α naive summary
        recent = lines[-50:]
        chunks: list[str] = []
        for raw in recent:
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        chunks.append(c.get("text", ""))
            elif isinstance(content, str):
                chunks.append(content)
        return "\n".join(chunks).strip() or None
    except OSError:
        return None


def _summarize_naively(text: str, max_chars: int = 2000) -> str:
    """Naive truncation summary for v1.0-α. Plan 3 replaces with LLM call."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated]"


def capture_session(
    payload: dict[str, Any],
    *,
    memory_root: Path | None = None,
    now: datetime | None = None,
    source: str = "claude-code",
) -> Path:
    """Convert a SessionEnd hook payload into a SessionMemory markdown file.

    `source` is recorded in frontmatter for downstream filtering. Conventional
    values: claude-code | codex | openclaw | manual.
    """
    if memory_root is None:
        memory_root = _data_root()
    if now is None:
        now = datetime.now()

    session_id = payload.get("session_id", "unknown")
    # Sanitize session_id since it flows into the filename slug;
    # CC currently emits UUIDs so this is defense in depth.
    session_id = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
    # Collapse consecutive dots so ".." cannot form a path traversal component.
    session_id = re.sub(r"\.{2,}", "_", session_id)
    transcript_path = payload.get("transcript_path", "")
    cwd = payload.get("cwd", str(Path.cwd()))

    scope_root = resolve_scope_root(Path(cwd))
    sh = scope_hash(scope_root)

    transcript_text = _read_transcript_text(transcript_path)
    if transcript_text is None:
        body = (
            f"## 无 transcript（transcript unavailable）\n\n"
            f"transcript_path: `{transcript_path}`\n"
            f"session_id: `{session_id}`\n"
        )
    else:
        summary = _summarize_naively(transcript_text)
        body = f"## 摘要（朴素截断，v1.0-α）\n\n{summary}\n"

    slug = f"{now:%Y-%m-%d}-{session_id}"
    title = f"{now:%Y-%m-%d} 会话 {session_id[:8]}"

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=title,
            slug=slug,
            type="session",
            scope_hash=sh,
            triggers=[],
            source=source,
            created_at=now,
        ),
        body=body,
    )
    return save_session(memory_root, session)


def cmd_capture(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("error: empty stdin; expected JSON payload", file=sys.stderr)
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON on stdin: {e}", file=sys.stderr)
        return 2
    if not isinstance(payload, dict):
        print(f"error: expected JSON object, got {type(payload).__name__}", file=sys.stderr)
        return 2
    path = capture_session(payload, source=args.source)
    print(f"captured -> {path}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="memoryd")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_capture = subs.add_parser("capture", help="read SessionEnd payload from stdin and save")
    p_capture.add_argument(
        "--source",
        default="claude-code",
        help="origin tool tag written to frontmatter (claude-code | codex | openclaw | ...)",
    )
    p_capture.set_defaults(func=cmd_capture)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
