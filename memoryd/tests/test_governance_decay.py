"""Decay state machine tests."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.governance.decay import sweep_decay
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


def _make_session(slug: str, scope: str = "h", ttl_days: int | None = 90) -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title=slug,
            slug=slug,
            type="session",
            scope_hash=scope,
            source="manual",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ttl_days=ttl_days,
        ),
        body="b",
    )


def _set_db_field(memory_root: Path, slug: str, field: str, value):
    idx = open_index(memory_root / "index.db")
    idx.conn.execute(f"UPDATE memories SET {field} = ? WHERE slug = ?", (value, slug))
    idx.conn.commit()
    idx.close()


def test_alive_to_dim_when_ttl_expired_and_never_recalled(memory_root: Path):
    s = _make_session("aged-session", ttl_days=90)
    save_memory(memory_root, s)
    # backdate created_at to 100 days ago; no last_recalled_at set
    _set_db_field(memory_root, "aged-session", "created_at",
                  (datetime.now(timezone.utc) - timedelta(days=100)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("aged-session")
    idx.close()
    assert row["decay_state"] == "dim"


def test_dim_to_soft_forgotten_after_30_days_no_recall(memory_root: Path):
    s = _make_session("aged-dim")
    save_memory(memory_root, s)
    # state=dim, last_recalled_at = 31 days ago
    _set_db_field(memory_root, "aged-dim", "decay_state", "dim")
    _set_db_field(memory_root, "aged-dim", "last_recalled_at",
                  (datetime.now(timezone.utc) - timedelta(days=31)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("aged-dim")
    idx.close()
    assert row["decay_state"] == "soft-forgotten"


def test_soft_forgotten_moved_to_forgotten_dir_after_90_more_days(memory_root: Path):
    s = _make_session("aged-sf")
    save_memory(memory_root, s)
    _set_db_field(memory_root, "aged-sf", "decay_state", "soft-forgotten")
    _set_db_field(memory_root, "aged-sf", "last_recalled_at",
                  (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    forgotten_dir = memory_root / "scopes" / "h" / "forgotten"
    assert forgotten_dir.exists()
    assert list(forgotten_dir.glob("aged-sf.md")), "should be moved to forgotten/"


def test_recent_recall_resets_alive_keeps_alive(memory_root: Path):
    """If last_recalled_at is recent, even with old created_at, stays alive."""
    s = _make_session("recently-used")
    save_memory(memory_root, s)
    _set_db_field(memory_root, "recently-used", "created_at",
                  (datetime.now(timezone.utc) - timedelta(days=200)).isoformat())
    _set_db_field(memory_root, "recently-used", "last_recalled_at",
                  (datetime.now(timezone.utc) - timedelta(days=2)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("recently-used")
    idx.close()
    assert row["decay_state"] == "alive"


def test_long_term_memory_with_null_ttl_never_decays(memory_root: Path):
    """Decisions / preferences etc have ttl_days=NULL → no auto decay."""
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="lt",
            slug="long-term-1",
            type="decision",
            scope_hash="h",
            source="manual",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ttl_days=None,
        ),
        body="x",
    )
    save_memory(memory_root, s)

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("long-term-1")
    idx.close()
    assert row["decay_state"] == "alive"
