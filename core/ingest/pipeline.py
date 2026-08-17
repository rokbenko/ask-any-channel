"""Shared ingestion stage functions and job orchestration.

Two Postgres-independent stages (list+upsert metadata, fetch captions) feed into a bundle
build stage that writes ONLY to local files (never to `chunks`/embeddings in Postgres) —
`build_dataset()` is what backs `aac dataset build`. A separate `load_bundle_into_store()`
reads a previously-built bundle and populates Postgres — what backs `aac dataset load`.
`run_ingest_job()` composes the two for the `aac ingest` convenience command and the future
worker daemon, so both entry points keep sharing code.
"""

import time
from datetime import UTC, datetime
from pathlib import Path

from core.config import get_settings
from core.constants import (
    CHUNK_OVERLAP_RATIO,
    CHUNK_TARGET_TOKENS,
    DATASET_SCHEMA_VERSION,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    TOKENIZER_ENCODING,
    TOOL_VERSION,
)
from core.credentials import CredentialsProvider
from core.dataset.bundle import (
    Bundle,
    ChunkRecord,
    VideoRecord,
    bundle_exists,
    read_bundle,
    write_bundle,
)
from core.dataset.manifest import (
    ChannelMeta,
    ChunkingParams,
    EmbeddingMeta,
    Manifest,
    get_contributor,
)
from core.ingest.captions import fetch_captions
from core.ingest.channel_source import list_channel_videos, resolve_channel_input
from core.ingest.chunker import ChunkDraft, chunk_timed_words
from core.ingest.vtt_parser import cues_to_clean_text, dedupe_rolling_cues, parse_vtt
from core.models import Channel, IngestJob, Video
from core.providers.base import LLMProvider
from core.providers.openai_provider import OpenAIProvider
from core.store.base import ChunkInput, VectorStore

VIDEO_FETCH_SLEEP_S = 0.5


def stage_list_and_upsert(
    store: VectorStore, channel_input: str, *, limit: int | None, sort: str
) -> tuple[Channel, list[Video]]:
    channel_url = resolve_channel_input(channel_input)
    listing = list_channel_videos(channel_url, limit=limit, sort=sort)

    channel = store.upsert_channel(
        yt_channel_id=listing.yt_channel_id,
        handle=listing.handle,
        title=listing.title,
        thumbnail_url=listing.thumbnail_url,
    )

    videos = [
        store.upsert_video(
            channel_id=channel.id,
            yt_video_id=v.yt_video_id,
            title=v.title,
            published_at=v.published_at,
            duration_s=v.duration_s,
            view_count=v.view_count,
        )
        for v in listing.videos
    ]

    return channel, videos


def stage_fetch_captions(store: VectorStore, video: Video, *, cache_dir: Path) -> Path | None:
    try:
        vtt_path = fetch_captions(video.yt_video_id, cache_dir=cache_dir)
    except Exception as exc:
        store.set_video_status(video.id, "failed", error=str(exc))
        return None

    if vtt_path is None:
        store.set_video_status(video.id, "no_captions")
        return None

    store.set_video_status(video.id, "fetched")
    return vtt_path


def stage_chunk_to_drafts(vtt_path: Path) -> list[ChunkDraft]:
    """Pure parse -> dedupe -> chunk. No store access — the bundle is the only output."""
    raw_vtt = vtt_path.read_text(encoding="utf-8")
    cues = parse_vtt(raw_vtt)
    deduped = dedupe_rolling_cues(cues)
    words = cues_to_clean_text(deduped)
    return chunk_timed_words(words)


def stage_embed_drafts(provider: LLMProvider, drafts: list[ChunkDraft]) -> list[list[float]]:
    return provider.embed([d.text for d in drafts])


def build_dataset(
    store: VectorStore,
    provider: LLMProvider | None,
    job: IngestJob,
    *,
    channel: Channel,
    videos: list[Video],
    out_dir: Path,
    skip_embeddings: bool,
) -> Path:
    """Builds a dataset bundle at `out_dir`. Only writes channel/video *metadata* and
    `ingest_jobs` progress to Postgres (for `aac status` observability) — chunk text and
    embeddings go exclusively to the bundle files. Whole-bundle idempotent: if a complete
    bundle already exists at `out_dir`, this is a no-op regardless of what Postgres says.
    """
    if bundle_exists(out_dir):
        store.update_job(
            job.id,
            status="done",
            progress={"stage": "already-built", "done": len(videos), "total": len(videos)},
        )
        return out_dir

    store.update_job(job.id, status="running")

    try:
        cache_dir = Path(get_settings().raw_captions_dir)
        total = len(videos)
        done = 0
        store.update_job(job.id, progress={"stage": "building", "done": done, "total": total})

        video_records: list[VideoRecord] = []
        chunk_records: list[ChunkRecord] = []
        embeddings: dict[tuple[str, int], list[float]] = {}

        for video in videos:
            vtt_path = stage_fetch_captions(store, video, cache_dir=cache_dir)
            time.sleep(VIDEO_FETCH_SLEEP_S)

            drafts: list[ChunkDraft] = []
            if vtt_path is not None:
                drafts = stage_chunk_to_drafts(vtt_path)
                store.set_video_status(video.id, "chunked")

                if drafts and not skip_embeddings:
                    assert provider is not None, "provider required unless skip_embeddings"
                    video_embeddings = stage_embed_drafts(provider, drafts)
                    for d, emb in zip(drafts, video_embeddings, strict=True):
                        embeddings[(video.yt_video_id, d.idx)] = emb
                    store.set_video_status(video.id, "embedded")

            video_records.append(
                VideoRecord(
                    yt_video_id=video.yt_video_id,
                    title=video.title,
                    duration_s=video.duration_s,
                    view_count=video.view_count,
                    published_at=video.published_at.isoformat() if video.published_at else None,
                    status=store.get_video_status(video.id),
                )
            )
            chunk_records.extend(
                ChunkRecord(
                    yt_video_id=video.yt_video_id,
                    idx=d.idx,
                    text=d.text,
                    t_start_s=d.t_start_s,
                    t_end_s=d.t_end_s,
                    token_count=d.token_count,
                )
                for d in drafts
            )

            done += 1
            store.update_job(job.id, progress={"stage": "building", "done": done, "total": total})

        manifest = Manifest(
            schema_version=DATASET_SCHEMA_VERSION,
            channel=ChannelMeta(
                yt_channel_id=channel.yt_channel_id,
                handle=channel.handle,
                title=channel.title,
                thumbnail_url=channel.thumbnail_url,
            ),
            snapshot_date=datetime.now(UTC).isoformat(),
            chunking=ChunkingParams(
                target_tokens=CHUNK_TARGET_TOKENS,
                overlap_ratio=CHUNK_OVERLAP_RATIO,
                encoding=TOKENIZER_ENCODING,
            ),
            embedding=(
                None
                if skip_embeddings
                else EmbeddingMeta(model=EMBEDDING_MODEL, dims=EMBEDDING_DIM)
            ),
            tool_version=TOOL_VERSION,
            contributor=get_contributor(),
            video_count=len(video_records),
            chunk_count=len(chunk_records),
            limit=job.payload.get("limit"),
            sort=job.payload.get("sort", "recent"),
        )

        write_bundle(out_dir, manifest, video_records, chunk_records, embeddings or None)
        store.update_job(job.id, status="done")
    except Exception as exc:
        store.update_job(job.id, status="failed", error=str(exc))
        raise

    return out_dir


def load_bundle_into_store(
    store: VectorStore, credentials: CredentialsProvider, bundle: Bundle
) -> Channel:
    """Loads a bundle's videos/chunks/embeddings into Postgres. Uses the bundle's own
    embeddings when they match the currently configured embedding model — zero API calls,
    zero credentials required. An LLMProvider is only ever constructed (and a key only ever
    required) when embeddings are missing or were built with a different model/dims.
    """
    manifest = bundle.manifest

    channel = store.upsert_channel(
        yt_channel_id=manifest.channel.yt_channel_id,
        handle=manifest.channel.handle,
        title=manifest.channel.title,
        thumbnail_url=manifest.channel.thumbnail_url,
    )

    embeddings_usable = (
        bundle.embeddings is not None
        and manifest.embedding is not None
        and manifest.embedding.model == EMBEDDING_MODEL
        and manifest.embedding.dims == EMBEDDING_DIM
    )

    provider: LLMProvider | None = None
    if not embeddings_usable:
        provider = OpenAIProvider(credentials)

    chunks_by_video: dict[str, list[ChunkRecord]] = {}
    for c in bundle.chunks:
        chunks_by_video.setdefault(c.yt_video_id, []).append(c)

    for v in bundle.videos:
        video = store.upsert_video(
            channel_id=channel.id,
            yt_video_id=v.yt_video_id,
            title=v.title,
            published_at=datetime.fromisoformat(v.published_at) if v.published_at else None,
            duration_s=v.duration_s,
            view_count=v.view_count,
        )

        video_chunks = sorted(chunks_by_video.get(v.yt_video_id, []), key=lambda c: c.idx)
        if not video_chunks:
            store.set_video_status(video.id, v.status)
            continue

        chunk_ids = store.replace_chunks(
            video.id,
            channel.id,
            [
                ChunkInput(
                    idx=c.idx,
                    text=c.text,
                    t_start_s=c.t_start_s,
                    t_end_s=c.t_end_s,
                    token_count=c.token_count,
                )
                for c in video_chunks
            ],
        )

        if embeddings_usable:
            video_embeddings = [bundle.embeddings[(v.yt_video_id, c.idx)] for c in video_chunks]
        else:
            video_embeddings = provider.embed([c.text for c in video_chunks])

        store.set_chunk_embeddings(chunk_ids, video_embeddings)
        store.set_video_status(video.id, "embedded")

    return channel


def run_ingest_job(
    store: VectorStore, credentials: CredentialsProvider, job: IngestJob, *, out_dir: Path
) -> IngestJob:
    """build_dataset + load_bundle_into_store, composed — the shared runner for `aac
    ingest` (build+load convenience) and the future polling worker daemon."""
    channel_input = job.payload["channel_input"]
    limit = job.payload.get("limit")
    sort = job.payload.get("sort", "recent")

    channel, videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    provider = OpenAIProvider(credentials)
    build_dataset(
        store,
        provider,
        job,
        channel=channel,
        videos=videos,
        out_dir=out_dir,
        skip_embeddings=False,
    )

    bundle = read_bundle(out_dir)
    load_bundle_into_store(store, credentials, bundle)

    return store.get_job(job.id)
