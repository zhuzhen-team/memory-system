"""Plan 7 Basic Memory schema alignment tests.

Frontmatter 新增 tags / category / observations 三字段（spec §4.8 #32）。
- tags 实际 Plan 1 已存，这里覆盖确保仍可传入
- category / observations 由 Plan 7 task 5 新增
- 全 optional + default empty，Plan 1-6 已存 .md 不破坏
"""
from datetime import datetime
from pathlib import Path

from memoryd.schema import Frontmatter, SessionMemory


def _minimal_kwargs() -> dict:
    """Minimum kwargs for current (post Plan 1-6) Frontmatter."""
    return dict(
        title="t",
        slug="s",
        type="session",
        scope_hash="abc",
        triggers=["x"],
        source="claude-code",
        created_at=datetime(2026, 5, 15, 0, 0),
    )


def test_frontmatter_accepts_basic_memory_fields() -> None:
    fm = Frontmatter(
        **_minimal_kwargs(),
        tags=["important", "logo"],
        category="decisions/architecture",
        observations=["obs-1", "obs-2"],
    )
    assert fm.tags == ["important", "logo"]
    assert fm.category == "decisions/architecture"
    assert fm.observations == ["obs-1", "obs-2"]


def test_frontmatter_defaults_when_basic_memory_fields_absent() -> None:
    """Backward compat: Plan 1-6 已存 .md 没新字段，应仍能 parse."""
    fm = Frontmatter(**_minimal_kwargs())
    assert fm.tags == []
    assert fm.category is None
    assert fm.observations == []


def test_session_memory_roundtrip_with_basic_memory_fields(memory_root: Path) -> None:
    """Save + load roundtrip should preserve new fields."""
    from memoryd.storage import save_session, load_session

    sess = SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-15-bm-test",
            type="session",
            scope_hash="abc",
            triggers=["x"],
            source="claude-code",
            created_at=datetime(2026, 5, 15, 0, 0),
            tags=["tag-a"],
            category="cat-a",
            observations=["obs-only-one"],
        ),
        body="body content",
    )
    path = save_session(memory_root, sess)
    loaded = load_session(path)
    assert loaded.frontmatter.tags == ["tag-a"]
    assert loaded.frontmatter.category == "cat-a"
    assert loaded.frontmatter.observations == ["obs-only-one"]


def test_frontmatter_pre_existing_md_loads_without_basic_memory_fields(
    tmp_path: Path,
) -> None:
    """Verify Plan 1-6 style .md (no new fields) still parses."""
    from memoryd.storage import load_session

    md = tmp_path / "old.md"
    md.write_text(
        "---\n"
        "title: old\n"
        "slug: old\n"
        "type: session\n"
        "scope_hash: abc\n"
        "triggers:\n"
        "- foo\n"
        "source: claude-code\n"
        "created_at: 2026-05-01T00:00:00+00:00\n"
        "---\n"
        "old body\n"
    )
    loaded = load_session(md)
    assert loaded.frontmatter.title == "old"
    assert loaded.frontmatter.tags == []
    assert loaded.frontmatter.category is None
    assert loaded.frontmatter.observations == []
