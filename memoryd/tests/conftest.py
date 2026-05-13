"""Shared pytest fixtures."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.schema import Frontmatter, SessionMemory


@pytest.fixture
def sample_session() -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="周一项目讨论",
            slug="2026-05-09-monday-discussion",
            type="session",
            scope_hash="abc123def456",
            triggers=["项目", "logo", "wolin"],
            source="claude-code",
            created_at=datetime(2026, 5, 9, 9, 30),
        ),
        body="## 摘要\n讨论了 wolin 项目的 logo 方向，决定深蓝+银灰。\n",
    )


@pytest.fixture
def memory_root(tmp_path: Path) -> Path:
    """A temp directory acting as memoryd's data root."""
    root = tmp_path / "memoryd_data"
    root.mkdir()
    return root
