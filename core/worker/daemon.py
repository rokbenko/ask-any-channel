"""Polls ingest_jobs for queued work and runs it through the shared pipeline. Uses the same
run_ingest_job()/run_update_job() the inline `aac ingest` CLI path and the UI's enqueue calls
feed into — this file is thin plumbing only.

Every poll iteration first reclaims 'running' jobs whose heartbeat has gone stale (the worker
that claimed them died without finishing) back to 'queued', so killing the container mid-job and
restarting it resumes work without a manual retry; a job reclaimed MAX_JOB_ATTEMPTS times is
failed instead of requeued (a poison job must not re-embed a channel forever). Any exception a
claimed job's run raises — including one from resolving the channel, which happens before
build_dataset's own try/except — is caught here and recorded as a job failure rather than
crashing the poll loop. A database outage mid-loop is logged and retried after a pause rather
than exiting (compose would restart us, but in a tight loop with no diagnostics).

Daemon-claimed jobs always derive their bundle output path from the job's channel_input
(deterministic, via default_bundle_dir/default_update_bundle_dir) rather than any custom --out a
CLI caller might have used when originally creating the job — the payload doesn't carry --out
today."""

import logging
import signal
import threading

from core.config import get_settings
from core.constants import JOB_STALE_AFTER_S, MAX_JOB_ATTEMPTS
from core.credentials import CredentialsProvider
from core.dataset.bundle import default_bundle_dir, default_update_bundle_dir
from core.ingest.pipeline import run_ingest_job, run_update_job
from core.models import IngestJob
from core.store.base import VectorStore
from core.store.pgvector_store import PgVectorStore

logger = logging.getLogger(__name__)

POLL_INTERVAL_S = 5.0
# After a DB/poll error, wait this long before trying again — long enough not to spam the log
# during a Postgres restart, short enough that a self-hoster doesn't think the worker hung.
ERROR_BACKOFF_S = 15.0


def _run_claimed_job(store: VectorStore, credentials: CredentialsProvider, job: IngestJob) -> None:
    channel_input = job.payload.get("channel_input", "")
    kind = job.payload.get("kind", "ingest")  # jobs created before this field existed are ingests

    if kind == "update":
        out_dir = default_update_bundle_dir(channel_input, job.id)
        run_update_job(store, credentials, job, out_dir=out_dir)
    else:
        out_dir = default_bundle_dir(channel_input)
        run_ingest_job(store, credentials, job, out_dir=out_dir)


def _install_signal_handlers(stop_event: threading.Event) -> None:
    def _request_stop(signum, _frame):
        logger.info("signal %s received — finishing the current job, then exiting", signum)
        stop_event.set()
        # A second Ctrl+C / TERM should force-quit rather than be swallowed: hand the signal
        # back to Python's default handling once the graceful stop is underway.
        signal.signal(signum, signal.SIG_DFL)

    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)


def poll_and_run(
    store: VectorStore,
    credentials: CredentialsProvider,
    *,
    poll_interval_s: float = POLL_INTERVAL_S,
    stop_event: threading.Event | None = None,
) -> None:
    # A caller-supplied stop_event (tests) means the caller owns process-wide signal handling
    # too — installing our own handler would clobber the test process's. The daemon's own
    # __main__/CLI entry point never passes one, so it always owns SIGTERM/SIGINT.
    owns_signals = stop_event is None
    stop_event = stop_event or threading.Event()
    if owns_signals:
        _install_signal_handlers(stop_event)

    logger.info(
        "worker started (poll every %.0fs, stale after %.0fs)", poll_interval_s, JOB_STALE_AFTER_S
    )
    while not stop_event.is_set():
        try:
            reclaimed = store.reclaim_stale_jobs(JOB_STALE_AFTER_S, max_attempts=MAX_JOB_ATTEMPTS)
            if reclaimed:
                logger.warning("requeued %d stale running job(s): %s", len(reclaimed), reclaimed)
            job = store.claim_next_queued_job()
        except Exception:
            logger.exception(
                "poll failed (database unreachable?) — retrying in %.0fs", ERROR_BACKOFF_S
            )
            stop_event.wait(ERROR_BACKOFF_S)
            continue

        if job is None:
            stop_event.wait(poll_interval_s)
            continue

        logger.info(
            "claimed job %s kind=%s input=%r attempt=%d",
            job.id,
            job.payload.get("kind", "ingest"),
            job.payload.get("channel_input"),
            int(job.progress.get("attempts", 0)) + 1,
        )
        try:
            _run_claimed_job(store, credentials, job)
        except Exception as exc:
            logger.exception("job %s failed", job.id)
            store.update_job(job.id, status="failed", error=str(exc))
        else:
            logger.info("job %s finished status=%s", job.id, store.get_job(job.id).status)
    logger.info("worker stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    poll_and_run(PgVectorStore(), CredentialsProvider(get_settings()))
