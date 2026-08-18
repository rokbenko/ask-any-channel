"""`aac worker` — runs the polling ingest daemon in the foreground. Exits cleanly on
SIGTERM/SIGINT (Docker's `stop`/Ctrl+C) after finishing the current job; a second signal
force-quits. Logs at INFO so `docker compose logs worker` can triage a job."""

import logging

from core.config import get_settings
from core.credentials import CredentialsProvider
from core.store.pgvector_store import PgVectorStore
from core.worker.daemon import poll_and_run


def worker() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    poll_and_run(PgVectorStore(), CredentialsProvider(get_settings()))
