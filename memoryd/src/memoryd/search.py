"""ripgrep-based full-text search over markdown sessions.

v1.0-α: simple substring/regex match. Semantic search lands in plan 3.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .storage import _sessions_dir, load_session

# Common installation locations for ripgrep
_RG_FALLBACK_PATHS = [
    "/opt/homebrew/bin/rg",  # macOS ARM (Apple Silicon)
    "/usr/local/bin/rg",     # macOS Intel / Linux
    "/usr/bin/rg",           # Linux system install
]


def _find_rg() -> str:
    """Return the path to the rg binary, raising RuntimeError if not found."""
    # shutil.which searches PATH; may be shadowed by shell functions in Claude Code
    rg = shutil.which("rg")
    if rg:
        return rg
    # Fallback: check well-known locations directly
    for candidate in _RG_FALLBACK_PATHS:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError(
        "ripgrep (rg) not found on PATH; install via `brew install ripgrep`"
    )


@dataclass(frozen=True)
class SearchHit:
    """A search result. `excerpt` is the matched line(s)."""

    path: Path
    title: str
    slug: str
    triggers: tuple[str, ...]
    excerpt: str


def search_sessions(
    root: Path,
    scope_hash: str,
    query: str,
    *,
    limit: int = 20,
) -> list[SearchHit]:
    """Search session markdowns in a scope for `query`. Returns up to `limit` hits."""
    scope_dir = _sessions_dir(root, scope_hash)
    if not scope_dir.exists():
        return []

    rg_bin = _find_rg()
    rg_cmd = [
        rg_bin,
        "--json",
        "--ignore-case",
        "--max-count", "1",  # one match line per file is enough; global cap applied via slice below
        "--",
        query,
        str(scope_dir),
    ]
    try:
        proc = subprocess.run(
            rg_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ripgrep (rg) not found on PATH; install via `brew install ripgrep`") from e

    # rg exits 1 when no matches found; that's not an error for us
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ripgrep failed: {proc.stderr}")

    matched_paths: dict[Path, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        evt = json.loads(line)
        if evt.get("type") != "match":
            continue
        path_str = evt["data"]["path"]["text"]
        excerpt = evt["data"]["lines"]["text"].rstrip("\n")
        path = Path(path_str)
        # Keep first match per file as excerpt
        matched_paths.setdefault(path, excerpt)

    hits: list[SearchHit] = []
    for path, excerpt in list(matched_paths.items())[:limit]:
        try:
            session = load_session(path)
        except (ValueError, OSError):
            continue
        hits.append(SearchHit(
            path=path,
            title=session.frontmatter.title,
            slug=session.frontmatter.slug,
            triggers=tuple(session.frontmatter.triggers),
            excerpt=excerpt,
        ))
    return hits
