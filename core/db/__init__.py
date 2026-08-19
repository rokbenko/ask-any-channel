"""Shared psycopg connection pool + pgvector adapter registration.

Migrations are applied lazily on the first DB touch (`get_pool()`), so commands that never
open a connection — `aac dataset validate`, `aac registry entry`, `--help` — work with no
Postgres and no DATABASE_URL at all. `core.store.pgvector_store` is the only module that
should issue SQL through this pool; everything else goes through the VectorStore interface.
"""

import atexit
import logging
from contextlib import contextmanager
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from core.config import get_settings
from core.db.migrate import MigrationError, apply_all

CONNECT_TIMEOUT_S = 5

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """Raised when Postgres can't be reached; message is safe to show to end users."""


def redact_database_url(url: str) -> str:
    """Public so callers building their own user-facing messages (core.doctor) never leak the
    password — e.g. `aac doctor` output is meant to be pasted into a bug report."""
    parts = urlsplit(url)
    if parts.password:
        netloc = parts.netloc.replace(f":{parts.password}@", ":***@")
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


def _configure(conn: psycopg.Connection) -> None:
    register_vector(conn)


@lru_cache
def get_pool() -> ConnectionPool:
    settings = get_settings()

    # Prove reachability (and apply pending migrations) with a plain synchronous connection
    # BEFORE creating the pool. ConnectionPool.wait() spawns a worker thread that keeps
    # retrying and stalls interpreter exit for several seconds when the DB is down; a direct
    # connect fails immediately and cleanly instead.
    try:
        newly_applied = apply_all(settings.database_url, connect_timeout=CONNECT_TIMEOUT_S)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailableError(
            f"Can't reach Postgres at {redact_database_url(settings.database_url)} — is it "
            "running? Try: docker compose up -d postgres"
        ) from exc
    except MigrationError as exc:
        # Reachable, but a migration failed (no pgvector extension, role can't CREATE, ...) —
        # the most common bring-your-own-Postgres failure; must be a sentence, not a traceback.
        raise DatabaseUnavailableError(str(exc)) from exc

    if newly_applied:
        logger.info("applied %d migration(s): %s", len(newly_applied), ", ".join(newly_applied))
    else:
        logger.info("database up to date, no migrations to apply")

    pool = ConnectionPool(
        settings.database_url,
        configure=_configure,
        min_size=1,
        max_size=5,
    )
    # Close explicitly at exit. Left to ConnectionPool.__del__ during interpreter teardown,
    # the worker-thread gather can hang for its full 5s timeout on every CLI invocation.
    atexit.register(pool.close)
    return pool


@contextmanager
def get_connection():
    with get_pool().connection() as conn:
        yield conn
