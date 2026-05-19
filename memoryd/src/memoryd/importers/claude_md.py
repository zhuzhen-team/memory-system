"""Import CLAUDE.md by heuristic section split."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .common import (
    ImportEntry,
    ImportReport,
    kebab,
    now_iso,
    write_entry,
)


_HEADING_PATTERN = re.compile(r"^(#{2,3})\s+(.+?)\s*$", re.MULTILINE)
_TYPE_HINTS = [
    (re.compile(r"warning|踩坑|不要|避免|caution", re.IGNORECASE), "warning"),
    (re.compile(r"playbook|流程|操作|how[- ]?to|steps?\b", re.IGNORECASE), "playbook"),
    (re.compile(r"decision|决策|选[择型方]?|chose|chosen", re.IGNORECASE), "decision"),
    (re.compile(r"preference|偏好|习惯|prefer|like to", re.IGNORECASE), "preference"),
]


@dataclass
class Section:
    level: int
    heading: str
    body: str


def parse_sections(text: str) -> list[Section]:
    matches = list(_HEADING_PATTERN.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        level = len(m.group(1))
        heading = m.group(2)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if body:
            sections.append(Section(level=level, heading=heading, body=body))
    return sections


def infer_type(heading: str) -> str:
    for pattern, type_ in _TYPE_HINTS:
        if pattern.search(heading):
            return type_
    return "fact"


def derive_triggers(heading: str) -> list[str]:
    words = re.findall(r"[A-Za-z一-鿿][A-Za-z0-9一-鿿_-]+", heading)
    triggers = [w for w in words if len(w) >= 3][:5]
    if len(triggers) < 2:
        triggers = ["imported", kebab(heading)] + triggers
    return triggers[:5]


def to_entries(
    text: str,
    *,
    kind: str = "claude-md",
    source_tag: str | None = None,
) -> list[ImportEntry]:
    src = source_tag or f"imported-{kind}"
    out: list[ImportEntry] = []
    seen_slugs: dict[str, int] = {}
    for sec in parse_sections(text):
        type_ = infer_type(sec.heading)
        base_slug = f"imported-{kind}-{kebab(sec.heading)}"
        n = seen_slugs.get(base_slug, 0)
        slug = base_slug if n == 0 else f"{base_slug}-{n}"
        seen_slugs[base_slug] = n + 1
        body = sec.body if len(sec.body) <= 8000 else sec.body[:8000] + "..."
        out.append(ImportEntry(
            slug=slug,
            type=type_,
            title=sec.heading,
            body=body,
            triggers=derive_triggers(sec.heading),
            source=src,
            created_at=now_iso(),
        ))
    return out


def run(
    md_path: Path,
    data_root: Path,
    scope_hash: str,
    *,
    dry_run: bool = False,
    force: bool = False,
    source_tag: str | None = None,
) -> ImportReport:
    text = Path(md_path).read_text(encoding="utf-8")
    entries = to_entries(text, kind="claude-md", source_tag=source_tag)
    report = ImportReport(parsed=len(entries), dry_run=dry_run)
    for e in entries:
        written = write_entry(
            data_root, scope_hash, e, dry_run=dry_run, force=force
        )
        if written:
            report.written += 1
            report.by_type[e.type] = report.by_type.get(e.type, 0) + 1
        else:
            report.skipped += 1
    return report
