-- One active (queued/running) job per channel, enforced by the database so it holds under
-- concurrent enqueues (double-submit, UI + CLI, several workers) — the pre-check in
-- core.ingest.jobs is only there for a friendlier message.
CREATE UNIQUE INDEX ux_ingest_jobs_one_active_per_channel
    ON ingest_jobs (channel_id)
    WHERE status IN ('queued', 'running');

-- Jobs enqueued for a not-yet-resolved channel have channel_id NULL (NULLs never collide in
-- a unique index), so dedupe those on the raw input string instead.
CREATE UNIQUE INDEX ux_ingest_jobs_one_active_per_input
    ON ingest_jobs ((payload->>'channel_input'))
    WHERE channel_id IS NULL AND status IN ('queued', 'running');
