"""Import Codex AGENTS.md (reuses claude_md heuristic split)."""
from __future__ import annotations

from pathlib import Path

from .claude_md import run as _claude_run
from .common import ImportReport


def run(
    md_path: Path,
    data_root: Path,
    scope_hash: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    source_tag: str | None = None,
) -> ImportReport:
    """AGENTS.md ≈ CLAUDE.md structure；复用 claude-md 切分。"""
    src = source_tag or "imported-agents-md"
    # 但 claude_md.run 内部 kind="claude-md" 写死 source；要 override
    # 走一段：先 to_entries(kind="agents-md") 再 write_entry
    from .claude_md import to_entries
    from .common import write_entry
    text = Path(md_path).read_text(encoding="utf-8")
    entries = to_entries(text, kind="agents-md", source_tag=src)
    report = ImportReport(parsed=len(entries), dry_run=dry_run)
    for e in entries:
        if write_entry(data_root, scope_hash, e,
                       dry_run=dry_run, force=force):
            report.written += 1
            report.by_type[e.type] = report.by_type.get(e.type, 0) + 1
        else:
            report.skipped += 1
    return report
