"""Knowledge graph schema 增量升级。

设计：
- SQLite 主 schema 通过 ``src/memoryd/migrations/004_knowledge_graph.sql``
  在 :func:`memoryd.index.open_index` 启动时一次性创建（已纳入主迁移流）。
- 本模块提供 **运行时增量升级 / 自检 hooks**：以后给 entities / relations 表
  加列时，新增一个 ``_ALTERS`` 条目即可，不必再写新的 ``.sql`` 文件。
- ``ensure_kg_schema(conn)`` 是幂等入口：测试代码可以拿一个空 ``:memory:``
  连接直接调它，得到完整的图谱三表 + 增量 ALTER。
"""
from __future__ import annotations

import sqlite3
from pathlib import Path


# ---- 基础三表 DDL（与 migrations/004_knowledge_graph.sql 完全一致）----------
# 这里复制一份是为了让 :memory: 测试可以脱离主 migration runner 独立建表。
# 改 schema 时务必同步两边（CI 可加一个比对脚本，目前手工保持一致）。
_BASE_DDL = """
CREATE TABLE IF NOT EXISTS entities (
  id              TEXT PRIMARY KEY,
  name            TEXT NOT NULL,
  type            TEXT NOT NULL CHECK(type IN (
                    'person','organization','place',
                    'library','tool','project','concept')),
  aliases         TEXT,
  context         TEXT,
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  mention_count   INTEGER NOT NULL DEFAULT 1,
  scope_hash      TEXT,
  decay_state     TEXT NOT NULL DEFAULT 'fresh'
);
CREATE INDEX IF NOT EXISTS idx_entities_type      ON entities(type);
CREATE INDEX IF NOT EXISTS idx_entities_name      ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_last_seen ON entities(last_seen_at);

CREATE TABLE IF NOT EXISTS relations (
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  subject_id        TEXT NOT NULL,
  subject_kind      TEXT NOT NULL CHECK(subject_kind IN ('entity','memory')),
  predicate         TEXT NOT NULL,
  object_id         TEXT NOT NULL,
  object_kind       TEXT NOT NULL CHECK(object_kind IN ('entity','memory')),
  source_memory_id  TEXT,
  scope_hash        TEXT,
  confidence        REAL,
  created_at        TEXT NOT NULL,
  superseded_at     TEXT,
  UNIQUE(subject_id, predicate, object_id, source_memory_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_subject   ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object    ON relations(object_id);
CREATE INDEX IF NOT EXISTS idx_relations_predicate ON relations(predicate);

CREATE TABLE IF NOT EXISTS supersedes_chain (
  newer_memory_id  TEXT NOT NULL,
  older_memory_id  TEXT NOT NULL,
  entity_id        TEXT,
  confidence       REAL NOT NULL,
  decided_at       TEXT NOT NULL,
  decided_by       TEXT NOT NULL,
  reason           TEXT,
  PRIMARY KEY(newer_memory_id, older_memory_id)
);
CREATE INDEX IF NOT EXISTS idx_supersedes_entity ON supersedes_chain(entity_id);
CREATE INDEX IF NOT EXISTS idx_supersedes_newer  ON supersedes_chain(newer_memory_id);
CREATE INDEX IF NOT EXISTS idx_supersedes_older  ON supersedes_chain(older_memory_id);
"""


# ---- 增量 ALTER 列表 -------------------------------------------------------
# (表名, 列名, "ADD COLUMN <col> <type> ..." 语句)
# 添加新列时 append 到这里即可——ensure_kg_schema 会跳过已经存在的列。
_ALTERS: list[tuple[str, str, str]] = [
    # 示例占位（无实际效果，演示用法）：
    # ("entities", "embedding_id", "ADD COLUMN embedding_id TEXT"),
]


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """返回 ``table`` 已有的列名集合（空集 = 表不存在）。"""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    except sqlite3.OperationalError:
        return set()
    return {r[1] for r in rows}


def ensure_kg_schema(conn: sqlite3.Connection) -> None:
    """幂等：建表 + 应用所有未应用的增量 ALTER。

    与主 ``open_index`` 流并存——主流跑 SQL 文件，这里在测试或单独图谱
    DB 场景下用。在主 DB 上调用是 no-op（IF NOT EXISTS / 列已存在）。
    """
    conn.executescript(_BASE_DDL)
    for table, col, ddl in _ALTERS:
        cols = _table_columns(conn, table)
        if col in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} {ddl}")
        except sqlite3.OperationalError:
            # 列已存在 / 表结构对不上时静默跳过，避免阻塞主流程。
            continue
    conn.commit()


def open_kg_db(path: Path) -> sqlite3.Connection:
    """开一个独立的图谱 DB（罕用，主路径仍走 :func:`memoryd.index.open_index`）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    ensure_kg_schema(conn)
    return conn


__all__ = ["ensure_kg_schema", "open_kg_db"]
