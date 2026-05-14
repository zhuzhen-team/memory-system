"""digest tests."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.governance.digest import build_digest, render_digest_text
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


def test_digest_lists_pending_promotions(memory_root: Path):
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(
            title="s", slug="s1", type="session", scope_hash="h",
            source="manual", created_at=datetime(2026, 5, 14),
        ),
        body="x",
    ))
    idx = open_index(memory_root / "index.db")
    idx.conn.execute(
        """INSERT INTO promotions (source_session_slug, proposed_type, proposed_title,
           proposed_body, proposed_triggers, dura_score, reasoning, proposed_supersedes,
           scope_hash, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        ("s1", "decision", "T", "B", "[]", "{}", "r", "[]", "h",
         datetime.now(timezone.utc).isoformat()),
    )
    idx.conn.commit()
    idx.close()

    d = build_digest(memory_root)
    assert len(d["promotions"]) == 1
    assert d["promotions"][0]["proposed_title"] == "T"


def test_digest_lists_duplicate_pairs(memory_root: Path):
    """Two memories with same fingerprint show up as a dup pair."""
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(title="a", slug="a1", type="decision", scope_hash="h",
                                source="manual", created_at=datetime(2026, 5, 14)),
        body="identical content for fingerprint",
    ))
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(title="b", slug="a2", type="decision", scope_hash="h",
                                source="manual", created_at=datetime(2026, 5, 14)),
        body="identical content for fingerprint",
    ))
    d = build_digest(memory_root)
    pairs = d["duplicates"]
    assert any(set(p) == {"a1", "a2"} for p in pairs)


def test_digest_lists_decayed_candidates(memory_root: Path):
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(title="x", slug="dim1", type="session", scope_hash="h",
                                source="manual", created_at=datetime(2026, 5, 14)),
        body="x",
    ))
    idx = open_index(memory_root / "index.db")
    idx.update_decay_state("dim1", "dim")
    idx.close()
    d = build_digest(memory_root)
    assert any(c["slug"] == "dim1" for c in d["decayed"])


def test_render_digest_text_is_string(memory_root: Path):
    d = build_digest(memory_root)
    out = render_digest_text(d)
    assert isinstance(out, str)
    assert "promotions" in out.lower() or "提升" in out
