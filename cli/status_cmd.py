"""`aac status` — channels, per-status video counts, recent ingest job states."""

from rich.console import Console
from rich.table import Table

from core.store.pgvector_store import PgVectorStore

console = Console()


def status() -> None:
    store = PgVectorStore()
    summary = store.status_summary()

    if not summary.channels:
        console.print("No channels ingested yet.")
    else:
        table = Table(title="Channels")
        table.add_column("Title")
        table.add_column("Handle")
        table.add_column("Video statuses")
        table.add_column("Auto-update")
        table.add_column("Last checked")

        for cs in summary.channels:
            counts = ", ".join(f"{c.status}={c.count}" for c in cs.video_status_counts)
            last_checked = (
                cs.channel.last_checked_at.strftime("%Y-%m-%d %H:%M")
                if cs.channel.last_checked_at
                else "never"
            )
            table.add_row(
                cs.channel.title or "-",
                cs.channel.handle or "-",
                counts or "-",
                "on" if cs.channel.auto_update else "off",
                last_checked,
            )

        console.print(table)

    if summary.recent_jobs:
        jobs_table = Table(title="Recent ingest jobs")
        jobs_table.add_column("Status")
        jobs_table.add_column("Progress")
        jobs_table.add_column("Error")
        jobs_table.add_column("Created")

        for job in summary.recent_jobs:
            progress = job.progress or {}
            progress_str = f"{progress.get('done', '?')}/{progress.get('total', '?')}"
            jobs_table.add_row(job.status, progress_str, job.error or "-", str(job.created_at))

        console.print(jobs_table)
