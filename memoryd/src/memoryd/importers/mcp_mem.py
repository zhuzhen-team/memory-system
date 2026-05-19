"""Import mcp-memory-service memories.json (single direction)."""
from __future__ import annotations

import json
from pathlib import Path

from .common import (
    ImportEntry,
    ImportReport,
    kebab,
    now_iso,
    short_hash,
    write_entry,
)
from .claude_md import derive_triggers


def _map_type(meta_type: str | None) -> str:
    if not meta_type:
        return "fact"
    s = meta_type.lower()
    if "decision" in s:
        return "decision"
    if "preference" in s or "pref" in s:
        return "preference"
    if "warning" in s or "warn" in s:
        return "warning"
    if "playbook" in s or "process" in s:
        return "playbook"
    return "fact"


def _unique(seq: list[str]) -> list[str]:
    seen = set()
    out = []
    for x in seq:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


def run(
    json_path: Path,
    data_root: Path,
    scope_hash: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    source_tag: str | None = None,
) -> ImportReport:
    src = source_tag or "imported-mcp-memory-service"
    report = ImportReport(dry_run=dry_run)
    text = Path(json_path).read_text(encoding="utf-8")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return report
    if isinstance(data, dict) and "memories" in data:
        data = data["memories"]
    if not isinstance(data, list):
        return report
    for item in data:
        if not isinstance(item, dict):
            continue
        content = item.get("content") or item.get("text") or ""
        if not content:
            continue
        meta = item.get("metadata") or {}
        tags = meta.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        item_id = item.get("id") or short_hash(content)
        meta_type = meta.get("type")
        memoryd_type = _map_type(meta_type)
        title = (content[:60] + "…") if len(content) > 60 else content
        title = title.strip()
        slug = f"imported-mcpmem-{kebab(str(item_id))[:30]}-{short_hash(content)}"
        # 用户 tags 优先；不足 2 再 fall back 到 derive_triggers
        triggers = _unique([str(t) for t in tags if t])
        if len(triggers) < 2:
            triggers = _unique(triggers + derive_triggers(title))[:5]
        else:
            triggers = triggers[:5]
        body = content if len(content) <= 8000 else content[:8000] + "..."
        entry = ImportEntry(
            slug=slug,
            type=memoryd_type,
            title=title or "imported",
            body=body,
            triggers=triggers,
            source=src,
            created_at=meta.get("created_at") or now_iso(),
        )
        report.parsed += 1
        if write_entry(data_root, scope_hash, entry,
                       dry_run=dry_run, force=force):
            report.written += 1
            report.by_type[memoryd_type] = report.by_type.get(memoryd_type, 0) + 1
        else:
            report.skipped += 1
    return report
