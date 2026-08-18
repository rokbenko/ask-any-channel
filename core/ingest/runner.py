"""CLI-facing entry points: create job rows and run the shared pipeline synchronously
in-process. Share core.ingest.pipeline functions with the polling worker daemon — these
files are thin plumbing only."""

import shutil
from pathlib import Path
from uuid import UUID

from core.config import get_settings
from core.credentials import CredentialsProvider
from core.dataset.bundle import (
    Bundle,
    bundle_exists,
    default_bundle_dir,
    find_bundle_dirs_for_channel,
    read_bundle,
)
from core.dataset.validate import validate_bundle
from core.ingest.captions import ensure_js_runtime
from core.ingest.jobs import ensure_no_active_job, validate_job_options
from core.ingest.pipeline import (
    build_dataset,
    load_bundle_into_store,
    run_ingest_job,
    stage_list_and_upsert,
)
from core.models import Channel, IngestJob
from core.providers.factory import build_chat_provider_if_configured
from core.providers.openai_provider import OpenAIProvider
from core.store.pgvector_store import PgVectorStore


class BundleInvalidError(RuntimeError):
    def __init__(self, path: Path, errors: list[str]):
        self.path = path
        self.errors = errors
        super().__init__(
            f"Bundle at {path} failed validation:\n" + "\n".join(f"  - {e}" for e in errors)
        )


def run_dataset_build(
    channel_input: str,
    *,
    limit: int | None,
    sort: str,
    out: Path | None,
    skip_embeddings: bool,
    force: bool = False,
) -> Path:
    out_dir = out or default_bundle_dir(channel_input)
    if bundle_exists(out_dir):
        if not force:
            return out_dir
        shutil.rmtree(out_dir)

    validate_job_options(limit=limit, sort=sort)
    ensure_js_runtime()

    store = PgVectorStore()
    settings = get_settings()
    credentials = CredentialsProvider(settings)
    provider = None if skip_embeddings else OpenAIProvider(credentials)

    channel, videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    ensure_no_active_job(store, channel.id)
    # status="running": this job is executed right here, in-process — it must never sit
    # `queued` where an always-on `aac worker` could claim it and run it a second time.
    job = store.create_job(
        channel_id=channel.id,
        payload={
            "channel_input": channel_input,
            "limit": limit,
            "sort": sort,
            "skip_embeddings": skip_embeddings,
            "kind": "ingest",
        },
        status="running",
    )

    build_dataset(
        store,
        provider,
        job,
        channel=channel,
        videos=videos,
        out_dir=out_dir,
        skip_embeddings=skip_embeddings,
        # --skip-embeddings is the "no API calls" build; don't sneak in a paid chat call.
        suggestions_provider=(
            None if skip_embeddings else build_chat_provider_if_configured(settings, credentials)
        ),
    )
    return out_dir


def run_dataset_load(path: Path) -> Channel:
    """Validates first: bundles may come from other people, so they're untrusted input.
    Loading is not one big transaction — each video's chunks are replaced atomically, but a
    failure mid-way leaves earlier videos loaded. Re-running `load` is the recovery: it's
    idempotent (upserts + replace_chunks)."""
    errors = validate_bundle(path)
    if errors:
        raise BundleInvalidError(Path(path), errors)

    store = PgVectorStore()
    credentials = CredentialsProvider(get_settings())
    bundle: Bundle = read_bundle(path)
    return load_bundle_into_store(store, credentials, bundle)


def run_ingest_inline(
    channel_input: str, *, limit: int | None, sort: str, out: Path | None = None
) -> IngestJob:
    validate_job_options(limit=limit, sort=sort)
    out_dir = out or default_bundle_dir(channel_input)
    if not bundle_exists(out_dir):
        ensure_js_runtime()

    store = PgVectorStore()
    credentials = CredentialsProvider(get_settings())

    channel, _videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    ensure_no_active_job(store, channel.id)
    # status="running" — see run_dataset_build: never leave an inline job claimable.
    job = store.create_job(
        channel_id=channel.id,
        payload={"channel_input": channel_input, "limit": limit, "sort": sort, "kind": "ingest"},
        status="running",
    )

    return run_ingest_job(store, credentials, job, out_dir=out_dir)


def run_delete_channel(channel_id: UUID) -> list[Path]:
    """Hard-deletes a channel: Postgres content (videos/chunks/chats/messages/ingest_jobs cascade
    via FK; usage_events survives via ON DELETE SET NULL — see core/db/migrations/0001_init.sql)
    plus every local dataset bundle directory whose manifest names this channel (the slug can't
    be reconstructed from the channel row — it came from whatever the user typed at build time),
    since the UI's type-to-confirm delete flow implies a full purge. Returns the dirs removed."""
    store = PgVectorStore()
    channel = store.get_channel(channel_id)
    if channel is None:
        return []

    store.delete_channel(channel_id)

    removed: list[Path] = []
    candidates = find_bundle_dirs_for_channel(channel.yt_channel_id)
    fallback = default_bundle_dir(channel.handle or channel.yt_channel_id)  # dir w/o manifest
    for bundle_dir in {*candidates, fallback}:
        if bundle_dir.exists():
            shutil.rmtree(bundle_dir)
            removed.append(bundle_dir)
    return removed
