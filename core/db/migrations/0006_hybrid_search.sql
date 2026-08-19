-- Generated column: Postgres maintains this automatically on every INSERT/UPDATE to chunks
-- (including existing rows, backfilled once at ALTER time), so replace_chunks needs no changes.
ALTER TABLE chunks ADD COLUMN tsv tsvector
    GENERATED ALWAYS AS (to_tsvector('english', text)) STORED;

CREATE INDEX idx_chunks_tsv ON chunks USING GIN (tsv);
