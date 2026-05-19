"""SQLite DAO for entities / relations / supersedes_chain.

接口契约（plan10）：

- ``upsert_entity(name, type, ...)`` — 名字 + 类型为 natural key，第二次
  调用会刷新 ``last_seen_at`` / ``mention_count`` / 合并 aliases，并保留首次
  的 ``first_seen_at`` / ``scope_hash``（首爆原则）。
- ``add_relation(subject_id, predicate, object_id, ...)`` — (subject, predicate,
  object, source_memory) 四元组幂等，重复 INSERT 视为同一条关系。
- ``add_supersede(newer, older, ...)`` — 写 supersedes_chain 一行；
  自动用 INSERT OR REPLACE 实现 PK 冲突更新（同一对 (newer, older) 可被
  digest / user 复评）。

设计约束：
- ``conn.row_factory = sqlite3.Row`` 假设由调用方设置（``open_index``
  已经设了）。若用 :func:`memoryd.knowledge_graph.migrations.open_kg_db`
  也会自动设置。
- 写入用 ``conn.commit()`` 即时落盘，避免长事务跨调用。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable


# ---- 常量 ----------------------------------------------------------------

ENTITY_TYPES = (
    "person",
    "organization",
    "place",
    "library",
    "tool",
    "project",
    "concept",
)

# 允许的 predicate（与契约文档同步）
ALLOWED_PREDICATES = (
    "mentions",
    "works_on",
    "uses",
    "prefers",
    "supersedes",
    "superseded_by",
    "conflicts_with",
    "cites",
    "runs_on",
    "belongs_to",
    "located_at",
)


# ---- 数据类 --------------------------------------------------------------


@dataclass
class Entity:
    id: str
    name: str
    type: str
    aliases: list[str]
    context: str
    first_seen_at: datetime
    last_seen_at: datetime
    mention_count: int
    scope_hash: str | None
    decay_state: str


@dataclass
class Relation:
    id: int
    subject_id: str
    subject_kind: str
    predicate: str
    object_id: str
    object_kind: str
    source_memory_id: str | None
    scope_hash: str | None
    confidence: float | None
    created_at: datetime
    superseded_at: datetime | None


# ---- 工具 ----------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        # 容错：旧数据可能写过 datetime('now') 无时区
        try:
            return datetime.fromisoformat(s + "+00:00")
        except ValueError:
            return None


def make_entity_id(type_: str, name: str) -> str:
    """规范化 entity id —— ``entity:<type>:<slug-name>``。

    name 仅做去空白 + 小写化，不做激进 slugify（中文保留）；冲突由表层
    通过 (name, type) 查找解决。
    """
    if type_ not in ENTITY_TYPES:
        raise ValueError(f"unknown entity type: {type_!r}")
    norm = name.strip().lower().replace(" ", "_")
    return f"entity:{type_}:{norm}"


def _row_to_entity(row: sqlite3.Row) -> Entity:
    aliases_raw = row["aliases"] or "[]"
    try:
        aliases = json.loads(aliases_raw)
        if not isinstance(aliases, list):
            aliases = []
    except (json.JSONDecodeError, TypeError):
        aliases = []
    return Entity(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        aliases=[str(a) for a in aliases],
        context=row["context"] or "",
        first_seen_at=_parse_iso(row["first_seen_at"]) or datetime.now(timezone.utc),
        last_seen_at=_parse_iso(row["last_seen_at"]) or datetime.now(timezone.utc),
        mention_count=int(row["mention_count"] or 1),
        scope_hash=row["scope_hash"],
        decay_state=row["decay_state"] or "fresh",
    )


def _row_to_relation(row: sqlite3.Row) -> Relation:
    return Relation(
        id=int(row["id"]),
        subject_id=row["subject_id"],
        subject_kind=row["subject_kind"],
        predicate=row["predicate"],
        object_id=row["object_id"],
        object_kind=row["object_kind"],
        source_memory_id=row["source_memory_id"],
        scope_hash=row["scope_hash"],
        confidence=row["confidence"],
        created_at=_parse_iso(row["created_at"]) or datetime.now(timezone.utc),
        superseded_at=_parse_iso(row["superseded_at"]),
    )


# ---- DAO -----------------------------------------------------------------


class KnowledgeGraphStore:
    """Thin SQLite DAO for the three graph tables.

    建议从 :func:`memoryd.index.open_index` 的 ``Index.conn`` 拿到的连接
    构造（与 ``memories`` 表共一份 DB）；测试场景可用
    :func:`memoryd.knowledge_graph.migrations.open_kg_db`。
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        # 确保 row_factory 是 Row（_row_to_* 用列名访问）
        if conn.row_factory is not sqlite3.Row:
            conn.row_factory = sqlite3.Row

    # ---- entities --------------------------------------------------------

    def upsert_entity(
        self,
        name: str,
        type: str,  # noqa: A002  契约要求
        *,
        aliases: Iterable[str] | None = None,
        scope_hash: str | None = None,
        context: str = "",
    ) -> Entity:
        """新增或更新一个 entity。

        - 主键 = ``make_entity_id(type, name)``，所以同 (type, name) 视作同
          一个实体；不同 type 的同名 entity 是两条独立记录。
        - 第二次（及以后）调用：
            * ``mention_count`` += 1
            * ``last_seen_at`` 刷新到 now()
            * ``context`` 覆盖（保留最近一次）
            * ``aliases`` union 合并（去重不丢历史别名）
            * ``first_seen_at`` / ``scope_hash`` 保持首次值
        """
        if type not in ENTITY_TYPES:
            raise ValueError(f"unknown entity type: {type!r}")
        eid = make_entity_id(type, name)
        now = _now_iso()
        new_aliases = list(dict.fromkeys(a for a in (aliases or []) if a))

        existing = self.conn.execute(
            "SELECT * FROM entities WHERE id = ?", (eid,)
        ).fetchone()

        if existing is None:
            self.conn.execute(
                """
                INSERT INTO entities
                  (id, name, type, aliases, context, first_seen_at, last_seen_at,
                   mention_count, scope_hash, decay_state)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, 'fresh')
                """,
                (
                    eid,
                    name,
                    type,
                    json.dumps(new_aliases, ensure_ascii=False),
                    context,
                    now,
                    now,
                    scope_hash,
                ),
            )
            self.conn.commit()
        else:
            # 合并 aliases：union + 去重，保留旧顺序优先
            old_aliases_raw = existing["aliases"] or "[]"
            try:
                old_aliases = json.loads(old_aliases_raw)
                if not isinstance(old_aliases, list):
                    old_aliases = []
            except (json.JSONDecodeError, TypeError):
                old_aliases = []
            merged: list[str] = []
            for a in (*old_aliases, *new_aliases):
                s = str(a)
                if s and s not in merged:
                    merged.append(s)

            self.conn.execute(
                """
                UPDATE entities
                SET aliases = ?,
                    context = COALESCE(NULLIF(?, ''), context),
                    last_seen_at = ?,
                    mention_count = mention_count + 1,
                    decay_state = 'fresh'
                WHERE id = ?
                """,
                (
                    json.dumps(merged, ensure_ascii=False),
                    context,
                    now,
                    eid,
                ),
            )
            self.conn.commit()

        row = self.conn.execute(
            "SELECT * FROM entities WHERE id = ?", (eid,)
        ).fetchone()
        return _row_to_entity(row)

    def get_entity(self, entity_id: str) -> Entity | None:
        row = self.conn.execute(
            "SELECT * FROM entities WHERE id = ?", (entity_id,)
        ).fetchone()
        return _row_to_entity(row) if row else None

    def find_entities_by_name(self, name: str, fuzzy: bool = True) -> list[Entity]:
        """按 name / aliases 查实体。

        - ``fuzzy=False``：仅 LOWER(name) 精确匹配。
        - ``fuzzy=True``（默认）：``name LIKE ?`` + aliases JSON 内匹配。
        """
        if not name:
            return []
        if fuzzy:
            like = f"%{name.lower()}%"
            rows = self.conn.execute(
                """
                SELECT * FROM entities
                WHERE LOWER(name) LIKE ?
                   OR LOWER(COALESCE(aliases, '')) LIKE ?
                ORDER BY mention_count DESC, last_seen_at DESC
                """,
                (like, like),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM entities WHERE LOWER(name) = ? "
                "ORDER BY mention_count DESC, last_seen_at DESC",
                (name.lower(),),
            ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def top_entities(
        self,
        scope_hash: str | None = None,
        window_days: int = 30,
        top_k: int = 20,
    ) -> list[Entity]:
        """按 ``mention_count`` desc 取活跃实体（可指定 scope）。

        ``window_days`` 是软窗口：要求 ``last_seen_at`` 在窗口内才计入。
        """
        # 计算窗口下界
        from datetime import timedelta

        cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
        sql = "SELECT * FROM entities WHERE last_seen_at >= ?"
        args: list[object] = [cutoff]
        if scope_hash is not None:
            sql += " AND scope_hash = ?"
            args.append(scope_hash)
        sql += " ORDER BY mention_count DESC, last_seen_at DESC LIMIT ?"
        args.append(top_k)
        rows = self.conn.execute(sql, args).fetchall()
        return [_row_to_entity(r) for r in rows]

    def list_entities(
        self,
        *,
        type: str | None = None,  # noqa: A002
        scope_hash: str | None = None,
    ) -> list[Entity]:
        sql = "SELECT * FROM entities WHERE 1=1"
        args: list[object] = []
        if type is not None:
            sql += " AND type = ?"
            args.append(type)
        if scope_hash is not None:
            sql += " AND scope_hash = ?"
            args.append(scope_hash)
        sql += " ORDER BY last_seen_at DESC"
        rows = self.conn.execute(sql, args).fetchall()
        return [_row_to_entity(r) for r in rows]

    def update_decay_state(self, entity_id: str, state: str) -> None:
        self.conn.execute(
            "UPDATE entities SET decay_state = ? WHERE id = ?", (state, entity_id)
        )
        self.conn.commit()

    # ---- relations -------------------------------------------------------

    def add_relation(
        self,
        subject_id: str,
        predicate: str,
        object_id: str,
        *,
        source_memory_id: str | None = None,
        confidence: float | None = None,
        scope_hash: str | None = None,
        subject_kind: str = "entity",
        object_kind: str = "entity",
    ) -> int:
        """插入一条 relation。

        如果 (subject_id, predicate, object_id, source_memory_id) 已存在
        （UNIQUE 约束），返回已有 row 的 id 而非 raise——上层调用方语义上
        通常视为"幂等添加"。
        """
        if predicate not in ALLOWED_PREDICATES:
            # 不阻塞，仅记录（保留未来扩展 predicate 的余地）
            pass
        now = _now_iso()
        try:
            cur = self.conn.execute(
                """
                INSERT INTO relations
                  (subject_id, subject_kind, predicate, object_id, object_kind,
                   source_memory_id, scope_hash, confidence, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    subject_id,
                    subject_kind,
                    predicate,
                    object_id,
                    object_kind,
                    source_memory_id,
                    scope_hash,
                    confidence,
                    now,
                ),
            )
            self.conn.commit()
            return int(cur.lastrowid)
        except sqlite3.IntegrityError:
            # UNIQUE 冲突 → 取回已有 id
            row = self.conn.execute(
                """
                SELECT id FROM relations
                WHERE subject_id = ? AND predicate = ? AND object_id = ?
                  AND COALESCE(source_memory_id,'') = COALESCE(?, '')
                """,
                (subject_id, predicate, object_id, source_memory_id),
            ).fetchone()
            return int(row["id"]) if row else 0

    def mark_relation_superseded(self, relation_id: int) -> None:
        self.conn.execute(
            "UPDATE relations SET superseded_at = ? WHERE id = ?",
            (_now_iso(), relation_id),
        )
        self.conn.commit()

    def get_relations(
        self,
        *,
        subject_id: str | None = None,
        object_id: str | None = None,
        predicate: str | None = None,
        active_only: bool = True,
    ) -> list[Relation]:
        sql = "SELECT * FROM relations WHERE 1=1"
        args: list[object] = []
        if subject_id is not None:
            sql += " AND subject_id = ?"
            args.append(subject_id)
        if object_id is not None:
            sql += " AND object_id = ?"
            args.append(object_id)
        if predicate is not None:
            sql += " AND predicate = ?"
            args.append(predicate)
        if active_only:
            sql += " AND superseded_at IS NULL"
        sql += " ORDER BY created_at DESC"
        rows = self.conn.execute(sql, args).fetchall()
        return [_row_to_relation(r) for r in rows]

    def neighbors(self, entity_id: str, active_only: bool = True) -> list[Relation]:
        """返回所有以 entity_id 为 subject 或 object 的关系。"""
        sql = """
        SELECT * FROM relations
        WHERE (subject_id = ? OR object_id = ?)
        """
        if active_only:
            sql += " AND superseded_at IS NULL"
        rows = self.conn.execute(sql, (entity_id, entity_id)).fetchall()
        return [_row_to_relation(r) for r in rows]

    # ---- supersedes_chain ------------------------------------------------

    def add_supersede(
        self,
        newer_memory_id: str,
        older_memory_id: str,
        *,
        entity_id: str | None = None,
        confidence: float,
        reason: str | None = None,
        decided_by: str = "auto",
    ) -> None:
        """写一条 supersede 记录。PK = (newer, older)，重复调用会覆盖。"""
        if newer_memory_id == older_memory_id:
            raise ValueError("newer / older memory ids must differ")
        self.conn.execute(
            """
            INSERT OR REPLACE INTO supersedes_chain
              (newer_memory_id, older_memory_id, entity_id,
               confidence, decided_at, decided_by, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                newer_memory_id,
                older_memory_id,
                entity_id,
                float(confidence),
                _now_iso(),
                decided_by,
                reason,
            ),
        )
        self.conn.commit()

    def get_supersedes_for(self, memory_id: str) -> list[dict]:
        """返回 ``memory_id`` 作为 newer 的所有 supersede 记录。"""
        rows = self.conn.execute(
            "SELECT * FROM supersedes_chain WHERE newer_memory_id = ? "
            "ORDER BY decided_at DESC",
            (memory_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_superseded_by(self, memory_id: str) -> list[dict]:
        """返回 ``memory_id`` 被哪些 newer 取代。"""
        rows = self.conn.execute(
            "SELECT * FROM supersedes_chain WHERE older_memory_id = ? "
            "ORDER BY decided_at DESC",
            (memory_id,),
        ).fetchall()
        return [dict(r) for r in rows]


__all__ = [
    "ALLOWED_PREDICATES",
    "ENTITY_TYPES",
    "Entity",
    "KnowledgeGraphStore",
    "Relation",
    "make_entity_id",
]
