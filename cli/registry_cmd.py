"""`aac registry entry <handle>` — emits the metadata-only registry/channels.json entry
for a locally built bundle, ready to commit for a PR. Never includes transcript content."""

import json
from pathlib import Path

import typer
from rich.console import Console

from core.dataset.bundle import default_bundle_dir, read_bundle
from core.dataset.registry import build_registry_entry

console = Console()

app = typer.Typer(help="Emit metadata-only registry/channels.json entries.")


@app.command("entry")
def entry(
    handle: str = typer.Argument(..., help="Channel handle, or a path to a built bundle"),
) -> None:
    bundle_dir = default_bundle_dir(handle)
    if not bundle_dir.exists():
        candidate = Path(handle)
        if candidate.exists():
            bundle_dir = candidate
        else:
            console.print(
                f"[red]No local bundle found for {handle!r}[/red] (looked in {bundle_dir}). "
                "Run `aac dataset build` first."
            )
            raise typer.Exit(code=1)

    bundle = read_bundle(bundle_dir)
    entry_dict = build_registry_entry(bundle.manifest)

    console.print(json.dumps(entry_dict, indent=2))
    console.print(
        "\nAdd this to registry/channels.json and open a PR — "
        "transcript content is never included or committed."
    )
