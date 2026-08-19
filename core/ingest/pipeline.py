"""Shared ingestion stage functions and job orchestration.

Two Postgres-independent stages (list+upsert metadata, fetch captions) feed into a bundle
build stage that writes ONLY to local files (never to `chunks`/embeddings in Postgres) —
`build_dataset()` is what backs `aac dataset build`. A separate `load_bundle_into_store()`
reads a previously-built bundle and populates Postgres — what backs `aac dataset load`.
`run_ingest_job()` composes the two for the `aac ingest` convenience command and the polling
worker daemon (`aac worker`); `run_update_job()` is the incremental variant. All entry points
share this code.

Dependency injection: everything here receives its store/providers from the caller
(`run_ingest_job`/`run_update_job` are the composition roots and only construct providers via
core.providers.factory) — no module-level settings reads beyond the caption cache dir.
"""

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from core.chat.suggestions import (
    BRANDING_KEY as SUGGESTIONS_KEY,
)
from core.chat.suggestions import (
    generate_suggested_questions,
    sanitize_questions,
)
from core.config import get_settings
from core.constants import (
    CHUNK_OVERLAP_RATIO,
    CHUNK_TARGET_TOKENS,
    DATASET_SCHEMA_VERSION,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    SUGGESTED_QUESTIONS_CHUNKS_PER_VIDEO,
    SUGGESTED_QUESTIONS_MAX_VIDEOS,
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
from core.persona import ensure_style_profile
from core.providers.base import LLMProvider
from core.providers.factory import build_chat_provider_if_configured
from core.providers.openai_provider import OpenAIProvider
from core.store.base import ChunkInput, VectorStore

logger = logging.getLogger(__name__)

VIDEO_FETCH_SLEEP_S = 0.5

# (provider, model) for the one-shot suggested-questions call, or None to skip it. A tuple
# rather than two params so "configured or not" is a single value that callers thread through.
SuggestionsProvider = tuple[LLMProvider, str] | None


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


def filter_new_videos(processed_ids: set[str], videos: list[Video]) -> list[Video]:
    """Pure diff for incremental updates: keeps the listed videos that haven't reached a
    terminal content state yet (see VectorStore.list_processed_video_ids)."""
    return [v for v in videos if v.yt_video_id not in processed_ids]


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


def _try_generate_suggested_questions(
    channel: Channel,
    video_records: list[VideoRecord],
    chunk_records: list[ChunkRecord],
    suggestions_provider: SuggestionsProvider,
) -> list[str]:
    """Best-effort, non-critical: sampled from the in-memory bundle just built (chunks aren't
    in Postgres yet at this point in build_dataset, so this can't reuse
    store.list_sample_chunk_texts, which is the Postgres-based lazy-generation path). Any
    failure (a transient API error, an unparseable reply) must never fail an otherwise
    fully-successful ingest — that's why this catches broadly instead of only ProviderError."""
    if suggestions_provider is None:
        return []
    chat_provider, model = suggestions_provider

    top_ids = {
        v.yt_video_id
        for v in sorted(video_records, key=lambda v: v.view_count or 0, reverse=True)[
            :SUGGESTED_QUESTIONS_MAX_VIDEOS
        ]
    }
    counts: dict[str, int] = {}
    sample_texts = []
    for c in chunk_records:
        if c.yt_video_id not in top_ids:
            continue
        if counts.get(c.yt_video_id, 0) >= SUGGESTED_QUESTIONS_CHUNKS_PER_VIDEO:
            continue
        counts[c.yt_video_id] = counts.get(c.yt_video_id, 0) + 1
        sample_texts.append(c.text)

    if not sample_texts:
        return []

    try:
        return generate_suggested_questions(chat_provider, model, channel.title, sample_texts)
    except Exception:
        logger.warning(
            "suggested-question generation skipped for channel %s", channel.id, exc_info=True
        )
        return []


def _try_build_style_profile(
    store: VectorStore, credentials: CredentialsProvider, channel: Channel
) -> None:
    """Best-effort, non-critical, run AFTER load_bundle_into_store — unlike suggested
    questions, a style profile is sampled from Postgres (core.persona.build_style_profile via
    VectorStore.list_style_sample_chunk_texts), so it needs the channel's chunks already
    loaded. Same broad-except contract as _try_generate_suggested_questions: a transient chat-
    API hiccup while building a voice profile must never fail an otherwise fully-successful
    ingest."""
    try:
        ensure_style_profile(store, credentials, get_settings(), channel)
    except Exception:
        logger.warning("style-profile generation skipped for channel %s", channel.id, exc_info=True)


def build_dataset(
    store: VectorStore,
    provider: LLMProvider | None,
    job: IngestJob,
    *,
    channel: Channel,
    videos: list[Video],
    out_dir: Path,
    skip_embeddings: bool,
    suggestions_provider: SuggestionsProvider = None,
) -> Path:
    """Builds a dataset bundle at `out_dir`. Only writes channel/video *metadata* and
    `ingest_jobs` progress to Postgres (for `aac status` observability) — chunk text and
    embeddings go exclusively to the bundle files. Whole-bundle idempotent: if a complete
    bundle already exists at `out_dir`, this is a no-op regardless of what Postgres says.

    `suggestions_provider=None` skips the suggested-questions chat call: for `--skip-embeddings`
    (an explicitly no-API-cost build), when no chat key is configured, and for run_update_job —
    regenerating from an incremental delta of typically low-view new videos would overwrite
    better questions already generated from the full catalog.
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

        suggested_questions = _try_generate_suggested_questions(
            channel, video_records, chunk_records, suggestions_provider
        )

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
            suggested_questions=suggested_questions,
        )

        write_bundle(out_dir, manifest, video_records, chunk_records, embeddings or None)
        if suggested_questions:
            store.set_channel_branding(channel.id, {SUGGESTIONS_KEY: suggested_questions})
        store.update_job(job.id, status="done")
    except Exception as exc:
        store.update_job(job.id, status="failed", error=str(exc))
        raise

    return out_dir


def load_bundle_into_store(
    store: VectorStore,
    credentials: CredentialsProvider,
    bundle: Bundle,
    *,
    heartbeat: Callable[[], None] | None = None,
) -> Channel:
    """Loads a bundle's videos/chunks/embeddings into Postgres. Uses the bundle's own
    embeddings when they match the currently configured embedding model — zero API calls,
    zero credentials required. An LLMProvider is only ever constructed (and a key only ever
    required) when embeddings are missing or were built with a different model/dims.

    `heartbeat` (called once per video) lets a job runner keep ingest_jobs.heartbeat_at fresh
    through a long load, so a second worker's stale-job reclaim can't mistake it for dead.
    """
    manifest = bundle.manifest

    channel = store.upsert_channel(
        yt_channel_id=manifest.channel.yt_channel_id,
        handle=manifest.channel.handle,
        title=manifest.channel.title,
        thumbnail_url=manifest.channel.thumbnail_url,
    )
    questions = sanitize_questions(manifest.suggested_questions)  # untrusted bundle → re-clean
    if questions:
        # Lets a bundle built with a chat key give the loader working suggested questions with
        # zero API calls — they travel in manifest.json as short derived text, not transcript
        # content, the same way channel title/thumbnail metadata already does.
        store.set_channel_branding(channel.id, {SUGGESTIONS_KEY: questions})

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
        if heartbeat is not None:
            heartbeat()
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
    ingest` (build+load convenience) and the polling worker daemon."""
    channel_input = job.payload["channel_input"]
    limit = job.payload.get("limit")
    sort = job.payload.get("sort", "recent")

    channel, videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    if job.channel_id != channel.id:
        # Jobs enqueued for a brand-new channel (core.ingest.jobs.enqueue_ingest_job) are
        # created with channel_id=None, since resolution hasn't happened yet at enqueue time —
        # backfill it now that the channel row exists, so status/UI queries can find this job.
        # Raises ActiveJobExistsError if that channel already has an active job (unique index).
        store.update_job(job.id, channel_id=channel.id)
    provider = OpenAIProvider(credentials)
    build_dataset(
        store,
        provider,
        job,
        channel=channel,
        videos=videos,
        out_dir=out_dir,
        skip_embeddings=False,
        suggestions_provider=build_chat_provider_if_configured(get_settings(), credentials),
    )

    bundle = read_bundle(out_dir)
    load_bundle_into_store(store, credentials, bundle, heartbeat=lambda: store.update_job(job.id))
    _try_build_style_profile(store, credentials, channel)

    return store.get_job(job.id)


def run_update_job(
    store: VectorStore, credentials: CredentialsProvider, job: IngestJob, *, out_dir: Path
) -> IngestJob:
    """Incremental update: lists the channel's current videos and processes only the ones not
    already *processed* (see VectorStore.list_processed_video_ids — a plain "row exists" diff
    would make every retry/reclaim of a failed update a no-op, because stage_list_and_upsert
    creates the metadata rows before anything is processed). `job.channel_id` is always set
    at creation for "update" jobs (enqueue_update_job requires an existing channel)."""
    channel_input = job.payload["channel_input"]
    limit = job.payload.get("limit")
    sort = job.payload.get("sort", "recent")

    processed_ids = store.list_processed_video_ids(job.channel_id)
    channel, videos = stage_list_and_upsert(store, channel_input, limit=limit, sort=sort)
    new_videos = filter_new_videos(processed_ids, videos)

    if not new_videos:
        store.update_job(
            job.id,
            status="done",
            progress={"stage": "no-new-videos", "done": 0, "total": 0},
        )
        return store.get_job(job.id)

    provider = OpenAIProvider(credentials)
    build_dataset(
        store,
        provider,
        job,
        channel=channel,
        videos=new_videos,
        out_dir=out_dir,
        skip_embeddings=False,
        suggestions_provider=None,
    )

    bundle = read_bundle(out_dir)
    load_bundle_into_store(store, credentials, bundle, heartbeat=lambda: store.update_job(job.id))

    return store.get_job(job.id)
