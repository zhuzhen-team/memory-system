import json
from pathlib import Path

from memoryd import cli


def test_cli_import_claude_md_dry_run(tmp_path, monkeypatch, capsys):
    md = tmp_path / "CLAUDE.md"
    md.write_text("## Foo\nbody one\n## Bar\nbody two\n")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path / "data")
    args = type("A", (), {
        "import_kind": "claude-md",
        "path": md,
        "scope": "h1",
        "dry_run": True,
        "force": False,
        "source_tag": None,
    })()
    rc = cli._cmd_import(args)
    assert rc == 0
    captured = capsys.readouterr().out
    parsed = json.loads(captured)
    assert parsed["kind"] == "claude-md"
    assert parsed["parsed"] == 2
    assert parsed["dry_run"] is True
    assert parsed["scope_hash"] == "h1"


def test_cli_import_unknown_kind(monkeypatch, capsys):
    monkeypatch.setattr("memoryd.cli._data_root", lambda: Path("/tmp"))
    args = type("A", (), {
        "import_kind": "weird-kind",
        "path": Path("/x"),
        "scope": "h1",
        "dry_run": False, "force": False, "source_tag": None,
    })()
    rc = cli._cmd_import(args)
    assert rc == 2


def test_cli_import_uses_cwd_scope_when_not_specified(tmp_path, monkeypatch, capsys):
    """When --scope omitted, falls back to scope_hash(resolve_scope_root(cwd))."""
    md = tmp_path / "CLAUDE.md"
    md.write_text("## a\nx\n")
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path / "data")
    # 让 cwd scope_hash 是 "from_cwd"：mock 两个函数
    monkeypatch.setattr(
        "memoryd.scope.resolve_scope_root", lambda p: Path("/tmp/proj")
    )
    monkeypatch.setattr(
        "memoryd.scope.scope_hash", lambda p: "from_cwd"
    )
    args = type("A", (), {
        "import_kind": "claude-md",
        "path": md,
        "scope": None,
        "dry_run": True, "force": False, "source_tag": None,
    })()
    cli._cmd_import(args)
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["scope_hash"] == "from_cwd"


def test_cli_import_auto_memory_writes(tmp_path, monkeypatch, capsys):
    """Smoke wire-up to auto_memory importer."""
    mem_dir = tmp_path / "mem"
    mem_dir.mkdir()
    (mem_dir / "real.md").write_text(
        "---\nname: x\nmetadata:\n  type: feedback\n---\n\nbody y\n"
    )
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path / "data")
    args = type("A", (), {
        "import_kind": "auto-memory",
        "path": mem_dir,
        "scope": "h1",
        "dry_run": False, "force": False, "source_tag": None,
    })()
    rc = cli._cmd_import(args)
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["written"] == 1


def test_cli_import_mcp_memory_service(tmp_path, monkeypatch, capsys):
    """Wire-up to mcp_mem importer."""
    p = tmp_path / "memories.json"
    p.write_text('[{"id":"x","content":"hello","metadata":{"tags":["t1","t2"]}}]')
    monkeypatch.setattr("memoryd.cli._data_root", lambda: tmp_path / "data")
    args = type("A", (), {
        "import_kind": "mcp-memory-service",
        "path": p,
        "scope": "h1",
        "dry_run": False, "force": False, "source_tag": None,
    })()
    rc = cli._cmd_import(args)
    assert rc == 0
    parsed = json.loads(capsys.readouterr().out)
    assert parsed["written"] == 1
