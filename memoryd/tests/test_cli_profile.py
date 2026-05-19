"""Plan 10 Task: `memoryd profile ...` CLI subcommands."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd import cli
from memoryd.index import open_index
from memoryd.profile import ProfileStore, identity_path, increment_trigger


def _args(**kw: object) -> argparse.Namespace:
    return argparse.Namespace(**kw)


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point every disk write into tmp_path."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(tmp_path / "profile"))
    monkeypatch.setattr(cli, "_data_root", lambda: tmp_path)
    return tmp_path


class _FakeLLM:
    """Sync stub that mimics LegacyLLMProvider.complete()."""

    def __init__(self, text: str = "新 identity 内容\n\n> change_summary: 测试摘要\n"):
        self.text = text
        self.calls: list[tuple[str, str]] = []

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.calls.append((system, user))
        return self.text


# ---------------------------------------------------------------------------
# profile show / history / diff
# ---------------------------------------------------------------------------


def test_profile_show_empty(tmp_path: Path, capsys):
    rc = cli._cmd_profile_show(_args(max_chars=2000))
    err = capsys.readouterr().err
    assert rc == 0
    assert "尚无 identity.md" in err or "no identity" in err.lower() or "尚无" in err


def test_profile_show_reads_identity(tmp_path: Path, capsys):
    p = identity_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("# identity\n\nhello\n", encoding="utf-8")
    rc = cli._cmd_profile_show(_args(max_chars=2000))
    out = capsys.readouterr().out
    assert rc == 0
    assert "hello" in out


def _seed_versions(tmp_path: Path, contents: list[str]) -> None:
    idx = open_index(tmp_path / "index.db")
    try:
        store = ProfileStore(idx.conn)
        prev = None
        for i, c in enumerate(contents, start=1):
            v = store.save_version(
                c,
                trigger="manual",
                prev_version=prev,
                change_summary=f"summary {i}",
                sources_count=i,
                written_at=datetime(2026, 5, i, tzinfo=timezone.utc),
            )
            prev = v
    finally:
        idx.close()


def test_profile_history_table(tmp_path: Path, capsys):
    _seed_versions(tmp_path, ["v1 body", "v2 body"])
    rc = cli._cmd_profile_history(_args(limit=20, as_json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "summary 1" in out
    assert "summary 2" in out


def test_profile_history_json(tmp_path: Path, capsys):
    _seed_versions(tmp_path, ["v1 body", "v2 body"])
    rc = cli._cmd_profile_history(_args(limit=20, as_json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert {r["version_num"] for r in data} == {1, 2}


def test_profile_diff_versions(tmp_path: Path, capsys):
    _seed_versions(tmp_path, ["line a\nline b\n", "line a\nline B'\n"])
    rc = cli._cmd_profile_diff(_args(from_=1, to=2))
    out = capsys.readouterr().out
    assert rc == 0
    # Unified diff markers + the changed lines.
    assert "+line B'" in out
    assert "-line b" in out


def test_profile_diff_missing_version(tmp_path: Path, capsys):
    _seed_versions(tmp_path, ["only one"])
    rc = cli._cmd_profile_diff(_args(from_=1, to=99))
    err = capsys.readouterr().err
    assert rc == 1
    assert "not found" in err


# ---------------------------------------------------------------------------
# profile rewrite
# ---------------------------------------------------------------------------


def test_profile_rewrite_dry_run(tmp_path: Path, monkeypatch, capsys):
    """dry-run should not write disk or DB rows; LLM is mocked."""
    fake = _FakeLLM(text="新版 identity\n\n> change_summary: 周复盘\n")

    # Patch get_provider so the cli handler picks up our stub instead of
    # hitting the network.
    monkeypatch.setattr(
        "memoryd.llm.get_provider", lambda: fake, raising=True
    )

    rc = cli._cmd_profile_rewrite(
        _args(dry_run=True, window_days=7, max_words=200)
    )
    out = capsys.readouterr().out
    assert rc == 0
    assert "新版 identity" in out
    # No on-disk identity.md and no profile_versions row created.
    assert not identity_path().exists()

    idx = open_index(tmp_path / "index.db")
    try:
        store = ProfileStore(idx.conn)
        assert store.latest_version() is None
    finally:
        idx.close()


def test_profile_rewrite_persists(tmp_path: Path, monkeypatch, capsys):
    fake = _FakeLLM(text="新画像\n\n> change_summary: 持久化测试\n")
    monkeypatch.setattr("memoryd.llm.get_provider", lambda: fake, raising=True)

    rc = cli._cmd_profile_rewrite(
        _args(dry_run=False, window_days=7, max_words=200)
    )
    err = capsys.readouterr().err
    assert rc == 0
    assert "profile rewrite" in err and "v1" in err
    # identity.md exists, profile_versions has one row.
    assert identity_path().exists()
    idx = open_index(tmp_path / "index.db")
    try:
        store = ProfileStore(idx.conn)
        latest = store.latest_version()
        assert latest is not None
        assert latest.version_num == 1
        assert "新画像" in latest.content_md
    finally:
        idx.close()


def test_profile_rewrite_no_llm_credentials(tmp_path: Path, monkeypatch, capsys):
    """When get_provider raises LLMUnavailable, we exit 1 with a helpful hint."""
    from memoryd.llm import LLMUnavailable

    def _unavailable():
        raise LLMUnavailable("ANTHROPIC_API_KEY env not set")

    monkeypatch.setattr("memoryd.llm.get_provider", _unavailable, raising=True)

    rc = cli._cmd_profile_rewrite(
        _args(dry_run=False, window_days=7, max_words=200)
    )
    err = capsys.readouterr().err
    assert rc == 1
    assert "ANTHROPIC_API_KEY" in err or "configure another provider" in err


# ---------------------------------------------------------------------------
# profile report
# ---------------------------------------------------------------------------


def test_profile_report_dry_run(tmp_path: Path, monkeypatch, capsys):
    fake = _FakeLLM(text="# 月度报告\n\n内容。\n")
    monkeypatch.setattr("memoryd.llm.get_provider", lambda: fake, raising=True)

    rc = cli._cmd_profile_report(
        _args(
            month="2026-05",
            current_month=False,
            dry_run=True,
            regenerate=False,
        )
    )
    out = capsys.readouterr().out
    err = capsys.readouterr().err
    assert rc == 0
    assert "月度报告" in out


def test_profile_report_persists(tmp_path: Path, monkeypatch, capsys):
    fake = _FakeLLM(text="# 月度报告\n\n本月内容\n")
    monkeypatch.setattr("memoryd.llm.get_provider", lambda: fake, raising=True)

    rc = cli._cmd_profile_report(
        _args(
            month="2026-05",
            current_month=False,
            dry_run=False,
            regenerate=False,
        )
    )
    assert rc == 0
    # Re-run without --regenerate: should not re-call LLM but should print existing.
    idx = open_index(tmp_path / "index.db")
    try:
        store = ProfileStore(idx.conn)
        existing = store.get_change_report("2026-05")
        assert existing is not None
        assert "本月内容" in existing["content_md"]
    finally:
        idx.close()


def test_profile_report_requires_month(tmp_path: Path, capsys):
    rc = cli._cmd_profile_report(
        _args(
            month=None,
            current_month=False,
            dry_run=True,
            regenerate=False,
        )
    )
    err = capsys.readouterr().err
    assert rc == 2
    assert "--month" in err


# ---------------------------------------------------------------------------
# profile trends
# ---------------------------------------------------------------------------


def test_profile_trends_renders_section(tmp_path: Path, capsys):
    # Seed some trigger_stats rows so the section has content.
    idx = open_index(tmp_path / "index.db")
    try:
        increment_trigger(idx.conn, "rust", "_global")
        increment_trigger(idx.conn, "rust", "_global")
        increment_trigger(idx.conn, "fastapi", "_global")
    finally:
        idx.close()

    rc = cli._cmd_profile_trends(_args(window_days=7, as_json=False))
    out = capsys.readouterr().out
    assert rc == 0
    assert "趋势" in out or "trends" in out.lower()
    assert "rust" in out


def test_profile_trends_json(tmp_path: Path, capsys):
    idx = open_index(tmp_path / "index.db")
    try:
        increment_trigger(idx.conn, "rust", "_global")
    finally:
        idx.close()

    rc = cli._cmd_profile_trends(_args(window_days=7, as_json=True))
    out = capsys.readouterr().out
    assert rc == 0
    data = json.loads(out)
    assert "top_triggers" in data
    assert any(t["trigger"] == "rust" for t in data["top_triggers"])
