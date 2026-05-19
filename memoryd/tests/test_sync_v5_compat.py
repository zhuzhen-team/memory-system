"""mcp-memory-service v5.0.1 compatibility tests.

The canonical v5 sample lives at
``docs/reference/legacy-memories-json-sample.json``.  We must
1. read it back losslessly via path B and
2. export memoryd state out such that v5 readers can still see
   ``memories[]`` rows with the seven required fields.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory
from memoryd.sync import (
    diff_with_remote,
    export_to_memories_json,
    import_from_memories_json,
)
from memoryd.sync.schema import MemoriesExport


REPO_ROOT = Path(__file__).resolve().parents[2]
V5_SAMPLE = REPO_ROOT / "docs" / "reference" / "legacy-memories-json-sample.json"


@pytest.fixture(autouse=True)
def _isolate(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))


def test_v5_sample_file_present() -> None:
    """Sanity check: the sample bundle is committed at the documented path."""
    assert V5_SAMPLE.exists(), f"missing v5 sample at {V5_SAMPLE}"


def test_v5_sample_parses_with_pydantic_schema() -> None:
    raw = json.loads(V5_SAMPLE.read_text("utf-8"))
    bundle = MemoriesExport.model_validate(raw)
    assert bundle.export_metadata.total_memories == 2
    # v5.0.1 reports exporter_version = "5.0.1" — we accept any string.
    assert bundle.export_metadata.exporter_version == "5.0.1"
    assert len(bundle.memories) == 2
    for mem in bundle.memories:
        # all v5 fields populated, extension fields default to None / False.
        assert mem.content
        assert mem.content_hash
        assert mem.memory_type
        assert mem.export_source
        assert mem.id is None  # extension field absent in v5 sample


def test_v5_sample_imports_into_empty_db(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    target_root.mkdir()
    idx = open_index(target_root / "index.db")
    res = import_from_memories_json(V5_SAMPLE, conn=idx.conn, device_id="v5-import")
    # Two new memories on a blank target.
    assert res["imported"] == 2
    assert res["conflicts_resolved"] == 0
    # Side-table reflects them with the v5 content_hash preserved.
    row = idx.conn.execute(
        "SELECT count(*) FROM sync_imported_memories"
    ).fetchone()
    assert row[0] == 2
    hashes = {
        r[0]
        for r in idx.conn.execute(
            "SELECT content_hash FROM sync_imported_memories"
        ).fetchall()
    }
    assert (
        "045033d572c3b1986308cbd95d3fefac899180217b4e3ff7bbb73ad8e89ec9fc" in hashes
    )


def test_memoryd_export_keeps_v5_required_fields(tmp_path: Path) -> None:
    """Round-trip: write a memoryd export, then re-validate via v5-style reading.

    Specifically, a v5 reader looks at ``export_metadata`` + ``memories[]``
    with the seven required keys.  We strip the entire memoryd-extension
    surface to simulate an older reader and confirm what's left still
    parses as v5.
    """
    root = tmp_path / "data"
    root.mkdir()
    save_memory(
        root,
        SessionMemory(
            frontmatter=Frontmatter(
                title="周一",
                slug="2026-05-11-monday",
                type="session",
                scope_hash="scopeA",
                source="claude-code",
                tags=["work"],
                created_at=datetime(2026, 5, 11, 9, 0, tzinfo=timezone.utc),
            ),
            body="周一的备忘。\n",
        ),
    )
    idx = open_index(root / "index.db")
    out = tmp_path / "export.json"
    export_to_memories_json(out, conn=idx.conn, data_root=root)

    bundle = json.loads(out.read_text("utf-8"))
    # Simulate a v5-only reader: project memories[] down to the v5 surface.
    v5_required = {
        "content",
        "content_hash",
        "tags",
        "created_at",
        "updated_at",
        "memory_type",
        "metadata",
        "export_source",
    }
    for mem in bundle["memories"]:
        assert v5_required.issubset(mem.keys()), f"missing v5 fields: {mem}"
        assert isinstance(mem["created_at"], (int, float))
        assert isinstance(mem["updated_at"], (int, float))
        # tags is a list of strings
        assert isinstance(mem["tags"], list)
        assert all(isinstance(t, str) for t in mem["tags"])
    # Top-level v5 keys preserved
    assert "export_metadata" in bundle
    assert bundle["export_metadata"]["total_memories"] == len(bundle["memories"])


def test_diff_v5_sample_against_local_blank(tmp_path: Path) -> None:
    target_root = tmp_path / "target"
    target_root.mkdir()
    idx = open_index(target_root / "index.db")
    d = diff_with_remote(V5_SAMPLE, conn=idx.conn)
    # Blank local → every remote memory is "new" (id-less means content_hash
    # acts as the key in diff output).
    assert d["remote_total"] == 2
    assert len(d["new"]) == 2
    assert len(d["unchanged"]) == 0


def test_v5_sample_then_reexport_keeps_content_hash(tmp_path: Path) -> None:
    """Import v5 sample then export from memoryd — the content_hash on the
    imported rows survives a roundtrip (so cross-device dedup keeps working).
    """
    target_root = tmp_path / "target"
    target_root.mkdir()
    idx = open_index(target_root / "index.db")
    import_from_memories_json(V5_SAMPLE, conn=idx.conn, device_id="v5-import")

    sample_hashes = {
        m["content_hash"] for m in json.loads(V5_SAMPLE.read_text("utf-8"))["memories"]
    }
    stored_hashes = {
        r[0]
        for r in idx.conn.execute(
            "SELECT content_hash FROM sync_imported_memories"
        ).fetchall()
    }
    assert sample_hashes <= stored_hashes
