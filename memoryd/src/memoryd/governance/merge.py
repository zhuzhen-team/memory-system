"""Merge duplicate memories: combine bodies + triggers, delete dropped entries.

Keep slug's .md gets a `## merged-from <drop-slug>` section appended for each
dropped entry. Triggers union into keep's frontmatter.
"""
from __future__ import annotations

from pathlib import Path

from ..index import open_index
from ..schema import Frontmatter, SessionMemory
from ..storage import load_session, save_memory


def merge_memories(memory_root: Path, *, keep_slug: str, drop_slugs: list[str]) -> None:
    idx = open_index(memory_root / "index.db")
    try:
        keep_row = idx.get_memory(keep_slug)
        if keep_row is None:
            raise KeyError(f"keep slug not found: {keep_slug}")
        keep_path = memory_root / keep_row["body_path"]
        keep_mem = load_session(keep_path)

        merged_body_parts = [keep_mem.body.rstrip()]
        merged_triggers = list(keep_mem.frontmatter.triggers)

        for drop in drop_slugs:
            drop_row = idx.get_memory(drop)
            if drop_row is None:
                continue
            drop_path = memory_root / drop_row["body_path"]
            if not drop_path.exists():
                idx.delete_memory(drop)
                continue
            drop_mem = load_session(drop_path)
            merged_body_parts.append(f"\n\n## merged-from {drop}\n\n{drop_mem.body}")
            for t in drop_mem.frontmatter.triggers:
                if t not in merged_triggers:
                    merged_triggers.append(t)
            # delete drop's .md + index row
            drop_path.unlink()
            idx.delete_memory(drop)

        new_keep = SessionMemory(
            frontmatter=Frontmatter(
                **{**keep_mem.frontmatter.model_dump(),
                   "triggers": merged_triggers}
            ),
            body="\n".join(merged_body_parts),
        )
        save_memory(memory_root, new_keep)
    finally:
        idx.close()
