-- Profile self-learning module: weekly identity rewrite + trigger frequency
-- stats + monthly change reports.

CREATE TABLE IF NOT EXISTS profile_versions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    version_num          INTEGER NOT NULL UNIQUE,
    written_at           TEXT NOT NULL,            -- ISO-8601 UTC
    trigger              TEXT NOT NULL,            -- weekly_cron / manual / on_event / monthly_report
    content_md           TEXT NOT NULL,
    diff_from_prev       TEXT,                     -- unified diff vs previous version
    change_summary       TEXT,                     -- one-line LLM summary
    sources_count        INTEGER,                  -- how many long-term entries fed the LLM
    sources_window_start TEXT,
    sources_window_end   TEXT
);

CREATE INDEX IF NOT EXISTS idx_profile_versions_written_at
    ON profile_versions (written_at);

CREATE TABLE IF NOT EXISTS trigger_stats (
    trigger     TEXT NOT NULL,
    scope_hash  TEXT NOT NULL DEFAULT '_global',
    day         TEXT NOT NULL,                     -- YYYY-MM-DD
    hits        INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (trigger, scope_hash, day)
);

CREATE INDEX IF NOT EXISTS idx_trigger_stats_day
    ON trigger_stats (day);

CREATE TABLE IF NOT EXISTS profile_change_reports (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    period            TEXT NOT NULL UNIQUE,        -- YYYY-MM
    generated_at      TEXT NOT NULL,
    content_md        TEXT NOT NULL,
    versions_count    INTEGER,
    supersedes_count  INTEGER,
    entities_added    INTEGER,
    entities_dropped  INTEGER
);
