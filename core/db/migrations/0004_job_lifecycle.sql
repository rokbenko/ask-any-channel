ALTER TABLE ingest_jobs ADD COLUMN heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now();

ALTER TABLE ingest_jobs DROP CONSTRAINT ingest_jobs_status_check;
ALTER TABLE ingest_jobs ADD CONSTRAINT ingest_jobs_status_check
    CHECK (status IN ('queued', 'running', 'done', 'failed', 'cancelled'));
