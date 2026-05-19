"""Tests for ``memoryd.profile.identity`` — weekly LLM rewrite.

The LLM is always mocked. We cover:
- atomic write to identity.md
- snapshot of prior version to identity.md.history/<isoweek>.md
- ProfileVersion row inserted with diff + summary + sources count
- sensitive scopes are excluded from LLM input
- ``read_current_identity`` truncates by paragraph boundary
- dry_run returns preview without touching disk or DB
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from memoryd.index import open_index
from memoryd.profile import identity as identity_mod
from memoryd.profile.identity import (
    _truncate_by_paragraph,
    _truncate_by_words,
    read_current_identity,
    rewrite_identity_weekly,
)
from memoryd.profile.store import ProfileStore
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def profile_dir(tmp_path: Path, monkeypatch) -> Path:
    """Redirect identity.md / history dirs to ``tmp_path``."""
    d = tmp_path / "profile"
    monkeypatch.setenv("MEMORYD_PROFILE_DIR", str(d))
    return d


@pytest.fixture
def memory_root_with_index(tmp_path: Path) -> Path:
    root = tmp_path / "memoryd_data"
    root.mkdir()
    return root


def _save_long_term(
    root: Path,
    *,
    slug: str,
    type_: str,
    title: str,
    scope_hash: str = "scope-main",
    triggers: list[str] | None = None,
    created_at: datetime | None = None,
    recall_count: int = 0,
    body: str = "body",
) -> None:
    mem = SessionMemory(
        frontmatter=Frontmatter(
            title=title,
            slug=slug,
            type=type_,
            scope_hash=scope_hash,
            source="manual",
            created_at=created_at or datetime.now(timezone.utc),
            triggers=triggers or [],
            recall_count=recall_count,
        ),
        body=body,
    )
    save_memory(root, mem)


class FakeLLM:
    """Mock LLM provider matching the ``LLMProvider`` protocol."""

    def __init__(self, response: str) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.calls.append({"system": system, "user": user, "model": model})
        return self.response


# ---------------------------------------------------------------------------
# read_current_identity
# ---------------------------------------------------------------------------


def test_read_current_identity_returns_empty_when_missing(profile_dir: Path):
    assert read_current_identity() == ""


def test_read_current_identity_returns_file_contents(profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    (profile_dir / "identity.md").write_text("hello\n", encoding="utf-8")
    assert read_current_identity() == "hello\n"


def test_read_current_identity_truncates_by_paragraph(profile_dir: Path):
    profile_dir.mkdir(parents=True, exist_ok=True)
    text = ("para1\n\n" + "para2 " * 200 + "\n\npara3\n").strip()
    (profile_dir / "identity.md").write_text(text, encoding="utf-8")
    out = read_current_identity(max_chars=20)
    assert len(out) <= 20 + 1  # +1 for trailing newline added by truncator
    assert "para1" in out


def test_truncate_by_paragraph_prefers_paragraph_break():
    text = "first paragraph here\n\nsecond paragraph here that is longer"
    out = _truncate_by_paragraph(text, 25)
    assert out.rstrip() == "first paragraph here"


def test_truncate_by_words_respects_budget():
    text = "paragraph one is short.\n\n" + "word " * 100 + "\n\nfinal."
    out = _truncate_by_words(text, max_words=10)
    assert "paragraph one" in out
    # Should not have included the 100-word block.
    assert out.count("word") < 100


# ---------------------------------------------------------------------------
# rewrite_identity_weekly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rewrite_writes_identity_md_and_inserts_version(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    root = memory_root_with_index
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    # Seed a single recent long-term entry so the LLM has signals.
    _save_long_term(
        root, slug="d-2026-05-15-rust", type_="decision",
        title="选 Rust 做 cli", created_at=now - timedelta(days=2),
        triggers=["rust", "cli"],
    )
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)

    llm = FakeLLM(
        response=(
            "# 用户画像\n\n"
            "倾向 Rust 系工具链。\n\n"
            "> change_summary: 新增 Rust CLI 偏好\n"
        )
    )

    version = await rewrite_identity_weekly(
        idx.conn,
        store,
        llm=llm,
        sources_window_days=7,
        now=now,
    )
    idx.close()

    assert version.version_num == 1
    assert version.trigger == "weekly_cron"
    assert version.sources_count >= 1
    assert version.change_summary == "新增 Rust CLI 偏好"
    assert "Rust CLI 偏好" not in version.content_md  # marker line is stripped
    assert version.content_md.startswith("# 用户画像")

    on_disk = (profile_dir / "identity.md").read_text(encoding="utf-8")
    assert on_disk == version.content_md
    assert llm.calls, "LLM should be called once"


@pytest.mark.asyncio
async def test_rewrite_snapshots_previous_version_to_history(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    root = memory_root_with_index
    now = datetime(2026, 5, 19, 12, 0, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)

    # Seed an existing identity.md + a prior profile_versions row.
    profile_dir.mkdir(parents=True, exist_ok=True)
    prev = "# old identity\n\nold line\n"
    (profile_dir / "identity.md").write_text(prev, encoding="utf-8")
    store.save_version(prev, trigger="manual", sources_count=0)

    llm = FakeLLM(response="# new identity\n\nnew line\n")
    v2 = await rewrite_identity_weekly(idx.conn, store, llm=llm, now=now)
    idx.close()

    assert v2.version_num == 2
    history_dir = profile_dir / "identity.md.history"
    assert history_dir.exists()
    snapshots = list(history_dir.iterdir())
    assert len(snapshots) == 1
    assert snapshots[0].read_text(encoding="utf-8") == prev


@pytest.mark.asyncio
async def test_rewrite_includes_diff_when_previous_exists(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    root = memory_root_with_index
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)
    store.save_version("# v1\nfoo\n", trigger="manual", sources_count=0)
    llm = FakeLLM(response="# v2\nbar\n")
    v = await rewrite_identity_weekly(idx.conn, store, llm=llm, now=now)
    idx.close()
    assert v.diff_from_prev is not None
    assert "+bar" in v.diff_from_prev or "+# v2" in v.diff_from_prev


@pytest.mark.asyncio
async def test_rewrite_excludes_sensitive_scope_memories(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    """A long-term entry in a sensitive scope must NOT appear in LLM prompt."""
    root = memory_root_with_index
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    # Register sensitive scope BEFORE saving — so save_memory flips
    # the scope_sensitive flag.
    idx.register_sensitive_scope("sensitive-scope-hash", "/fake/private")
    idx.close()

    # Now save one memory in sensitive scope, one in normal scope.
    _save_long_term(
        root, slug="public-1", type_="preference", title="公开偏好",
        scope_hash="public-scope", created_at=now - timedelta(days=1),
    )
    # save_memory will write encrypted .md.enc for the sensitive scope.
    _save_long_term(
        root, slug="private-1", type_="preference", title="私密偏好",
        scope_hash="sensitive-scope-hash", created_at=now - timedelta(days=1),
    )

    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)
    llm = FakeLLM(response="# profile\n\nfoo\n")
    await rewrite_identity_weekly(idx.conn, store, llm=llm, now=now)
    idx.close()

    assert llm.calls, "LLM should be called"
    user_prompt = llm.calls[0]["user"]
    assert "公开偏好" in user_prompt
    assert "私密偏好" not in user_prompt


@pytest.mark.asyncio
async def test_rewrite_dry_run_does_not_touch_disk_or_db(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    root = memory_root_with_index
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)
    llm = FakeLLM(response="# preview\n")
    preview = await rewrite_identity_weekly(
        idx.conn, store, llm=llm, dry_run=True, now=now
    )

    assert isinstance(preview, dict)
    assert preview["content_md"].startswith("# preview")
    assert not (profile_dir / "identity.md").exists()
    assert store.latest_version() is None
    idx.close()


@pytest.mark.asyncio
async def test_rewrite_accepts_sync_or_async_llm(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    """The LLM mock can be sync (returns str) — _maybe_await handles both."""
    root = memory_root_with_index
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)

    class SyncLLM:
        def complete(self, *, system, user, model=None):
            return "# sync result\n"

    v = await rewrite_identity_weekly(idx.conn, store, llm=SyncLLM(), now=now)
    idx.close()
    assert v.content_md.startswith("# sync result")


@pytest.mark.asyncio
async def test_rewrite_persists_sources_window(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    root = memory_root_with_index
    now = datetime(2026, 5, 19, 0, 0, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)
    llm = FakeLLM(response="# v1\n")
    v = await rewrite_identity_weekly(
        idx.conn, store, llm=llm, sources_window_days=7, now=now
    )
    idx.close()
    assert v.sources_window_end == now
    assert v.sources_window_start == now - timedelta(days=7)


@pytest.mark.asyncio
async def test_rewrite_trigger_label_propagates(
    profile_dir: Path,
    memory_root_with_index: Path,
):
    root = memory_root_with_index
    now = datetime(2026, 5, 19, tzinfo=timezone.utc)
    idx = open_index(root / "index.db")
    store = ProfileStore(idx.conn)
    llm = FakeLLM(response="x\n")
    v = await rewrite_identity_weekly(
        idx.conn, store, llm=llm, trigger="manual", now=now
    )
    idx.close()
    assert v.trigger == "manual"
