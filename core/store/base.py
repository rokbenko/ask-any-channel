"""The VectorStore seam. All persistence/retrieval goes through this interface so the
storage backend (currently pgvector) can be swapped without touching ingestion, search,
or CLI code."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

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


@dataclass
class ChatSummary:
    id: UUID
    channel_id: UUID
    title: str | None  # derived from the first user message, truncated ~60 chars
    created_at: datetime


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
        self, *, channel_id: UUID, query_embedding: list[float], top_k: int
    ) -> list[SearchResult]: ...

    def create_job(self, *, channel_id: UUID | None, payload: dict) -> IngestJob: ...

    def get_job(self, job_id: UUID) -> IngestJob: ...

    def claim_next_queued_job(self) -> IngestJob | None: ...

    def update_job(
        self,
        job_id: UUID,
        *,
        status: str | None = None,
        progress: dict | None = None,
        error: str | None = None,
    ) -> None: ...

    def get_channel_by_handle_or_id(self, ref: str) -> Channel | None: ...

    def status_summary(self) -> StatusSummary: ...

    def get_channel(self, channel_id: UUID) -> Channel | None: ...

    def list_channels(self) -> list[ChannelSummary]: ...

    def create_chat(self, *, channel_id: UUID) -> Chat: ...

    def get_chat(self, chat_id: UUID) -> Chat | None: ...

    def list_chats(self, *, channel_id: UUID, limit: int = 50) -> list[ChatSummary]: ...

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
    ) -> UsageEvent: ...
