"""Import ~/.claude/projects/<proj>/memory/ auto-memory files (single direction)."""
from __future__ import annotations

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


_TYPE_MAP = {
    "user": "fact",
    "feedback": "preference",
    "project": "fact",
    "reference": "fact",
}


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """Naive YAML frontmatter parser (handles only the small subset auto-memory writes).

    Returns (frontmatter_dict, body). frontmatter_dict 是平坦的 key→value 字典；
    nested 字段（metadata.type 等）通过点号 key（"metadata.type"）暴露。
    """
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---\n", 4)
    if end < 0:
        return {}, text
    fm_text = text[4:end]
    body = text[end + 5:]
    out: dict = {}
    cur_list_key: str | None = None
    nested_prefix: str | None = None
    for line in fm_text.splitlines():
        if not line.strip():
            cur_list_key = None
            nested_prefix = None
            continue
        # nested 2-space indent for "metadata:" / "tags:" subfields
        if line.startswith("  ") and not line.startswith("    "):
            inner = line[2:]
            if inner.startswith("- "):
                # list item under cur_list_key
                if cur_list_key:
                    out.setdefault(cur_list_key, []).append(inner[2:].strip())
                continue
            if ": " in inner:
                k, v = inner.split(": ", 1)
                full = f"{nested_prefix}.{k.strip()}" if nested_prefix else k.strip()
                out[full] = v.strip()
                continue
        if line.startswith("    "):
            continue
        # top-level
        cur_list_key = None
        nested_prefix = None
        if ": " in line:
            k, v = line.split(": ", 1)
            k = k.strip()
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                out[k] = [x.strip().strip('"').strip("'")
                          for x in v[1:-1].split(",") if x.strip()]
            elif v:
                out[k] = v
            else:
                # bare "key:" → either list-coming or nested-coming
                cur_list_key = k
                nested_prefix = k
                out[k] = []
        elif line.rstrip().endswith(":"):
            # bare "key:" with no trailing space → nested/list coming
            k = line.rstrip()[:-1].strip()
            if k:
                cur_list_key = k
                nested_prefix = k
                out[k] = []
    return out, body


def run(
    memory_dir: Path,
    data_root: Path,
    scope_hash: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    source_tag: str | None = None,
) -> ImportReport:
    src = source_tag or "imported-auto-memory"
    memory_dir = Path(memory_dir)
    report = ImportReport(dry_run=dry_run)
    if not memory_dir.exists():
        return report
    for md in sorted(memory_dir.glob("*.md")):
        if md.name == "MEMORY.md":
            continue
        text = md.read_text(encoding="utf-8")
        fm, body = _parse_frontmatter(text)
        auto_type = fm.get("metadata.type") or fm.get("type") or "user"
        memoryd_type = _TYPE_MAP.get(auto_type, "fact")
        title = fm.get("name") or md.stem
        slug = f"imported-auto-memory-{kebab(title)}-{short_hash(text)}"
        body = body.strip() or text.strip()
        if len(body) > 8000:
            body = body[:8000] + "..."
        # 去重 triggers 以满足 sqlite (slug, trigger) PRIMARY KEY 约束
        seen: set[str] = set()
        triggers = [t for t in derive_triggers(title)
                    if not (t in seen or seen.add(t))]
        entry = ImportEntry(
            slug=slug,
            type=memoryd_type,
            title=title,
            body=body,
            triggers=triggers,
            source=src,
            created_at=now_iso(),
        )
        report.parsed += 1
        if write_entry(data_root, scope_hash, entry,
                       dry_run=dry_run, force=force):
            report.written += 1
            report.by_type[memoryd_type] = report.by_type.get(memoryd_type, 0) + 1
        else:
            report.skipped += 1
    return report
