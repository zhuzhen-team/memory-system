"""Tests for `memoryd sync bundle` / `memoryd sync restore`.

The bundle is a portable snapshot of the data root, used for cross-device
migration and offline backup. These tests verify:

1. bundle includes the expected file groups (markdown, profile, db)
2. bundle skips encrypted (.md.enc) by default + can opt-in
3. restore is the inverse — bundle then restore on a fresh root yields
   bitwise identical content
4. restore refuses non-empty target without --force (data-loss guard)
5. restore rejects path-traversal members (security)
"""
from __future__ import annotations

import tarfile
from pathlib import Path

import pytest

from memoryd.sync.bundle import bundle, restore


def _seed_data_root(root: Path) -> None:
    """Populate a minimal but representative data tree for tests."""
    (root / "scopes" / "abc123" / "sessions").mkdir(parents=True)
    (root / "scopes" / "abc123" / "decisions").mkdir(parents=True)
    (root / "scopes" / "abc123" / "sessions" / "s1.md").write_text(
        "session one body", encoding="utf-8"
    )
    (root / "scopes" / "abc123" / "decisions" / "d1.md").write_text(
        "decision body", encoding="utf-8"
    )
    # encrypted file we expect to be skipped by default
    (root / "scopes" / "abc123" / "decisions" / "secret.md.enc").write_bytes(
        b"ciphertext goes here"
    )
    # scope marker files
    (root / "scopes" / "abc123" / ".scope-name").write_text("my-project", encoding="utf-8")
    # profile
    (root / "profile").mkdir()
    (root / "profile" / "identity.md").write_text("# identity body", encoding="utf-8")
    (root / "profile" / "change-reports").mkdir()
    (root / "profile" / "change-reports" / "2026-05.md").write_text(
        "monthly report", encoding="utf-8"
    )
    # fake db + audit chain (canonical layout: root/audit/audit.jsonl)
    (root / "index.db").write_bytes(b"sqlite-binary-data")
    (root / "audit").mkdir()
    (root / "audit" / "audit.jsonl").write_text("{\"ts\":1}\n", encoding="utf-8")
    # noise that should NOT make it into the bundle
    (root / "logs").mkdir()
    (root / "logs" / "noise.log").write_text("host-specific log", encoding="utf-8")


def test_bundle_packs_expected_layout(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _seed_data_root(data)
    out = tmp_path / "snap.tar.gz"
    stats = bundle(out=out, data_root=data)

    assert out.exists()
    assert stats.has_index_db is True
    assert stats.has_audit_log is True
    assert stats.scopes_md == 2
    assert stats.profile_files == 2  # identity + change-report

    with tarfile.open(out, "r:gz") as tar:
        names = set(tar.getnames())
    # markdown + scope marker
    assert "scopes/abc123/sessions/s1.md" in names
    assert "scopes/abc123/decisions/d1.md" in names
    assert "scopes/abc123/.scope-name" in names
    # profile
    assert "profile/identity.md" in names
    assert "profile/change-reports/2026-05.md" in names
    # db + audit chain (canonical path inside data root)
    assert "index.db" in names
    assert "audit/audit.jsonl" in names
    # excluded
    assert not any(n.startswith("logs/") for n in names)


def test_bundle_skips_encrypted_by_default(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _seed_data_root(data)
    out = tmp_path / "no-enc.tar.gz"
    stats = bundle(out=out, data_root=data, include_encrypted=False)
    assert stats.encrypted_skipped == 1
    with tarfile.open(out, "r:gz") as tar:
        assert not any(n.endswith(".md.enc") for n in tar.getnames())


def test_bundle_can_opt_in_to_encrypted(tmp_path: Path) -> None:
    data = tmp_path / "data"
    data.mkdir()
    _seed_data_root(data)
    out = tmp_path / "with-enc.tar.gz"
    stats = bundle(out=out, data_root=data, include_encrypted=True)
    assert stats.encrypted_skipped == 0
    with tarfile.open(out, "r:gz") as tar:
        assert any(n.endswith(".md.enc") for n in tar.getnames())


def test_bundle_missing_data_root_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        bundle(out=tmp_path / "x.tar.gz", data_root=tmp_path / "nope")


def test_restore_roundtrip(tmp_path: Path) -> None:
    """bundle → restore on a fresh root reproduces the markdown + profile."""
    src = tmp_path / "src"
    src.mkdir()
    _seed_data_root(src)
    snap = tmp_path / "snap.tar.gz"
    bundle(out=snap, data_root=src)

    dst = tmp_path / "dst"
    stats = restore(src=snap, data_root=dst)
    assert stats.scopes_md == 2
    assert stats.has_index_db is True
    # Content preserved
    assert (dst / "scopes/abc123/sessions/s1.md").read_text(encoding="utf-8") == "session one body"
    assert (dst / "profile/identity.md").read_text(encoding="utf-8") == "# identity body"
    assert (dst / "index.db").read_bytes() == b"sqlite-binary-data"


def test_restore_refuses_non_empty_target_without_force(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _seed_data_root(src)
    snap = tmp_path / "snap.tar.gz"
    bundle(out=snap, data_root=src)

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "existing.txt").write_text("don't delete me", encoding="utf-8")
    with pytest.raises(FileExistsError):
        restore(src=snap, data_root=dst, force=False)
    # Existing data untouched
    assert (dst / "existing.txt").read_text(encoding="utf-8") == "don't delete me"


def test_restore_force_overwrites(tmp_path: Path) -> None:
    src = tmp_path / "src"
    src.mkdir()
    _seed_data_root(src)
    snap = tmp_path / "snap.tar.gz"
    bundle(out=snap, data_root=src)

    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "stale.txt").write_text("old", encoding="utf-8")
    restore(src=snap, data_root=dst, force=True)
    assert (dst / "scopes/abc123/sessions/s1.md").exists()


def test_restore_rejects_path_traversal(tmp_path: Path) -> None:
    """A malicious bundle with `../etc/passwd` entries must not write outside root."""
    bad = tmp_path / "evil.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="../escaped.txt")
        info.size = 4
        from io import BytesIO
        tar.addfile(info, BytesIO(b"evil"))
    dst = tmp_path / "dst"
    with pytest.raises(ValueError, match="path-traversal"):
        restore(src=bad, data_root=dst)
    # Nothing got written outside dst
    assert not (tmp_path / "escaped.txt").exists()


def test_restore_rejects_sibling_prefix_traversal(tmp_path: Path) -> None:
    """Regression for the startswith-prefix-confusion bypass.

    Old guard ``str(target).startswith(str(root.resolve()))`` let
    ``../dst2/escaped.txt`` slip through when ``root=/.../dst`` because the
    sibling ``/.../dst2`` shares the string prefix ``/.../dst``.
    The fix uses ``Path.relative_to``.
    """
    bad = tmp_path / "evil.tar.gz"
    with tarfile.open(bad, "w:gz") as tar:
        info = tarfile.TarInfo(name="../dst2/escaped.txt")
        info.size = 4
        from io import BytesIO
        tar.addfile(info, BytesIO(b"evil"))
    dst = tmp_path / "dst"
    (tmp_path / "dst2").mkdir()  # the sibling exists, prefix confusion possible
    with pytest.raises(ValueError, match="path-traversal"):
        restore(src=bad, data_root=dst)
    assert not (tmp_path / "dst2" / "escaped.txt").exists()
