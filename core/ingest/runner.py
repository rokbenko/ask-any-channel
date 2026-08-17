"""CLI-facing entry points: create job rows and run the shared pipeline synchronously
in-process. Share core.ingest.pipeline functions with the polling worker daemon — these
files are thin plumbing only."""

import shutil
from pathlib import Path

from core.config import get_settings
from core.credentials import CredentialsProvider
from core.dataset.bundle import Bundle, bundle_exists, default_bundle_dir, read_bundle
from core.dataset.validate import validate_bundle
from core.ingest.captions import ensure_js_runtime
from core.ingest.pipeline import (
    build_dataset,
    load_bundle_into_store,
    run_ingest_job,
    stage_list_and_upsert,
)
from core.models import Channel, IngestJob
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

    ensure_js_runtime()

    store = PgVectorStore()
    provider = None if skip_embeddings else OpenAIProvider(CredentialsProvider(get_settings()))

    channel, videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    job = store.create_job(
        channel_id=channel.id,
        payload={
            "channel_input": channel_input,
            "limit": limit,
            "sort": sort,
            "skip_embeddings": skip_embeddings,
        },
    )

    build_dataset(
        store,
        provider,
        job,
        channel=channel,
        videos=videos,
        out_dir=out_dir,
        skip_embeddings=skip_embeddings,
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
    out_dir = out or default_bundle_dir(channel_input)
    if not bundle_exists(out_dir):
        ensure_js_runtime()

    store = PgVectorStore()
    credentials = CredentialsProvider(get_settings())

    channel, _videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    job = store.create_job(
        channel_id=channel.id,
        payload={"channel_input": channel_input, "limit": limit, "sort": sort},
    )

    return run_ingest_job(store, credentials, job, out_dir=out_dir)
