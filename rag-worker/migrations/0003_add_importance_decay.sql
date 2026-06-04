-- Migration: add_importance_decay
-- Sequence: 0003

BEGIN TRANSACTION;

ALTER TABLE facts ADD COLUMN importance_score REAL DEFAULT 0.5;
ALTER TABLE facts ADD COLUMN decayed_at TEXT;
ALTER TABLE facts ADD COLUMN archived INTEGER DEFAULT 0 CHECK (archived IN (0, 1));

CREATE INDEX IF NOT EXISTS idx_facts_importance ON facts(importance_score);
CREATE INDEX IF NOT EXISTS idx_facts_archived ON facts(archived);

UPDATE facts SET importance_score = 0.5 WHERE importance_score IS NULL;

INSERT INTO _migrations (version, name, checksum) VALUES (3, 'add_importance_decay', 'v1');

COMMIT;
