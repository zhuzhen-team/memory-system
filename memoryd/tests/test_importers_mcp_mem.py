import json

import pytest

from memoryd.importers.mcp_mem import run, _map_type


SAMPLE = [
    {"id": "1", "content": "DB is postgres 15",
     "metadata": {"tags": ["db", "infra"], "type": "fact",
                  "created_at": "2026-01-01T00:00:00+00:00"}},
    {"id": "2", "content": "Logo: deep blue + silver",
     "metadata": {"tags": ["logo"], "type": "decision",
                  "created_at": "2026-02-01T00:00:00+00:00"}},
    {"id": "3", "content": "Don't push --force to main",
     "metadata": {"tags": ["ci"], "type": "warning"}},
]


def test_map_type_keywords():
    assert _map_type("decision") == "decision"
    assert _map_type("user.preference") == "preference"
    assert _map_type("warning") == "warning"
    assert _map_type("process") == "playbook"
    assert _map_type(None) == "fact"
    assert _map_type("") == "fact"


def test_run_writes_each_memory(tmp_path):
    p = tmp_path / "memories.json"
    p.write_text(json.dumps(SAMPLE))
    data_root = tmp_path / "data"
    report = run(p, data_root, scope_hash="h1")
    assert report.parsed == 3
    assert report.written == 3
    assert report.by_type == {"fact": 1, "decision": 1, "warning": 1}


def test_run_handles_wrapped_dict(tmp_path):
    """Some mcp-memory-service exports use {"memories": [...]}."""
    p = tmp_path / "memories.json"
    p.write_text(json.dumps({"memories": SAMPLE}))
    data_root = tmp_path / "data"
    report = run(p, data_root, scope_hash="h1")
    assert report.parsed == 3


def test_run_skips_invalid_entries(tmp_path):
    p = tmp_path / "memories.json"
    p.write_text(json.dumps([
        {"id": "ok", "content": "good content here for one tag",
         "metadata": {"tags": ["foo", "bar"]}},
        {"no_content": True},
        "string-not-dict",
    ]))
    data_root = tmp_path / "data"
    report = run(p, data_root, scope_hash="h1")
    assert report.parsed == 1
    assert report.written == 1


def test_run_invalid_json_returns_empty(tmp_path):
    p = tmp_path / "memories.json"
    p.write_text("{not json")
    data_root = tmp_path / "data"
    report = run(p, data_root, scope_hash="h1")
    assert report.parsed == 0


def test_run_dry_run(tmp_path):
    p = tmp_path / "memories.json"
    p.write_text(json.dumps(SAMPLE))
    data_root = tmp_path / "data"
    report = run(p, data_root, scope_hash="h1", dry_run=True)
    assert report.dry_run is True
    assert report.parsed == 3
    assert report.written == 3
    assert not (data_root / "scopes" / "h1").exists()


def test_run_uses_meta_created_at(tmp_path):
    """created_at in entry should reflect metadata.created_at when present."""
    p = tmp_path / "memories.json"
    p.write_text(json.dumps([SAMPLE[0]]))  # has created_at
    data_root = tmp_path / "data"
    run(p, data_root, scope_hash="h1")
    md_paths = list((data_root / "scopes" / "h1").rglob("*.md"))
    assert len(md_paths) == 1
    text = md_paths[0].read_text()
    assert "2026-01-01" in text
