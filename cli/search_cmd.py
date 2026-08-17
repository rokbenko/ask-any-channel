"""`aac search` — embeds a question, searches the channel's chunks, prints ranked,
timestamped results."""

import typer
from rich.console import Console
from rich.table import Table

from core.config import get_settings
from core.credentials import CredentialError, CredentialsProvider
from core.providers.openai_provider import OpenAIProvider
from core.search.search import ChannelNotFoundError, build_timestamped_url, search_channel
from core.store.pgvector_store import PgVectorStore

console = Console()


def _snippet(text: str, max_len: int = 200) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


def search(
    query: str = typer.Argument(..., help="Question to search for"),
    channel: str = typer.Option(..., "--channel", help="Channel URL, @handle, or channel id"),
    top_k: int = typer.Option(8, "--top-k"),
) -> None:
    store = PgVectorStore()

    try:
        provider = OpenAIProvider(CredentialsProvider(get_settings()))
        results = search_channel(store, provider, channel_ref=channel, query=query, top_k=top_k)
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
            _snippet(r.text),
        )

    console.print(table)
