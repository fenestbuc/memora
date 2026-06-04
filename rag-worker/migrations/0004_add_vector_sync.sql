-- Migration: add_vector_sync
-- Sequence: 0004

BEGIN TRANSACTION;

ALTER TABLE facts ADD COLUMN vector_id TEXT;
ALTER TABLE facts ADD COLUMN pending_vector_sync INTEGER DEFAULT 0;

CREATE INDEX IF NOT EXISTS idx_facts_vector ON facts(vector_id);
CREATE INDEX IF NOT EXISTS idx_facts_pending_sync ON facts(pending_vector_sync);

-- Backfill vector_id from existing ids
UPDATE facts SET vector_id = id WHERE vector_id IS NULL;

INSERT INTO _migrations (version, name, checksum) VALUES (4, 'add_vector_sync', 'v1');

COMMIT;
