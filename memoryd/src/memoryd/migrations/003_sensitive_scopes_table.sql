CREATE TABLE IF NOT EXISTS sensitive_scopes (
  scope_hash TEXT PRIMARY KEY,
  scope_root TEXT NOT NULL,
  marked_at  TEXT NOT NULL
);
