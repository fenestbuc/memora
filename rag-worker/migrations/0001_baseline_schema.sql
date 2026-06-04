-- Migration: baseline_schema
-- Sequence: 0001
-- Initial schema with original columns only.

CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  parent_id TEXT,
  source_session TEXT,
  source_file TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(source_session);
CREATE INDEX IF NOT EXISTS idx_facts_parent ON facts(parent_id);

CREATE TABLE IF NOT EXISTS sync_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  fact_id TEXT,
  timestamp TEXT DEFAULT (datetime('now')),
  details TEXT
);

-- Migration state tracking
CREATE TABLE IF NOT EXISTS _migrations (
  version INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  applied_at TEXT DEFAULT (datetime('now')),
  checksum TEXT NOT NULL,
  duration_ms INTEGER
);

INSERT INTO _migrations (version, name, checksum) VALUES (1, 'baseline_schema', 'init');
