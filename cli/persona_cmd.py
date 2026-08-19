"""`aac persona build` — generates (or regenerates) a channel's corpus-derived voice profile,
sampled from the channel's own ingested transcripts. Prints the editable markdown profile;
nothing here decides whether the voice is used in chat (that's the persona's `enabled` flag,
set from the UI's Voice popover)."""

import typer
from rich.console import Console

from core.config import get_settings
from core.credentials import CredentialsProvider
from core.persona import ensure_style_profile
from core.store.pgvector_store import PgVectorStore

console = Console()

app = typer.Typer(help="Build a channel's corpus-derived voice profile.")


@app.command("build")
def build(
    channel: str = typer.Argument(..., help="Channel URL, @handle, or channel id"),
    force: bool = typer.Option(
        False, "--force", help="Regenerate even if a profile already exists."
    ),
) -> None:
    store = PgVectorStore()
    channel_row = store.get_channel_by_handle_or_id(channel)
    if channel_row is None:
        console.print(f"[red]No channel found matching {channel!r}[/red]")
        raise typer.Exit(code=1)

    settings = get_settings()
    credentials = CredentialsProvider(settings)
    profile = ensure_style_profile(store, credentials, settings, channel_row, force=force)

    if profile is None:
        console.print(
            "[yellow]Could not build a style profile[/yellow] — this needs a configured chat "
            "key (see CHAT_PROVIDER) and at least one embedded video for this channel."
        )
        raise typer.Exit(code=1)

    console.print(profile)
