"""`aac retrieval compare` — runs the same question through dense-only and hybrid retrieval
over the same channel set, side by side, so a hybrid win (exact names, numbers, framework
titles) is visible rather than asserted."""

import typer
from rich.console import Console
from rich.table import Table

from core.config import get_settings
from core.credentials import CredentialError, CredentialsProvider
from core.providers.openai_provider import OpenAIProvider
from core.search.compare import compare_retrieval
from core.search.format import mmss, snippet
from core.search.search import ChannelNotFoundError
from core.store.pgvector_store import PgVectorStore

console = Console()

app = typer.Typer(help="Compare dense vs hybrid retrieval for a question.")


def _table(title: str, results, other_ids: set) -> Table:
    table = Table(title=title, show_lines=True)
    table.add_column("Rank")
    table.add_column("Score")
    table.add_column("Creator")
    table.add_column("Title")
    table.add_column("Time")
    table.add_column("Snippet")
    table.add_column("Also in other mode?")
    for i, r in enumerate(results, start=1):
        table.add_row(
            str(i),
            f"{r.score:.3f}",
            r.channel_title or r.channel_handle or "",
            r.video_title or r.yt_video_id,
            mmss(r.t_start_s),
            snippet(r.text, max_len=120),
            "yes" if r.chunk_id in other_ids else "no",
        )
    return table


@app.command("compare")
def compare(
    query: str = typer.Argument(..., help="Question to search for"),
    channels: str = typer.Option(
        ..., "--channels", help="Comma-separated channel URLs/@handles/ids"
    ),
    top_k: int = typer.Option(8, "--top-k"),
) -> None:
    refs = [ref.strip() for ref in channels.split(",") if ref.strip()]
    store = PgVectorStore()

    try:
        provider = OpenAIProvider(CredentialsProvider(get_settings()))
        result = compare_retrieval(store, provider, channel_refs=refs, query=query, top_k=top_k)
    except CredentialError as exc:
        console.print(f"[red]{exc}.[/red] Comparing retrieval needs an API key to embed the query.")
        raise typer.Exit(code=1) from exc
    except ChannelNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(code=1) from exc

    dense_ids = {r.chunk_id for r in result.dense}
    hybrid_ids = {r.chunk_id for r in result.hybrid}
    console.print(_table("Dense (pgvector cosine only)", result.dense, hybrid_ids))
    console.print(_table("Hybrid (dense + lexical, RRF-fused)", result.hybrid, dense_ids))
