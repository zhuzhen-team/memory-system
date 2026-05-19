"""Field-level LWW + audit-chain replay tests."""
from __future__ import annotations

from datetime import datetime, timezone

from memoryd.sync.conflict import merge_memory_fields
from memoryd.sync.schema import AuditEntry, MemoryEntry


def _mk(
    *,
    mid: str = "m1",
    content: str = "hello",
    tags: list[str] | None = None,
    updated_at: float = 100.0,
    relations: list[dict] | None = None,
    entities: list[str] | None = None,
    metadata: dict | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        content=content,
        content_hash=f"h-{content}",
        tags=tags or [],
        created_at=10.0,
        updated_at=updated_at,
        memory_type="note",
        metadata=metadata or {},
        export_source="test",
        id=mid,
        scope="scope1",
        relations=relations,
        entities=entities,
    )


def test_lww_picks_newer_content_when_no_audit() -> None:
    local = _mk(content="local-text", updated_at=200.0)
    remote = _mk(content="remote-text", updated_at=300.0)
    out = merge_memory_fields(local, remote, [])
    assert out.content == "remote-text"
    assert out.updated_at == 300.0
    # merge note recorded
    assert any("content-conflict" in n for n in out.merge_notes or [])


def test_lww_keeps_local_when_local_newer() -> None:
    local = _mk(content="local-text", updated_at=500.0)
    remote = _mk(content="remote-text", updated_at=300.0)
    out = merge_memory_fields(local, remote, [])
    assert out.content == "local-text"
    assert out.updated_at == 500.0


def test_tags_union_preserves_order_and_dedups() -> None:
    local = _mk(tags=["a", "b", "c"])
    remote = _mk(tags=["b", "d", "a", "e"])
    out = merge_memory_fields(local, remote, [])
    assert out.tags == ["a", "b", "c", "d", "e"]


def test_relations_union_keyed_by_triple() -> None:
    local = _mk(
        relations=[
            {"subject_id": "x", "predicate": "knows", "object_id": "y"},
            {"subject_id": "x", "predicate": "likes", "object_id": "z"},
        ]
    )
    remote = _mk(
        relations=[
            {"subject_id": "x", "predicate": "knows", "object_id": "y"},  # dup
            {"subject_id": "x", "predicate": "knows", "object_id": "w"},  # new
        ]
    )
    out = merge_memory_fields(local, remote, [])
    triples = {(r["subject_id"], r["predicate"], r["object_id"]) for r in out.relations or []}
    assert triples == {("x", "knows", "y"), ("x", "likes", "z"), ("x", "knows", "w")}


def test_entities_union_preserves_order() -> None:
    local = _mk(entities=["e1", "e2"])
    remote = _mk(entities=["e2", "e3"])
    out = merge_memory_fields(local, remote, [])
    assert out.entities == ["e1", "e2", "e3"]


def test_audit_chain_decides_when_field_named() -> None:
    """A later audit write to ``content`` wins over the naive LWW."""
    local = _mk(content="local-text", updated_at=500.0)
    remote = _mk(content="remote-text", updated_at=400.0)
    chain = [
        AuditEntry(
            seq=1,
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
            action="update",
            target_id="m1",
            details={"changed_fields": ["content"], "after": {"content": "remote-text"}},
            this_hash="hash-1",
        ),
    ]
    out = merge_memory_fields(local, remote, chain)
    # Audit chain explicitly recorded that content was changed to remote-text;
    # merge_memory_fields surfaces that fact even though local.updated_at is higher.
    assert out.content == "remote-text"
    # Audit decided → note records that
    assert any("audit" in n for n in out.merge_notes or [])


def test_same_field_different_writers_last_seq_wins() -> None:
    local = _mk(content="v1", updated_at=100.0)
    remote = _mk(content="v3", updated_at=150.0)
    chain = [
        AuditEntry(
            seq=1,
            ts=datetime(2026, 1, 1, tzinfo=timezone.utc),
            action="update",
            target_id="m1",
            details={"changed_fields": ["content"], "after": {"content": "v2"}},
            this_hash="h1",
        ),
        AuditEntry(
            seq=2,
            ts=datetime(2026, 1, 2, tzinfo=timezone.utc),
            action="update",
            target_id="m1",
            details={"changed_fields": ["content"], "after": {"content": "v3"}},
            this_hash="h2",
        ),
    ]
    out = merge_memory_fields(local, remote, chain)
    assert out.content == "v3"


def test_no_conflict_when_content_identical() -> None:
    local = _mk(content="same", tags=["a"])
    remote = _mk(content="same", tags=["b"])
    out = merge_memory_fields(local, remote, [])
    assert out.content == "same"
    # No content-conflict note expected
    assert not any("content-conflict" in n for n in (out.merge_notes or []))
    assert set(out.tags) == {"a", "b"}


def test_supersedes_chains_unioned() -> None:
    local = MemoryEntry(
        content="x",
        content_hash="hx",
        tags=[],
        created_at=1.0,
        updated_at=10.0,
        memory_type="note",
        metadata={},
        export_source="test",
        id="m1",
        supersedes=["older-a"],
    )
    remote = MemoryEntry(
        content="x",
        content_hash="hx",
        tags=[],
        created_at=1.0,
        updated_at=20.0,
        memory_type="note",
        metadata={},
        export_source="test",
        id="m1",
        supersedes=["older-a", "older-b"],
    )
    out = merge_memory_fields(local, remote, [])
    assert sorted(out.supersedes or []) == ["older-a", "older-b"]


def test_created_at_uses_min_of_both_sides() -> None:
    local = _mk(updated_at=100.0)
    local = local.model_copy(update={"created_at": 50.0})
    remote = _mk(updated_at=200.0)
    remote = remote.model_copy(update={"created_at": 30.0})
    out = merge_memory_fields(local, remote, [])
    assert out.created_at == 30.0
    assert out.updated_at == 200.0
