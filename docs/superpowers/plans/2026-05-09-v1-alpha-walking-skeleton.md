# v1.0-α Walking Skeleton 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 端到端验证：macOS 上 Claude Code 一次会话结束自动捕获到 Markdown 文件（用 spec § 7 约定的 frontmatter schema），新会话能通过 memoryd MCP server 调用 `search_memory` 工具召回到该会话。

**Architecture:** 三层独立模块：(1) `memoryd` Python 包提供 schema、storage、search、MCP server；(2) `cc-session-end-hook` 是一个 bash 脚本，由 Claude Code 在 SessionEnd 触发，调用 memoryd CLI 把 transcript 转成 Markdown；(3) 通过 `~/.claude.json`（用户主目录下的顶层文件，CC 读取用户级 MCP server 的唯一来源）的 `mcpServers` key 把 memoryd 注册为 MCP server，CC 的智能体调用 `search_memory` 工具召回。memsearch fork 推迟到 Plan 2 引入（plan 1 写最小 DIY 钩子，避免 memsearch 适配阻塞 walking skeleton）。

**Tech Stack:** Python 3.11+，`uv`（Python 包管理），`mcp[cli]`（官方 MCP Python SDK + FastMCP），`pydantic` v2（schema），`pyyaml`（frontmatter），`pytest`（测试），`ripgrep`（rg，已在 macOS 上可用或通过 `brew install ripgrep` 装）。

**Decomposition Note:** 见上文 plan 列表。本 plan 是 8 plan 中的第 1 个；后续 plan 全部基于本 plan 产出。

---

## 文件结构

执行本 plan 后会产生这些文件（**所有路径都从 repo 根 `/Users/abble/project-management-personal/` 算起**）：

| 路径 | 责任 |
|---|---|
| `memoryd/pyproject.toml` | Python 包配置（依赖、scripts） |
| `memoryd/src/memoryd/__init__.py` | 包入口 + 版本 |
| `memoryd/src/memoryd/schema.py` | Markdown frontmatter Pydantic schema（v1 最小子集） |
| `memoryd/src/memoryd/storage.py` | 把 SessionMemory 写成 Markdown / 读 Markdown 解析回 SessionMemory |
| `memoryd/src/memoryd/search.py` | 用 ripgrep 在 scope 目录搜匹配的 session 文件，返回摘要列表 |
| `memoryd/src/memoryd/server.py` | FastMCP server，注册 `search_memory` 工具 |
| `memoryd/src/memoryd/cli.py` | 命令行入口 `memoryd capture` 给 hook 脚本调用 |
| `memoryd/src/memoryd/scope.py` | 把 cwd 解析成 scope hash（sha1 of cwd 或 git root） |
| `memoryd/tests/conftest.py` | pytest fixtures（临时目录、样例 session） |
| `memoryd/tests/test_schema.py` | schema roundtrip 测试 |
| `memoryd/tests/test_storage.py` | storage 写读测试 |
| `memoryd/tests/test_search.py` | search 测试 |
| `memoryd/tests/test_server.py` | MCP server 端到端测试（in-process） |
| `memoryd/tests/test_cli.py` | CLI capture 测试 |
| `memoryd/tests/test_scope.py` | scope hash 测试 |
| `scripts/cc-session-end-hook.sh` | Claude Code SessionEnd hook bash 脚本 |
| `memoryd/README.md` | 安装与配置说明 |

不在本 plan：memsearch fork（推迟到 plan 2）、SQLite（推迟到 plan 3）、加密（plan 4）、Win/Linux（plan 5）、Web Dashboard / TUI（plan 7）。

**默认运行时数据目录：** `~/.local/share/memoryd/scopes/<scope-hash>/sessions/<YYYY-MM-DD>-<session-id>.md`

---

### Task 1：初始化 Python 项目结构

**Files:**
- Create: `memoryd/pyproject.toml`
- Create: `memoryd/src/memoryd/__init__.py`
- Create: `memoryd/README.md`
- Create: `memoryd/tests/__init__.py`
- Create: `.gitignore` 增加 `.venv` 等行（如未有）

- [ ] **Step 1：创建目录结构**

```bash
cd /Users/abble/project-management-personal
mkdir -p memoryd/src/memoryd memoryd/tests scripts
touch memoryd/tests/__init__.py
```

- [ ] **Step 2：写 `memoryd/pyproject.toml`**

```toml
[project]
name = "memoryd"
version = "0.1.0a0"
description = "Personal memory daemon — long-term memory governance MCP server"
requires-python = ">=3.11"
dependencies = [
    "mcp[cli]>=1.0.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]

[project.scripts]
memoryd = "memoryd.cli:main"
memoryd-server = "memoryd.server:main"

[tool.pytest.ini_options]
testpaths = ["tests"]
asyncio_mode = "auto"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/memoryd"]
```

- [ ] **Step 3：写 `memoryd/src/memoryd/__init__.py`**

```python
"""memoryd — personal memory governance MCP server."""

__version__ = "0.1.0a0"
```

- [ ] **Step 4：写 `memoryd/README.md`（占位，最后一个 task 才补完整内容）**

```markdown
# memoryd

Personal memory governance MCP server.

WIP — see `docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.md` for status.
```

- [ ] **Step 5：把 `.venv` 等加入 `.gitignore`（如未有）**

确认 `/Users/abble/project-management-personal/.gitignore` 包含：
```
.venv/
venv/
__pycache__/
*.egg-info/
.pytest_cache/
```
（已有的不重复，缺哪条加哪条。）

- [ ] **Step 6：用 uv 装依赖**

Run: `cd /Users/abble/project-management-personal/memoryd && uv venv && uv pip install -e ".[dev]"`
Expected: 输出包含 `Installed 30+ packages`，无红色错误。

- [ ] **Step 7：跑 pytest 确认空 test suite 能跑**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest`
Expected: `no tests ran in 0.XXs`（可以零测试退出码 5）；如果是 exit code 5（无测试），也算 OK。

- [ ] **Step 8：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/pyproject.toml memoryd/src/memoryd/__init__.py memoryd/README.md memoryd/tests/__init__.py .gitignore
git commit -m "scaffold memoryd Python project"
```

---

### Task 2：定义 frontmatter schema

**Files:**
- Create: `memoryd/src/memoryd/schema.py`
- Create: `memoryd/tests/test_schema.py`

按 spec § 7（与 Basic Memory 兼容）+ 4 月研究 § 4.2 的精简子集。Plan 1 只支持 `session` 类型；其他类型（decision/preference/fact）在 plan 3 加。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_schema.py`**

```python
"""Schema roundtrip tests."""
from datetime import datetime

import pytest
import yaml

from memoryd.schema import SessionMemory, Frontmatter


def test_frontmatter_required_fields():
    fm = Frontmatter(
        title="周一项目讨论",
        slug="2026-05-09-monday-discussion",
        type="session",
        scope_hash="abc123",
        triggers=["项目", "logo"],
        source="claude-code",
        created_at=datetime(2026, 5, 9, 9, 30),
    )
    assert fm.title == "周一项目讨论"
    assert fm.type == "session"
    assert "项目" in fm.triggers


def test_session_to_markdown_roundtrip():
    """Write a session to markdown text and parse it back."""
    session = SessionMemory(
        frontmatter=Frontmatter(
            title="测试会话",
            slug="2026-05-09-test",
            type="session",
            scope_hash="abc123",
            triggers=["test"],
            source="claude-code",
            created_at=datetime(2026, 5, 9, 12, 0),
        ),
        body="## 摘要\n用户问 X，回答 Y。\n",
    )
    md_text = session.to_markdown()
    parsed = SessionMemory.from_markdown(md_text)
    assert parsed.frontmatter.title == "测试会话"
    assert parsed.frontmatter.triggers == ["test"]
    assert "用户问 X" in parsed.body


def test_from_markdown_rejects_missing_frontmatter():
    """Markdown without frontmatter should raise ValueError."""
    with pytest.raises(ValueError, match="frontmatter"):
        SessionMemory.from_markdown("## just a body\n\nno fm here.\n")
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_schema.py -v`
Expected: 全部 FAIL，错误信息含 `ModuleNotFoundError: No module named 'memoryd.schema'`.

- [ ] **Step 3：实现 `memoryd/src/memoryd/schema.py`**

```python
"""Markdown frontmatter schema for memory entries.

v1.0-α: only supports `session` type. Other types (decision/preference/fact)
land in plan 3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field


MemoryType = Literal["session"]
"""v1.0-α scope. Plan 3 expands to: decision | preference | fact | playbook | warning."""


class Frontmatter(BaseModel):
    """YAML frontmatter for a memory file.

    Fields chosen to be compatible with Basic Memory schema where reasonable:
    - `title`, `slug`, `type`, `created_at`, `updated_at` are common
    - `triggers` is our addition for keyword routing (per OpenClaw Tier 3)
    - `scope_hash` ties the memory to its directory scope
    """

    title: str
    slug: str
    type: MemoryType
    scope_hash: str
    triggers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str  # e.g. "claude-code", "codex", "openclaw", "manual"
    created_at: datetime
    updated_at: datetime | None = None
    relations: list[str] = Field(default_factory=list)


class SessionMemory(BaseModel):
    """A single memory entry: frontmatter + free-form markdown body."""

    frontmatter: Frontmatter
    body: str

    def to_markdown(self) -> str:
        """Serialize to a string with YAML frontmatter + body."""
        fm_dict = self.frontmatter.model_dump(mode="json", exclude_none=True)
        fm_yaml = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        return f"---\n{fm_yaml}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, text: str) -> "SessionMemory":
        """Parse a markdown string with YAML frontmatter."""
        if not text.startswith("---\n"):
            raise ValueError("Missing YAML frontmatter delimiter at start of file")
        try:
            _, fm_text, body = text.split("---\n", 2)
        except ValueError as e:
            raise ValueError("Malformed frontmatter delimiters") from e
        fm_data = yaml.safe_load(fm_text)
        return cls(
            frontmatter=Frontmatter(**fm_data),
            body=body.lstrip("\n"),
        )
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_schema.py -v`
Expected: 3 passed in 0.XXs

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/schema.py memoryd/tests/test_schema.py
git commit -m "add Markdown frontmatter schema (session type only)"
```

---

### Task 3：实现 scope 解析（cwd → scope hash）

**Files:**
- Create: `memoryd/src/memoryd/scope.py`
- Create: `memoryd/tests/test_scope.py`

Scope 解析：从一个 cwd 沿着父目录找最近的 `.git` 目录作为 scope root；找不到就用 cwd 自身。Hash 用 sha1 截前 12 位。

- [ ] **Step 1：写失败测试**

`memoryd/tests/test_scope.py`：

```python
"""Scope resolution tests."""
import subprocess
from pathlib import Path

import pytest

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
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd memoryd && uv run pytest tests/test_scope.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3：实现 `memoryd/src/memoryd/scope.py`**

```python
"""Scope = directory unit. One scope = one memory collection.

Resolution rule (per spec § 3):
1. Walk parents from given path; first dir containing `.git` wins.
2. If no `.git` ancestor, the given path itself is the scope root.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def resolve_scope_root(start: Path) -> Path:
    """Find scope root for `start`. Returns absolute resolved path."""
    cur = Path(start).resolve()
    for ancestor in [cur, *cur.parents]:
        if (ancestor / ".git").exists():
            return ancestor
    return cur


def scope_hash(path: str | Path) -> str:
    """Stable 12-char sha1 prefix of the absolute scope root path."""
    abs_path = str(Path(path).resolve())
    return hashlib.sha1(abs_path.encode("utf-8")).hexdigest()[:12]
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd memoryd && uv run pytest tests/test_scope.py -v`
Expected: 4 passed.

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/scope.py memoryd/tests/test_scope.py
git commit -m "add scope resolution (git-root preferred, sha1 hashed)"
```

---

### Task 4：实现 storage（写读 Markdown 文件）

**Files:**
- Create: `memoryd/src/memoryd/storage.py`
- Create: `memoryd/tests/conftest.py`
- Create: `memoryd/tests/test_storage.py`

- [ ] **Step 1：写共享 fixture `memoryd/tests/conftest.py`**

```python
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
```

- [ ] **Step 2：写失败测试 `memoryd/tests/test_storage.py`**

```python
"""Storage layer tests."""
from pathlib import Path

from memoryd.schema import SessionMemory
from memoryd.storage import save_session, load_session, list_sessions


def test_save_creates_markdown_file(memory_root: Path, sample_session: SessionMemory):
    path = save_session(memory_root, sample_session)
    assert path.exists()
    assert path.suffix == ".md"
    assert sample_session.frontmatter.scope_hash in str(path)


def test_save_then_load_roundtrip(memory_root: Path, sample_session: SessionMemory):
    path = save_session(memory_root, sample_session)
    loaded = load_session(path)
    assert loaded.frontmatter.title == sample_session.frontmatter.title
    assert loaded.frontmatter.triggers == sample_session.frontmatter.triggers
    assert "logo 方向" in loaded.body


def test_list_sessions_filters_by_scope(memory_root: Path, sample_session: SessionMemory):
    save_session(memory_root, sample_session)

    found_in_scope = list_sessions(memory_root, scope_hash="abc123def456")
    assert len(found_in_scope) == 1

    found_other_scope = list_sessions(memory_root, scope_hash="zzz999")
    assert len(found_other_scope) == 0
```

- [ ] **Step 3：跑测试确认失败**

Run: `cd memoryd && uv run pytest tests/test_storage.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.storage'`.

- [ ] **Step 4：实现 `memoryd/src/memoryd/storage.py`**

```python
"""Markdown file storage for memory entries.

Layout:
    <root>/scopes/<scope_hash>/sessions/<slug>.md
"""
from __future__ import annotations

from pathlib import Path

from .schema import SessionMemory


def _scope_dir(root: Path, scope_hash: str) -> Path:
    return root / "scopes" / scope_hash / "sessions"


def save_session(root: Path, session: SessionMemory) -> Path:
    """Write a session to <root>/scopes/<hash>/sessions/<slug>.md.

    Returns the path written. Creates parent dirs as needed.
    """
    scope_dir = _scope_dir(root, session.frontmatter.scope_hash)
    scope_dir.mkdir(parents=True, exist_ok=True)
    path = scope_dir / f"{session.frontmatter.slug}.md"
    path.write_text(session.to_markdown(), encoding="utf-8")
    return path


def load_session(path: Path) -> SessionMemory:
    """Parse a markdown file at `path` back into a SessionMemory."""
    text = path.read_text(encoding="utf-8")
    return SessionMemory.from_markdown(text)


def list_sessions(root: Path, scope_hash: str) -> list[Path]:
    """List all session markdown files for a given scope."""
    scope_dir = _scope_dir(root, scope_hash)
    if not scope_dir.exists():
        return []
    return sorted(scope_dir.glob("*.md"))
```

- [ ] **Step 5：跑测试确认通过**

Run: `cd memoryd && uv run pytest tests/test_storage.py -v`
Expected: 3 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/storage.py memoryd/tests/conftest.py memoryd/tests/test_storage.py
git commit -m "add Markdown storage (save/load/list per scope)"
```

---

### Task 5：实现 search（ripgrep 子进程）

**Files:**
- Create: `memoryd/src/memoryd/search.py`
- Create: `memoryd/tests/test_search.py`

Plan 1 用 ripgrep + 简单全文匹配。语义搜索 / 嵌入留 plan 3。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_search.py`**

```python
"""Search tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.schema import Frontmatter, SessionMemory
from memoryd.search import SearchHit, search_sessions
from memoryd.storage import save_session


@pytest.fixture
def populated_root(memory_root: Path) -> Path:
    sessions = [
        SessionMemory(
            frontmatter=Frontmatter(
                title="logo 讨论",
                slug="2026-05-09-logo",
                type="session",
                scope_hash="scope_a",
                triggers=["logo", "wolin"],
                source="claude-code",
                created_at=datetime(2026, 5, 9),
            ),
            body="深蓝+银灰方向\n",
        ),
        SessionMemory(
            frontmatter=Frontmatter(
                title="API 调试",
                slug="2026-05-08-api",
                type="session",
                scope_hash="scope_a",
                triggers=["stripe", "webhook"],
                source="claude-code",
                created_at=datetime(2026, 5, 8),
            ),
            body="stripe webhook 排错\n",
        ),
        SessionMemory(
            frontmatter=Frontmatter(
                title="不相关项目",
                slug="2026-05-07-other",
                type="session",
                scope_hash="scope_other",
                triggers=["other"],
                source="claude-code",
                created_at=datetime(2026, 5, 7),
            ),
            body="其他项目话题\n",
        ),
    ]
    for s in sessions:
        save_session(memory_root, s)
    return memory_root


def test_search_finds_match_in_body(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="深蓝")
    assert len(hits) == 1
    assert hits[0].title == "logo 讨论"


def test_search_finds_match_in_triggers(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="stripe")
    assert len(hits) == 1
    assert hits[0].title == "API 调试"


def test_search_filters_by_scope(populated_root: Path):
    """Searching scope_a should not return scope_other matches."""
    hits = search_sessions(populated_root, scope_hash="scope_a", query="项目")
    titles = [h.title for h in hits]
    assert "不相关项目" not in titles


def test_search_returns_empty_for_no_match(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="不存在的关键词xyz123")
    assert hits == []


def test_search_hit_includes_path_and_excerpt(populated_root: Path):
    hits = search_sessions(populated_root, scope_hash="scope_a", query="深蓝")
    h = hits[0]
    assert isinstance(h, SearchHit)
    assert h.path.suffix == ".md"
    assert "深蓝" in h.excerpt
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd memoryd && uv run pytest tests/test_search.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3：确认 ripgrep 已装**

Run: `which rg`
Expected: 输出 `/opt/homebrew/bin/rg` 或类似路径。
如果没有：`brew install ripgrep`

- [ ] **Step 4：实现 `memoryd/src/memoryd/search.py`**

```python
"""ripgrep-based full-text search over markdown sessions.

v1.0-α: simple substring/regex match. Semantic search lands in plan 3.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .schema import SessionMemory
from .storage import _scope_dir, load_session


@dataclass(frozen=True)
class SearchHit:
    """A search result. `excerpt` is the matched line(s)."""

    path: Path
    title: str
    slug: str
    triggers: list[str]
    excerpt: str


def search_sessions(
    root: Path,
    scope_hash: str,
    query: str,
    *,
    limit: int = 20,
) -> list[SearchHit]:
    """Search session markdowns in a scope for `query`. Returns up to `limit` hits."""
    scope_dir = _scope_dir(root, scope_hash)
    if not scope_dir.exists():
        return []

    rg_cmd = [
        "rg",
        "--json",
        "--ignore-case",
        "--max-count", str(limit),
        "--",
        query,
        str(scope_dir),
    ]
    try:
        proc = subprocess.run(
            rg_cmd,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as e:
        raise RuntimeError("ripgrep (rg) not found on PATH; install via `brew install ripgrep`") from e

    # rg exits 1 when no matches found; that's not an error for us
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"ripgrep failed: {proc.stderr}")

    matched_paths: dict[Path, str] = {}
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        evt = json.loads(line)
        if evt.get("type") != "match":
            continue
        path_str = evt["data"]["path"]["text"]
        excerpt = evt["data"]["lines"]["text"].rstrip("\n")
        path = Path(path_str)
        # Keep first match per file as excerpt
        matched_paths.setdefault(path, excerpt)

    hits: list[SearchHit] = []
    for path, excerpt in list(matched_paths.items())[:limit]:
        try:
            session = load_session(path)
        except (ValueError, OSError):
            continue
        hits.append(SearchHit(
            path=path,
            title=session.frontmatter.title,
            slug=session.frontmatter.slug,
            triggers=session.frontmatter.triggers,
            excerpt=excerpt,
        ))
    return hits
```

- [ ] **Step 5：跑测试确认通过**

Run: `cd memoryd && uv run pytest tests/test_search.py -v`
Expected: 5 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/search.py memoryd/tests/test_search.py
git commit -m "add ripgrep-based session search"
```

---

### Task 6：写 CLI 入口（`memoryd capture`）

**Files:**
- Create: `memoryd/src/memoryd/cli.py`
- Create: `memoryd/tests/test_cli.py`

CLI 入口给 SessionEnd hook 脚本调用，把 transcript 转成 SessionMemory 并 save。Plan 1 实现最薄逻辑：从 stdin 读 hook payload JSON，提取 transcript_path，把 transcript 最后 N 轮做朴素摘要（截断字符串），写入 markdown。LLM 摘要在 plan 3 加。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_cli.py`**

```python
"""CLI capture tests."""
import json
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.cli import capture_session
from memoryd.storage import list_sessions


def _write_fake_transcript(transcript_path: Path) -> None:
    """Write a JSONL file mimicking Claude Code transcript format."""
    lines = [
        {"type": "user", "message": {"content": [{"type": "text", "text": "聊聊 wolin logo 方向"}]}},
        {"type": "assistant", "message": {"content": [{"type": "text", "text": "建议深蓝+银灰"}]}},
        {"type": "user", "message": {"content": [{"type": "text", "text": "好"}]}},
    ]
    transcript_path.write_text("\n".join(json.dumps(l, ensure_ascii=False) for l in lines))


def test_capture_creates_session_file(memory_root: Path, tmp_path: Path):
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()

    payload = {
        "session_id": "test-session-123",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 9, 14, 0))

    # The session should land under the scope hash for `cwd`
    from memoryd.scope import scope_hash
    sh = scope_hash(cwd)
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    md_text = files[0].read_text(encoding="utf-8")
    assert "wolin logo" in md_text
    assert "深蓝+银灰" in md_text


def test_capture_handles_missing_transcript(memory_root: Path, tmp_path: Path):
    """Missing transcript_path should not crash; it should write a stub session."""
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "test-no-transcript",
        "transcript_path": "/nonexistent/path.jsonl",
        "cwd": str(cwd),
    }
    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 9, 15, 0))

    from memoryd.scope import scope_hash
    sh = scope_hash(cwd)
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
    md_text = files[0].read_text(encoding="utf-8")
    assert "transcript unavailable" in md_text.lower() or "无 transcript" in md_text


def test_main_reads_payload_from_stdin(memory_root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """`memoryd capture` reads JSON payload from stdin and writes a session."""
    transcript = tmp_path / "transcript.jsonl"
    _write_fake_transcript(transcript)
    cwd = tmp_path / "project"
    cwd.mkdir()
    payload = {
        "session_id": "stdin-test",
        "transcript_path": str(transcript),
        "cwd": str(cwd),
    }
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))

    proc = subprocess.run(
        ["uv", "run", "memoryd", "capture"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**__import__("os").environ, "MEMORYD_DATA_ROOT": str(memory_root)},
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    from memoryd.scope import scope_hash
    sh = scope_hash(cwd)
    files = list_sessions(memory_root, scope_hash=sh)
    assert len(files) == 1
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd memoryd && uv run pytest tests/test_cli.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.cli'`.

- [ ] **Step 3：实现 `memoryd/src/memoryd/cli.py`**

```python
"""CLI entry points.

v1.0-α subcommands:
  memoryd capture   — invoked by tool hooks; reads JSON payload from stdin
                       and writes a session markdown to the data root
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from .schema import Frontmatter, SessionMemory
from .scope import resolve_scope_root, scope_hash
from .storage import save_session


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "memoryd"


def _data_root() -> Path:
    override = os.environ.get("MEMORYD_DATA_ROOT")
    if override:
        return Path(override)
    return DEFAULT_DATA_ROOT


def _read_transcript_text(transcript_path: str) -> str | None:
    """Read up to last 50 message contents from a Claude Code transcript JSONL.

    Returns None if file missing/unreadable.
    """
    try:
        path = Path(transcript_path)
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        # Take last 50 lines for v1.0-α naive summary
        recent = lines[-50:]
        chunks: list[str] = []
        for raw in recent:
            if not raw.strip():
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            msg = obj.get("message", {})
            content = msg.get("content")
            if isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and c.get("type") == "text":
                        chunks.append(c.get("text", ""))
            elif isinstance(content, str):
                chunks.append(content)
        return "\n".join(chunks).strip() or None
    except OSError:
        return None


def _summarize_naively(text: str, max_chars: int = 2000) -> str:
    """Naive truncation summary for v1.0-α. Plan 3 replaces with LLM call."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[...truncated]"


def capture_session(
    payload: dict[str, Any],
    *,
    memory_root: Path | None = None,
    now: datetime | None = None,
) -> Path:
    """Convert a SessionEnd hook payload into a SessionMemory markdown file."""
    if memory_root is None:
        memory_root = _data_root()
    if now is None:
        now = datetime.now()

    session_id = payload.get("session_id", "unknown")
    transcript_path = payload.get("transcript_path", "")
    cwd = payload.get("cwd", str(Path.cwd()))

    scope_root = resolve_scope_root(Path(cwd))
    sh = scope_hash(scope_root)

    transcript_text = _read_transcript_text(transcript_path)
    if transcript_text is None:
        body = (
            f"## 无 transcript（transcript unavailable）\n\n"
            f"transcript_path: `{transcript_path}`\n"
            f"session_id: `{session_id}`\n"
        )
    else:
        summary = _summarize_naively(transcript_text)
        body = f"## 摘要（朴素截断，v1.0-α）\n\n{summary}\n"

    slug = f"{now:%Y-%m-%d}-{session_id}"
    title = f"{now:%Y-%m-%d} 会话 {session_id[:8]}"

    session = SessionMemory(
        frontmatter=Frontmatter(
            title=title,
            slug=slug,
            type="session",
            scope_hash=sh,
            triggers=[],  # plan 3: extract via LLM
            source="claude-code",
            created_at=now,
        ),
        body=body,
    )
    return save_session(memory_root, session)


def cmd_capture(args: argparse.Namespace) -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        print("error: empty stdin; expected JSON payload", file=sys.stderr)
        return 2
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"error: invalid JSON on stdin: {e}", file=sys.stderr)
        return 2
    path = capture_session(payload)
    print(f"captured -> {path}", file=sys.stderr)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="memoryd")
    subs = parser.add_subparsers(dest="cmd", required=True)

    p_capture = subs.add_parser("capture", help="read SessionEnd payload from stdin and save")
    p_capture.set_defaults(func=cmd_capture)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd memoryd && uv run pytest tests/test_cli.py -v`
Expected: 3 passed. （subprocess 测试可能慢一些。）

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/cli.py memoryd/tests/test_cli.py
git commit -m "add memoryd capture CLI (naive truncation summary)"
```

---

### Task 7：实现 MCP server（`search_memory` tool）

**Files:**
- Create: `memoryd/src/memoryd/server.py`
- Create: `memoryd/tests/test_server.py`

用 FastMCP 暴露 1 个工具 `search_memory(query: str, scope_hash: str | None = None)`。如果不传 scope_hash，从 `MEMORYD_DEFAULT_SCOPE` 环境变量读，再不行报错（让智能体明确 scope）。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_server.py`**

```python
"""MCP server tests (in-process)."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_session


@pytest.fixture
def server_with_data(memory_root: Path, monkeypatch: pytest.MonkeyPatch):
    """Build a memoryd MCP server pointed at a temp memory root with sample data."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))

    s = SessionMemory(
        frontmatter=Frontmatter(
            title="logo 讨论",
            slug="2026-05-09-logo",
            type="session",
            scope_hash="test_scope",
            triggers=["logo", "wolin"],
            source="claude-code",
            created_at=datetime(2026, 5, 9),
        ),
        body="深蓝+银灰方向已定。\n",
    )
    save_session(memory_root, s)

    # Import after env vars set so server picks up the path
    from memoryd.server import build_server
    return build_server()


@pytest.mark.asyncio
async def test_search_memory_returns_matching_session(server_with_data):
    server = server_with_data
    # FastMCP exposes tools via internal registry; call directly via tool function
    result = await server.call_tool("search_memory", {"query": "深蓝", "scope_hash": "test_scope"})
    # Result is a list of TextContent / structured blocks; assert content includes title
    assert any("logo 讨论" in str(item) for item in result)


@pytest.mark.asyncio
async def test_search_memory_empty_when_no_match(server_with_data):
    server = server_with_data
    result = await server.call_tool(
        "search_memory",
        {"query": "不存在的关键词xyz", "scope_hash": "test_scope"},
    )
    # No hits → result should mention zero matches or empty
    text_blob = "".join(str(item) for item in result)
    assert "0" in text_blob or "no" in text_blob.lower() or text_blob == "" or text_blob == "[]"
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd memoryd && uv run pytest tests/test_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'memoryd.server'`.

- [ ] **Step 3：实现 `memoryd/src/memoryd/server.py`**

```python
"""memoryd MCP server.

Exposes one tool in v1.0-α:
  search_memory(query, scope_hash=None) — substring/regex search over session
                                            markdowns in a scope; returns hits

Plan 3 will add: list_promotions, promote_to_long_term, merge_duplicates,
                 list_decisions, get_decision, etc.
Plan 4 adds: request_sensitive_read.
Total tools must stay ≤ 12 per spec § 3.
"""
from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel

from .search import search_sessions


DEFAULT_DATA_ROOT = Path.home() / ".local" / "share" / "memoryd"


def _data_root() -> Path:
    return Path(os.environ.get("MEMORYD_DATA_ROOT") or DEFAULT_DATA_ROOT)


def _default_scope() -> str | None:
    return os.environ.get("MEMORYD_DEFAULT_SCOPE")


class SearchResult(BaseModel):
    """A single search hit, JSON-serializable."""

    title: str
    slug: str
    triggers: list[str]
    excerpt: str
    path: str


def build_server() -> FastMCP:
    """Build and return the FastMCP server (split for testability)."""
    mcp = FastMCP("memoryd")

    @mcp.tool()
    def search_memory(query: str, scope_hash: str | None = None) -> list[SearchResult]:
        """Search session memories in a scope for `query` (substring/regex).

        Args:
            query: The text to search for. Case-insensitive.
            scope_hash: Hash identifying the scope. If omitted, uses
                MEMORYD_DEFAULT_SCOPE env var. Must be set somewhere.

        Returns:
            Up to 20 hits, each with title, slug, triggers, excerpt, and file path.
        """
        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError(
                "scope_hash required (pass argument or set MEMORYD_DEFAULT_SCOPE)"
            )
        hits = search_sessions(_data_root(), scope_hash=sh, query=query)
        return [
            SearchResult(
                title=h.title,
                slug=h.slug,
                triggers=h.triggers,
                excerpt=h.excerpt,
                path=str(h.path),
            )
            for h in hits
        ]

    return mcp


def main() -> None:
    mcp = build_server()
    mcp.run()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4：跑测试确认通过**

Run: `cd memoryd && uv run pytest tests/test_server.py -v`
Expected: 2 passed.
注意：`call_tool` 是 FastMCP 的内部 API。如果版本不同导致 API 不一致，临时改测试用 `mcp.tools["search_memory"]` 拿函数直接调；以测试通过为准。

- [ ] **Step 5：手工启 server 确认 stdio 模式可启**

Run: `cd memoryd && timeout 2 uv run memoryd-server || echo "server started and stopped after 2s — OK"`
Expected: 输出 "server started and stopped after 2s — OK" 或类似 timeout 退出。如果立刻报错（exit code 非 0 且非 124），说明 server 启动失败。

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/server.py memoryd/tests/test_server.py
git commit -m "add FastMCP server with search_memory tool"
```

---

### Task 8：写 Claude Code SessionEnd hook 脚本

**Files:**
- Create: `scripts/cc-session-end-hook.sh`

bash 脚本：从 stdin 拿 CC 的 SessionEnd payload（JSON），转给 `memoryd capture`。后台跑（`&`），不阻塞 CC 退出。

- [ ] **Step 1：写脚本 `/Users/abble/project-management-personal/scripts/cc-session-end-hook.sh`**

```bash
#!/usr/bin/env bash
# Claude Code SessionEnd hook → memoryd capture
#
# Reads JSON payload from stdin, pipes it to `memoryd capture`.
# Runs in background so CC's exit isn't blocked.
#
# Install: see memoryd/README.md.

set -euo pipefail

# Default to the venv binary because CC's hook process doesn't inherit a
# venv-activated shell. Override via MEMORYD_BIN if you've installed memoryd
# globally or moved the venv.
MEMORYD_BIN="${MEMORYD_BIN:-/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd}"

# Fail early with a clear message if the bin really isn't there
if [[ ! -x "$MEMORYD_BIN" ]]; then
    echo "[$(date -Iseconds)] cc-session-end-hook: memoryd binary not executable at $MEMORYD_BIN" \
        >> "${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs/cc-session-end.log" 2>/dev/null || true
    exit 0  # never block CC's exit
fi

# Buffer stdin (CC pipes it once; we may detach)
PAYLOAD="$(cat)"

# Detach so CC's session-close isn't blocked.
# Errors land in a debug log under the data root.
LOG_DIR="${MEMORYD_DATA_ROOT:-$HOME/.local/share/memoryd}/logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/cc-session-end.log"

(
    if echo "$PAYLOAD" | "$MEMORYD_BIN" capture >> "$LOG_FILE" 2>&1; then
        echo "$(date -Iseconds)  ok" >> "$LOG_FILE"
    else
        echo "$(date -Iseconds)  failed (exit $?)" >> "$LOG_FILE"
    fi
) &

disown $! 2>/dev/null || true
exit 0
```

- [ ] **Step 2：让脚本可执行**

Run: `chmod +x /Users/abble/project-management-personal/scripts/cc-session-end-hook.sh`
Expected: 无输出。

- [ ] **Step 3：手工烟雾测试**

```bash
cd /Users/abble/project-management-personal
TMPDIR=$(mktemp -d)
TRANSCRIPT="$TMPDIR/transcript.jsonl"
cat > "$TRANSCRIPT" <<'EOF'
{"type":"user","message":{"content":[{"type":"text","text":"smoke test query"}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"smoke test response"}]}}
EOF

PAYLOAD=$(cat <<EOF
{"session_id": "smoke-test-$(date +%s)", "transcript_path": "$TRANSCRIPT", "cwd": "$TMPDIR"}
EOF
)

# Make memoryd available
export PATH="$PWD/memoryd/.venv/bin:$PATH"
export MEMORYD_DATA_ROOT="$TMPDIR/memoryd-data"

echo "$PAYLOAD" | scripts/cc-session-end-hook.sh

# Wait briefly for background task
sleep 1

# Verify a markdown file landed
find "$MEMORYD_DATA_ROOT" -name "*.md" -print
```

Expected: 一行输出，类似 `<TMPDIR>/memoryd-data/scopes/<hash>/sessions/<date>-smoke-test-XXXX.md`

- [ ] **Step 4：检查日志**

Run: `cat $TMPDIR/memoryd-data/logs/cc-session-end.log`
Expected: 包含 `captured -> ...` 和 `ok`。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add scripts/cc-session-end-hook.sh
git commit -m "add Claude Code SessionEnd hook script"
```

---

### Task 9：把 memoryd 接到 Claude Code（`~/.claude.json` mcpServers + `settings.json`）

**Files:**
- Modify: `~/.claude/settings.json`（用户的全局 CC 配置，存 hooks）
- Modify: `~/.claude.json`（用户主目录下的顶层文件，CC 读取用户级 MCP server 的唯一来源）

**重要：** 这一步要改用户全局 CC 配置。不直接 `Edit` 用户的 `~/.claude/settings.json`，让用户复制片段自己加（避免破坏其他配置）。

**注意（e2e 发现的 wire-up 陷阱）：** Claude Code 读取用户级 MCP server 配置的正确路径是 `~/.claude.json`（主目录，无子目录），而**不是** `~/.claude/.mcp.json`。`~/.claude/.mcp.json` 会被 CC 完全忽略。`~/.claude.json` 是一个已有的大型 JSON 文件（含 OAuth tokens、其他 MCP server 等），合并时必须只在其 `mcpServers` key 下添加 `memoryd` 条目，绝不能覆盖整个文件。

- [ ] **Step 1：备份用户当前 CC 配置**

```bash
mkdir -p ~/.claude/backups
cp ~/.claude/settings.json ~/.claude/backups/settings.json.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null || echo "no existing settings.json"
cp ~/.claude.json ~/.claude/backups/.claude.json.bak.$(date +%Y%m%d-%H%M%S) 2>/dev/null || echo "no existing ~/.claude.json"
```

- [ ] **Step 2：检查 settings.json 是否已有 hooks 配置**

Run: `cat ~/.claude/settings.json 2>/dev/null | python3 -c "import json, sys; d = json.load(sys.stdin); print('has hooks:', 'hooks' in d, '; has SessionEnd:', 'SessionEnd' in d.get('hooks', {}))" || echo "no settings.json"`
记下输出（`has hooks: True/False`）。

- [ ] **Step 3：把以下片段合并进 `~/.claude/settings.json`**

如果文件不存在，创建以下完整内容：
```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/Users/abble/project-management-personal/scripts/cc-session-end-hook.sh"
          }
        ]
      }
    ]
  }
}
```

如果文件已存在但没有 `hooks.SessionEnd`，**手动**把上面 `hooks.SessionEnd` 数组合并进去（不要覆盖其他 keys）。

- [ ] **Step 4：把 `memoryd` 合并进 `~/.claude.json` 的顶层 `mcpServers` 对象**

CC 读用户级 MCP server 唯一来源是 `~/.claude.json`（主目录顶层，已存在，含 OAuth 等重要数据）。**务必用 Python json 模块，不要 jq，不要直接写文件。** 只在现有 `mcpServers` key 下插入 `memoryd`，其余内容原封不动。

```python
import json
from pathlib import Path

path = Path.home() / ".claude.json"
with open(path) as f:
    d = json.load(f)

d.setdefault("mcpServers", {})
d["mcpServers"]["memoryd"] = {
    "command": "/Users/abble/project-management-personal/memoryd/.venv/bin/memoryd-server",
    "args": [],
    "env": {
        "MEMORYD_DATA_ROOT": "/Users/abble/.local/share/memoryd"
    }
}

tmp = path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
tmp.replace(path)
print("merged ok")
```

确认合并结果：
```bash
python3 -c "import json; d = json.load(open('/Users/abble/.claude.json')); print('mcpServers keys:', sorted(d['mcpServers'].keys()))"
```
Expected: 输出含 `memoryd` 和原有的其他 server（如 `feishu-user-plugin`）。

- [ ] **Step 5：验证 JSON 合法，原有配置完整**

```bash
python3 -c "import json; json.load(open('/Users/abble/.claude/settings.json'))" && echo "settings.json OK"
python3 -c "import json; d = json.load(open('/Users/abble/.claude.json')); print('memoryd entry:', d['mcpServers']['memoryd'])" && echo "~/.claude.json OK"
```
Expected: 两行 OK 都打印，memoryd entry 正确显示。

- [ ] **Step 6：本步骤无需 commit**（改的是用户配置，不在 repo 内）

记录变更到 plan 备注：
```bash
echo "[$(date -Iseconds)] wired memoryd to ~/.claude.json mcpServers + settings.json SessionEnd hook" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.execution-log.txt
```

---

### Task 10：端到端手工集成测试

**Files:** 无 repo 文件改动。这是黑盒手测。

- [ ] **Step 1：清空一个临时 scope**

```bash
TEST_PROJECT="$HOME/tmp/memoryd-e2e-test"
rm -rf "$TEST_PROJECT" "$HOME/.local/share/memoryd/scopes"
mkdir -p "$TEST_PROJECT"
cd "$TEST_PROJECT"
git init -q  # makes this a scope root
```

- [ ] **Step 2：第一次 CC 会话——产生工作记忆**

```bash
cd "$TEST_PROJECT"
claude
```

在 CC 里输入（让对话有可搜索关键词）：
```
我在 walking-skeleton-test 项目里测试 memoryd。请记住：本次会话的暗号是 PINEAPPLE-9999。
```

让 Claude 回复（任意内容），然后 `/exit` 退出 CC。

- [ ] **Step 3：验证 markdown 文件已生成**

```bash
sleep 2  # background hook 跑完
SCOPE_HASH=$(python3 -c "
import sys, hashlib
sys.path.insert(0, '/Users/abble/project-management-personal/memoryd/src')
from memoryd.scope import scope_hash, resolve_scope_root
from pathlib import Path
print(scope_hash(resolve_scope_root(Path('$TEST_PROJECT'))))
")
ls -la "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/"
cat "$HOME/.local/share/memoryd/scopes/$SCOPE_HASH/sessions/"*.md
```
Expected: 至少 1 个 `.md` 文件；`cat` 输出包含 `PINEAPPLE-9999`。

- [ ] **Step 4：第二次 CC 会话——召回**

```bash
cd "$TEST_PROJECT"
claude
```

在新会话里输入：
```
请用 search_memory 工具找一下我上次会话里提到的"暗号"。
```

期望：Claude 调用 `search_memory(query="暗号", scope_hash="<auto>")`，召回 `PINEAPPLE-9999` 所在 session，并把暗号告诉你。

如果智能体不知道有 search_memory 工具：在 CC 里运行 `/mcp` 列工具，确认 `memoryd` 出现且 `search_memory` 在列。

- [ ] **Step 5：记录 e2e 结果到 execution log**

```bash
echo "[$(date -Iseconds)] e2e PASS: PINEAPPLE-9999 successfully recalled in new session" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.execution-log.txt
```
（如果 FAIL，记录失败现象，回头排错；不进 task 11。）

- [ ] **Step 6：本步骤无 commit**（无 repo 改动）

---

### Task 11：完善 README

**Files:**
- Modify: `memoryd/README.md`

- [ ] **Step 1：写完整 README 替换占位**

```markdown
# memoryd

Personal memory governance MCP server. Part of `project-management-personal`.

**Status:** v0.1.0a0 — Walking Skeleton (plan 1 of 8)

Currently supports:
- macOS only
- Claude Code only (Codex / OpenClaw land in plan 2)
- Single machine (multi-machine sync in plan 6)
- Session capture only (decisions/preferences/promotions in plan 3)
- Plain Markdown storage (encryption in plan 4)
- ripgrep-based search (semantic search in plan 3)

## Install (macOS)

Prereqs: Python 3.11+, [`uv`](https://github.com/astral-sh/uv), `ripgrep` (`brew install ripgrep`).

```bash
cd /path/to/project-management-personal/memoryd
uv venv
uv pip install -e ".[dev]"
```

## Wire into Claude Code

Add the following to `~/.claude/settings.json` (merge with existing keys):

```json
{
  "hooks": {
    "SessionEnd": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "/path/to/project-management-personal/scripts/cc-session-end-hook.sh"
          }
        ]
      }
    ]
  }
}
```

Add the `memoryd` entry to `~/.claude.json` (the flat file at your home root — **not** `~/.claude/.mcp.json`, which CC ignores for user-level servers). Merge under the existing top-level `mcpServers` key using Python to avoid corrupting other entries (tokens, other servers):

```python
import json
from pathlib import Path

path = Path.home() / ".claude.json"
with open(path) as f:
    d = json.load(f)

d.setdefault("mcpServers", {})
d["mcpServers"]["memoryd"] = {
    "command": "/path/to/project-management-personal/memoryd/.venv/bin/memoryd-server",
    "args": [],
    "env": {
        "MEMORYD_DATA_ROOT": "/Users/<you>/.local/share/memoryd"
    }
}

tmp = path.with_suffix(".json.tmp")
with open(tmp, "w") as f:
    json.dump(d, f, indent=2, ensure_ascii=False)
tmp.replace(path)
```

Restart Claude Code. Run `/mcp` and verify `memoryd` appears with `search_memory` tool.

## Layout

```
src/memoryd/
  schema.py    # Pydantic Markdown frontmatter schema
  scope.py     # cwd → scope_hash (git-root preferred)
  storage.py   # save/load/list session markdowns
  search.py    # ripgrep-based search
  server.py    # FastMCP server with search_memory tool
  cli.py       # `memoryd capture` for hook scripts

tests/
  test_schema.py
  test_scope.py
  test_storage.py
  test_search.py
  test_cli.py
  test_server.py
```

Memory data root (default `~/.local/share/memoryd`):

```
scopes/
  <scope_hash>/
    sessions/
      2026-05-09-<session-id>.md
logs/
  cc-session-end.log
```

## Run tests

```bash
uv run pytest -v
```

## Limitations of v1.0-α

- Naive truncation summary (no LLM call). Plan 3 replaces with 4-criteria filter.
- No SQLite index — all search via ripgrep. Plan 3 adds SQLite for type filters.
- No encryption. Don't put secrets here in plan 1.
- No sync. Single-machine only.

See `docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.md` for full plan history; subsequent plans live alongside it.
```

- [ ] **Step 2：跑全测确认整个 plan 1 集成无回归**

Run: `cd memoryd && uv run pytest -v`
Expected: All tests pass — 应有 `~17 passed`（schema 3 + scope 4 + storage 3 + search 5 + cli 3 + server 2 ≈ 20）。

- [ ] **Step 3：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/README.md
git commit -m "expand memoryd README with install + wire-up instructions"
```

- [ ] **Step 4：标记 plan 1 完成**

```bash
echo "[$(date -Iseconds)] plan 1 (walking skeleton) complete; e2e PASS recorded above" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.execution-log.txt
git add docs/superpowers/plans/2026-05-09-v1-alpha-walking-skeleton.execution-log.txt
git commit -m "log plan 1 walking-skeleton completion"
```

---

## Plan 1 完成判定

下面任一条**未达成**即认为 plan 1 未完成：

1. ✅ 所有 pytest 测试通过（约 20 个）
2. ✅ Task 10 端到端手工测试中，"PINEAPPLE-9999" 在新 CC 会话里被正确召回
3. ✅ `~/.claude/settings.json`（hooks）和 `~/.claude.json`（mcpServers）合并后，原有其他配置（其他 hooks / 其他 MCP server）都没被破坏
4. ✅ memoryd 的 SessionEnd hook 后台跑、不阻塞 CC 退出（hook 触发后 CC 立刻能退出）
5. ✅ 至少一个 `.md` session 文件用 spec § 7 frontmatter schema 写出（`scope_hash` 字段存在、`source: claude-code` 字段存在）

## 下一个 plan

Plan 2：三端共享。把 memsearch fork 引入，扩展到 Codex + OpenClaw 钩子。验证三端共享同一个项目目录下的工作记忆。本 plan 的 `scripts/cc-session-end-hook.sh` 在 plan 2 后可能被 memsearch 自带 hook 替换，但 schema、storage、search、server 都保留沿用。
