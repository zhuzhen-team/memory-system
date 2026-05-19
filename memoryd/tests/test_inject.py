"""Unit tests for memoryd.inject.render_session_context.

The function is the SessionStart hook back-end: it reads identity.md +
top entities + recent long-term memories and renders a small markdown
block. Contract: never raises, sensitive scopes always skipped,
graceful fallback if everything is missing.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.index import open_index
from memoryd.inject import render_session_context, _EMPTY_FALLBACK


@pytest.fixture
def isolated_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fresh data root + index.db with all migrations applied."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(tmp_path / "profile"))
    idx = open_index(tmp_path / "index.db")
    idx.close()
    return tmp_path


def _write_identity(root: Path, body: str) -> None:
    profile = root / "profile"
    profile.mkdir(parents=True, exist_ok=True)
    (profile / "identity.md").write_text(body, encoding="utf-8")


def _insert_memory(
    root: Path,
    *,
    slug: str,
    type_: str,
    title: str,
    scope_hash: str = "abc123",
    created_days_ago: int = 0,
    scope_sensitive: int = 0,
) -> None:
    created = (datetime.now(timezone.utc) - timedelta(days=created_days_ago)).isoformat()
    body_path = f"scopes/{scope_hash}/sessions/{slug}.md"
    with sqlite3.connect(str(root / "index.db")) as conn:
        conn.execute(
            """
            INSERT INTO memories
                (slug, type, scope_hash, title, source, created_at, updated_at,
                 ttl_days, decay_state, last_recalled_at, recall_count,
                 fingerprint, body_path, scope_sensitive)
            VALUES (?, ?, ?, ?, 'test', ?, NULL, NULL, 'fresh', NULL, 0, ?, ?, ?)
            """,
            (slug, type_, scope_hash, title, created, slug, body_path, scope_sensitive),
        )
        conn.commit()


def _insert_entity(
    root: Path,
    *,
    name: str,
    type_: str = "project",
    mention_count: int = 1,
    scope_hash: str | None = "abc123",
    days_ago: int = 0,
) -> None:
    last_seen = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()
    eid = f"entity:{type_}:{name.lower()}"
    with sqlite3.connect(str(root / "index.db")) as conn:
        conn.execute(
            """
            INSERT INTO entities
                (id, name, type, aliases, context, first_seen_at, last_seen_at,
                 mention_count, scope_hash, decay_state)
            VALUES (?, ?, ?, '[]', '', ?, ?, ?, ?, 'fresh')
            """,
            (eid, name, type_, last_seen, last_seen, mention_count, scope_hash),
        )
        conn.commit()


def _mark_scope_sensitive(root: Path, scope_hash: str) -> None:
    with sqlite3.connect(str(root / "index.db")) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO sensitive_scopes (scope_hash, scope_root, marked_at) "
            "VALUES (?, '/sensitive', datetime('now'))",
            (scope_hash,),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# normal / mixed paths
# ---------------------------------------------------------------------------


def test_render_full_picture_normal(isolated_root: Path) -> None:
    _write_identity(
        isolated_root,
        "# abble\n\nNanjing-based eng. Builds local-first memory systems.\n",
    )
    _insert_memory(
        isolated_root,
        slug="2026-05-12-react-to-solid",
        type_="decision",
        title="React → Solid 切换",
        created_days_ago=2,
    )
    _insert_memory(
        isolated_root,
        slug="2026-05-10-prefer-uv",
        type_="preference",
        title="偏好 uv 管 venv",
        created_days_ago=4,
    )
    _insert_entity(isolated_root, name="memory-system", mention_count=45)
    _insert_entity(isolated_root, name="Solid", mention_count=17)
    _insert_entity(isolated_root, name="abble", type_="person", mention_count=12)

    out = render_session_context()

    assert "## 与 abble 的最近上下文" in out
    assert "Nanjing-based eng" in out  # identity excerpt
    assert "memory-system (45)" in out  # top entities
    assert "Solid (17)" in out
    assert "React → Solid 切换" in out  # recent memory title
    assert "偏好 uv 管 venv" in out
    assert "decision" in out
    assert _EMPTY_FALLBACK not in out


# ---------------------------------------------------------------------------
# branches — partial data
# ---------------------------------------------------------------------------


def test_render_no_identity(isolated_root: Path) -> None:
    _insert_entity(isolated_root, name="zhuzhen", mention_count=8)
    _insert_memory(
        isolated_root,
        slug="2026-05-15-test",
        type_="fact",
        title="zhuzhen 域名 owner",
    )
    out = render_session_context()
    assert "## 与 abble 的最近上下文" in out
    assert "**画像摘要**" not in out
    assert "zhuzhen (8)" in out
    assert "zhuzhen 域名 owner" in out


def test_render_no_kg(isolated_root: Path) -> None:
    _write_identity(isolated_root, "abble: builder.\n")
    _insert_memory(
        isolated_root,
        slug="2026-05-12-fact",
        type_="fact",
        title="builds memory-system",
    )
    out = render_session_context()
    assert "abble: builder" in out
    assert "**最近常提及的实体**" not in out  # no entities
    assert "builds memory-system" in out


def test_render_no_profile_only_entities(isolated_root: Path) -> None:
    """Index exists, entities exist, but no identity.md and no memories."""
    _insert_entity(isolated_root, name="memoryd", mention_count=5)
    out = render_session_context()
    assert "**最近常提及的实体**" in out
    assert "memoryd (5)" in out
    assert "**画像摘要**" not in out
    assert "**最近长期记忆**" not in out


# ---------------------------------------------------------------------------
# sensitive scope handling
# ---------------------------------------------------------------------------


def test_render_skips_sensitive_scope_entities(isolated_root: Path) -> None:
    """Entities in a sensitive scope must never leak into the context block."""
    _mark_scope_sensitive(isolated_root, "secret_hash")
    _insert_entity(
        isolated_root,
        name="sensitive_project",
        scope_hash="secret_hash",
        mention_count=100,
    )
    _insert_entity(isolated_root, name="public_project", mention_count=3)
    out = render_session_context()
    assert "public_project" in out
    assert "sensitive_project" not in out


def test_render_excludes_scope_sensitive_memories(isolated_root: Path) -> None:
    """Memories with scope_sensitive=1 must never show up."""
    _insert_memory(
        isolated_root,
        slug="secret-mem",
        type_="decision",
        title="never leak this",
        scope_sensitive=1,
    )
    _insert_memory(
        isolated_root,
        slug="public-mem",
        type_="decision",
        title="public decision",
    )
    out = render_session_context()
    assert "public decision" in out
    assert "never leak this" not in out


# ---------------------------------------------------------------------------
# total-failure fallback (graceful, never raises)
# ---------------------------------------------------------------------------


def test_render_returns_fallback_on_empty(isolated_root: Path) -> None:
    out = render_session_context()
    assert out == _EMPTY_FALLBACK


def test_render_returns_fallback_when_no_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No index.db, no identity → fallback (no crash)."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(tmp_path / "profile"))
    out = render_session_context()
    assert out == _EMPTY_FALLBACK


def test_render_does_not_raise_on_corrupted_db(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupted SQLite file must not blow up the hook."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(tmp_path / "profile"))
    (tmp_path / "index.db").write_bytes(b"not a sqlite db at all")
    # Should not raise; identity.md doesn't exist either → fallback.
    out = render_session_context()
    assert _EMPTY_FALLBACK in out or out.strip() != ""  # never crash


# ---------------------------------------------------------------------------
# parameters
# ---------------------------------------------------------------------------


def test_render_respects_max_chars(isolated_root: Path) -> None:
    long_identity = "abble. " * 500
    _write_identity(isolated_root, long_identity)
    out = render_session_context(identity_max_chars=200)
    # The excerpt should be visibly truncated relative to the full text.
    # Block quote prefix adds bytes per line — assert the rendered output
    # is meaningfully shorter than the raw identity body.
    assert len(out) < len(long_identity)


def test_render_scope_filter_isolates_entities(isolated_root: Path) -> None:
    _insert_entity(isolated_root, name="proj-A", scope_hash="hash-A", mention_count=10)
    _insert_entity(isolated_root, name="proj-B", scope_hash="hash-B", mention_count=10)
    out_a = render_session_context(scope="hash-A")
    assert "proj-A" in out_a
    assert "proj-B" not in out_a


def test_render_include_trends_flag(isolated_root: Path) -> None:
    """include_trends=True appends the 'recent trigger' block if data exists."""
    # Insert some trigger_stats rows for today
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with sqlite3.connect(str(isolated_root / "index.db")) as conn:
        conn.execute(
            "INSERT INTO trigger_stats (trigger, scope_hash, day, hits) "
            "VALUES ('memoryd', '_global', ?, 7)",
            (today,),
        )
        conn.commit()
    _write_identity(isolated_root, "abble.\n")
    out = render_session_context(include_trends=True)
    assert "memoryd" in out
    assert "**最近 trigger**" in out


def test_render_recent_types_override(isolated_root: Path) -> None:
    _insert_memory(
        isolated_root,
        slug="warn-1",
        type_="warning",
        title="never delete .git",
    )
    _insert_memory(
        isolated_root,
        slug="dec-1",
        type_="decision",
        title="use uv",
    )
    # Default would exclude warnings; explicit override should include them.
    out = render_session_context(recent_memory_types=["warning"])
    assert "never delete .git" in out
    assert "use uv" not in out
