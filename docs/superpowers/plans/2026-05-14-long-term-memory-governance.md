# 长期记忆治理（Plan 3）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 Plan 2.5 已交付的 capture 通路之上加治理层：6 种记忆类型 + SQLite 索引 + 4 准则 LLM 候选筛选 + TTL/decay/soft-forget 状态机 + 周期 digest（CLI 文本形态）+ 6 个新 MCP 工具（总 7/12）。

**Architecture:** Markdown 仍是 source of truth；SQLite (`~/.local/share/memoryd/index.db`) 只作可重建索引；session 落盘时自动 index；session-end 后台 fork `memoryd analyze-session` 调 LLM 跑 DURA 评分把候选写到 `promotions` 表；用户跑 `memoryd digest` 查看 / 批准 / 合并 / 拒绝候选；decay-sweep CLI 走状态机 alive → dim → soft-forgotten → forgotten/ 子目录。

**Tech Stack:** Python 3.11+；新增运行时依赖 `anthropic>=0.40`（LLM SDK）；新增 stdlib 使用 `sqlite3`；schema 改动用 pydantic v2 已有；测试用 `pytest` + mock LLM client；CLI 无 TUI（推迟到 Plan 7）。

**Scope adjustments (vs spec):**
- spec §4.3 #11 TUI 交互界面 → 推迟到 Plan 7（用户 prompt 明确 Plan 7 含"TUI 完善"），本 plan 用纯文本 `memoryd digest` 命令
- spec §4.4 #13 launchd 周期自启 / 桌面通知 → 推迟到 Plan 5（跨平台 daemon），本 plan 用户手动 `memoryd decay-sweep` / `memoryd digest`
- spec §3 列了 4 个 LLM provider（anthropic / openai / openrouter / local）→ 本 plan 只实现 Anthropic（用户实际机器有 Anthropic 路子），其他通过 Protocol 抽象留接口

**Decomposition Note:** 8 plan 中的第 3 个。上游 Plan 1 / 2 / 2.5 已 merge（`b140b35`）。下游 Plan 4 依赖本 plan 的 SQLite index 标"sensitive=true" flag；Plan 5 接本 plan 的 decay-sweep 装 cron；Plan 7 给 digest 加 TUI；Plan 8 用本 plan 的 type 系统反向导入 CLAUDE.md / AGENTS.md 等。

**Spec：** `docs/superpowers/specs/2026-05-14-long-term-memory-governance-design.md`（commit `c81abab`）。

---

## 文件结构

| 路径 | 责任 | 操作 |
|---|---|---|
| `memoryd/pyproject.toml` | 加 `anthropic>=0.40` 运行时依赖 | Modify |
| `memoryd/src/memoryd/schema.py` | MemoryType 扩展 6 种 + Frontmatter 7 个新 optional 字段 | Modify |
| `memoryd/src/memoryd/index.py` | SQLite 连接 / 初始化 / index_memory / get_memory / list_by_type / 等 | Create |
| `memoryd/src/memoryd/migrations/__init__.py` | 空 marker | Create |
| `memoryd/src/memoryd/migrations/001_initial_schema.sql` | memories / triggers / promotions 表 + 索引 | Create |
| `memoryd/src/memoryd/storage.py` | save_session 后调 index；加 save_to_type helpers | Modify |
| `memoryd/src/memoryd/search.py` | SQLite-backed search + type filter + include_decayed；ripgrep fallback | Modify |
| `memoryd/src/memoryd/config.py` | 用户级 `~/.config/memoryd/config.toml` 读写 | Create |
| `memoryd/src/memoryd/llm.py` | LLMProvider Protocol + AnthropicProvider 实现 | Create |
| `memoryd/src/memoryd/prompts/__init__.py` | 空 marker | Create |
| `memoryd/src/memoryd/prompts/dura_extract.txt` | DURA 4 准则 prompt 模板 | Create |
| `memoryd/src/memoryd/governance/__init__.py` | 空 marker | Create |
| `memoryd/src/memoryd/governance/analyze.py` | analyze_session：跑 DURA → 写 promotions | Create |
| `memoryd/src/memoryd/governance/decay.py` | sweep_decay：状态机迁移 | Create |
| `memoryd/src/memoryd/governance/merge.py` | merge_memories：合并 .md + 更新 SQLite | Create |
| `memoryd/src/memoryd/governance/digest.py` | build_digest：JSON / 文本输出（无 TUI） | Create |
| `memoryd/src/memoryd/cli.py` | 加 analyze-session / decay-sweep / merge / digest / rebuild-index / config / list 子命令 | Modify |
| `memoryd/src/memoryd/server.py` | 注册 6 个新 MCP 工具 | Modify |
| `memoryd/tests/test_schema.py` | 加 type 扩展 + 新字段 roundtrip 测试 | Modify |
| `memoryd/tests/test_index.py` | SQLite 单测 | Create |
| `memoryd/tests/test_storage.py` | save 后 index 验证 | Modify |
| `memoryd/tests/test_search.py` | type filter + decayed 测试 | Modify |
| `memoryd/tests/test_config.py` | config 读写测试 | Create |
| `memoryd/tests/test_llm.py` | LLM provider 测试（mock HTTP） | Create |
| `memoryd/tests/test_governance_analyze.py` | analyze 单测（mock LLM） | Create |
| `memoryd/tests/test_governance_decay.py` | decay 状态机测试 | Create |
| `memoryd/tests/test_governance_merge.py` | merge 单测 | Create |
| `memoryd/tests/test_governance_digest.py` | digest 单测 | Create |
| `memoryd/tests/test_cli.py` | 加新子命令测试 | Modify |
| `memoryd/tests/test_server.py` | 加 6 个新工具测试 | Modify |
| `memoryd/README.md` | 加长期记忆使用文档 | Modify |
| `docs/superpowers/plans/2026-05-14-long-term-memory-governance.execution-log.txt` | 实施日志 + Phase 1 手册 | Create |

数据目录新增：
```
~/.local/share/memoryd/
  index.db                                  # SQLite 索引（新）
  scopes/<scope_hash>/
    sessions/                               # Plan 1-2.5 已有
    decisions/  preferences/  facts/        # Plan 3 新（6 种类型各自目录）
    playbooks/  warnings/
    forgotten/                              # Plan 3 新（soft-forgotten 物理迁出）
```

用户配置：`~/.config/memoryd/config.toml`（**不**在 `~/.codex/` / `~/.claude/` 下，区别于三端原生）。

---

## 风险与不确定性（先读再开工）

1. **Anthropic SDK 接口**：`anthropic` Python SDK 通过 `client.messages.create(...)` 调用。env 用 `ANTHROPIC_API_KEY`。本机 `HTTPS_PROXY=http://127.0.0.1:7897` 已设；SDK 自动尊重 `https_proxy` env。Task 7 实际首次跑要确认 SDK 通过 proxy 工作（subagent 实施时跑 smoke test）。
2. **SQLite WAL 模式**：单进程内并发安全；Plan 6 跨设备同步时 `index.db` **不**进同步盘（spec §4.7 #26）。Plan 3 暂不考虑同步问题；只确保单机正确。
3. **LLM 返回 non-JSON**：模型偶尔会包 ```json fence 或加解释。analyze.py 容忍：尝试解析；失败 → 用正则抓 `[{...}]`；再失败 → log + skip（不阻塞 capture）。
4. **migration / 升级**：Plan 3 是首次引入 SQLite。`index.py` 启动时检查 `index.db` 是否存在；不存在则跑 `001_initial_schema.sql`；之后增量 migration（Plan 4-8 各自加 002+）。
5. **TTL 与 last_recalled_at 时区**：全程用 UTC（`datetime.now(timezone.utc)`），SQLite 存 ISO 字符串。已实测 Plan 1-2.5 用 UTC 一致。

---

## Task 1：Schema 扩展（MemoryType 6 种 + 7 个新字段）

**Files:**
- Modify: `memoryd/src/memoryd/schema.py`
- Modify: `memoryd/tests/test_schema.py`

- [ ] **Step 1：先加新字段测试**

在 `memoryd/tests/test_schema.py` 末尾追加：

```python
from memoryd.schema import MemoryType


def test_memory_type_supports_six_kinds():
    """All six types accepted by Frontmatter."""
    for kind in ("session", "decision", "preference", "fact", "playbook", "warning"):
        fm = Frontmatter(
            title="t",
            slug=f"2026-05-14-{kind}",
            type=kind,
            scope_hash="h",
            source="manual",
            created_at=datetime(2026, 5, 14),
        )
        assert fm.type == kind


def test_frontmatter_accepts_new_governance_fields():
    fm = Frontmatter(
        title="t",
        slug="2026-05-14-x",
        type="decision",
        scope_hash="h",
        source="manual",
        created_at=datetime(2026, 5, 14),
        promoted_from="2026-05-13-session-abc",
        supersedes=["2026-04-30-old"],
        ttl_days=90,
        decay_state="alive",
        last_recalled_at=datetime(2026, 5, 13),
        recall_count=3,
        dura_score={"D": 0.85, "U": 0.92, "R": 0.78, "A": 0.95},
    )
    assert fm.promoted_from == "2026-05-13-session-abc"
    assert fm.supersedes == ["2026-04-30-old"]
    assert fm.ttl_days == 90
    assert fm.decay_state == "alive"
    assert fm.recall_count == 3
    assert fm.dura_score["D"] == 0.85


def test_frontmatter_new_fields_all_optional():
    """Plan 1-2.5 frontmatter still parses (zero new fields)."""
    fm = Frontmatter(
        title="legacy",
        slug="2026-04-01-legacy",
        type="session",
        scope_hash="h",
        source="claude-code",
        created_at=datetime(2026, 4, 1),
    )
    assert fm.promoted_from is None
    assert fm.supersedes == []
    assert fm.ttl_days is None
    assert fm.decay_state == "alive"  # default value
    assert fm.recall_count == 0       # default value
    assert fm.dura_score is None


def test_session_roundtrip_with_governance_fields():
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="logo decision",
            slug="2026-05-14-logo",
            type="decision",
            scope_hash="h",
            source="manual",
            created_at=datetime(2026, 5, 14),
            ttl_days=None,
            dura_score={"D": 0.9, "U": 0.9, "R": 0.8, "A": 1.0},
            supersedes=["2026-04-30-old-logo"],
        ),
        body="深蓝+银灰",
    )
    md = s.to_markdown()
    parsed = SessionMemory.from_markdown(md)
    assert parsed.frontmatter.type == "decision"
    assert parsed.frontmatter.dura_score["D"] == 0.9
    assert parsed.frontmatter.supersedes == ["2026-04-30-old-logo"]
```

- [ ] **Step 2：跑测试确认失败**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_schema.py -v`
Expected: 新 4 个测试 FAIL（`type=decision` 不接受 / Frontmatter 不识别新字段）。

- [ ] **Step 3：修改 `memoryd/src/memoryd/schema.py`**

把 `MemoryType` Literal 扩展，Frontmatter 加新字段：

```python
"""Markdown frontmatter schema for memory entries.

Plan 3: 6 types + governance fields (TTL / decay / DURA / promotion / relations).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal

import yaml
from pydantic import BaseModel, Field


MemoryType = Literal[
    "session",
    "decision",
    "preference",
    "fact",
    "playbook",
    "warning",
]


DecayState = Literal["alive", "dim", "soft-forgotten"]


class Frontmatter(BaseModel):
    """YAML frontmatter for a memory file.

    Plan 1 base fields (title / slug / type / scope_hash / source / created_at /
    updated_at / triggers / tags / relations) plus Plan 3 governance fields.
    Every Plan 3 field is optional so Plan 1-2.5 `.md` files still parse.
    """

    title: str
    slug: str
    type: MemoryType
    scope_hash: str
    triggers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: str
    created_at: datetime
    updated_at: datetime | None = None
    relations: list[str] = Field(default_factory=list)

    # Plan 3 governance fields (all optional, sensible defaults)
    promoted_from: str | None = None
    supersedes: list[str] = Field(default_factory=list)
    ttl_days: int | None = None
    decay_state: DecayState = "alive"
    last_recalled_at: datetime | None = None
    recall_count: int = 0
    dura_score: dict[str, float] | None = None


class SessionMemory(BaseModel):
    """A single memory entry: frontmatter + free-form markdown body."""

    frontmatter: Frontmatter
    body: str

    def to_markdown(self) -> str:
        fm_dict = self.frontmatter.model_dump(mode="json", exclude_none=True)
        # exclude_none drops None fields but pydantic v2 may keep empty lists;
        # drop those explicitly to keep file clean
        for k in ("triggers", "tags", "relations", "supersedes"):
            if fm_dict.get(k) == []:
                del fm_dict[k]
        fm_yaml = yaml.safe_dump(fm_dict, sort_keys=False, allow_unicode=True)
        return f"---\n{fm_yaml}---\n\n{self.body}"

    @classmethod
    def from_markdown(cls, text: str) -> "SessionMemory":
        if not text.startswith("---\n"):
            raise ValueError("Missing YAML frontmatter delimiter at start of file")
        try:
            _, fm_text, body = text.split("---\n", 2)
        except ValueError as e:
            raise ValueError("Malformed frontmatter delimiters") from e
        fm_data = yaml.safe_load(fm_text)
        if not isinstance(fm_data, dict):
            raise ValueError("Frontmatter must be a mapping")
        return cls(frontmatter=Frontmatter(**fm_data), body=body.lstrip("\n"))
```

- [ ] **Step 4：跑测试**

Run: `cd /Users/abble/project-management-personal/memoryd && uv run pytest tests/test_schema.py -v`
Expected: 原 6 + 新 4 = 10 passed。

跑全量回归（Plan 1-2.5 测试不能挂）：

Run: `cd memoryd && uv run pytest -v`
Expected: 61 + 4 = 65 passed。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/schema.py memoryd/tests/test_schema.py
git commit -m "$(cat <<'EOF'
schema 扩展：MemoryType 6 种 + governance fields

Plan 3 类型治理：type 从 'session' 扩到 6 种（decision/preference/fact/
playbook/warning）。Frontmatter 加 7 个 optional 字段（promoted_from /
supersedes / ttl_days / decay_state / last_recalled_at / recall_count /
dura_score）；所有字段 default 兼容 Plan 1-2.5 已存在的 .md 文件。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 2：SQLite index 模块

**Files:**
- Create: `memoryd/src/memoryd/index.py`
- Create: `memoryd/src/memoryd/migrations/__init__.py`
- Create: `memoryd/src/memoryd/migrations/001_initial_schema.sql`
- Create: `memoryd/tests/test_index.py`

- [ ] **Step 1：写 migration SQL `memoryd/src/memoryd/migrations/001_initial_schema.sql`**

```sql
-- Plan 3 initial schema. Pure index — Markdown is source of truth.
-- `body_path` is relative to MEMORYD_DATA_ROOT.

CREATE TABLE IF NOT EXISTS memories (
    slug             TEXT PRIMARY KEY,
    type             TEXT NOT NULL,
    scope_hash       TEXT NOT NULL,
    title            TEXT NOT NULL,
    source           TEXT NOT NULL,
    created_at       TEXT NOT NULL,    -- ISO-8601 UTC
    updated_at       TEXT,
    ttl_days         INTEGER,           -- NULL for non-expiring long-term memory
    decay_state      TEXT NOT NULL DEFAULT 'alive',
    last_recalled_at TEXT,
    recall_count     INTEGER NOT NULL DEFAULT 0,
    fingerprint      TEXT NOT NULL,    -- sha1(body[:500]) for cross-path dedup
    body_path        TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_memories_scope_type     ON memories (scope_hash, type);
CREATE INDEX IF NOT EXISTS idx_memories_fingerprint    ON memories (fingerprint);
CREATE INDEX IF NOT EXISTS idx_memories_decay          ON memories (decay_state, last_recalled_at);
CREATE INDEX IF NOT EXISTS idx_memories_created        ON memories (created_at);

CREATE TABLE IF NOT EXISTS triggers (
    slug     TEXT NOT NULL,
    trigger  TEXT NOT NULL,
    PRIMARY KEY (slug, trigger),
    FOREIGN KEY (slug) REFERENCES memories(slug) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_triggers_term ON triggers (trigger);

CREATE TABLE IF NOT EXISTS promotions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    source_session_slug  TEXT NOT NULL,
    proposed_type        TEXT NOT NULL,
    proposed_title       TEXT NOT NULL,
    proposed_body        TEXT NOT NULL,
    proposed_triggers    TEXT NOT NULL,    -- JSON array
    dura_score           TEXT NOT NULL,    -- JSON object
    reasoning            TEXT,
    proposed_supersedes  TEXT NOT NULL DEFAULT '[]',  -- JSON array of slugs
    scope_hash           TEXT NOT NULL,
    status               TEXT NOT NULL DEFAULT 'pending',  -- pending|approved|rejected|merged
    created_at           TEXT NOT NULL,
    decided_at           TEXT,
    FOREIGN KEY (source_session_slug) REFERENCES memories(slug) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_promotions_status_scope ON promotions (status, scope_hash);

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;
```

- [ ] **Step 2：`memoryd/src/memoryd/migrations/__init__.py`（空 marker）**

```python
"""Plan 3 SQLite migrations. Each file is applied once in numeric order."""
```

- [ ] **Step 3：写失败测试 `memoryd/tests/test_index.py`**

```python
"""SQLite index module tests."""
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from memoryd.index import (
    DEFAULT_DB_PATH,
    Index,
    fingerprint_body,
    open_index,
)
from memoryd.schema import Frontmatter, SessionMemory


def _build_memory(slug: str = "2026-05-14-t", scope: str = "h", type_: str = "session") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug=slug,
            type=type_,
            scope_hash=scope,
            triggers=["k1", "k2"],
            source="manual",
            created_at=datetime(2026, 5, 14, 10, 0, tzinfo=timezone.utc),
        ),
        body="some body content",
    )


def test_open_index_creates_db_and_runs_migrations(tmp_path: Path):
    db = tmp_path / "x.db"
    idx = open_index(db)
    # tables exist
    cur = idx.conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    tables = [r[0] for r in cur.fetchall()]
    assert "memories" in tables
    assert "triggers" in tables
    assert "promotions" in tables
    idx.close()


def test_index_memory_inserts_row_and_triggers(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    mem = _build_memory()
    idx.index_memory(mem, body_path="scopes/h/sessions/2026-05-14-t.md")

    row = idx.conn.execute("SELECT slug, type, fingerprint FROM memories WHERE slug=?", ("2026-05-14-t",)).fetchone()
    assert row is not None
    assert row[0] == "2026-05-14-t"
    assert row[1] == "session"
    expected_fp = hashlib.sha1("some body content"[:500].encode()).hexdigest()
    assert row[2] == expected_fp

    triggers = idx.conn.execute("SELECT trigger FROM triggers WHERE slug=? ORDER BY trigger", ("2026-05-14-t",)).fetchall()
    assert [t[0] for t in triggers] == ["k1", "k2"]
    idx.close()


def test_index_memory_is_upsert(tmp_path: Path):
    """Re-indexing the same slug updates fields instead of duplicating."""
    idx = open_index(tmp_path / "x.db")
    mem1 = _build_memory()
    idx.index_memory(mem1, body_path="path1.md")

    mem2 = _build_memory()
    mem2 = mem2.model_copy(update={
        "frontmatter": mem2.frontmatter.model_copy(update={"title": "updated"})
    })
    idx.index_memory(mem2, body_path="path1.md")

    rows = idx.conn.execute("SELECT slug, title FROM memories WHERE slug=?", ("2026-05-14-t",)).fetchall()
    assert len(rows) == 1
    assert rows[0][1] == "updated"
    idx.close()


def test_get_memory_returns_row(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(), body_path="p.md")
    row = idx.get_memory("2026-05-14-t")
    assert row is not None
    assert row["slug"] == "2026-05-14-t"
    assert row["type"] == "session"


def test_get_memory_returns_none_when_missing(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    assert idx.get_memory("nope") is None


def test_list_by_type_filters_correctly(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(slug="s1", type_="session"), body_path="s1.md")
    idx.index_memory(_build_memory(slug="d1", type_="decision"), body_path="d1.md")
    idx.index_memory(_build_memory(slug="d2", type_="decision"), body_path="d2.md")

    sessions = idx.list_by_type("session", scope_hash="h")
    decisions = idx.list_by_type("decision", scope_hash="h")
    assert len(sessions) == 1 and sessions[0]["slug"] == "s1"
    assert len(decisions) == 2


def test_list_by_type_filters_decay_state_by_default(tmp_path: Path):
    """Default include_decayed=False excludes soft-forgotten."""
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(slug="alive1"), body_path="a.md")
    idx.index_memory(_build_memory(slug="forgotten1"), body_path="f.md")
    idx.conn.execute(
        "UPDATE memories SET decay_state='soft-forgotten' WHERE slug=?", ("forgotten1",)
    )
    idx.conn.commit()

    default = idx.list_by_type("session", scope_hash="h")
    assert {r["slug"] for r in default} == {"alive1"}

    all_states = idx.list_by_type("session", scope_hash="h", include_decayed=True)
    assert {r["slug"] for r in all_states} == {"alive1", "forgotten1"}


def test_fingerprint_body_uses_first_500_chars(tmp_path: Path):
    long_body = "x" * 600
    fp1 = fingerprint_body(long_body)
    fp2 = fingerprint_body(long_body[:500])
    assert fp1 == fp2
    assert fp1 != fingerprint_body("y")


def test_record_recall_updates_last_recalled_and_count(tmp_path: Path):
    idx = open_index(tmp_path / "x.db")
    idx.index_memory(_build_memory(), body_path="p.md")
    idx.record_recall("2026-05-14-t")
    row = idx.get_memory("2026-05-14-t")
    assert row["recall_count"] == 1
    assert row["last_recalled_at"] is not None
```

- [ ] **Step 4：跑失败**

Run: `cd memoryd && uv run pytest tests/test_index.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 5：实现 `memoryd/src/memoryd/index.py`**

```python
"""SQLite index over Markdown memory files.

Markdown is source of truth; this index is rebuildable via `memoryd
rebuild-index` (Task 4). The index makes type filters / decay queries /
promotions list / fingerprint dedup cheap. `open_index` runs all
migrations under `migrations/` in numeric order on first open.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import SessionMemory


DEFAULT_DB_PATH = Path.home() / ".local" / "share" / "memoryd" / "index.db"


def _db_path() -> Path:
    override = os.environ.get("MEMORYD_INDEX_DB")
    if override:
        return Path(override)
    root = os.environ.get("MEMORYD_DATA_ROOT")
    if root:
        return Path(root) / "index.db"
    return DEFAULT_DB_PATH


_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def fingerprint_body(body: str) -> str:
    """sha1 over first 500 chars; used for cross-path dedup heuristic."""
    return hashlib.sha1(body[:500].encode("utf-8")).hexdigest()


class Index:
    """Wrapper around a sqlite3.Connection with helper methods."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.conn.row_factory = sqlite3.Row

    # -- write side ---------------------------------------------------------

    def index_memory(self, mem: SessionMemory, *, body_path: str) -> None:
        """Insert-or-update the memory row + replace its triggers."""
        fm = mem.frontmatter
        fp = fingerprint_body(mem.body)
        self.conn.execute(
            """
            INSERT INTO memories
                (slug, type, scope_hash, title, source, created_at, updated_at,
                 ttl_days, decay_state, last_recalled_at, recall_count,
                 fingerprint, body_path)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(slug) DO UPDATE SET
                type=excluded.type,
                scope_hash=excluded.scope_hash,
                title=excluded.title,
                source=excluded.source,
                created_at=excluded.created_at,
                updated_at=excluded.updated_at,
                ttl_days=excluded.ttl_days,
                decay_state=excluded.decay_state,
                last_recalled_at=excluded.last_recalled_at,
                recall_count=excluded.recall_count,
                fingerprint=excluded.fingerprint,
                body_path=excluded.body_path
            """,
            (
                fm.slug,
                fm.type,
                fm.scope_hash,
                fm.title,
                fm.source,
                fm.created_at.isoformat(),
                fm.updated_at.isoformat() if fm.updated_at else None,
                fm.ttl_days,
                fm.decay_state,
                fm.last_recalled_at.isoformat() if fm.last_recalled_at else None,
                fm.recall_count,
                fp,
                body_path,
            ),
        )
        self.conn.execute("DELETE FROM triggers WHERE slug = ?", (fm.slug,))
        if fm.triggers:
            self.conn.executemany(
                "INSERT INTO triggers (slug, trigger) VALUES (?, ?)",
                [(fm.slug, t) for t in fm.triggers],
            )
        self.conn.commit()

    def record_recall(self, slug: str) -> None:
        """Bump recall_count + set last_recalled_at to now (UTC)."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE memories SET recall_count = recall_count + 1, "
            "last_recalled_at = ? WHERE slug = ?",
            (now, slug),
        )
        self.conn.commit()

    def update_decay_state(self, slug: str, state: str) -> None:
        self.conn.execute("UPDATE memories SET decay_state = ? WHERE slug = ?", (state, slug))
        self.conn.commit()

    def delete_memory(self, slug: str) -> None:
        """Cascades to triggers + promotions via FK."""
        self.conn.execute("DELETE FROM memories WHERE slug = ?", (slug,))
        self.conn.commit()

    # -- read side ----------------------------------------------------------

    def get_memory(self, slug: str) -> dict[str, Any] | None:
        row = self.conn.execute("SELECT * FROM memories WHERE slug = ?", (slug,)).fetchone()
        return dict(row) if row else None

    def list_by_type(
        self,
        type_: str,
        *,
        scope_hash: str | None = None,
        include_decayed: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM memories WHERE type = ?"
        args: list[Any] = [type_]
        if scope_hash is not None:
            sql += " AND scope_hash = ?"
            args.append(scope_hash)
        if not include_decayed:
            sql += " AND decay_state != 'soft-forgotten'"
        sql += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        return [dict(r) for r in self.conn.execute(sql, args).fetchall()]

    def find_by_fingerprint(self, fingerprint: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE fingerprint = ?",
            (fingerprint,),
        ).fetchall()
        return [dict(r) for r in rows]

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self.conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    for sql_file in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        sql = sql_file.read_text(encoding="utf-8")
        conn.executescript(sql)
    conn.commit()


def open_index(path: Path | None = None) -> Index:
    """Open (creating if needed) the SQLite index and run pending migrations."""
    p = path or _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA foreign_keys = ON")
    _run_migrations(conn)
    return Index(conn)
```

- [ ] **Step 6：跑测试**

Run: `cd memoryd && uv run pytest tests/test_index.py -v`
Expected: 9 passed.

- [ ] **Step 7：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/index.py memoryd/src/memoryd/migrations/ memoryd/tests/test_index.py
git commit -m "$(cat <<'EOF'
SQLite index 模块 + 初始 migration

Plan 3 索引层：memories / triggers / promotions 三张表，WAL + FK 开启。
Index 类提供 index_memory（upsert + 重置 triggers）/ get_memory /
list_by_type（默认排除 soft-forgotten）/ record_recall / fingerprint
（sha1 body 前 500 字）等 helper。Markdown 仍 source of truth；本表
可重建。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 3：Storage 集成（save 后自动 index）

**Files:**
- Modify: `memoryd/src/memoryd/storage.py`
- Modify: `memoryd/tests/test_storage.py`

`save_session` 写盘后调 `Index.index_memory`；加 6 种类型共用的 `save_memory` helper（自动按 type 落到对应子目录）。

- [ ] **Step 1：在 `memoryd/tests/test_storage.py` 末尾追加**

```python
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory


def test_save_session_indexes_into_sqlite(memory_root: Path, sample_session: SessionMemory, monkeypatch):
    """save_session calls Index.index_memory automatically."""
    monkeypatch.setenv("MEMORYD_DATA_ROOT", str(memory_root))
    from memoryd.storage import save_session  # re-import to pick up env

    save_session(memory_root, sample_session)
    idx = open_index(memory_root / "index.db")
    row = idx.get_memory(sample_session.frontmatter.slug)
    assert row is not None
    assert row["type"] == sample_session.frontmatter.type
    idx.close()


def test_save_memory_routes_decision_to_decisions_dir(memory_root: Path):
    from memoryd.storage import save_memory

    decision = SessionMemory(
        frontmatter=Frontmatter(
            title="logo decision",
            slug="2026-05-14-logo-decision",
            type="decision",
            scope_hash="proj1",
            triggers=["logo"],
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="深蓝+银灰",
    )
    path = save_memory(memory_root, decision)
    assert path.parent.name == "decisions"
    assert path.parent.parent.name == "proj1"
    assert path.exists()


def test_save_memory_routes_each_type_to_own_dir(memory_root: Path):
    from memoryd.storage import save_memory
    for kind, expected_dir in [
        ("preference", "preferences"),
        ("fact", "facts"),
        ("playbook", "playbooks"),
        ("warning", "warnings"),
    ]:
        m = SessionMemory(
            frontmatter=Frontmatter(
                title="t",
                slug=f"2026-05-14-{kind}",
                type=kind,
                scope_hash="h",
                source="manual",
                created_at=datetime(2026, 5, 14),
            ),
            body="b",
        )
        path = save_memory(memory_root, m)
        assert path.parent.name == expected_dir, f"type={kind} -> {path}"
```

`from datetime import datetime` 若未在文件顶 import，加上。

- [ ] **Step 2：失败**

Run: `cd memoryd && uv run pytest tests/test_storage.py -v -k "indexes_into_sqlite or save_memory_routes"`
Expected: 3 FAIL（save_memory ImportError / save_session 没碰 sqlite）。

- [ ] **Step 3：改 `memoryd/src/memoryd/storage.py`**

完整新文件：

```python
"""Markdown file storage for memory entries.

Plan 1: `save_session` writes to `<root>/scopes/<hash>/sessions/<slug>.md`.
Plan 3: `save_memory` is the generic helper that routes any of the 6 types
        to its own subdirectory (decisions/ preferences/ facts/ playbooks/
        warnings/ — sessions/ stays where Plan 1 put it). save_session is
        kept as backwards-compat shim → save_memory.

Both helpers also call Index.index_memory so the SQLite index stays in
sync with disk. Index opens lazily and is closed per call.
"""
from __future__ import annotations

import re
from pathlib import Path

from .index import open_index
from .schema import SessionMemory


_TYPE_TO_DIR = {
    "session": "sessions",
    "decision": "decisions",
    "preference": "preferences",
    "fact": "facts",
    "playbook": "playbooks",
    "warning": "warnings",
}


_SAFE_SLUG = re.compile(r"^[A-Za-z0-9._-]+$")


def _validate_slug(slug: str) -> None:
    if not _SAFE_SLUG.match(slug):
        raise ValueError(f"unsafe slug: {slug!r}")
    if ".." in slug:
        raise ValueError(f"slug contains ..: {slug!r}")


def _type_dir(root: Path, scope_hash: str, type_: str) -> Path:
    subdir = _TYPE_TO_DIR.get(type_)
    if subdir is None:
        raise ValueError(f"unknown memory type: {type_!r}")
    return root / "scopes" / scope_hash / subdir


def save_memory(root: Path, mem: SessionMemory) -> Path:
    """Write a memory to its type-specific subdirectory and index it."""
    _validate_slug(mem.frontmatter.slug)
    target_dir = _type_dir(root, mem.frontmatter.scope_hash, mem.frontmatter.type)
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{mem.frontmatter.slug}.md"
    path.write_text(mem.to_markdown(), encoding="utf-8")

    # Update SQLite index. body_path is relative to data root for portability.
    body_rel = str(path.relative_to(root))
    idx = open_index(root / "index.db")
    try:
        idx.index_memory(mem, body_path=body_rel)
    finally:
        idx.close()
    return path


def save_session(root: Path, session: SessionMemory) -> Path:
    """Plan 1 entry; now routes through save_memory."""
    return save_memory(root, session)


def load_session(path: Path) -> SessionMemory:
    text = path.read_text(encoding="utf-8")
    return SessionMemory.from_markdown(text)


def list_sessions(root: Path, scope_hash: str) -> list[Path]:
    """List session `.md` files for a scope (Plan 1 API kept verbatim)."""
    scope_dir = root / "scopes" / scope_hash / "sessions"
    if not scope_dir.exists():
        return []
    return sorted(scope_dir.glob("*.md"))


def list_by_type(root: Path, scope_hash: str, type_: str) -> list[Path]:
    """List `.md` files of any single type for a scope."""
    d = _type_dir(root, scope_hash, type_)
    if not d.exists():
        return []
    return sorted(d.glob("*.md"))


# Backwards-compat: callers in mirror_codex.py / mirror_openclaw.py use
# this private helper to find scope dirs; keep export.
def _scope_dir(root: Path, scope_hash: str) -> Path:
    return root / "scopes" / scope_hash / "sessions"
```

- [ ] **Step 4：跑测试**

Run: `cd memoryd && uv run pytest tests/test_storage.py -v`
Expected: 6 passed（原 4 + 新 3，自动加测试 1 个 routes_each_type 含 4 个 type 子断言但是 1 个测试用例）。

加全量回归：

Run: `cd memoryd && uv run pytest -v`
Expected: 65 + 4 = 69 passed（Task 1 已加 4；Task 3 加 3）。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/storage.py memoryd/tests/test_storage.py
git commit -m "$(cat <<'EOF'
storage 集成 SQLite index + 加 save_memory 多类型路由

save_session 现在调 save_memory 路由到正确子目录（sessions/decisions/
preferences/facts/playbooks/warnings/）并 index 进 SQLite。Plan 1 的
save_session 签名保留为薄壳兼容旧代码（Plan 2.5 cli.capture / mirror_
codex / mirror_openclaw 都通过它）。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 4：rebuild-index CLI 子命令

**Files:**
- Modify: `memoryd/src/memoryd/cli.py`
- Modify: `memoryd/tests/test_cli.py`

`memoryd rebuild-index` 删旧 index.db，遍历所有 `.md` 文件重建。

- [ ] **Step 1：在 `memoryd/tests/test_cli.py` 末尾加**

```python
def test_rebuild_index_recreates_db_from_markdown(memory_root: Path, tmp_path: Path):
    """rebuild-index wipes index.db and re-walks all .md files."""
    import subprocess
    import os
    from memoryd.index import open_index
    from memoryd.schema import Frontmatter, SessionMemory
    from memoryd.storage import save_memory

    # save 3 memories; this auto-indexes them
    for i in range(3):
        save_memory(
            memory_root,
            SessionMemory(
                frontmatter=Frontmatter(
                    title=f"t{i}",
                    slug=f"2026-05-14-x{i}",
                    type="session",
                    scope_hash="proj1",
                    source="manual",
                    created_at=datetime(2026, 5, 14),
                ),
                body=f"body {i}",
            ),
        )

    # Delete the db to simulate corruption / first run
    (memory_root / "index.db").unlink()

    proc = subprocess.run(
        ["uv", "run", "memoryd", "rebuild-index"],
        capture_output=True,
        text=True,
        cwd="/Users/abble/project-management-personal/memoryd",
        env={**os.environ, "MEMORYD_DATA_ROOT": str(memory_root)},
        timeout=30,
    )
    assert proc.returncode == 0, f"stderr: {proc.stderr}"

    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT slug FROM memories ORDER BY slug").fetchall()
    assert [r[0] for r in rows] == ["2026-05-14-x0", "2026-05-14-x1", "2026-05-14-x2"]
    idx.close()
```

- [ ] **Step 2：跑失败**

Run: `cd memoryd && uv run pytest tests/test_cli.py::test_rebuild_index_recreates_db_from_markdown -v`
Expected: FAIL（unrecognized command rebuild-index）。

- [ ] **Step 3：改 `memoryd/src/memoryd/cli.py`，在 main() subparser 段加新子命令注册**

加 imports（如未有）：

```python
from .index import open_index as _open_idx
from .storage import load_session
```

在 `cmd_capture` / `cmd_mirror` 之后加：

```python
def cmd_rebuild_index(args: argparse.Namespace) -> int:
    memory_root = _data_root()
    db_path = memory_root / "index.db"
    if db_path.exists():
        db_path.unlink()
    idx = _open_idx(db_path)
    scopes_dir = memory_root / "scopes"
    if not scopes_dir.exists():
        idx.close()
        print("rebuild-index: no scopes dir; nothing to do", file=sys.stderr)
        return 0
    count = 0
    for md in scopes_dir.rglob("*.md"):
        try:
            mem = load_session(md)
        except Exception as e:
            print(f"skip {md}: {e}", file=sys.stderr)
            continue
        body_rel = str(md.relative_to(memory_root))
        idx.index_memory(mem, body_path=body_rel)
        count += 1
    idx.close()
    print(f"rebuild-index: {count} memories indexed", file=sys.stderr)
    return 0
```

在 main() subparser 注册段加：

```python
    p_rebuild = subs.add_parser("rebuild-index", help="wipe and rebuild SQLite index from all Markdown files")
    p_rebuild.set_defaults(func=cmd_rebuild_index)
```

- [ ] **Step 4：跑测试**

Run: `cd memoryd && uv run pytest tests/test_cli.py -v -k rebuild`
Expected: 1 passed.

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 69 + 1 = 70 passed。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/cli.py memoryd/tests/test_cli.py
git commit -m "$(cat <<'EOF'
加 memoryd rebuild-index CLI

从 ~/.local/share/memoryd/scopes/ 下所有 .md 文件重建 index.db。损坏 /
版本升级 / 跨设备复制时使用。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 5：Search 扩展（SQLite-backed + type filter + include_decayed）

**Files:**
- Modify: `memoryd/src/memoryd/search.py`
- Modify: `memoryd/tests/test_search.py`

新 `search_sessions` 走 SQLite + triggers 表 + LIKE body_path 上的 `.md` 全文（ripgrep 仍作 fallback）。加 `type_` / `include_decayed` 参数。每次命中调 `Index.record_recall`。

- [ ] **Step 1：在 `memoryd/tests/test_search.py` 末尾加**

```python
def test_search_filters_by_type(populated_root: Path):
    from memoryd.storage import save_memory
    from memoryd.schema import Frontmatter, SessionMemory

    # add a decision in same scope
    save_memory(populated_root, SessionMemory(
        frontmatter=Frontmatter(
            title="logo decision",
            slug="2026-05-14-logo-decision",
            type="decision",
            scope_hash="scope_a",
            triggers=["logo"],
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="深蓝+银灰",
    ))

    sessions_only = search_sessions(populated_root, scope_hash="scope_a", query="logo", type_="session")
    decisions_only = search_sessions(populated_root, scope_hash="scope_a", query="logo", type_="decision")

    titles_s = {h.title for h in sessions_only}
    titles_d = {h.title for h in decisions_only}
    assert "logo decision" not in titles_s
    assert "logo decision" in titles_d


def test_search_excludes_soft_forgotten_by_default(populated_root: Path):
    from memoryd.index import open_index

    idx = open_index(populated_root / "index.db")
    idx.update_decay_state("2026-05-09-logo", "soft-forgotten")
    idx.close()

    hits = search_sessions(populated_root, scope_hash="scope_a", query="深蓝")
    assert all(h.slug != "2026-05-09-logo" for h in hits)

    hits_all = search_sessions(populated_root, scope_hash="scope_a", query="深蓝", include_decayed=True)
    assert any(h.slug == "2026-05-09-logo" for h in hits_all)


def test_search_bumps_recall_count_on_hit(populated_root: Path):
    from memoryd.index import open_index

    search_sessions(populated_root, scope_hash="scope_a", query="深蓝")
    idx = open_index(populated_root / "index.db")
    row = idx.get_memory("2026-05-09-logo")
    assert row["recall_count"] >= 1
    idx.close()
```

- [ ] **Step 2：跑失败**

Run: `cd memoryd && uv run pytest tests/test_search.py -v -k "type or forgotten or recall"`
Expected: FAIL（type_ unexpected kw / include_decayed unexpected）。

- [ ] **Step 3：改 `memoryd/src/memoryd/search.py`**

完整文件（注意 ripgrep 部分保留作 fallback）：

```python
"""Search over memoryd memory files.

Plan 3 prefers SQLite + triggers + LIKE-on-body for speed and structured
filters; falls back to ripgrep when needed for full-text patterns the
SQLite path can't express. Every hit bumps recall_count via record_recall.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .index import open_index
from .schema import SessionMemory
from .storage import load_session


@dataclass(frozen=True)
class SearchHit:
    path: Path
    title: str
    slug: str
    triggers: list[str]
    excerpt: str


def _hit_from_row(row: dict[str, Any], memory_root: Path, excerpt: str) -> SearchHit:
    return SearchHit(
        path=memory_root / row["body_path"],
        title=row["title"],
        slug=row["slug"],
        triggers=[],
        excerpt=excerpt,
    )


def search_sessions(
    root: Path,
    scope_hash: str,
    query: str,
    *,
    type_: str | None = None,
    include_decayed: bool = False,
    limit: int = 20,
) -> list[SearchHit]:
    """Search memories in a scope for `query` (case-insensitive substring).

    type_=None → all six types; otherwise restricts to that type.
    include_decayed=False → excludes soft-forgotten rows.
    Bumps recall_count on every hit via record_recall.
    """
    idx = open_index(root / "index.db")
    try:
        # First try trigger match (cheap, structured)
        sql_t = (
            "SELECT m.* FROM memories m JOIN triggers t ON m.slug = t.slug "
            "WHERE m.scope_hash = ? AND LOWER(t.trigger) LIKE LOWER(?)"
        )
        args: list[Any] = [scope_hash, f"%{query}%"]
        if type_ is not None:
            sql_t += " AND m.type = ?"
            args.append(type_)
        if not include_decayed:
            sql_t += " AND m.decay_state != 'soft-forgotten'"
        sql_t += " GROUP BY m.slug ORDER BY m.created_at DESC LIMIT ?"
        args.append(limit)
        trigger_rows = idx.conn.execute(sql_t, args).fetchall()

        # Then full-text on body via reading body_path (small per file)
        sql_a = "SELECT * FROM memories WHERE scope_hash = ?"
        a_args: list[Any] = [scope_hash]
        if type_ is not None:
            sql_a += " AND type = ?"
            a_args.append(type_)
        if not include_decayed:
            sql_a += " AND decay_state != 'soft-forgotten'"
        sql_a += " ORDER BY created_at DESC"
        all_rows = idx.conn.execute(sql_a, a_args).fetchall()

        seen: set[str] = set()
        hits: list[SearchHit] = []
        for row in trigger_rows:
            d = dict(row)
            if d["slug"] in seen:
                continue
            seen.add(d["slug"])
            excerpt = _excerpt_for(root / d["body_path"], query)
            hits.append(_hit_from_row(d, root, excerpt))

        for row in all_rows:
            d = dict(row)
            if d["slug"] in seen:
                continue
            md_path = root / d["body_path"]
            if not md_path.exists():
                continue
            text = md_path.read_text(encoding="utf-8", errors="replace")
            if query.lower() in text.lower():
                seen.add(d["slug"])
                excerpt = _excerpt_for(md_path, query)
                hits.append(_hit_from_row(d, root, excerpt))
            if len(hits) >= limit:
                break

        # Bump recall_count for each hit
        for h in hits:
            idx.record_recall(h.slug)
        return hits[:limit]
    finally:
        idx.close()


def _excerpt_for(md_path: Path, query: str) -> str:
    """Find the first line containing `query` in the .md; ≤ 200 chars."""
    try:
        for line in md_path.read_text(encoding="utf-8", errors="replace").splitlines():
            if query.lower() in line.lower():
                return line[:200]
    except OSError:
        pass
    return ""
```

注意：保留 `search_sessions` 函数名 + `SearchHit` 类型以兼容 Plan 1 调用方（server.py）。

- [ ] **Step 4：跑测试**

Run: `cd memoryd && uv run pytest tests/test_search.py -v`
Expected: 原 7 + 新 3 = 10 passed（注意 Plan 1 原测试 5 个 + Plan 2.5 加的 2 个 = 7，含 limit / corrupt 等）。

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 70 + 3 = 73 passed。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/search.py memoryd/tests/test_search.py
git commit -m "$(cat <<'EOF'
search 扩展：SQLite-backed + type filter + include_decayed + 自动 recall

搜索改走 SQLite index：先按 triggers 表匹配，再扫 body 正文。加
type_ 过滤、include_decayed flag（默认排除 soft-forgotten）。每次命中
自动 bump recall_count + last_recalled_at（喂给 Task 9 decay 状态机）。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 6：Config 模块

**Files:**
- Create: `memoryd/src/memoryd/config.py`
- Create: `memoryd/tests/test_config.py`
- Modify: `memoryd/src/memoryd/cli.py`（加 `config` 子命令组）

`memoryd config` 子命令支持 `show` / `set` / `get`。底层 TOML 文件 `~/.config/memoryd/config.toml`。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_config.py`**

```python
"""Config read/write tests."""
import tomllib
from pathlib import Path

import pytest

from memoryd.config import (
    DEFAULT_CONFIG,
    get_config_path,
    load_config,
    set_config_key,
    show_config,
)


def test_load_default_when_file_missing(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    cfg = load_config()
    assert cfg["llm"]["provider"] == DEFAULT_CONFIG["llm"]["provider"]


def test_set_key_creates_file_and_value(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    set_config_key("llm.provider", "openai")
    cfg_path = get_config_path()
    parsed = tomllib.loads(cfg_path.read_text())
    assert parsed["llm"]["provider"] == "openai"


def test_set_nested_key_preserves_other_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    set_config_key("llm.provider", "anthropic")
    set_config_key("llm.model", "claude-haiku-4-5")
    cfg = load_config()
    assert cfg["llm"]["provider"] == "anthropic"
    assert cfg["llm"]["model"] == "claude-haiku-4-5"


def test_show_config_is_dict(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    out = show_config()
    assert isinstance(out, dict)
    assert "llm" in out


def test_set_key_rejects_invalid_path(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    with pytest.raises(ValueError, match="path"):
        set_config_key("bare", "value")  # missing dot → invalid
```

- [ ] **Step 2：失败**

Run: `cd memoryd && uv run pytest tests/test_config.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3：实现 `memoryd/src/memoryd/config.py`**

```python
"""User-level memoryd config at ~/.config/memoryd/config.toml.

Schema (minimal v0.3 — Plan 3):
    [llm]
    provider = "anthropic"      # anthropic | openai | openrouter | local
    model = "claude-haiku-4-5"
    api_key_env = "ANTHROPIC_API_KEY"
    request_timeout_sec = 30

    [prompts]
    dura_extract = ""           # path override; empty → use bundled
"""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import Any


DEFAULT_CONFIG: dict[str, Any] = {
    "llm": {
        "provider": "anthropic",
        "model": "claude-haiku-4-5",
        "api_key_env": "ANTHROPIC_API_KEY",
        "request_timeout_sec": 30,
    },
    "prompts": {
        "dura_extract": "",
    },
}


def get_config_path() -> Path:
    home = os.environ.get("MEMORYD_CONFIG_HOME")
    base = Path(home) if home else (Path.home() / ".config" / "memoryd")
    return base / "config.toml"


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _merge_dict(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(merged.get(k), dict):
            merged[k] = _merge_dict(merged[k], v)
        else:
            merged[k] = v
    return merged


def load_config() -> dict[str, Any]:
    p = get_config_path()
    if not p.exists():
        return dict(DEFAULT_CONFIG)
    parsed = tomllib.loads(p.read_text(encoding="utf-8"))
    return _merge_dict(DEFAULT_CONFIG, parsed)


def show_config() -> dict[str, Any]:
    return load_config()


def _render_toml(data: dict[str, Any]) -> str:
    """Hand-rolled minimal TOML writer (stdlib has no writer)."""
    lines: list[str] = []
    # Top-level scalars first (none in our schema)
    for k, v in data.items():
        if isinstance(v, dict):
            continue
        lines.append(f"{k} = {json.dumps(v)}")
    for section, body in data.items():
        if not isinstance(body, dict):
            continue
        lines.append(f"\n[{section}]")
        for k, v in body.items():
            lines.append(f"{k} = {json.dumps(v)}")
    return "\n".join(lines) + "\n"


def set_config_key(key_path: str, value: Any) -> None:
    """`key_path` like `llm.provider`; raises on bare keys (no dot)."""
    if "." not in key_path:
        raise ValueError(f"invalid key path (need at least one dot): {key_path!r}")
    parts = key_path.split(".")
    cfg = load_config()
    cursor = cfg
    for p in parts[:-1]:
        if p not in cursor or not isinstance(cursor[p], dict):
            cursor[p] = {}
        cursor = cursor[p]
    cursor[parts[-1]] = value
    _atomic_write(get_config_path(), _render_toml(cfg))
```

- [ ] **Step 4：在 `memoryd/src/memoryd/cli.py` 加 `config` 子命令**

加 import：

```python
from . import config as config_mod
```

加 wrapper：

```python
def cmd_config(args: argparse.Namespace) -> int:
    if args.config_action == "show":
        import json as _j
        print(_j.dumps(config_mod.show_config(), indent=2, ensure_ascii=False))
    elif args.config_action == "set":
        # try to coerce value to int/float/bool/str
        v: object = args.value
        try:
            v = int(args.value)
        except ValueError:
            try:
                v = float(args.value)
            except ValueError:
                if args.value.lower() in ("true", "false"):
                    v = args.value.lower() == "true"
        config_mod.set_config_key(args.key, v)
        print(f"set {args.key} = {v!r}", file=sys.stderr)
    return 0
```

在 main() subparser 注册段加：

```python
    p_config = subs.add_parser("config", help="show / set memoryd config")
    cfg_subs = p_config.add_subparsers(dest="config_action", required=True)
    cfg_subs.add_parser("show", help="print resolved config as JSON")
    p_set = cfg_subs.add_parser("set", help="set a dotted key (e.g. llm.provider openai)")
    p_set.add_argument("key")
    p_set.add_argument("value")
    p_config.set_defaults(func=cmd_config)
```

- [ ] **Step 5：跑测试**

Run: `cd memoryd && uv run pytest tests/test_config.py -v`
Expected: 5 passed.

加 CLI smoke：

```bash
cd /Users/abble/project-management-personal/memoryd
MEMORYD_CONFIG_HOME=/tmp/memoryd-cfg-test uv run memoryd config show
MEMORYD_CONFIG_HOME=/tmp/memoryd-cfg-test uv run memoryd config set llm.provider openai
MEMORYD_CONFIG_HOME=/tmp/memoryd-cfg-test uv run memoryd config show | grep openai
```

期望第 3 个 grep 输出含 `"provider": "openai"`。

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 73 + 5 = 78 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/config.py memoryd/src/memoryd/cli.py memoryd/tests/test_config.py
git commit -m "$(cat <<'EOF'
加 memoryd config 子命令 + ~/.config/memoryd/config.toml

show / set / get 读写用户级配置。默认 schema 含 llm.provider /
llm.model / llm.api_key_env / prompts.dura_extract。set 支持点路径
（llm.provider）+ value 自动类型转换。MEMORYD_CONFIG_HOME env 覆盖
便于单测。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 7：LLM provider 抽象 + Anthropic 实现

**Files:**
- Modify: `memoryd/pyproject.toml`（加 anthropic 依赖）
- Create: `memoryd/src/memoryd/llm.py`
- Create: `memoryd/tests/test_llm.py`

Protocol-based design：可插拔多 provider。Plan 3 只实现 Anthropic；其余 stub 留 Plan 5/6 扩展。

- [ ] **Step 1：加 `anthropic>=0.40` 到 `memoryd/pyproject.toml` 的 dependencies 列表，跑 `cd memoryd && uv sync`**

修改 dependencies 列表为：

```toml
dependencies = [
    "mcp[cli]>=1.0.0",
    "pydantic>=2.0",
    "pyyaml>=6.0",
    "watchdog>=4.0",
    "anthropic>=0.40",
]
```

跑 `uv sync` 验证安装。

- [ ] **Step 2：写失败测试 `memoryd/tests/test_llm.py`**

```python
"""LLM provider tests (mock HTTP, no real API calls)."""
from unittest.mock import MagicMock

import pytest

from memoryd.llm import (
    AnthropicProvider,
    LLMUnavailable,
    get_provider,
)


def test_anthropic_provider_calls_messages_create(monkeypatch):
    """AnthropicProvider.complete builds a Messages request and returns text."""
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [MagicMock(type="text", text="hello world")]
    fake_client.messages.create.return_value = fake_msg

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    p = AnthropicProvider(client=fake_client, model="claude-haiku-4-5")
    out = p.complete(system="sys", user="user prompt")
    assert out == "hello world"
    args, kwargs = fake_client.messages.create.call_args
    assert kwargs["model"] == "claude-haiku-4-5"
    assert kwargs["system"] == "sys"
    assert kwargs["messages"][0]["role"] == "user"
    assert kwargs["messages"][0]["content"] == "user prompt"


def test_anthropic_provider_concatenates_multi_block_content(monkeypatch):
    fake_client = MagicMock()
    fake_msg = MagicMock()
    fake_msg.content = [
        MagicMock(type="text", text="part1"),
        MagicMock(type="text", text="part2"),
    ]
    fake_client.messages.create.return_value = fake_msg
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")

    p = AnthropicProvider(client=fake_client)
    assert p.complete(system="s", user="u") == "part1part2"


def test_anthropic_provider_raises_on_missing_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(LLMUnavailable, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()


def test_get_provider_returns_anthropic_when_configured(monkeypatch, tmp_path):
    """get_provider() reads ~/.config/memoryd/config.toml."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    p = get_provider()
    assert isinstance(p, AnthropicProvider)


def test_get_provider_raises_for_unknown_provider(monkeypatch, tmp_path):
    monkeypatch.setenv("MEMORYD_CONFIG_HOME", str(tmp_path))
    # write config with unknown provider
    cfg = tmp_path / "config.toml"
    cfg.write_text('[llm]\nprovider = "weird"\n')
    with pytest.raises(LLMUnavailable, match="weird"):
        get_provider()
```

- [ ] **Step 3：失败**

Run: `cd memoryd && uv run pytest tests/test_llm.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 4：实现 `memoryd/src/memoryd/llm.py`**

```python
"""LLM provider abstraction. Plan 3 implements Anthropic; other providers
(openai / openrouter / local ollama) are stubs that raise LLMUnavailable
until Plan 5+ adds them.
"""
from __future__ import annotations

import os
from typing import Protocol

from .config import load_config


class LLMUnavailable(Exception):
    """Raised when no usable provider can be constructed."""


class LLMProvider(Protocol):
    def complete(self, *, system: str, user: str, model: str | None = None) -> str: ...


class AnthropicProvider:
    """Calls Anthropic Messages API.

    `anthropic` SDK auto-respects HTTPS_PROXY / ANTHROPIC_API_KEY env.
    Pass `client=` to inject a mock in tests.
    """

    def __init__(self, *, client: object | None = None, model: str = "claude-haiku-4-5") -> None:
        if client is None:
            try:
                import anthropic
            except ImportError as e:
                raise LLMUnavailable("anthropic SDK not installed") from e
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise LLMUnavailable("ANTHROPIC_API_KEY env not set")
            client = anthropic.Anthropic()
        self.client = client
        self.model = model

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        msg = self.client.messages.create(
            model=model or self.model,
            max_tokens=2048,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        # Concatenate all text blocks
        parts: list[str] = []
        for block in getattr(msg, "content", []) or []:
            if getattr(block, "type", "") == "text":
                parts.append(getattr(block, "text", ""))
        return "".join(parts)


def get_provider() -> LLMProvider:
    """Construct the provider configured in ~/.config/memoryd/config.toml."""
    cfg = load_config()
    name = cfg["llm"]["provider"]
    model = cfg["llm"]["model"]
    if name == "anthropic":
        return AnthropicProvider(model=model)
    raise LLMUnavailable(f"unsupported llm provider: {name!r}")
```

- [ ] **Step 5：跑测试**

Run: `cd memoryd && uv run pytest tests/test_llm.py -v`
Expected: 5 passed.

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 78 + 5 = 83 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/pyproject.toml memoryd/uv.lock memoryd/src/memoryd/llm.py memoryd/tests/test_llm.py
git commit -m "$(cat <<'EOF'
加 LLM provider 抽象 + Anthropic 实现

LLMProvider Protocol + AnthropicProvider 调 messages.create；其他
provider（openai/openrouter/local）留 Plan 5/6 实现，当前 raise
LLMUnavailable。get_provider() 从 ~/.config/memoryd/config.toml 读
provider/model 字段。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 8：DURA prompt + analyze-session + capture hook

**Files:**
- Create: `memoryd/src/memoryd/prompts/__init__.py`
- Create: `memoryd/src/memoryd/prompts/dura_extract.txt`
- Create: `memoryd/src/memoryd/governance/__init__.py`
- Create: `memoryd/src/memoryd/governance/analyze.py`
- Modify: `memoryd/src/memoryd/cli.py`（加 `analyze-session` 子命令 + 在 capture_session 末尾 fork _spawn_analyze）
- Create: `memoryd/tests/test_governance_analyze.py`
- Modify: `memoryd/tests/test_cli.py`（验证 capture 后 fork 行为）

- [ ] **Step 1：写 prompt 模板 `memoryd/src/memoryd/prompts/__init__.py`（空 marker）**

```python
"""Plan 3 prompt templates."""
```

`memoryd/src/memoryd/prompts/dura_extract.txt`：

```
你正在从一段 AI 会话里抽取值得长期记住的内容。

4 准则（DURA），每项给 0.0-1.0 分。只有 4 项都 ≥ 0.6 才推荐提升。

- D**urability**：3 个月后这条信息还有意义吗？
- U**niqueness**：这条是否已经在现有记忆里？
- R**etrievability**：用户能想出 ≥2 个触发词来找它吗？
- A**uthority**：是用户明确决策 / 事实，还是 AI 推断？

输入：

Session 正文（已截到 8000 字符内）：
<<<
{{session_body}}
>>>

Scope（项目根路径）：{{scope_root}}

该 scope 现有长期记忆 titles（避免重复推荐）：
{{existing_titles}}

输出：仅 JSON，无解释，candidates 数组。schema：

[
  {
    "type": "decision" | "preference" | "fact" | "playbook" | "warning",
    "title": "<一行，≤100 字>",
    "body": "<markdown 正文，≤500 字>",
    "triggers": ["<关键词>", "<关键词>"],
    "dura": { "D": 0.0-1.0, "U": 0.0-1.0, "R": 0.0-1.0, "A": 0.0-1.0 },
    "reasoning": "<一行>",
    "supersedes": []
  }
]

如果没有任何候选满足 4 项 ≥ 0.6，输出 `[]`。
绝不输出非 JSON 内容（包括 ```json fence）。
```

- [ ] **Step 2：写失败测试 `memoryd/tests/test_governance_analyze.py`**

```python
"""governance/analyze.py tests with mock LLM."""
import json
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.governance.analyze import (
    analyze_session,
    build_dura_prompt,
    parse_candidates,
)
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


class _FakeLLM:
    def __init__(self, returns: str) -> None:
        self.returns = returns
        self.called_with = None

    def complete(self, *, system: str, user: str, model: str | None = None) -> str:
        self.called_with = (system, user)
        return self.returns


def _build_session_in_root(memory_root: Path, body: str = "user said: 决定 logo 用深蓝色") -> SessionMemory:
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-14-s",
            type="session",
            scope_hash="proj1",
            triggers=["logo"],
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body=body,
    )
    save_memory(memory_root, s)
    return s


def test_build_dura_prompt_substitutes_placeholders():
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="t",
            slug="2026-05-14-x",
            type="session",
            scope_hash="proj1",
            source="manual",
            created_at=datetime(2026, 5, 14),
        ),
        body="HELLO BODY",
    )
    prompt = build_dura_prompt(s, scope_root="/path", existing_titles=["a", "b"])
    assert "HELLO BODY" in prompt
    assert "/path" in prompt
    assert "a" in prompt and "b" in prompt


def test_parse_candidates_strict_json():
    raw = '[{"type":"decision","title":"x","body":"y","triggers":["t1","t2"],"dura":{"D":0.7,"U":0.8,"R":0.7,"A":0.9},"reasoning":"r","supersedes":[]}]'
    out = parse_candidates(raw)
    assert len(out) == 1
    assert out[0]["type"] == "decision"


def test_parse_candidates_tolerates_fenced_output():
    raw = "```json\n[]\n```"
    out = parse_candidates(raw)
    assert out == []


def test_parse_candidates_returns_empty_on_garbage():
    raw = "not json at all"
    out = parse_candidates(raw)
    assert out == []


def test_parse_candidates_filters_below_threshold():
    raw = json.dumps([
        {
            "type": "decision", "title": "high", "body": "x", "triggers": ["a", "b"],
            "dura": {"D": 0.9, "U": 0.8, "R": 0.7, "A": 0.95}, "reasoning": "", "supersedes": []
        },
        {
            "type": "fact", "title": "low", "body": "x", "triggers": ["c", "d"],
            "dura": {"D": 0.9, "U": 0.5, "R": 0.7, "A": 0.95}, "reasoning": "", "supersedes": []
        },
    ])
    out = parse_candidates(raw)
    titles = [c["title"] for c in out]
    assert "high" in titles
    assert "low" not in titles  # U=0.5 < 0.6


def test_analyze_session_writes_promotion(memory_root: Path):
    sess = _build_session_in_root(memory_root)
    fake = _FakeLLM(json.dumps([{
        "type": "decision",
        "title": "use deep blue for logo",
        "body": "decided deep blue + silver-gray",
        "triggers": ["logo", "blue"],
        "dura": {"D": 0.9, "U": 0.9, "R": 0.85, "A": 0.95},
        "reasoning": "user explicit",
        "supersedes": [],
    }]))
    analyze_session(memory_root, session_slug=sess.frontmatter.slug, provider=fake)

    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT proposed_type, proposed_title, status FROM promotions").fetchall()
    idx.close()
    assert len(rows) == 1
    assert rows[0][0] == "decision"
    assert rows[0][1] == "use deep blue for logo"
    assert rows[0][2] == "pending"


def test_analyze_session_skips_when_session_not_found(memory_root: Path):
    """Missing session is a no-op (best-effort daemon)."""
    fake = _FakeLLM("[]")
    # Should not raise:
    analyze_session(memory_root, session_slug="no-such-slug", provider=fake)


def test_analyze_session_handles_llm_returning_empty(memory_root: Path):
    sess = _build_session_in_root(memory_root)
    fake = _FakeLLM("[]")
    analyze_session(memory_root, session_slug=sess.frontmatter.slug, provider=fake)
    from memoryd.index import open_index
    idx = open_index(memory_root / "index.db")
    rows = idx.conn.execute("SELECT * FROM promotions").fetchall()
    idx.close()
    assert rows == []
```

- [ ] **Step 3：失败**

Run: `cd memoryd && uv run pytest tests/test_governance_analyze.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 4：实现 `memoryd/src/memoryd/governance/__init__.py`**

```python
"""Plan 3 governance jobs: analyze / decay / merge / digest."""
```

`memoryd/src/memoryd/governance/analyze.py`：

```python
"""Run DURA 4-criteria extraction on a single session.

Strategy:
- load session.md
- query existing long-term titles in same scope (for U criterion)
- call LLM with bundled prompt template
- parse JSON candidates; filter by DURA ≥ 0.6 all four
- write each as a row in promotions table (status=pending)

Never raises into caller — best-effort daemon. On LLM failure logs +
skips. Session capture path keeps working regardless.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from ..config import load_config
from ..index import open_index
from ..schema import SessionMemory


_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "dura_extract.txt"


def build_dura_prompt(
    session: SessionMemory,
    *,
    scope_root: str,
    existing_titles: list[str],
) -> str:
    cfg = load_config()
    override = cfg.get("prompts", {}).get("dura_extract", "")
    if override and Path(override).exists():
        template = Path(override).read_text(encoding="utf-8")
    else:
        template = _PROMPT_PATH.read_text(encoding="utf-8")
    body_clip = session.body[:8000]
    return (
        template
        .replace("{{session_body}}", body_clip)
        .replace("{{scope_root}}", scope_root)
        .replace("{{existing_titles}}", "\n".join(f"- {t}" for t in existing_titles) or "(none)")
    )


def parse_candidates(raw: str) -> list[dict]:
    """Robust JSON parse: strip fences, accept array."""
    stripped = raw.strip()
    if stripped.startswith("```"):
        # strip leading fence (with optional 'json' tag) and trailing fence
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        # try to find an array somewhere
        m = re.search(r"\[.*\]", stripped, re.DOTALL)
        if not m:
            return []
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return []
    if not isinstance(data, list):
        return []
    out: list[dict] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        dura = item.get("dura") or {}
        if not all(k in dura for k in ("D", "U", "R", "A")):
            continue
        if not all(isinstance(dura[k], (int, float)) and dura[k] >= 0.6 for k in ("D", "U", "R", "A")):
            continue
        out.append(item)
    return out


def analyze_session(
    memory_root: Path,
    *,
    session_slug: str,
    provider,
) -> None:
    """Best-effort: read session, ask LLM, write promotions. Never raises."""
    try:
        idx = open_index(memory_root / "index.db")
    except Exception:
        return
    try:
        row = idx.get_memory(session_slug)
        if row is None:
            return
        sess_path = memory_root / row["body_path"]
        if not sess_path.exists():
            return
        from ..storage import load_session
        session = load_session(sess_path)
        scope_hash = row["scope_hash"]

        # existing titles in same scope (long-term only — exclude sessions)
        existing_titles = []
        for t in ("decision", "preference", "fact", "playbook", "warning"):
            for r in idx.list_by_type(t, scope_hash=scope_hash):
                existing_titles.append(r["title"])

        prompt = build_dura_prompt(session, scope_root=scope_hash, existing_titles=existing_titles)
        try:
            raw = provider.complete(system="Extract durable insights.", user=prompt)
        except Exception:
            return
        candidates = parse_candidates(raw)

        now = datetime.now(timezone.utc).isoformat()
        for c in candidates:
            idx.conn.execute(
                """
                INSERT INTO promotions (
                  source_session_slug, proposed_type, proposed_title,
                  proposed_body, proposed_triggers, dura_score, reasoning,
                  proposed_supersedes, scope_hash, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    session_slug,
                    c["type"],
                    c["title"][:200],
                    c["body"][:5000],
                    json.dumps(c.get("triggers", [])),
                    json.dumps(c["dura"]),
                    c.get("reasoning", "")[:500],
                    json.dumps(c.get("supersedes", [])),
                    scope_hash,
                    now,
                ),
            )
        idx.conn.commit()
    finally:
        idx.close()
```

- [ ] **Step 5：CLI analyze-session 子命令 + capture 后 fork**

在 `memoryd/src/memoryd/cli.py` 加 import：

```python
from .governance.analyze import analyze_session as _analyze_session
from .llm import LLMUnavailable, get_provider
```

加 wrapper：

```python
def cmd_analyze_session(args: argparse.Namespace) -> int:
    memory_root = _data_root()
    try:
        provider = get_provider()
    except LLMUnavailable as e:
        print(f"analyze-session skip: {e}", file=sys.stderr)
        return 0
    _analyze_session(memory_root, session_slug=args.session_slug, provider=provider)
    print("analyze-session: ok", file=sys.stderr)
    return 0


def _spawn_analyze(session_slug: str) -> None:
    """Background spawn `memoryd analyze-session <slug>`. Never blocks."""
    try:
        subprocess.Popen(
            [sys.executable, "-m", "memoryd", "analyze-session", session_slug],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        pass
```

`capture_session` 函数末尾（在 `return save_session(...)` 之前）改成：

```python
    path = save_session(memory_root, session)
    _spawn_analyze(session.frontmatter.slug)
    return path
```

main() subparser 注册段加：

```python
    p_az = subs.add_parser("analyze-session", help="run DURA extraction on a session (called by capture hook)")
    p_az.add_argument("session_slug")
    p_az.set_defaults(func=cmd_analyze_session)
```

注意 import subprocess 顶部已有（Plan 1）。

- [ ] **Step 6：在 `memoryd/tests/test_cli.py` 末尾加 capture-fork 测试（用 mock 验证不真的跑 LLM）**

```python
def test_capture_session_spawns_analyze(memory_root: Path, tmp_path: Path, monkeypatch):
    """capture_session 完成后 fork memoryd analyze-session 后台。"""
    from memoryd.cli import capture_session
    spawned: list[list[str]] = []

    def fake_popen(cmd, **kwargs):
        spawned.append(list(cmd))
        class _P:
            pass
        return _P()

    monkeypatch.setattr("subprocess.Popen", fake_popen)

    transcript = tmp_path / "t.jsonl"
    transcript.write_text('{"type":"user","message":{"content":[{"type":"text","text":"hi"}]}}\n')
    cwd = tmp_path / "proj"
    cwd.mkdir()
    payload = {"session_id": "sf-test", "transcript_path": str(transcript), "cwd": str(cwd)}

    capture_session(payload, memory_root=memory_root, now=datetime(2026, 5, 14, 12, 0))

    assert any("analyze-session" in cmd for cmd in spawned)
```

- [ ] **Step 7：跑测试**

Run: `cd memoryd && uv run pytest tests/test_governance_analyze.py tests/test_cli.py -v -k "analyze or spawns"`
Expected: 8 + 1 = 9 passed.

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 83 + 9 = 92 passed.

- [ ] **Step 8：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/prompts/ memoryd/src/memoryd/governance/ memoryd/src/memoryd/cli.py memoryd/tests/test_governance_analyze.py memoryd/tests/test_cli.py
git commit -m "$(cat <<'EOF'
加 DURA 4 准则 analyze + capture 后 fork

- prompts/dura_extract.txt 模板：D/U/R/A 阈值 0.6
- governance/analyze.py: build_dura_prompt + parse_candidates（容忍
  fenced JSON / 严格 schema 过滤）+ analyze_session（best-effort，写
  promotions pending）
- cli.py: analyze-session 子命令；capture_session 末尾 _spawn_analyze
  后台 fork 不阻塞
- LLM 不可用 / 失败 / 返回空 / 返回非 JSON 全部静默 skip

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 9：Decay 状态机

**Files:**
- Create: `memoryd/src/memoryd/governance/decay.py`
- Create: `memoryd/tests/test_governance_decay.py`
- Modify: `memoryd/src/memoryd/cli.py`（加 `decay-sweep` 子命令）

状态机：`alive` → ttl 到期 + 没召回 → `dim` → 再 30 天没召回 → `soft-forgotten` → 再 90 天没召回 → 物理迁移到 `forgotten/` 子目录。

- [ ] **Step 1：写失败测试 `memoryd/tests/test_governance_decay.py`**

```python
"""Decay state machine tests."""
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.governance.decay import sweep_decay
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


def _make_session(slug: str, scope: str = "h", ttl_days: int | None = 90) -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title=slug,
            slug=slug,
            type="session",
            scope_hash=scope,
            source="manual",
            created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
            ttl_days=ttl_days,
        ),
        body="b",
    )


def _set_db_field(memory_root: Path, slug: str, field: str, value):
    idx = open_index(memory_root / "index.db")
    idx.conn.execute(f"UPDATE memories SET {field} = ? WHERE slug = ?", (value, slug))
    idx.conn.commit()
    idx.close()


def test_alive_to_dim_when_ttl_expired_and_never_recalled(memory_root: Path):
    s = _make_session("aged-session", ttl_days=90)
    save_memory(memory_root, s)
    # backdate created_at to 100 days ago; no last_recalled_at set
    _set_db_field(memory_root, "aged-session", "created_at",
                  (datetime.now(timezone.utc) - timedelta(days=100)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("aged-session")
    idx.close()
    assert row["decay_state"] == "dim"


def test_dim_to_soft_forgotten_after_30_days_no_recall(memory_root: Path):
    s = _make_session("aged-dim")
    save_memory(memory_root, s)
    # state=dim, last_recalled_at = 31 days ago
    _set_db_field(memory_root, "aged-dim", "decay_state", "dim")
    _set_db_field(memory_root, "aged-dim", "last_recalled_at",
                  (datetime.now(timezone.utc) - timedelta(days=31)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("aged-dim")
    idx.close()
    assert row["decay_state"] == "soft-forgotten"


def test_soft_forgotten_moved_to_forgotten_dir_after_90_more_days(memory_root: Path):
    s = _make_session("aged-sf")
    save_memory(memory_root, s)
    _set_db_field(memory_root, "aged-sf", "decay_state", "soft-forgotten")
    _set_db_field(memory_root, "aged-sf", "last_recalled_at",
                  (datetime.now(timezone.utc) - timedelta(days=91)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    forgotten_dir = memory_root / "scopes" / "h" / "forgotten"
    assert forgotten_dir.exists()
    assert list(forgotten_dir.glob("aged-sf.md")), "should be moved to forgotten/"


def test_recent_recall_resets_alive_keeps_alive(memory_root: Path):
    """If last_recalled_at is recent, even with old created_at, stays alive."""
    s = _make_session("recently-used")
    save_memory(memory_root, s)
    _set_db_field(memory_root, "recently-used", "created_at",
                  (datetime.now(timezone.utc) - timedelta(days=200)).isoformat())
    _set_db_field(memory_root, "recently-used", "last_recalled_at",
                  (datetime.now(timezone.utc) - timedelta(days=2)).isoformat())

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("recently-used")
    idx.close()
    assert row["decay_state"] == "alive"


def test_long_term_memory_with_null_ttl_never_decays(memory_root: Path):
    """Decisions / preferences etc have ttl_days=NULL → no auto decay."""
    s = SessionMemory(
        frontmatter=Frontmatter(
            title="lt",
            slug="long-term-1",
            type="decision",
            scope_hash="h",
            source="manual",
            created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
            ttl_days=None,
        ),
        body="x",
    )
    save_memory(memory_root, s)

    sweep_decay(memory_root, now=datetime.now(timezone.utc))

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("long-term-1")
    idx.close()
    assert row["decay_state"] == "alive"
```

- [ ] **Step 2：失败**

Run: `cd memoryd && uv run pytest tests/test_governance_decay.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3：实现 `memoryd/src/memoryd/governance/decay.py`**

```python
"""TTL + decay + soft-forget state machine.

State transitions on `memoryd decay-sweep`:
  alive  → dim          : ttl_days set + age_since_recall_or_create > ttl_days
  dim    → soft-forgot  : 30 days since last touch
  soft-f → forgotten/   : 90 more days since last touch (physical move)
  any    ← alive        : record_recall resets via search hits

`age_since_recall_or_create` = (now - max(last_recalled_at, created_at))
"""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..index import open_index


DIM_AFTER_TTL = 0           # days after ttl_days → enter dim
SOFT_FORGET_AFTER_DIM = 30  # days dim with no recall → soft-forgotten
FORGOTTEN_AFTER_SF = 90     # days soft-forgotten with no recall → physical move


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _age_days(now: datetime, ref_iso: str | None) -> float:
    ref = _parse_iso(ref_iso)
    if ref is None:
        return float("inf")
    return (now - ref).total_seconds() / 86400.0


def sweep_decay(memory_root: Path, *, now: datetime | None = None) -> dict[str, int]:
    """Walk SQLite index, transition states. Returns counts of each transition."""
    if now is None:
        now = datetime.now(timezone.utc)
    idx = open_index(memory_root / "index.db")
    counts = {"to_dim": 0, "to_soft_forgotten": 0, "to_forgotten_dir": 0}
    try:
        rows = idx.conn.execute(
            "SELECT slug, decay_state, ttl_days, created_at, last_recalled_at, body_path, scope_hash "
            "FROM memories"
        ).fetchall()
        for r in rows:
            row = dict(r)
            ttl = row["ttl_days"]
            state = row["decay_state"]
            # Effective "age since last touch": max of created_at / last_recalled_at gone.
            last_iso = row["last_recalled_at"] or row["created_at"]
            age = _age_days(now, last_iso)

            if state == "alive":
                if ttl is None:
                    continue  # long-term never auto-decays
                if age > ttl + DIM_AFTER_TTL:
                    idx.update_decay_state(row["slug"], "dim")
                    counts["to_dim"] += 1
            elif state == "dim":
                if age > (ttl or 0) + DIM_AFTER_TTL + SOFT_FORGET_AFTER_DIM:
                    idx.update_decay_state(row["slug"], "soft-forgotten")
                    counts["to_soft_forgotten"] += 1
            elif state == "soft-forgotten":
                if age > (ttl or 0) + DIM_AFTER_TTL + SOFT_FORGET_AFTER_DIM + FORGOTTEN_AFTER_SF:
                    # Physical move to scopes/<scope_hash>/forgotten/<slug>.md
                    src = memory_root / row["body_path"]
                    if not src.exists():
                        continue
                    dest_dir = memory_root / "scopes" / row["scope_hash"] / "forgotten"
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    dest = dest_dir / src.name
                    shutil.move(str(src), str(dest))
                    # update body_path in index
                    new_body_path = str(dest.relative_to(memory_root))
                    idx.conn.execute(
                        "UPDATE memories SET body_path = ? WHERE slug = ?",
                        (new_body_path, row["slug"]),
                    )
                    idx.conn.commit()
                    counts["to_forgotten_dir"] += 1
    finally:
        idx.close()
    return counts
```

- [ ] **Step 4：CLI 加 `decay-sweep`**

`cli.py` 加 wrapper：

```python
def cmd_decay_sweep(args: argparse.Namespace) -> int:
    from .governance.decay import sweep_decay
    counts = sweep_decay(_data_root())
    print(
        f"decay-sweep: to_dim={counts['to_dim']} "
        f"to_soft_forgotten={counts['to_soft_forgotten']} "
        f"to_forgotten_dir={counts['to_forgotten_dir']}",
        file=sys.stderr,
    )
    return 0
```

main() subparser 注册：

```python
    p_decay = subs.add_parser("decay-sweep", help="step memories through alive→dim→soft-forgotten state machine")
    p_decay.set_defaults(func=cmd_decay_sweep)
```

- [ ] **Step 5：跑测试**

Run: `cd memoryd && uv run pytest tests/test_governance_decay.py -v`
Expected: 5 passed.

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 92 + 5 = 97 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/governance/decay.py memoryd/src/memoryd/cli.py memoryd/tests/test_governance_decay.py
git commit -m "$(cat <<'EOF'
加 decay 状态机 + decay-sweep CLI

alive → ttl 到期 + 没召回 → dim → 30 天没召回 → soft-forgotten → 90 天
没召回 → 物理迁移 forgotten/ 子目录。ttl_days=NULL（长期记忆）永不
auto decay。decay-sweep 手动跑（cron 推迟 Plan 5）。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 10：Merge + Digest

**Files:**
- Create: `memoryd/src/memoryd/governance/merge.py`
- Create: `memoryd/src/memoryd/governance/digest.py`
- Create: `memoryd/tests/test_governance_merge.py`
- Create: `memoryd/tests/test_governance_digest.py`
- Modify: `memoryd/src/memoryd/cli.py`（加 `digest` / `merge` 子命令）

`memoryd merge keep:slug drop:s1,s2` 合并：把 drop_slugs 的 body 段追加到 keep 的 body 末尾、把 drop 的 triggers 合到 keep、删除 drop 的 SQLite 行 + .md 文件。

`memoryd digest` 文本输出三栏（候选提升 / 重复合并 / TTL 到期）；`--json` 模式给脚本调用。

- [ ] **Step 1：写测试 `memoryd/tests/test_governance_merge.py`**

```python
"""merge tests."""
from datetime import datetime
from pathlib import Path

import pytest

from memoryd.governance.merge import merge_memories
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


def _mem(slug: str, body: str, triggers: list[str], type_: str = "decision") -> SessionMemory:
    return SessionMemory(
        frontmatter=Frontmatter(
            title=slug, slug=slug, type=type_, scope_hash="h",
            triggers=triggers, source="manual", created_at=datetime(2026, 5, 14),
        ),
        body=body,
    )


def test_merge_appends_drop_body_to_keep(memory_root: Path):
    save_memory(memory_root, _mem("keep1", "A original", ["k1"]))
    save_memory(memory_root, _mem("drop1", "B duplicate content", ["k2"]))

    merge_memories(memory_root, keep_slug="keep1", drop_slugs=["drop1"])

    keep_path = memory_root / "scopes" / "h" / "decisions" / "keep1.md"
    text = keep_path.read_text()
    assert "A original" in text
    assert "B duplicate content" in text


def test_merge_combines_triggers(memory_root: Path):
    save_memory(memory_root, _mem("k", "A", ["t1"]))
    save_memory(memory_root, _mem("d", "B", ["t2", "t3"]))

    merge_memories(memory_root, keep_slug="k", drop_slugs=["d"])

    idx = open_index(memory_root / "index.db")
    triggers = idx.conn.execute("SELECT trigger FROM triggers WHERE slug=? ORDER BY trigger", ("k",)).fetchall()
    idx.close()
    assert set(t[0] for t in triggers) == {"t1", "t2", "t3"}


def test_merge_deletes_dropped_memory_from_db_and_disk(memory_root: Path):
    save_memory(memory_root, _mem("kk", "A", ["t"]))
    save_memory(memory_root, _mem("dd", "B", ["t"]))

    merge_memories(memory_root, keep_slug="kk", drop_slugs=["dd"])

    idx = open_index(memory_root / "index.db")
    row = idx.get_memory("dd")
    idx.close()
    assert row is None
    drop_path = memory_root / "scopes" / "h" / "decisions" / "dd.md"
    assert not drop_path.exists()


def test_merge_rejects_unknown_keep(memory_root: Path):
    save_memory(memory_root, _mem("d", "x", ["t"]))
    with pytest.raises(KeyError, match="keep"):
        merge_memories(memory_root, keep_slug="no-such-slug", drop_slugs=["d"])
```

`memoryd/tests/test_governance_digest.py`:

```python
"""digest tests."""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from memoryd.governance.digest import build_digest, render_digest_text
from memoryd.index import open_index
from memoryd.schema import Frontmatter, SessionMemory
from memoryd.storage import save_memory


def test_digest_lists_pending_promotions(memory_root: Path):
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(
            title="s", slug="s1", type="session", scope_hash="h",
            source="manual", created_at=datetime(2026, 5, 14),
        ),
        body="x",
    ))
    idx = open_index(memory_root / "index.db")
    idx.conn.execute(
        """INSERT INTO promotions (source_session_slug, proposed_type, proposed_title,
           proposed_body, proposed_triggers, dura_score, reasoning, proposed_supersedes,
           scope_hash, status, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
        ("s1", "decision", "T", "B", "[]", "{}", "r", "[]", "h",
         datetime.now(timezone.utc).isoformat()),
    )
    idx.conn.commit()
    idx.close()

    d = build_digest(memory_root)
    assert len(d["promotions"]) == 1
    assert d["promotions"][0]["proposed_title"] == "T"


def test_digest_lists_duplicate_pairs(memory_root: Path):
    """Two memories with same fingerprint show up as a dup pair."""
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(title="a", slug="a1", type="decision", scope_hash="h",
                                source="manual", created_at=datetime(2026, 5, 14)),
        body="identical content for fingerprint",
    ))
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(title="b", slug="a2", type="decision", scope_hash="h",
                                source="manual", created_at=datetime(2026, 5, 14)),
        body="identical content for fingerprint",
    ))
    d = build_digest(memory_root)
    pairs = d["duplicates"]
    assert any(set(p) == {"a1", "a2"} for p in pairs)


def test_digest_lists_decayed_candidates(memory_root: Path):
    save_memory(memory_root, SessionMemory(
        frontmatter=Frontmatter(title="x", slug="dim1", type="session", scope_hash="h",
                                source="manual", created_at=datetime(2026, 5, 14)),
        body="x",
    ))
    idx = open_index(memory_root / "index.db")
    idx.update_decay_state("dim1", "dim")
    idx.close()
    d = build_digest(memory_root)
    assert any(c["slug"] == "dim1" for c in d["decayed"])


def test_render_digest_text_is_string(memory_root: Path):
    d = build_digest(memory_root)
    out = render_digest_text(d)
    assert isinstance(out, str)
    assert "promotions" in out.lower() or "提升" in out
```

- [ ] **Step 2：失败**

Run: `cd memoryd && uv run pytest tests/test_governance_merge.py tests/test_governance_digest.py -v`
Expected: ModuleNotFoundError.

- [ ] **Step 3：实现 `memoryd/src/memoryd/governance/merge.py`**

```python
"""Merge duplicate memories: combine bodies + triggers, delete dropped entries.

Keep slug's .md gets a `## merged-from <drop-slug>` section appended for each
dropped entry. Triggers union into keep's frontmatter.
"""
from __future__ import annotations

from pathlib import Path

from ..index import open_index
from ..schema import Frontmatter, SessionMemory
from ..storage import load_session, save_memory


def merge_memories(memory_root: Path, *, keep_slug: str, drop_slugs: list[str]) -> None:
    idx = open_index(memory_root / "index.db")
    try:
        keep_row = idx.get_memory(keep_slug)
        if keep_row is None:
            raise KeyError(f"keep slug not found: {keep_slug}")
        keep_path = memory_root / keep_row["body_path"]
        keep_mem = load_session(keep_path)

        merged_body_parts = [keep_mem.body.rstrip()]
        merged_triggers = list(keep_mem.frontmatter.triggers)

        for drop in drop_slugs:
            drop_row = idx.get_memory(drop)
            if drop_row is None:
                continue
            drop_path = memory_root / drop_row["body_path"]
            if not drop_path.exists():
                idx.delete_memory(drop)
                continue
            drop_mem = load_session(drop_path)
            merged_body_parts.append(f"\n\n## merged-from {drop}\n\n{drop_mem.body}")
            for t in drop_mem.frontmatter.triggers:
                if t not in merged_triggers:
                    merged_triggers.append(t)
            # delete drop's .md + index row
            drop_path.unlink()
            idx.delete_memory(drop)

        new_keep = SessionMemory(
            frontmatter=Frontmatter(
                **{**keep_mem.frontmatter.model_dump(),
                   "triggers": merged_triggers}
            ),
            body="\n".join(merged_body_parts),
        )
        save_memory(memory_root, new_keep)
    finally:
        idx.close()
```

`memoryd/src/memoryd/governance/digest.py`:

```python
"""Build the weekly digest payload (no TUI yet — Plan 7).

Three sections:
- promotions (status=pending in promotions table)
- duplicates (memories sharing fingerprint)
- decayed (decay_state ∈ {dim, soft-forgotten})
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..index import open_index


def build_digest(memory_root: Path) -> dict[str, Any]:
    idx = open_index(memory_root / "index.db")
    try:
        promos = [dict(r) for r in idx.conn.execute(
            "SELECT * FROM promotions WHERE status = 'pending' ORDER BY created_at DESC"
        ).fetchall()]

        # duplicates: group by fingerprint where count >= 2
        fp_rows = idx.conn.execute(
            "SELECT fingerprint, GROUP_CONCAT(slug, '||') AS slugs, COUNT(*) AS n "
            "FROM memories GROUP BY fingerprint HAVING n >= 2"
        ).fetchall()
        duplicates = [r["slugs"].split("||") for r in fp_rows]

        decayed = [dict(r) for r in idx.conn.execute(
            "SELECT slug, type, title, decay_state, last_recalled_at "
            "FROM memories WHERE decay_state IN ('dim', 'soft-forgotten') "
            "ORDER BY last_recalled_at"
        ).fetchall()]

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "promotions": promos,
            "duplicates": duplicates,
            "decayed": decayed,
        }
    finally:
        idx.close()


def render_digest_text(digest: dict[str, Any]) -> str:
    """Plain-text rendering. TUI lives in Plan 7."""
    lines: list[str] = []
    lines.append(f"=== memoryd weekly digest @ {digest['generated_at']} ===")
    lines.append("")
    lines.append(f"候选提升 promotions ({len(digest['promotions'])} 待审):")
    for p in digest["promotions"][:30]:
        try:
            dura = json.loads(p["dura_score"])
        except Exception:
            dura = {}
        dura_str = " ".join(f"{k}={v:.2f}" for k, v in dura.items())
        lines.append(f"  [{p['proposed_type']}] {p['proposed_title']}  ({dura_str})")
        lines.append(f"    source: {p['source_session_slug']}  scope: {p['scope_hash']}")
    lines.append("")
    lines.append(f"重复合并 duplicates ({len(digest['duplicates'])} 对):")
    for pair in digest["duplicates"][:30]:
        lines.append(f"  ~ {' / '.join(pair)}")
    lines.append("")
    lines.append(f"TTL / decay 提醒 ({len(digest['decayed'])} 条):")
    for d in digest["decayed"][:30]:
        lines.append(f"  [{d['decay_state']}] {d['type']} {d['slug']} — {d['title']}")
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4：CLI 加 `merge` / `digest`**

`cli.py` 加 wrapper：

```python
def cmd_merge(args: argparse.Namespace) -> int:
    from .governance.merge import merge_memories
    merge_memories(_data_root(), keep_slug=args.keep, drop_slugs=args.drop)
    print(f"merge: keep={args.keep} drop={','.join(args.drop)}", file=sys.stderr)
    return 0


def cmd_digest(args: argparse.Namespace) -> int:
    from .governance.digest import build_digest, render_digest_text
    d = build_digest(_data_root())
    if args.json:
        import json as _j
        print(_j.dumps(d, indent=2, ensure_ascii=False))
    else:
        print(render_digest_text(d))
    return 0
```

main() subparser 段加：

```python
    p_merge = subs.add_parser("merge", help="merge dup memories (keep one, drop others)")
    p_merge.add_argument("--keep", required=True)
    p_merge.add_argument("--drop", nargs="+", required=True)
    p_merge.set_defaults(func=cmd_merge)

    p_digest = subs.add_parser("digest", help="show weekly digest (promotions / duplicates / decayed)")
    p_digest.add_argument("--json", action="store_true")
    p_digest.set_defaults(func=cmd_digest)
```

- [ ] **Step 5：跑测试**

Run: `cd memoryd && uv run pytest tests/test_governance_merge.py tests/test_governance_digest.py -v`
Expected: 4 + 4 = 8 passed.

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 97 + 8 = 105 passed.

- [ ] **Step 6：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/governance/merge.py memoryd/src/memoryd/governance/digest.py memoryd/src/memoryd/cli.py memoryd/tests/test_governance_merge.py memoryd/tests/test_governance_digest.py
git commit -m "$(cat <<'EOF'
加 merge 合并 + digest 文本视图

- governance/merge.py: merge_memories 合 body + triggers，删 drop
  条目（.md + SQLite 行）
- governance/digest.py: build_digest 出三栏 dict（pending promotions /
  duplicates by fingerprint / decayed states），render_digest_text
  纯文本输出（TUI 推迟 Plan 7）
- cli.py: memoryd merge --keep X --drop Y Z；memoryd digest [--json]

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 11：6 个新 MCP 工具

**Files:**
- Modify: `memoryd/src/memoryd/server.py`
- Modify: `memoryd/tests/test_server.py`

注册：`promote_to_long_term` / `record_long_term` / `list_by_type` / `get_memory` / `list_promotions` / `merge_duplicates`。

- [ ] **Step 1：在 `memoryd/tests/test_server.py` 末尾加测试**

```python
@pytest.mark.asyncio
async def test_record_long_term_creates_decision(server_with_data):
    server = server_with_data
    result = await server.call_tool("record_long_term", {
        "type": "decision",
        "title": "logo choice",
        "body": "deep blue",
        "triggers": ["logo", "color"],
        "scope_hash": "test_scope",
    })
    assert any("logo choice" in str(item) for item in result)


@pytest.mark.asyncio
async def test_list_by_type_filters(server_with_data):
    server = server_with_data
    # record one first
    await server.call_tool("record_long_term", {
        "type": "fact",
        "title": "stack is FastAPI",
        "body": "the API runs FastAPI",
        "triggers": ["stack", "fastapi"],
        "scope_hash": "test_scope",
    })
    result = await server.call_tool("list_by_type", {"type": "fact", "scope_hash": "test_scope"})
    blob = "".join(str(item) for item in result)
    assert "stack is FastAPI" in blob


@pytest.mark.asyncio
async def test_get_memory_returns_known_slug(server_with_data):
    server = server_with_data
    # The fixture data has slug "2026-05-09-logo"
    result = await server.call_tool("get_memory", {"slug": "2026-05-09-logo"})
    assert any("logo" in str(item).lower() for item in result)


@pytest.mark.asyncio
async def test_list_promotions_returns_empty_initially(server_with_data):
    server = server_with_data
    result = await server.call_tool("list_promotions", {"scope_hash": "test_scope"})
    # No promotions yet → empty list
    blob = "".join(str(item) for item in result)
    assert blob in ("", "[]") or "[]" in blob
```

- [ ] **Step 2：失败**

Run: `cd memoryd && uv run pytest tests/test_server.py -v -k "record or list_by or get_memory or list_promotions"`
Expected: 4 FAIL（工具未注册）。

- [ ] **Step 3：改 `memoryd/src/memoryd/server.py` 注册新工具**

在 `build_server()` 函数体里，原 `search_memory` 注册之后追加：

```python
    @mcp.tool()
    def record_long_term(
        type: str,
        title: str,
        body: str,
        triggers: list[str],
        scope_hash: str | None = None,
    ) -> dict:
        """Write a new long-term memory directly (no DURA promotion).

        type must be one of: decision / preference / fact / playbook / warning.
        Use this when the user explicitly says 'remember this as a decision' etc.
        """
        import re as _re
        from datetime import datetime, timezone
        from .schema import Frontmatter, SessionMemory
        from .storage import save_memory

        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError("scope_hash required")
        if type not in {"decision", "preference", "fact", "playbook", "warning"}:
            raise ValueError(f"type must be a long-term type, got {type!r}")
        now = datetime.now(timezone.utc)
        safe_title = _re.sub(r"[^A-Za-z0-9_-]", "_", title)[:40]
        slug = f"{now:%Y-%m-%d}-{safe_title}-{int(now.timestamp())}"
        mem = SessionMemory(
            frontmatter=Frontmatter(
                title=title,
                slug=slug,
                type=type,
                scope_hash=sh,
                triggers=triggers,
                source="manual",
                created_at=now,
                ttl_days=None,
            ),
            body=body,
        )
        save_memory(_data_root(), mem)
        return {"slug": slug, "type": type, "title": title}

    @mcp.tool()
    def list_by_type(type: str, scope_hash: str | None = None, limit: int = 20) -> list[dict]:
        """List up to `limit` memories of a given type in a scope."""
        from .index import open_index
        sh = scope_hash or _default_scope()
        if not sh:
            raise ValueError("scope_hash required")
        idx = open_index(_data_root() / "index.db")
        try:
            return idx.list_by_type(type, scope_hash=sh, limit=limit)
        finally:
            idx.close()

    @mcp.tool()
    def get_memory(slug: str) -> dict | None:
        """Return one memory's full row (metadata + body_path)."""
        from .index import open_index
        idx = open_index(_data_root() / "index.db")
        try:
            row = idx.get_memory(slug)
            if row is None:
                return None
            # also include body
            body_path = _data_root() / row["body_path"]
            try:
                row["body"] = body_path.read_text(encoding="utf-8")
            except OSError:
                row["body"] = ""
            return row
        finally:
            idx.close()

    @mcp.tool()
    def list_promotions(scope_hash: str | None = None, status: str = "pending") -> list[dict]:
        """List promotion candidates produced by analyze-session."""
        from .index import open_index
        idx = open_index(_data_root() / "index.db")
        try:
            sql = "SELECT * FROM promotions WHERE status = ?"
            args: list = [status]
            if scope_hash is not None:
                sql += " AND scope_hash = ?"
                args.append(scope_hash)
            sql += " ORDER BY created_at DESC LIMIT 50"
            return [dict(r) for r in idx.conn.execute(sql, args).fetchall()]
        finally:
            idx.close()

    @mcp.tool()
    def promote_to_long_term(
        session_slug: str,
        type: str,
        title: str,
        body: str | None = None,
        triggers: list[str] | None = None,
        reason: str | None = None,
    ) -> dict:
        """Promote a slice of a captured session into a typed long-term memory.

        If body / triggers omitted, the session body and triggers are reused.
        """
        from datetime import datetime, timezone
        import re as _re
        from .index import open_index
        from .schema import Frontmatter, SessionMemory
        from .storage import load_session, save_memory

        if type not in {"decision", "preference", "fact", "playbook", "warning"}:
            raise ValueError(f"type must be a long-term type, got {type!r}")
        idx = open_index(_data_root() / "index.db")
        try:
            row = idx.get_memory(session_slug)
            if row is None:
                raise ValueError(f"session_slug not found: {session_slug}")
            sess_path = _data_root() / row["body_path"]
            sess = load_session(sess_path)
        finally:
            idx.close()
        now = datetime.now(timezone.utc)
        safe_title = _re.sub(r"[^A-Za-z0-9_-]", "_", title)[:40]
        slug = f"{now:%Y-%m-%d}-{safe_title}-{int(now.timestamp())}"
        new_body = body if body is not None else sess.body[:5000]
        new_triggers = triggers if triggers is not None else sess.frontmatter.triggers
        mem = SessionMemory(
            frontmatter=Frontmatter(
                title=title,
                slug=slug,
                type=type,
                scope_hash=row["scope_hash"],
                triggers=new_triggers,
                source="manual",
                created_at=now,
                ttl_days=None,
                promoted_from=session_slug,
            ),
            body=new_body,
        )
        save_memory(_data_root(), mem)
        return {"slug": slug, "promoted_from": session_slug, "reason": reason or ""}

    @mcp.tool()
    def merge_duplicates(keep_slug: str, drop_slugs: list[str]) -> dict:
        """Merge `drop_slugs` into `keep_slug` (bodies appended, triggers unioned)."""
        from .governance.merge import merge_memories
        merge_memories(_data_root(), keep_slug=keep_slug, drop_slugs=drop_slugs)
        return {"kept": keep_slug, "dropped": drop_slugs}
```

- [ ] **Step 4：跑测试**

Run: `cd memoryd && uv run pytest tests/test_server.py -v`
Expected: 原 3 + 新 4 = 7 passed.

全量：

Run: `cd memoryd && uv run pytest -v`
Expected: 105 + 4 = 109 passed.

CLI smoke 检查工具总数：

```bash
cd memoryd
uv run python -c "
from memoryd.server import build_server
s = build_server()
# FastMCP _tool_manager._tools 是字典；count
print('tools:', list(s._tool_manager._tools.keys()))
print('total:', len(s._tool_manager._tools))
"
```

期望输出 7 个工具名，total=7。如果 FastMCP API 名变了，subagent 用 `dir()` 找到实际属性即可（不阻塞测试）。

- [ ] **Step 5：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/src/memoryd/server.py memoryd/tests/test_server.py
git commit -m "$(cat <<'EOF'
注册 6 个新 MCP 工具（总 7/12 budget）

新增：promote_to_long_term / record_long_term / list_by_type /
get_memory / list_promotions / merge_duplicates。
全部基于已有的 storage / index / governance/merge 模块；scope_hash
延用 search_memory 同样的 default scope 兜底。预算剩余 5（Plan 4
sensitive 占 1）。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Task 12：README + execution log + Phase 1 用户手册

**Files:**
- Modify: `memoryd/README.md`
- Create: `docs/superpowers/plans/2026-05-14-long-term-memory-governance.execution-log.txt`

- [ ] **Step 1：Read 现 README，扩展 Limitations 段 + 加新章节**

把 "Limitations of v1.0-α" 段更新（移除已实现项），加新章节：

````markdown
## Long-term memory governance (Plan 3)

### LLM 配置

Plan 3 用 LLM 做 4 准则候选筛选。默认 Anthropic Claude Haiku 4.5。

```bash
# 1. 把 API key 写到 shell rc（~/.zshrc / ~/.bashrc）
export ANTHROPIC_API_KEY=sk-ant-xxx

# 2.（可选）改 provider / model
memoryd config show
memoryd config set llm.model claude-sonnet-4-6
```

如果不配 API key，capture 仍正常工作，只是不会自动跑 DURA → 不生成 promotion 候选。可以手动跑 `memoryd analyze-session <slug>` 重试。

### 类型扩展

会话 capture 走 `source` tag 路径（Plan 1-2.5）；长期记忆走 6 种类型：

| 类型 | 何时用 | TTL |
|---|---|---|
| session | 自动捕获摘要 | 90 天 → decay |
| decision | 用户明确决策 | 永不过期 |
| preference | 工作偏好 | 永不过期 |
| fact | 客观事实 | 永不过期 |
| playbook | 操作流程 | 永不过期 |
| warning | 踩过的坑 | 永不过期 |

智能体在会话中说"记一下这个决策"→ 调 `promote_to_long_term` 或 `record_long_term` MCP 工具自动写。

### Digest 复盘

每周（默认）跑：

```bash
memoryd digest                  # 文本视图
memoryd digest --json           # JSON（脚本调用）
```

三栏：
- **候选提升**：DURA ≥ 0.6 的 LLM 推荐
- **重复合并**：fingerprint 相同的条目对
- **TTL 到期**：进 dim / soft-forgotten 的提醒

合并：
```bash
memoryd merge --keep <good-slug> --drop <bad-slug-1> <bad-slug-2>
```

### Decay / soft-forget

session 90 天没召回 → `dim`；再 30 天 → `soft-forgotten`（默认 search 不返回）；再 90 天 → 物理迁到 `forgotten/`。手动跑：

```bash
memoryd decay-sweep
```

Plan 5 会加 cron 自动每天跑。

### MCP 工具清单（7/12）

| # | 工具 | 用途 |
|---|---|---|
| 1 | search_memory | 全文 + trigger + type 过滤 |
| 2 | promote_to_long_term | 把 session 段提升为长期记忆 |
| 3 | record_long_term | 直接写长期记忆（不通过 session） |
| 4 | list_by_type | 列单类型 |
| 5 | get_memory | 取详情 |
| 6 | list_promotions | 列待审批候选 |
| 7 | merge_duplicates | 合并 |
````

- [ ] **Step 2：写 execution log**

```bash
cat > /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-14-long-term-memory-governance.execution-log.txt <<'LOG'
=== Plan 3 实施日志 ===

[Phase 0 由 subagent 完成；commit SHA 由 git log 给出]

=== Phase 1 用户手册（按顺序执行）===

# 0. 准备
cd /Users/abble/project-management-personal
git pull --ff-only main
cd memoryd && uv sync && cd ..

# 1. 配 LLM API key（必须；否则 analyze-session 静默 skip）
# 把下面这行加到 ~/.zshrc 永久生效；或本 session export
export ANTHROPIC_API_KEY=sk-ant-xxx

# 2. 验 config 默认
memoryd/.venv/bin/memoryd config show
# 期望含 llm.provider=anthropic / llm.model=claude-haiku-4-5

# 3. 跑一次 rebuild-index 把 Plan 1-2.5 已有的 .md 都索引进去
memoryd/.venv/bin/memoryd rebuild-index
# 期望输出 "rebuild-index: N memories indexed"，N 是当前 ~/.local/share/memoryd/scopes/ 下 .md 总数

# 4. 对一个已有 session 手动跑 analyze 看 LLM 是否真返回候选
SOME_SLUG=$(find ~/.local/share/memoryd/scopes -name "*.md" | head -1 | xargs basename -s .md)
memoryd/.venv/bin/memoryd analyze-session "$SOME_SLUG"
# 看 stderr 应有 "analyze-session: ok"；可能耗时 5-30s（取决于 LLM）

# 5. 跑 digest 看是否出候选
memoryd/.venv/bin/memoryd digest
# 三栏。若 promotions 为空，说明 LLM 没找出值得提升的内容（不一定是 bug）

# 6. 试用新 MCP 工具
#    在任意 CC / Codex / openclaw 会话里说："用 record_long_term 工具
#    记一条 decision，title 是 'test plan 3 decision'，body 'works',
#    triggers ['plan3', 'test']"
#    然后 memoryd list-by-type --type decision 查（或用 MCP list_by_type）

# 7. 试 decay-sweep（不会动新条目）
memoryd/.venv/bin/memoryd decay-sweep
# 期望 "to_dim=0 to_soft_forgotten=0 to_forgotten_dir=0"

# 8. 回报结果
echo "[$(date -Iseconds)] Plan 3 Phase 1: <PASS/FAIL+症状>" \
    >> /Users/abble/project-management-personal/docs/superpowers/plans/2026-05-14-long-term-memory-governance.execution-log.txt

=== Phase 1 手册 end ===
LOG
```

- [ ] **Step 3：跑全量回归**

Run: `cd memoryd && uv run pytest -v`
Expected: 109 passed.

Run: `cd scripts/openclaw-memoryd-plugin && npm test`
Expected: 12 passed (Plan 2.5 不动 OpenClaw 插件，应不变).

- [ ] **Step 4：Commit**

```bash
cd /Users/abble/project-management-personal
git add memoryd/README.md docs/superpowers/plans/2026-05-14-long-term-memory-governance.execution-log.txt
git commit -m "$(cat <<'EOF'
Plan 3 收尾：README 长期记忆治理章节 + Phase 1 用户手册

README 加 Long-term memory governance 章节：LLM 配置 / 类型扩展 / digest
复盘 / decay / 7 个 MCP 工具清单。execution log 含 Phase 1 6 步用户操作
（API key 配置 → rebuild-index → analyze → digest → MCP 工具试用 →
decay-sweep）。

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Plan 3 完成判据

下面任一未达成即视为未完成：

1. ✅ pytest 109 passed（Plan 2.5 的 61 + 新增 48）
2. ✅ MemoryType 6 种全部能 roundtrip
3. ✅ SQLite `index.db` 在 save 后自动 sync；`memoryd rebuild-index` 重建一致
4. ✅ search_memory 默认排除 soft-forgotten；`include_decayed=true` 时返回
5. ✅ analyze-session 跑 Anthropic Provider 真实返回候选（Phase 1 实测）
6. ✅ digest 三栏（promotions / duplicates / decayed）正确显示
7. ✅ decay-sweep 状态机迁移正确（5 个 unit test 覆盖）
8. ✅ 6 个新 MCP 工具都在 `/mcp` 列表里出现
9. ✅ MCP 工具总数 ≤ 12（实际 7）
10. ✅ Plan 1-2.5 无回归（CC capture / Codex mirror / OpenClaw plugin 都正常）
11. ✅ promote_to_long_term + record_long_term 在 CC 会话里能用

## Plan 间依赖

**上游：** Plan 1 / 2 / 2.5 merged on main `b140b35`。

**下游：**
- Plan 4 (敏感作用域)：用 SQLite 加 `sensitive` 列 + `request_sensitive_read` 工具（8/12）
- Plan 5 (跨平台)：把 `memoryd decay-sweep` 装到 launchd / Task Scheduler / systemd timer
- Plan 6 (多电脑同步)：SQLite **不**进同步盘；export/import 走 Markdown
- Plan 7 (浏览界面)：digest 的 TUI 化 + Web Dashboard
- Plan 8 (旧记忆导入)：基于本 plan 的 6 类型系统反向导入 CLAUDE.md / AGENTS.md / mcp-memory-service

---

## Self-Review

Spec → plan 映射：

- spec §1 上游约束 → plan 顶部"上游与硬约束"段
- spec §2 架构图 → 12 个 task 实现
- spec §3 类型扩展 → Task 1（schema）+ Task 3（storage routes）
- spec §4 DURA prompt → Task 8 prompts/dura_extract.txt
- spec §5 TTL 状态机 → Task 9 decay
- spec §6 周期 digest → Task 10 digest（纯文本，TUI 推迟 Plan 7）
- spec §7 新 MCP 工具 → Task 11
- spec §8 capture-time LLM 调用 → Task 8 _spawn_analyze hook
- spec §9 LLM provider → Task 7
- spec §10 文件结构 → plan 文件结构表
- spec §11 边界 → plan scope adjustments + Plan 间依赖
- spec §12 风险 → plan 顶部"风险与不确定性" + 每 task 的容错代码
- spec §13 完成判据 → plan 同名段

Placeholder scan：

- 无 "TBD" / "TODO" / "实现细节后补"
- 每个 step 附完整 code block
- 每个测试 expected 都给数字

Type 一致性：

- `Index.index_memory(mem, *, body_path)` ↔ `save_memory` / `rebuild-index` 调用方式一致
- `MemoryType` Literal 6 种在 schema.py / storage.py `_TYPE_TO_DIR` / server.py 工具 type 验证三处一致
- `search_sessions(root, scope_hash, query, *, type_=None, include_decayed=False, limit=20)` 在 Task 5 / Task 11 server.py 调用一致
- `analyze_session(memory_root, *, session_slug, provider)` 在 Task 8 / Task 9 CLI 调用一致
- promotions 表 schema 在 Task 2 migration / Task 8 analyze / Task 10 digest / Task 11 list_promotions 四处字段名一致（source_session_slug / proposed_type / proposed_title / ...）
- 状态机常量 `DIM_AFTER_TTL = 0` / `SOFT_FORGET_AFTER_DIM = 30` / `FORGOTTEN_AFTER_SF = 90` 仅 Task 9 内使用，无 cross-task drift

通过。Plan 完成。
