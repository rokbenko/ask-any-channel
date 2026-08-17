"""`aac dataset build/load/validate` — the two-step dataset-bundle pipeline plus
integrity checking. Bundles are local-only; nothing here ever writes transcript content
into the repo (see .gitignore's `datasets/` entry)."""

from pathlib import Path

import typer
from rich.console import Console

from core.constants import EMBEDDING_COST_PER_1K_TOKENS_USD
from core.credentials import CredentialError
from core.dataset.bundle import bundle_exists, default_bundle_dir, read_bundle
from core.dataset.manifest import read_manifest
from core.dataset.validate import validate_bundle
from core.ingest.captions import JsRuntimeMissingError
from core.ingest.runner import BundleInvalidError, run_dataset_build, run_dataset_load

console = Console()

app = typer.Typer(help="Build, load, and validate local dataset bundles.")


def _dir_size_mb(path: Path) -> float:
    total = sum(f.stat().st_size for f in path.glob("*") if f.is_file())
    return total / (1024 * 1024)


def _param_mismatches(out_dir: Path, *, limit: int | None, skip_embeddings: bool) -> list[str]:
    """Human-readable differences between an existing bundle and the requested build."""
    try:
        manifest = read_manifest(out_dir)
    except Exception:
        return []
    diffs: list[str] = []
    if manifest.limit != limit:
        diffs.append(f"--limit {manifest.limit} (existing) vs {limit} (requested)")
    if (manifest.embedding is None) != skip_embeddings:
        existing = "no embeddings" if manifest.embedding is None else "with embeddings"
        requested = "no embeddings" if skip_embeddings else "with embeddings"
        diffs.append(f"{existing} (existing) vs {requested} (requested)")
    return diffs


@app.command("build")
def build(
    channel: str = typer.Argument(..., help="Channel URL, @handle, or channel id"),
    limit: int | None = typer.Option(None, "--limit", help="Max videos to build"),
    sort: str = typer.Option(
        "recent",
        "--sort",
        help="recent | views (note: 'views' ranks within the N most recent, not all-time top)",
    ),
    out: Path | None = typer.Option(None, "--out", help="Bundle output directory"),
    skip_embeddings: bool = typer.Option(
        False, "--skip-embeddings", help="Build the bundle without calling the embedding API"
    ),
    force: bool = typer.Option(
        False, "--force", help="Rebuild even if a bundle already exists at the output path"
    ),
) -> None:
    if sort not in ("views", "recent"):
        raise typer.BadParameter("--sort must be 'views' or 'recent'")

    out_dir = out or default_bundle_dir(channel)
    if bundle_exists(out_dir) and not force:
        console.print(f"[yellow]Already built:[/yellow] {out_dir} (use --force to rebuild)")
        for diff in _param_mismatches(out_dir, limit=limit, skip_embeddings=skip_embeddings):
            console.print(f"  [yellow]note:[/yellow] existing bundle differs — {diff}")
        return

    console.print(f"Building dataset for [bold]{channel}[/bold] -> {out_dir} ...")
    try:
        result_dir = run_dataset_build(
            channel,
            limit=limit,
            sort=sort,
            out=out_dir,
            skip_embeddings=skip_embeddings,
            force=force,
        )
    except JsRuntimeMissingError as exc:
        console.print(f"[red]Build failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc
    except CredentialError as exc:
        console.print(
            f"[red]Build failed:[/red] {exc}. Embeddings need an API key — set it in .env, "
            "or pass --skip-embeddings to build a bundle without them."
        )
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]Build failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    bundle = read_bundle(result_dir)
    manifest = bundle.manifest
    size_mb = _dir_size_mb(result_dir)

    console.print(f"[green]Bundle ready:[/green] {result_dir}")
    console.print(
        f"  videos: {manifest.video_count}   "
        f"chunks: {manifest.chunk_count}   "
        f"size: {size_mb:.1f} MB"
    )

    if manifest.embedding is not None:
        total_tokens = sum(c.token_count for c in bundle.chunks)
        cost = (total_tokens / 1000) * EMBEDDING_COST_PER_1K_TOKENS_USD
        console.print(f"  embedding cost (approx): ${cost:.4f} — {manifest.embedding.model}")
    else:
        console.print("  embeddings: skipped (--skip-embeddings)")

    handle = manifest.channel.handle.lstrip("@") if manifest.channel.handle else channel.lstrip("@")
    console.print(
        f"\nPut {handle} on the map: `aac registry entry {handle}` and open a PR "
        "(metadata only — your bundle stays local, see README)."
    )


@app.command("load")
def load(path: Path = typer.Argument(..., help="Path to a dataset bundle directory")) -> None:
    console.print(f"Loading {path} into Postgres...")
    try:
        channel = run_dataset_load(path)
    except BundleInvalidError as exc:
        console.print(f"[red]Load refused — invalid bundle:[/red] {exc.path}")
        for e in exc.errors:
            console.print(f"  - {e}")
        raise typer.Exit(code=1) from exc
    except CredentialError as exc:
        console.print(
            f"[red]Load failed:[/red] {exc}. This bundle's embeddings are missing or were "
            "built with a different model, so they must be recomputed — that needs an API key."
        )
        raise typer.Exit(code=1) from exc
    except Exception as exc:
        console.print(f"[red]Load failed:[/red] {exc}")
        raise typer.Exit(code=1) from exc

    console.print(f"[green]Loaded.[/green] Channel: {channel.title or channel.handle}")


@app.command("validate")
def validate(path: Path = typer.Argument(..., help="Path to a dataset bundle directory")) -> None:
    errors = validate_bundle(path)
    if not errors:
        console.print(f"[green]Valid.[/green] {path}")
        return

    console.print(f"[red]Invalid bundle:[/red] {path}")
    for e in errors:
        console.print(f"  - {e}")
    raise typer.Exit(code=1)
