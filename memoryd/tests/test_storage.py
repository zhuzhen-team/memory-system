"""Storage layer tests."""
from pathlib import Path

from memoryd.schema import SessionMemory
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
