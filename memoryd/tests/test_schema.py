"""Schema roundtrip tests."""
from datetime import datetime

import pytest

from memoryd.schema import Frontmatter, MemoryType, SessionMemory


def test_frontmatter_required_fields():
    fm = Frontmatter(
        title="周一项目讨论",
        slug="2026-05-09-monday-discussion",
        type="session",
        scope_hash="abc123",
        triggers=["项目", "logo"],
        source="claude-code",
        created_at=datetime(2026, 5, 9, 9, 30),
    )
    assert fm.title == "周一项目讨论"
    assert fm.type == "session"
    assert "项目" in fm.triggers


def test_session_to_markdown_roundtrip():
    """Write a session to markdown text and parse it back."""
    session = SessionMemory(
        frontmatter=Frontmatter(
            title="测试会话",
            slug="2026-05-09-test",
            type="session",
            scope_hash="abc123",
            triggers=["test"],
            source="claude-code",
            created_at=datetime(2026, 5, 9, 12, 0),
        ),
        body="## 摘要\n用户问 X，回答 Y。\n",
    )
    md_text = session.to_markdown()
    parsed = SessionMemory.from_markdown(md_text)
    assert parsed.frontmatter.title == "测试会话"
    assert parsed.frontmatter.triggers == ["test"]
    assert "用户问 X" in parsed.body


def test_from_markdown_rejects_missing_frontmatter():
    """Markdown without leading `---\\n` should raise with a distinct message."""
    with pytest.raises(ValueError, match="Missing YAML frontmatter delimiter"):
        SessionMemory.from_markdown("## just a body\n\nno fm here.\n")


def test_session_roundtrip_with_updated_at_set():
    """When updated_at is set, it must survive the roundtrip."""
    original = SessionMemory(
        frontmatter=Frontmatter(
            title="updated entry",
            slug="2026-05-09-updated",
            type="session",
            scope_hash="abc",
            triggers=[],
            source="manual",
            created_at=datetime(2026, 5, 9, 9, 0),
            updated_at=datetime(2026, 5, 10, 11, 30),
        ),
        body="body\n",
    )
    parsed = SessionMemory.from_markdown(original.to_markdown())
    assert parsed.frontmatter.updated_at == datetime(2026, 5, 10, 11, 30)


def test_from_markdown_rejects_malformed_delimiters():
    """File starting with --- but no closing --- raises distinct error."""
    with pytest.raises(ValueError, match="Malformed frontmatter delimiters"):
        SessionMemory.from_markdown("---\ntitle: x\nno closing here\n")


def test_from_markdown_rejects_non_dict_frontmatter():
    """YAML between delimiters that isn't a mapping (None / list / scalar) raises ValueError."""
    # Empty YAML (yaml.safe_load returns None)
    with pytest.raises(ValueError, match="must be a mapping"):
        SessionMemory.from_markdown("---\n---\n\nbody\n")
    # List instead of mapping
    with pytest.raises(ValueError, match="must be a mapping"):
        SessionMemory.from_markdown("---\n- a\n- b\n---\n\nbody\n")
    # Scalar
    with pytest.raises(ValueError, match="must be a mapping"):
        SessionMemory.from_markdown("---\njust a string\n---\n\nbody\n")


def test_memory_type_supports_six_kinds():
    """All six types accepted by Frontmatter."""
    for kind in ("session", "decision", "preference", "fact", "playbook", "warning"):
        fm = Frontmatter(
            title="t",
            slug=f"2026-05-14-{kind}",
            type=kind,
            scope_hash="h",
            source="manual",
            created_at=datetime(2026, 5, 14),
        )
        assert fm.type == kind


def test_frontmatter_accepts_new_governance_fields():
    fm = Frontmatter(
        title="t",
        slug="2026-05-14-x",
        type="decision",
        scope_hash="h",
        source="manual",
        created_at=datetime(2026, 5, 14),
        promoted_from="2026-05-13-session-abc",
        supersedes=["2026-04-30-old"],
        ttl_days=90,
        decay_state="alive",
        last_recalled_at=datetime(2026, 5, 13),
        recall_count=3,
        dura_score={"D": 0.85, "U": 0.92, "R": 0.78, "A": 0.95},
    )
    assert fm.promoted_from == "2026-05-13-session-abc"
    assert fm.supersedes == ["2026-04-30-old"]
    assert fm.ttl_days == 90
    assert fm.decay_state == "alive"
    assert fm.recall_count == 3
    assert fm.dura_score["D"] == 0.85


def test_frontmatter_new_fields_all_optional():
    """Plan 1-2.5 frontmatter still parses (zero new fields)."""
    fm = Frontmatter(
        title="legacy",
        slug="2026-04-01-legacy",
        type="session",
        scope_hash="h",
        source="claude-code",
        created_at=datetime(2026, 4, 1),
    )
    assert fm.promoted_from is None
    assert fm.supersedes == []
    assert fm.ttl_days is None
    assert fm.decay_state == "alive"  # default value
    assert fm.recall_count == 0       # default value
    assert fm.dura_score is None


def test_session_roundtrip_with_governance_fields():
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="logo decision",
            slug="2026-05-14-logo",
            type="decision",
            scope_hash="h",
            source="manual",
            created_at=datetime(2026, 5, 14),
            ttl_days=None,
            dura_score={"D": 0.9, "U": 0.9, "R": 0.8, "A": 1.0},
            supersedes=["2026-04-30-old-logo"],
        ),
        body="深蓝+银灰",
    )
    md = s.to_markdown()
    parsed = SessionMemory.from_markdown(md)
    assert parsed.frontmatter.type == "decision"
    assert parsed.frontmatter.dura_score["D"] == 0.9
    assert parsed.frontmatter.supersedes == ["2026-04-30-old-logo"]
