"""The `aac` CLI. Every command is a thin wrapper — all logic lives in core/.

There is deliberately no global "connect to the DB" callback: commands that never touch
Postgres (`dataset validate`, `registry entry`, `--help`) must work with no DATABASE_URL and
no running database. Migrations apply lazily on the first DB touch (see core.db.get_pool).
"""

import sys

import typer
from rich.console import Console

from cli.dataset_cmd import app as dataset_app
from cli.doctor_cmd import doctor
from cli.ingest_cmd import ingest
from cli.registry_cmd import app as registry_app
from cli.search_cmd import search
from cli.status_cmd import status
from cli.worker_cmd import worker
from core.config import ConfigError
from core.constants import APP_NAME, CLI_NAME, TOOL_VERSION
from core.credentials import CredentialError
from core.db import DatabaseUnavailableError
from core.ingest.channel_source import ChannelInputError
from core.ingest.jobs import (
    ActiveJobExistsError,
    InvalidJobOptionsError,
    JobNotCancellableError,
    JobNotRetryableError,
)
from core.providers.base import ProviderError

# Errors whose message is already written for end users. Anything else is a bug and should
# surface as a normal traceback so it can be pasted into an issue.
_USER_FACING_ERRORS = (
    ConfigError,
    DatabaseUnavailableError,
    CredentialError,
    ProviderError,
    ChannelInputError,
    ActiveJobExistsError,
    InvalidJobOptionsError,
    JobNotRetryableError,
    JobNotCancellableError,
)

app = typer.Typer(
    name=CLI_NAME,
    help=f"{APP_NAME} CLI",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"{APP_NAME} {TOOL_VERSION}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_print_version,
        is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """Root callback: exists only to host --version. Deliberately no DB/config work here (see
    module docstring)."""


app.command("ingest")(ingest)
app.command("search")(search)
app.command("status")(status)
app.command("worker")(worker)
app.command("doctor")(doctor)
app.add_typer(dataset_app, name="dataset")
app.add_typer(registry_app, name="registry")


def main() -> None:
    try:
        app()
    except _USER_FACING_ERRORS as exc:
        Console(stderr=True).print(f"[red]Error:[/red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
