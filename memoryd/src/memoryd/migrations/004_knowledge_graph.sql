-- Plan 10 knowledge_graph: entities / relations / supersedes_chain 三表
-- 用于自动学习用户画像（人物、组织、项目、概念……的关系图）。
-- 与 memories 表松耦合：relations 通过 source_memory_id 引用，但不设外键
-- 以便 memories 表 rebuild / 删除 不强制级联（图层独立可重建）。

CREATE TABLE IF NOT EXISTS entities (
  id              TEXT PRIMARY KEY,                  -- "entity:person:abble" 形式
  name            TEXT NOT NULL,
  type            TEXT NOT NULL CHECK(type IN (
                    'person','organization','place',
                    'library','tool','project','concept')),
  aliases         TEXT,                              -- JSON array
  context         TEXT,                              -- 最近一次出现的上下文片段
  first_seen_at   TEXT NOT NULL,
  last_seen_at    TEXT NOT NULL,
  mention_count   INTEGER NOT NULL DEFAULT 1,
  scope_hash      TEXT,                              -- 主 scope（首次出现）
  decay_state     TEXT NOT NULL DEFAULT 'fresh'      -- fresh / dim / forgotten
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
  source_memory_id  TEXT,                            -- 触发关系的 memory slug
  scope_hash        TEXT,
  confidence        REAL,
  created_at        TEXT NOT NULL,
  superseded_at     TEXT,                            -- NULL = active
  UNIQUE(subject_id, predicate, object_id, source_memory_id)
);
CREATE INDEX IF NOT EXISTS idx_relations_subject   ON relations(subject_id);
CREATE INDEX IF NOT EXISTS idx_relations_object    ON relations(object_id);
CREATE INDEX IF NOT EXISTS idx_relations_predicate ON relations(predicate);

CREATE TABLE IF NOT EXISTS supersedes_chain (
  newer_memory_id  TEXT NOT NULL,
  older_memory_id  TEXT NOT NULL,
  entity_id        TEXT,                             -- 触发 supersede 的 entity（可空）
  confidence       REAL NOT NULL,
  decided_at       TEXT NOT NULL,
  decided_by       TEXT NOT NULL,                    -- 'auto' / 'user' / 'digest'
  reason           TEXT,
  PRIMARY KEY(newer_memory_id, older_memory_id)
);
CREATE INDEX IF NOT EXISTS idx_supersedes_entity ON supersedes_chain(entity_id);
CREATE INDEX IF NOT EXISTS idx_supersedes_newer  ON supersedes_chain(newer_memory_id);
CREATE INDEX IF NOT EXISTS idx_supersedes_older  ON supersedes_chain(older_memory_id);
