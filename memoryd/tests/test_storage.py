"""Storage layer tests."""
import pytest
from datetime import datetime
from pathlib import Path

from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_session, load_session, list_sessions


def test_save_creates_markdown_file(memory_root: Path, sample_session: SessionMemory):
    path = save_session(memory_root, sample_session)
    assert path.exists()
    assert path.suffix == ".md"
    assert sample_session.frontmatter.scope_hash in str(path)


def test_save_then_load_roundtrip(memory_root: Path, sample_session: SessionMemory):
    path = save_session(memory_root, sample_session)
    loaded = load_session(path)
    assert loaded.frontmatter.title == sample_session.frontmatter.title
    assert loaded.frontmatter.triggers == sample_session.frontmatter.triggers
    assert "logo 方向" in loaded.body
    assert loaded.frontmatter.slug == sample_session.frontmatter.slug
    assert loaded.frontmatter.scope_hash == sample_session.frontmatter.scope_hash


def test_list_sessions_filters_by_scope(memory_root: Path, sample_session: SessionMemory):
    save_session(memory_root, sample_session)

    found_in_scope = list_sessions(memory_root, scope_hash="abc123def456")
    assert len(found_in_scope) == 1

    found_other_scope = list_sessions(memory_root, scope_hash="zzz999")
    assert len(found_other_scope) == 0


def test_save_rejects_traversal_slug(memory_root: Path, sample_session: SessionMemory):
    """save_session must reject slugs with path separators or '..' even if a caller bypasses cli sanitization."""
    bad = sample_session.model_copy(deep=True)
    bad.frontmatter.slug = "../../etc/passwd"
    with pytest.raises(ValueError, match="slug"):
        save_session(memory_root, bad)


def test_save_rejects_slug_with_double_dots(memory_root: Path, sample_session: SessionMemory):
    bad = sample_session.model_copy(deep=True)
    bad.frontmatter.slug = "valid..but..dotted"
    with pytest.raises(ValueError, match="slug"):
        save_session(memory_root, bad)


from memoryd.index import open_index


def test_save_session_indexes_into_sqlite(memory_root: Path, sample_session: SessionMemory, monkeypatch):
    """save_session calls Index.index_memory automatically."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))
    from memoryd.storage import save_session  # re-import to pick up env

    save_session(memory_root, sample_session)
    idx = open_index(memory_root / "index.db")
    row = idx.get_memory(sample_session.frontmatter.slug)
    assert row is not None
    assert row["type"] == sample_session.frontmatter.type
    idx.close()


def test_save_memory_routes_decision_to_decisions_dir(memory_root: Path):
    from memoryd.storage import save_memory

    decision = SessionMemory(
        frontmatter=Frontmatter(
            title="logo decision",
            slug="2026-05-14-logo-decision",
            type="decision",
            scope_hash="proj1",
            triggers=["logo"],
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="深蓝+银灰",
    )
    path = save_memory(memory_root, decision)
    assert path.parent.name == "decisions"
    assert path.parent.parent.name == "proj1"
    assert path.exists()


def test_save_memory_routes_each_type_to_own_dir(memory_root: Path):
    from memoryd.storage import save_memory
    for kind, expected_dir in [
        ("preference", "preferences"),
        ("fact", "facts"),
        ("playbook", "playbooks"),
        ("warning", "warnings"),
    ]:
        m = SessionMemory(
            frontmatter=Frontmatter(
                title="t",
                slug=f"2026-05-14-{kind}",
                type=kind,
                scope_hash="h",
                source="manual",
                created_at=datetime(2026, 5, 14),
            ),
            body="b",
        )
        path = save_memory(memory_root, m)
        assert path.parent.name == expected_dir, f"type={kind} -> {path}"
