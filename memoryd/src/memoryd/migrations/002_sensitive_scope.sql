ALTER TABLE memories ADD COLUMN scope_sensitive INTEGER NOT NULL DEFAULT 0;
CREATE INDEX IF NOT EXISTS idx_memories_sensitive ON memories (scope_sensitive);
