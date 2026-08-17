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
    Raises psycopg.OperationalError if the database can't be reached within connect_timeout."""
    applied_now: list[str] = []
    with psycopg.connect(database_url, connect_timeout=connect_timeout) as conn:
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

    return applied_now


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
