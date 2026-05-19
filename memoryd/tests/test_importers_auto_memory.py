import pytest

from memoryd.importers.auto_memory import run, _parse_frontmatter, _TYPE_MAP


SAMPLE_FACT = """---
name: db-version
description: postgres version note
metadata:
  type: user
---

We use postgres 15 for jsonb support.
"""

SAMPLE_FEEDBACK = """---
name: pr-merge-pref
metadata:
  type: feedback
---

Prefer merge commits over squash for PRs touching docs.
"""


def test_parse_frontmatter_basic():
    fm, body = _parse_frontmatter(SAMPLE_FACT)
    assert fm.get("name") == "db-version"
    assert "postgres" in body


def test_parse_frontmatter_nested_metadata_type():
    fm, _ = _parse_frontmatter(SAMPLE_FACT)
    assert fm.get("metadata.type") == "user"


def test_run_skips_memory_md(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "MEMORY.md").write_text("# index\nlinks")
    (mem_dir / "real.md").write_text(SAMPLE_FACT)
    data_root = tmp_path / "data"
    report = run(mem_dir, data_root, scope_hash="h1")
    assert report.parsed == 1
    assert (data_root / "scopes" / "h1" / "facts").exists()


def test_type_map_feedback_to_preference(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "pref.md").write_text(SAMPLE_FEEDBACK)
    data_root = tmp_path / "data"
    report = run(mem_dir, data_root, scope_hash="h1")
    assert "preference" in report.by_type
    assert (data_root / "scopes" / "h1" / "preferences").exists()


def test_missing_dir_returns_empty(tmp_path):
    report = run(tmp_path / "nope", tmp_path / "data", "h1")
    assert report.parsed == 0
    assert report.written == 0


def test_dry_run_no_write(tmp_path):
    mem_dir = tmp_path / "memory"
    mem_dir.mkdir()
    (mem_dir / "x.md").write_text(SAMPLE_FACT)
    data_root = tmp_path / "data"
    report = run(mem_dir, data_root, scope_hash="h1", dry_run=True)
    assert report.parsed == 1
    # dry_run=True 也 +1 written（"would write"语义)
    assert report.written == 1
    assert not (data_root / "scopes" / "h1").exists()
