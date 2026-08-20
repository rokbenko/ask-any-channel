"""Plain dataclasses mirroring database rows. No ORM — core/store/pgvector_store.py maps
raw SQL rows onto these directly."""

from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass
class Channel:
    id: UUID
    yt_channel_id: str
    handle: str | None
    title: str | None
    thumbnail_url: str | None
    branding: dict
    created_at: datetime


@dataclass
class Video:
    id: UUID
    channel_id: UUID
    yt_video_id: str
    title: str | None
    published_at: datetime | None
    duration_s: int | None
    view_count: int | None
    status: str
    error: str | None
    created_at: datetime
    updated_at: datetime


@dataclass
class Chunk:
    id: UUID
    video_id: UUID
    channel_id: UUID
    idx: int
    text: str
    t_start_s: float
    t_end_s: float
    token_count: int
    embedding: list[float] | None
    created_at: datetime


@dataclass
class IngestJob:
    id: UUID
    channel_id: UUID | None
    payload: dict
    status: str
    progress: dict
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    heartbeat_at: datetime


@dataclass
class Chat:
    id: UUID
    voice_channel_id: UUID | None  # None = Neutral
    created_at: datetime
    source_channel_ids: list[UUID] = field(default_factory=list)  # ordered by chat_sources.position


@dataclass
class Message:
    id: UUID
    chat_id: UUID
    role: str
    content: str
    citations: list = field(default_factory=list)
    created_at: datetime | None = None


@dataclass
class UsageEvent:
    id: UUID
    channel_id: UUID | None  # voice channel, else first source — backward-compat single value
    chat_id: UUID | None
    model: str | None
    tokens_in: int | None
    tokens_out: int | None
    est_cost_usd: float | None
    created_at: datetime
    source_channel_ids: list = field(default_factory=list)  # full scope in effect for this turn
