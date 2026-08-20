"""Pydantic request/response shapes for the HTTP API — the one place pydantic is used in this
codebase; core stays dataclass-only (see DECISIONS.md)."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel


class PersonaOut(BaseModel):
    enabled: bool
    family_friendly: bool
    has_profile: bool
    disclosure: str | None


class ChannelOut(BaseModel):
    id: UUID
    handle: str | None
    yt_channel_id: str
    title: str | None
    thumbnail_url: str | None
    video_count: int
    embedded_video_count: int
    chunk_count: int
    suggested_questions: list[str]
    persona: PersonaOut


class CreateChatRequest(BaseModel):
    sources: list[str]
    voice: str | None = None


class ChatOut(BaseModel):
    id: UUID
    sources: list[UUID]
    voice: UUID | None
    disclosure: str | None


class MessageOut(BaseModel):
    id: UUID
    role: str
    content: str
    citations: list[dict]
    created_at: datetime | None


class ChatMessageRequest(BaseModel):
    question: str


class AskRequest(BaseModel):
    sources: list[str]
    voice: str | None = None
    question: str
