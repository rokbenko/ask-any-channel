"""`aac worker` — runs the polling ingest daemon in the foreground. Exits cleanly on
SIGTERM/SIGINT (Docker's `stop`/Ctrl+C) after finishing the current job; a second signal
force-quits. Logs at INFO so `docker compose logs worker` can triage a job."""

import logging
import sys

from core.config import get_settings
from core.credentials import CredentialsProvider
from core.doctor import run_checks, versions_line
from core.store.pgvector_store import PgVectorStore
from core.worker.daemon import poll_and_run


def worker() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logger = logging.getLogger(__name__)
    logger.info(versions_line())

    # Same subset the compose healthcheck runs (`aac doctor --role worker`), from one table in
    # core.doctor — so the worker can't boot healthy and be flagged unhealthy 30 s later.
    failed = False
    for result in run_checks("worker"):
        if result.ok:
            logger.info("%s: %s", result.name, result.detail)
        else:
            failed = True
            logger.error("%s: %s", result.name, result.detail)
    if failed:
        logger.error("worker not started — fix the failing check(s) above and restart")
        sys.exit(1)

    settings = get_settings()
    poll_and_run(
        PgVectorStore(),
        CredentialsProvider(settings),
        auto_ingest_interval_hours=settings.auto_ingest_interval_hours,
    )
