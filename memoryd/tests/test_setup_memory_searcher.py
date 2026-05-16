import pytest

from memoryd.setup import install_memory_searcher


def test_install_creates_file_in_target(tmp_path):
    target = tmp_path / ".claude" / "agents"
    out = install_memory_searcher(target_dir=target)
    assert out.exists()
    assert out.name == "memory-searcher.md"
    text = out.read_text()
    assert "memory-searcher" in text
    assert "claude-haiku" in text
    assert "tools: Read, Grep" in text


def test_install_default_target_under_home(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    out = install_memory_searcher()
    assert out == tmp_path / ".claude" / "agents" / "memory-searcher.md"


def test_install_refuses_overwrite_without_force(tmp_path):
    target = tmp_path / "a"
    install_memory_searcher(target_dir=target)
    with pytest.raises(FileExistsError):
        install_memory_searcher(target_dir=target)


def test_install_overwrites_with_force(tmp_path):
    target = tmp_path / "a"
    out = install_memory_searcher(target_dir=target)
    out.write_text("# corrupted")
    out2 = install_memory_searcher(target_dir=target, force=True)
    assert "memory-searcher" in out2.read_text()
    assert "claude-haiku" in out2.read_text()


def test_install_creates_missing_target_dir(tmp_path):
    """Should mkdir parents=True."""
    target = tmp_path / "new" / "deep" / "tree" / "agents"
    out = install_memory_searcher(target_dir=target)
    assert out.exists()
    assert out.parent == target
