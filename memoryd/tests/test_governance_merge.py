"""merge tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.governance.merge import merge_memories
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


def _mem(slug: str, body: str, triggers: list[str], type_: str = "decision") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title=slug, slug=slug, type=type_, scope_hash="h",
            triggers=triggers, source="manual", created_at=datetime(2026, 5, 14),
        ),
        body=body,
    )


def test_merge_appends_drop_body_to_keep(memory_root: Path):
    save_memory(memory_root, _mem("keep1", "A original", ["k1"]))
    save_memory(memory_root, _mem("drop1", "B duplicate content", ["k2"]))

    merge_memories(memory_root, keep_slug="keep1", drop_slugs=["drop1"])

    keep_path = memory_root / "scopes" / "h" / "decisions" / "keep1.md"
    text = keep_path.read_text()
    assert "A original" in text
    assert "B duplicate content" in text


def test_merge_combines_triggers(memory_root: Path):
    save_memory(memory_root, _mem("k", "A", ["t1"]))
    save_memory(memory_root, _mem("d", "B", ["t2", "t3"]))

    merge_memories(memory_root, keep_slug="k", drop_slugs=["d"])

    idx = open_index(memory_root / "index.db")
    triggers = idx.conn.execute("SELECT trigger FROM triggers WHERE slug=? ORDER BY trigger", ("k",)).fetchall()
    idx.close()
    assert set(t[0] for t in triggers) == {"t1", "t2", "t3"}


def test_merge_deletes_dropped_memory_from_db_and_disk(memory_root: Path):
    save_memory(memory_root, _mem("kk", "A", ["t"]))
    save_memory(memory_root, _mem("dd", "B", ["t"]))

    merge_memories(memory_root, keep_slug="kk", drop_slugs=["dd"])

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("dd")
    idx.close()
    assert row is None
    drop_path = memory_root / "scopes" / "h" / "decisions" / "dd.md"
    assert not drop_path.exists()


def test_merge_rejects_unknown_keep(memory_root: Path):
    save_memory(memory_root, _mem("d", "x", ["t"]))
    with pytest.raises(KeyError, match="keep"):
        merge_memories(memory_root, keep_slug="no-such-slug", drop_slugs=["d"])
