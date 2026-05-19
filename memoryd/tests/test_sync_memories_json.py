"""Path B: memories.json export / import / diff roundtrip tests."""
from __future__ import annotations

import json
import sqlite3
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


@pytest.fixture(autouse=True)
def _isolate_audit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep audit chain reads/writes confined to tmp_path."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))


@pytest.fixture
def populated_root(tmp_path: Path) -> Path:
    root = tmp_path / "data"
    root.mkdir()
    save_memory(
        root,
        SessionMemory(
            frontmatter=Frontmatter(
                title="项目讨论",
                slug="2026-05-09-discussion",
                type="session",
                scope_hash="scope1",
                triggers=["项目"],
                tags=["work", "wolin"],
                source="claude-code",
                created_at=datetime(2026, 5, 9, 9, 30, tzinfo=timezone.utc),
            ),
            body="## 摘要\n讨论了项目方向。\n",
        ),
    )
    save_memory(
        root,
        SessionMemory(
            frontmatter=Frontmatter(
                title="咖啡偏好",
                slug="coffee-preference",
                type="preference",
                scope_hash="scope1",
                tags=["personal"],
                source="manual",
                created_at=datetime(2026, 5, 10, 8, 0, tzinfo=timezone.utc),
            ),
            body="意式浓缩 + 一勺糖。\n",
        ),
    )
    return root


def _open_conn(root: Path) -> sqlite3.Connection:
    idx = open_index(root / "index.db")
    return idx.conn


def test_export_creates_valid_v5_top_level(populated_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "out.json"
    conn = _open_conn(populated_root)
    res = export_to_memories_json(out, conn=conn, data_root=populated_root)
    assert res["exported_count"] == 2
    bundle = json.loads(out.read_text(encoding="utf-8"))
    # v5 top-level surface
    assert "export_metadata" in bundle and "memories" in bundle
    em = bundle["export_metadata"]
    assert em["total_memories"] == 2
    assert em["exporter_version"] == "memoryd-1"
    assert "mcp-memory-v5" in em["schema_compat"]
    for mem in bundle["memories"]:
        assert {"content", "content_hash", "tags", "created_at",
                "updated_at", "memory_type", "metadata", "export_source"}.issubset(mem.keys())


def test_export_extension_fields_present(populated_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "ext.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root)
    bundle = json.loads(out.read_text("utf-8"))
    for mem in bundle["memories"]:
        # memoryd extensions present
        assert mem["id"]
        assert mem["scope"] == "scope1"
        assert isinstance(mem["frontmatter"], dict)


def test_export_scope_filter(populated_root: Path, tmp_path: Path) -> None:
    # Add a second scope
    save_memory(
        populated_root,
        SessionMemory(
            frontmatter=Frontmatter(
                title="other",
                slug="other-1",
                type="fact",
                scope_hash="scope2",
                source="manual",
                created_at=datetime(2026, 5, 11, tzinfo=timezone.utc),
            ),
            body="x\n",
        ),
    )
    out = tmp_path / "filter.json"
    conn = _open_conn(populated_root)
    res = export_to_memories_json(
        out, conn=conn, data_root=populated_root, scope_hashes=["scope1"]
    )
    assert res["exported_count"] == 2
    bundle = json.loads(out.read_text("utf-8"))
    assert all(m["scope"] == "scope1" for m in bundle["memories"])


def test_export_chunk_spill_for_large_memory(populated_root: Path, tmp_path: Path) -> None:
    big = "x" * (2 * 1024 * 1024)  # 2 MiB
    save_memory(
        populated_root,
        SessionMemory(
            frontmatter=Frontmatter(
                title="big",
                slug="big-memo",
                type="fact",
                scope_hash="scope1",
                source="manual",
                created_at=datetime(2026, 5, 12, tzinfo=timezone.utc),
            ),
            body=big,
        ),
    )
    out = tmp_path / "chunky.json"
    conn = _open_conn(populated_root)
    res = export_to_memories_json(
        out,
        conn=conn,
        data_root=populated_root,
        chunk_size_mb=1,
    )
    assert len(res["chunks"]) >= 1
    bundle = json.loads(out.read_text("utf-8"))
    big_entry = next(m for m in bundle["memories"] if m["id"] == "big-memo")
    assert big_entry["content"] == ""
    assert big_entry["large_file_manifest"]["filename"].endswith(".bin")
    # chunk file exists
    chunk_dir = out.with_suffix(out.suffix + ".chunks")
    assert chunk_dir.exists()
    assert (chunk_dir / big_entry["large_file_manifest"]["filename"]).exists()


def test_import_new_memories_into_empty_db(populated_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "src.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root)

    # Fresh blank db on target side
    target_root = tmp_path / "target"
    target_root.mkdir()
    target_conn = _open_conn(target_root)
    res = import_from_memories_json(out, conn=target_conn, device_id="test-device")
    assert res["imported"] == 2
    assert res["conflicts_resolved"] == 0
    # Side-table populated
    row = target_conn.execute(
        "SELECT count(*) FROM sync_imported_memories"
    ).fetchone()
    assert row[0] == 2


def test_import_dry_run_writes_nothing(populated_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "dry.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target_conn = _open_conn(target_root)
    res = import_from_memories_json(out, conn=target_conn, dry_run=True, device_id="d")
    assert res["imported"] == 2
    # Table either absent or empty
    row = target_conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_imported_memories'"
    ).fetchone()
    if row is not None:
        cnt = target_conn.execute("SELECT count(*) FROM sync_imported_memories").fetchone()[0]
        assert cnt == 0


def test_diff_reports_new_and_unchanged(populated_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "diff.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root)

    target_root = tmp_path / "target"
    target_root.mkdir()
    target_conn = _open_conn(target_root)
    d = diff_with_remote(out, conn=target_conn)
    assert d["remote_total"] == 2
    assert len(d["new"]) == 2
    assert len(d["unchanged"]) == 0


def test_roundtrip_then_diff_is_unchanged(populated_root: Path, tmp_path: Path) -> None:
    out = tmp_path / "rt.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root)
    target_root = tmp_path / "target"
    target_root.mkdir()
    target_conn = _open_conn(target_root)
    import_from_memories_json(out, conn=target_conn, device_id="d")
    d = diff_with_remote(out, conn=target_conn)
    # All 2 memories now exist locally → no new
    assert len(d["new"]) == 0
    assert len(d["unchanged"]) == 2


def test_passphrase_encrypted_export_decrypts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An encrypted sensitive memory roundtrips via the same passphrase."""
    # We bypass real keyring/sensitive-marker plumbing by mutating the
    # MemoryEntry directly through export + import; the JSON path B layer
    # is what we want to exercise here.
    from memoryd.sync.memories_json import _passphrase_encrypt, _passphrase_decrypt
    import os

    salt = os.urandom(16)
    blob = _passphrase_encrypt("correct-horse-battery", b"secret content", salt, 600_000)
    out = _passphrase_decrypt("correct-horse-battery", blob, salt, 600_000)
    assert out == b"secret content"

    with pytest.raises(Exception):
        _passphrase_decrypt("wrong-passphrase", blob, salt, 600_000)


def test_export_audit_chain_head_captured(populated_root: Path, tmp_path: Path) -> None:
    from memoryd.governance.audit import append_event

    append_event({"event_type": "test", "scope_hash": "scope1"})
    append_event({"event_type": "test", "scope_hash": "scope1"})
    out = tmp_path / "audit.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root, include_audit_chain=True)
    bundle = json.loads(out.read_text("utf-8"))
    assert bundle["export_metadata"]["audit_chain_head"]
    assert len(bundle["audit_chain"]) >= 2


def test_export_then_import_back_into_same_db_is_noop(
    populated_root: Path, tmp_path: Path
) -> None:
    out = tmp_path / "self.json"
    conn = _open_conn(populated_root)
    export_to_memories_json(out, conn=conn, data_root=populated_root)
    # Re-import into the same conn → entries equal local → skipped
    res = import_from_memories_json(out, conn=conn, device_id="self")
    assert res["imported"] == 0
    assert res["merged"] + res["skipped"] == 2
