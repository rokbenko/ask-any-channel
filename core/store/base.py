"""The VectorStore seam. All persistence/retrieval goes through this interface so the
storage backend (currently pgvector) can be swapped without touching ingestion, search,
or CLI code."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from core.constants import DEFAULT_RETRIEVAL_MODE
from core.models import Channel, Chat, IngestJob, Message, UsageEvent, Video


@dataclass
class ChunkInput:
    idx: int
    text: str
    t_start_s: float
    t_end_s: float
    token_count: int


@dataclass
class SearchResult:
    chunk_id: UUID
    video_id: UUID
    yt_video_id: str
    video_title: str | None
    text: str
    t_start_s: float
    t_end_s: float
    score: float
    channel_id: UUID
    channel_title: str | None
    channel_handle: str | None


@dataclass
class VideoStatusCount:
    status: str
    count: int


@dataclass
class ChannelStatusSummary:
    channel: Channel
    video_status_counts: list[VideoStatusCount]


@dataclass
class StatusSummary:
    channels: list[ChannelStatusSummary]
    recent_jobs: list[IngestJob]


@dataclass
class ChannelSummary:
    channel: Channel
    video_count: int
    embedded_video_count: int  # status = 'embedded' — actually searchable/answerable
    chunk_count: int
    last_updated_at: datetime | None  # MAX(videos.updated_at); None if the channel has no videos


@dataclass
class ChatSummary:
    id: UUID
    title: str | None  # derived from the first user message, truncated ~60 chars
    created_at: datetime
    source_channel_ids: list[UUID]
    voice_channel_id: UUID | None  # None = Neutral


class ActiveJobExistsError(RuntimeError):
    """A channel (or, before resolution, a raw channel input) already has a queued/running job.
    Enforced by the store (partial unique indexes in migration 0005) so it holds under
    concurrent enqueues, not just by the pre-check in core.ingest.jobs."""


class VectorStore(Protocol):
    def upsert_channel(
        self,
        *,
        yt_channel_id: str,
        handle: str | None,
        title: str | None,
        thumbnail_url: str | None,
    ) -> Channel: ...

    def upsert_video(
        self,
        *,
        channel_id: UUID,
        yt_video_id: str,
        title: str | None,
        published_at: datetime | None,
        duration_s: int | None,
        view_count: int | None,
    ) -> Video: ...

    def get_video_status(self, video_id: UUID) -> str: ...

    def set_video_status(self, video_id: UUID, status: str, error: str | None = None) -> None: ...

    def replace_chunks(
        self, video_id: UUID, channel_id: UUID, chunks: list[ChunkInput]
    ) -> list[UUID]: ...

    def set_chunk_embeddings(
        self, chunk_ids: list[UUID], embeddings: list[list[float]]
    ) -> None: ...

    def search(
        self,
        *,
        channel_ids: list[UUID],
        query_embedding: list[float],
        top_k: int,
        query_text: str | None = None,
        mode: str = DEFAULT_RETRIEVAL_MODE,
    ) -> list[SearchResult]:
        """Retrieves across ALL of channel_ids at once (a single-element list is the original
        one-channel behaviour). mode="dense" (or query_text=None) is pure pgvector cosine
        ranking — today's semantics. mode="hybrid" additionally ranks by full-text search
        (websearch_to_tsquery/ts_rank_cd over chunks.tsv) and fuses the two rankings with
        Reciprocal Rank Fusion (core.search.hybrid.rrf_fuse) before taking the top_k."""
        ...

    def sample_embedding_dim(self) -> int | None:
        """Dimension of one arbitrary stored chunk embedding, or None if no chunks exist yet.
        Used by core.doctor to catch a live EMBEDDING_DIM/stored-data mismatch — nothing else
        checks this against actual Postgres content (core.chat.answer only compares a live
        embed call's output length, not what's already stored)."""
        ...

    def create_job(
        self, *, channel_id: UUID | None, payload: dict, status: str = "queued"
    ) -> IngestJob:
        """`status="running"` is for callers that run the job inline in-process right away
        (the CLI) — the row must never sit `queued` where an always-on worker could claim it
        too. Raises ActiveJobExistsError if the channel/input already has an active job."""
        ...

    def get_job(self, job_id: UUID) -> IngestJob: ...

    def claim_next_queued_job(self) -> IngestJob | None: ...

    def update_job(
        self,
        job_id: UUID,
        *,
        status: str | None = None,
        progress: dict | None = None,
        error: str | None = None,
        channel_id: UUID | None = None,
    ) -> None:
        """Every call bumps heartbeat_at. `progress` is MERGED into the stored jsonb (keys not
        in the patch survive — e.g. `attempts` set by reclaim_stale_jobs), never replaced.
        Raises ActiveJobExistsError if a channel_id backfill collides with an active job."""
        ...

    def get_latest_job_for_channel(self, channel_id: UUID) -> IngestJob | None: ...

    def list_latest_jobs_by_channel(self) -> dict[UUID, IngestJob]: ...

    def list_unattached_jobs(self) -> list[IngestJob]:
        """Jobs whose channel isn't resolved yet (channel_id IS NULL) and that still matter to
        a user: queued, running, or failed — the "pending adds" the Channels page shows."""
        ...

    def count_stale_queued_jobs(self, older_than_s: float) -> int: ...

    def retry_job(self, job_id: UUID) -> IngestJob | None: ...

    def cancel_job(self, job_id: UUID) -> IngestJob | None: ...

    def reclaim_stale_jobs(self, stale_after_s: float, *, max_attempts: int) -> list[UUID]:
        """Requeues running jobs whose heartbeat is stale, incrementing progress.attempts; a job
        that has already been reclaimed max_attempts-1 times is marked failed instead (a poison
        job must not re-embed a channel forever). Returns the requeued ids."""
        ...

    def get_channel_by_handle_or_id(self, ref: str) -> Channel | None: ...

    def status_summary(self) -> StatusSummary: ...

    def get_channel(self, channel_id: UUID) -> Channel | None: ...

    def get_channels(self, channel_ids: list[UUID]) -> list[Channel]:
        """Channels found for the given ids, in the SAME order as channel_ids — missing ids are
        simply absent from the result (the caller decides whether that's an error)."""
        ...

    def list_channels(self) -> list[ChannelSummary]: ...

    def list_processed_video_ids(self, channel_id: UUID) -> set[str]:
        """yt_video_ids that reached a terminal content state (embedded / no_captions). Used as
        the incremental-update diff signal — NOT "a videos row exists": stage_list_and_upsert
        creates metadata rows before any processing, so a retried/reclaimed update job would
        otherwise see every video as known and process nothing."""
        ...

    def set_channel_branding(self, channel_id: UUID, patch: dict) -> Channel: ...

    def list_sample_chunk_texts(
        self, channel_id: UUID, *, max_videos: int = 5, max_chunks_per_video: int = 3
    ) -> list[str]: ...

    def list_style_sample_chunk_texts(
        self,
        channel_id: UUID,
        *,
        top_videos: int = 10,
        chunks_per_video: int = 5,
        random_chunks: int = 30,
    ) -> list[str]:
        """A wider sample than list_sample_chunk_texts, for core.persona's style-profile
        generation: top-viewed embedded videos (where a creator's signature material usually
        lives) plus a random spread across the whole channel, deduped."""
        ...

    def count_channel_chunks(self, channel_id: UUID) -> int: ...

    def delete_channel(self, channel_id: UUID) -> None:
        """Cascades videos/chunks/jobs (FK). chat_sources rows for this channel cascade too;
        chats.voice_channel_id is nulled (falls back to Neutral) rather than deleting the chat.
        A chat left with zero sources after that is deleted explicitly (its messages cascade;
        usage_events survive with their FKs nulled, same as a direct channel delete always
        did)."""
        ...

    def create_chat(
        self, *, source_channel_ids: list[UUID], voice_channel_id: UUID | None
    ) -> Chat: ...

    def get_chat(self, chat_id: UUID) -> Chat | None: ...

    def set_chat_scope(
        self, chat_id: UUID, *, source_channel_ids: list[UUID], voice_channel_id: UUID | None
    ) -> Chat:
        """Replaces a chat's knowledge scope and voice — sources/voice are editable on an
        already-open chat, not fixed at creation."""
        ...

    def list_chats(self, *, channel_id: UUID | None = None, limit: int = 50) -> list[ChatSummary]:
        """channel_id=None lists every chat (the sidebar's case — chats aren't scoped to one
        channel anymore); a channel_id filters to chats that have it among their sources."""
        ...

    def list_messages(self, chat_id: UUID) -> list[Message]: ...

    def add_message(
        self,
        *,
        chat_id: UUID,
        role: str,
        content: str,
        citations: list[dict] | None = None,
    ) -> Message: ...

    def record_usage_event(
        self,
        *,
        channel_id: UUID | None,
        chat_id: UUID | None,
        model: str | None,
        tokens_in: int | None,
        tokens_out: int | None,
        est_cost_usd: float | None,
        source_channel_ids: list[UUID] | None = None,
    ) -> UsageEvent: ...
