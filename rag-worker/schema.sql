CREATE TABLE IF NOT EXISTS facts (
  id TEXT PRIMARY KEY,
  vector_id TEXT,
  category TEXT NOT NULL,
  content TEXT NOT NULL,
  parent_id TEXT,
  owner_id TEXT DEFAULT 'anonymous',
  scope TEXT DEFAULT 'personal',
  importance_score REAL DEFAULT 0.5,
  decayed_at TEXT,
  archived INTEGER DEFAULT 0 CHECK (archived IN (0, 1)),
  pending_vector_sync INTEGER DEFAULT 0,
  source_session TEXT,
  source_file TEXT,
  created_at TEXT DEFAULT (datetime('now')),
  updated_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category);
CREATE INDEX IF NOT EXISTS idx_facts_session ON facts(source_session);
CREATE INDEX IF NOT EXISTS idx_facts_parent ON facts(parent_id);
CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(owner_id);
CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope);
CREATE INDEX IF NOT EXISTS idx_facts_importance ON facts(importance_score);
CREATE INDEX IF NOT EXISTS idx_facts_archived ON facts(archived);
CREATE INDEX IF NOT EXISTS idx_facts_vector ON facts(vector_id);
CREATE INDEX IF NOT EXISTS idx_facts_pending_sync ON facts(pending_vector_sync);

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
