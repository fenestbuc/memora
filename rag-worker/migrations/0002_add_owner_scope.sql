-- Migration: add_owner_scope
-- Sequence: 0002

BEGIN TRANSACTION;

ALTER TABLE facts ADD COLUMN owner_id TEXT DEFAULT 'anonymous';
ALTER TABLE facts ADD COLUMN scope TEXT DEFAULT 'personal';

CREATE INDEX IF NOT EXISTS idx_facts_owner ON facts(owner_id);
CREATE INDEX IF NOT EXISTS idx_facts_scope ON facts(scope);

UPDATE facts SET owner_id = 'anonymous', scope = 'personal' WHERE owner_id IS NULL;

INSERT INTO _migrations (version, name, checksum) VALUES (2, 'add_owner_scope', 'v2');

COMMIT;
