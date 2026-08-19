"""`aac search` — embeds a question, searches the channel's chunks, prints ranked,
timestamped results."""

import typer
from rich.console import Console
from rich.table import Table

from core.config import get_settings
from core.constants import VALID_RETRIEVAL_MODES
from core.credentials import CredentialError, CredentialsProvider
from core.providers.openai_provider import OpenAIProvider
from core.search.format import snippet
from core.search.search import ChannelNotFoundError, build_timestamped_url, search_channel
from core.store.pgvector_store import PgVectorStore

console = Console()


def search(
    query: str = typer.Argument(..., help="Question to search for"),
    channel: str = typer.Option(..., "--channel", help="Channel URL, @handle, or channel id"),
    top_k: int = typer.Option(8, "--top-k"),
    mode: str = typer.Option(
        None, "--mode", help=f"One of {VALID_RETRIEVAL_MODES}. Defaults to RETRIEVAL_MODE."
    ),
) -> None:
    settings = get_settings()
    mode = mode or settings.retrieval_mode
    if mode not in VALID_RETRIEVAL_MODES:
        console.print(f"[red]--mode must be one of {VALID_RETRIEVAL_MODES}, got {mode!r}[/red]")
        raise typer.Exit(code=1)

    store = PgVectorStore()

    try:
        provider = OpenAIProvider(CredentialsProvider(settings))
        results = search_channel(
            store, provider, channel_ref=channel, query=query, top_k=top_k, mode=mode
        )
    except CredentialError as exc:
        console.print(f"[red]{exc}.[/red] Search needs an API key to embed the query text.")
        raise typer.Exit(code=1) from exc
    except ChannelNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    if not results:
        console.print("No results.")
        return

    table = Table(show_lines=True)
    table.add_column("Score")
    table.add_column("Video")
    table.add_column("Link")
    table.add_column("Snippet")

    for r in results:
        table.add_row(
            f"{r.score:.3f}",
            r.video_title or r.yt_video_id,
            build_timestamped_url(r.yt_video_id, r.t_start_s),
            snippet(r.text),
        )

    console.print(table)
