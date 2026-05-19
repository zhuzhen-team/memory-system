"""Scope resolution tests."""
import subprocess
from pathlib import Path

from memoryd.scope import resolve_scope_root, scope_hash


def test_scope_hash_is_deterministic():
    h1 = scope_hash("/Users/abble/projects/wolin")
    h2 = scope_hash("/Users/abble/projects/wolin")
    assert h1 == h2
    assert len(h1) == 12


def test_scope_hash_differs_per_path():
    h_a = scope_hash("/Users/abble/projects/wolin")
    h_b = scope_hash("/Users/abble/projects/zhuzhen")
    assert h_a != h_b


def test_resolve_scope_root_prefers_git_parent(tmp_path: Path):
    """A nested working dir under a git root resolves to the git root."""
    git_root = tmp_path / "myproject"
    git_root.mkdir()
    subprocess.run(["git", "init"], cwd=git_root, check=True, capture_output=True)
    nested = git_root / "src" / "submodule"
    nested.mkdir(parents=True)

    resolved = resolve_scope_root(nested)
    assert resolved == git_root.resolve()


def test_resolve_scope_root_falls_back_to_cwd_when_no_git(tmp_path: Path):
    """Non-git directory resolves to itself."""
    plain = tmp_path / "plain"
    plain.mkdir()
    resolved = resolve_scope_root(plain)
    assert resolved == plain.resolve()


def test_resolve_scope_root_when_start_is_git_root(tmp_path: Path):
    """When `start` itself is a git root, return it (don't walk up)."""
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    assert resolve_scope_root(tmp_path) == tmp_path.resolve()
