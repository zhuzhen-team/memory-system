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
