from memoryd.importers.agents_md import run as agents_run


def test_run_sets_imported_agents_md_source(tmp_path):
    md = tmp_path / "AGENTS.md"
    md.write_text(
        "## How to deploy\nstep 1 step 2\n## Warning: be careful\nthings\n"
    )
    data_root = tmp_path / "data"
    report = agents_run(md, data_root, scope_hash="h1")
    assert report.written == 2
    found_source = False
    for md_out in (data_root / "scopes" / "h1").rglob("*.md"):
        if "imported-agents-md" in md_out.read_text():
            found_source = True
            break
    assert found_source


def test_run_dry_run(tmp_path):
    md = tmp_path / "AGENTS.md"
    md.write_text("## Foo\nbar\n")
    data_root = tmp_path / "data"
    report = agents_run(md, data_root, scope_hash="h1", dry_run=True)
    assert report.dry_run is True
    assert report.parsed == 1
    assert not (data_root / "scopes" / "h1").exists()


def test_run_custom_source_tag(tmp_path):
    md = tmp_path / "AGENTS.md"
    md.write_text("## Foo\nbar\n")
    data_root = tmp_path / "data"
    agents_run(md, data_root, scope_hash="h1", source_tag="my-custom")
    found = False
    for md_out in (data_root / "scopes" / "h1").rglob("*.md"):
        if "my-custom" in md_out.read_text():
            found = True
            break
    assert found
