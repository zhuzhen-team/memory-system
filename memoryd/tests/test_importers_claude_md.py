import pytest

from memoryd.importers.claude_md import (
    derive_triggers,
    infer_type,
    parse_sections,
    to_entries,
    run,
)


SAMPLE = """\
# Project Notes

Some intro.

## Database decisions

We use postgres 15. Reason: jsonb support.

## How to deploy

1. Push to main
2. CI runs
3. Tag v0.x.y

## Warning: do not push --force

It triggers double deploys.

### prefer merge over squash

For PRs touching docs.
"""


def test_parse_sections_split_by_h2_and_h3():
    secs = parse_sections(SAMPLE)
    assert len(secs) == 4
    assert secs[0].heading == "Database decisions"
    assert secs[2].heading.startswith("Warning")
    assert secs[3].level == 3


def test_infer_type_keywords():
    assert infer_type("Database decisions") == "decision"
    assert infer_type("How to deploy") == "playbook"
    assert infer_type("Warning: do not push --force") == "warning"
    assert infer_type("prefer merge over squash") == "preference"
    assert infer_type("Random fact about life") == "fact"


def test_derive_triggers_at_least_two():
    assert len(derive_triggers("Database decisions")) >= 2
    assert len(derive_triggers("X")) >= 2


def test_to_entries_round_trip():
    entries = to_entries(SAMPLE)
    assert len(entries) == 4
    types = sorted(e.type for e in entries)
    assert types == ["decision", "playbook", "preference", "warning"]
    assert all(e.source == "imported-claude-md" for e in entries)
    assert all(len(e.triggers) >= 2 for e in entries)


def test_to_entries_unique_slugs_for_duplicate_headings():
    text = "## Foo\nbody 1\n## Foo\nbody 2\n"
    entries = to_entries(text)
    slugs = [e.slug for e in entries]
    assert len(set(slugs)) == 2


def test_run_writes_to_data_root(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text(SAMPLE)
    data_root = tmp_path / "data"
    report = run(md, data_root, scope_hash="h1")
    assert report.parsed == 4
    assert report.written == 4
    assert report.skipped == 0
    assert (data_root / "scopes" / "h1" / "decisions").exists()
    assert (data_root / "scopes" / "h1" / "warnings").exists()


def test_run_dry_run_writes_nothing(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text(SAMPLE)
    data_root = tmp_path / "data"
    report = run(md, data_root, scope_hash="h1", dry_run=True)
    assert report.dry_run is True
    assert report.parsed == 4
    assert not (data_root / "scopes" / "h1").exists()


def test_run_skips_duplicate_without_force(tmp_path):
    md = tmp_path / "CLAUDE.md"
    md.write_text(SAMPLE)
    data_root = tmp_path / "data"
    run(md, data_root, scope_hash="h1")
    report = run(md, data_root, scope_hash="h1")
    assert report.written == 0
    assert report.skipped == 4
