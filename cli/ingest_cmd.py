"""`aac ingest` — convenience command: builds a dataset bundle (see `aac dataset build`)
and immediately loads it into Postgres (see `aac dataset load`). An already-built bundle
is reused as-is (build no-ops); loading is safe to re-run."""

import typer
from rich.console import Console

from core.ingest.runner import run_ingest_inline

console = Console()


def ingest(
    channel: str = typer.Argument(..., help="Channel URL, @handle, or channel id"),
    limit: int | None = typer.Option(None, "--limit", help="Max videos to ingest"),
    sort: str = typer.Option("recent", "--sort", help="views | recent"),
) -> None:
    if sort not in ("views", "recent"):
        raise typer.BadParameter("--sort must be 'views' or 'recent'")

    console.print(f"Ingesting [bold]{channel}[/bold] (limit={limit}, sort={sort})...")

    try:
        job = run_ingest_inline(channel, limit=limit, sort=sort)
    except Exception as exc:
        console.print(f"[red]Ingest failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    progress = job.progress or {}
    if job.status == "done":
        done, total = progress.get("done", 0), progress.get("total", 0)
        console.print(f"[green]Done.[/green] {done}/{total} videos processed.")
    else:
        console.print(f"[red]Job {job.status}.[/red] {job.error or ''}")
        raise typer.Exit(code=1)
