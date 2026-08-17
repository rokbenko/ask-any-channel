"""Polls ingest_jobs for queued work and runs it through the shared pipeline. Uses the
exact same run_ingest_job() as the inline `aac ingest` CLI path — this file is thin
plumbing only, per the "both entry points share code" requirement.

Daemon-claimed jobs always derive their bundle output path from the job's channel_input
(deterministic, via default_bundle_dir) rather than any custom --out a CLI caller might
have used when originally creating the job — the payload doesn't carry --out today."""

import time

from core.config import get_settings
from core.credentials import CredentialsProvider
from core.dataset.bundle import default_bundle_dir
from core.ingest.pipeline import run_ingest_job
from core.store.base import VectorStore
from core.store.pgvector_store import PgVectorStore

POLL_INTERVAL_S = 5.0


def poll_and_run(
    store: VectorStore,
    credentials: CredentialsProvider,
    *,
    poll_interval_s: float = POLL_INTERVAL_S,
) -> None:
    while True:
        job = store.claim_next_queued_job()
        if job is None:
            time.sleep(poll_interval_s)
            continue

        out_dir = default_bundle_dir(job.payload["channel_input"])
        run_ingest_job(store, credentials, job, out_dir=out_dir)


if __name__ == "__main__":
    poll_and_run(PgVectorStore(), CredentialsProvider(get_settings()))
