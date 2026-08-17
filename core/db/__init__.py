"""Shared psycopg connection pool + pgvector adapter registration.

Migrations are applied lazily on the first DB touch (`get_pool()`), so commands that never
open a connection — `aac dataset validate`, `aac registry entry`, `--help` — work with no
Postgres and no DATABASE_URL at all. `core.store.pgvector_store` is the only module that
should issue SQL through this pool; everything else goes through the VectorStore interface.
"""

import atexit
from contextlib import contextmanager
from functools import lru_cache
from urllib.parse import urlsplit, urlunsplit

import psycopg
from pgvector.psycopg import register_vector
from psycopg_pool import ConnectionPool

from core.config import get_settings
from core.db.migrate import apply_all

CONNECT_TIMEOUT_S = 5


class DatabaseUnavailableError(RuntimeError):
    """Raised when Postgres can't be reached; message is safe to show to end users."""


def _redact(url: str) -> str:
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
        apply_all(settings.database_url, connect_timeout=CONNECT_TIMEOUT_S)
    except psycopg.OperationalError as exc:
        raise DatabaseUnavailableError(
            f"Can't reach Postgres at {_redact(settings.database_url)} — is it running? "
            "Try: docker compose up -d postgres"
        ) from exc

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
