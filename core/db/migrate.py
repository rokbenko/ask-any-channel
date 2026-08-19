"""Dependency-light SQL migration runner.

Applies numbered .sql files from core/db/migrations/ in filename order, tracking what has
already run in a self-created schema_migrations table. Deliberately dumb (no ORM, no
migration DSL) so a future non-Python implementation only needs to replicate: read files in
order, run each in a transaction, record the filename. Called both as a script
(`python -m core.db.migrate`) and lazily from core.db.get_pool() on the first DB touch, so
the CLI never needs a separate manual migrate step. Must not import core.db (circular).
"""

import os
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv

DEFAULT_MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Arbitrary fixed key for a session-level advisory lock serializing concurrent migration
# attempts — e.g. a container's own boot-time check and its Docker HEALTHCHECK (`aac doctor`)
# can both hit a freshly-created database within the same second. Without this, two sessions
# race CREATE TABLE IF NOT EXISTS schema_migrations and one gets a UniqueViolation on the
# catalog's own unique index (found live running `docker compose up` against a wiped volume).
# Released automatically if the holding session dies, so a crash can't leave it stuck.
_MIGRATION_LOCK_KEY = 847_213_001


class MigrationError(RuntimeError):
    """Postgres was reachable but applying a migration failed. The message is already written
    for a self-hoster (see describe_migration_error); the original psycopg error is __cause__."""


def ensure_schema_migrations_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    conn.commit()


def get_applied(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    return {row[0] for row in rows}


def apply_all(
    database_url: str,
    migrations_dir: Path = DEFAULT_MIGRATIONS_DIR,
    *,
    connect_timeout: int = 30,
) -> list[str]:
    """Applies un-applied .sql files in filename-sorted order. Returns newly-applied filenames.

    Raises psycopg.OperationalError if the database can't be *reached* within connect_timeout,
    and MigrationError (message already user-facing) if it was reached but a migration failed —
    missing pgvector extension, role can't CREATE, and so on. The split is structural (connect
    vs. everything after), NOT by exception class: psycopg maps "could not open extension
    control file" (no pgvector) to UndefinedFile, which is itself an OperationalError subclass,
    so class-based catching would report a present-but-unusable server as "is it running?"."""
    applied_now: list[str] = []
    conn = psycopg.connect(database_url, connect_timeout=connect_timeout)  # OperationalError
    with conn:
        try:
            conn.execute("SELECT pg_advisory_lock(%s)", (_MIGRATION_LOCK_KEY,))
            ensure_schema_migrations_table(conn)
            already_applied = get_applied(conn)

            for path in sorted(migrations_dir.glob("*.sql")):
                if path.name in already_applied:
                    continue
                sql = path.read_text(encoding="utf-8")
                with conn.transaction():
                    conn.execute(sql)
                    conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES (%s)",
                        (path.name,),
                    )
                applied_now.append(path.name)
        except psycopg.Error as exc:
            raise MigrationError(describe_migration_error(exc)) from exc
        finally:
            # Best-effort: if the connection died mid-migration the lock is already gone with
            # the session, and a failing unlock must not mask the original exception.
            try:
                conn.execute("SELECT pg_advisory_unlock(%s)", (_MIGRATION_LOCK_KEY,))
                conn.commit()
            except psycopg.Error:
                pass

    return applied_now


def describe_migration_error(exc: psycopg.Error) -> str:
    """One actionable, user-facing line for a psycopg error raised *after* connecting — i.e. a
    migration failed. The two failures a self-hoster bringing their own Postgres actually hits
    are "no pgvector extension" and "role can't CREATE"; name the fix for each."""
    diag = getattr(exc, "diag", None)
    primary = (diag.message_primary if diag and diag.message_primary else str(exc)).strip()
    lowered = primary.lower()
    # Privilege first: "permission denied to create extension \"vector\"" mentions vector too,
    # but the fix is a grant/superuser run, not installing pgvector.
    if "permission denied" in lowered or "must be owner" in lowered:
        hint = (
            " — the DATABASE_URL role lacks the privilege: run the first `aac doctor` (or any "
            "`aac` command) once as a superuser so the extensions and tables get created, or "
            "grant CREATE on the database to this role"
        )
    elif "vector" in lowered and ("extension" in lowered or "type" in lowered or "file" in lowered):
        hint = (
            " — the pgvector extension isn't installed on this server: use the "
            "`pgvector/pgvector:pg16` image (what docker-compose.yml runs), or install pgvector "
            "and run `CREATE EXTENSION vector` as a superuser"
        )
    else:
        hint = ""
    return f"Postgres is reachable but applying migrations failed: {primary}{hint}"


if __name__ == "__main__":
    load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("DATABASE_URL is required", file=sys.stderr)
        sys.exit(1)

    newly_applied = apply_all(database_url)
    if newly_applied:
        print(f"Applied {len(newly_applied)} migration(s): {', '.join(newly_applied)}")
    else:
        print("No new migrations to apply.")
