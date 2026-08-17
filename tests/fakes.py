"""Minimal in-memory VectorStore/LLMProvider stand-ins for chat-orchestration tests. No DB,
no network — duck-typed against the subset of each Protocol core.chat.answer actually calls."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from core.models import Channel, Chat, Message, UsageEvent
from core.providers.base import ChatChunk, ChatUsage


class FakeVectorStore:
    """`channel` is the one channel this store knows; every chat_id it's asked about is treated
    as belonging to that channel unless `chat_channel_id` says otherwise (for the tenant-check
    test). `messages` and `usage_events` are public so tests can assert on persistence."""

    def __init__(
        self,
        *,
        channel: Channel | None = None,
        search_results=None,
        history=None,
        chat_channel_id: UUID | None = None,
    ):
        self._channel = channel
        self._search_results = search_results or []
        self._chat_channel_id = chat_channel_id
        self.messages: list[Message] = list(history or [])
        self.usage_events: list[UsageEvent] = []

    def get_channel(self, channel_id):
        if self._channel is not None and self._channel.id == channel_id:
            return self._channel
        return None

    def get_chat(self, chat_id):
        if self._channel is None:
            return None
        channel_id = self._chat_channel_id or self._channel.id
        return Chat(id=chat_id, channel_id=channel_id, created_at=datetime.now(UTC))

    def search(self, *, channel_id, query_embedding, top_k):
        return self._search_results[:top_k]

    def list_messages(self, chat_id):
        return [m for m in self.messages if m.chat_id == chat_id]

    def add_message(self, *, chat_id, role, content, citations=None):
        message = Message(
            id=uuid4(), chat_id=chat_id, role=role, content=content, citations=citations or []
        )
        self.messages.append(message)
        return message

    def record_usage_event(
        self, *, channel_id, chat_id, model, tokens_in, tokens_out, est_cost_usd
    ):
        event = UsageEvent(
            id=uuid4(),
            channel_id=channel_id,
            chat_id=chat_id,
            model=model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=est_cost_usd,
            created_at=datetime.now(UTC),
        )
        self.usage_events.append(event)
        return event


class FakeLLMProvider:
    """embed() returns a fixed-length vector; stream_chat() replays a scripted list of
    ChatChunk events (or raises, for mid-stream-failure tests) and records the `messages` it
    was called with, for assertions."""

    def __init__(self, *, embedding_dim, stream_chunks=None, raise_after=None):
        self._embedding_dim = embedding_dim
        self._stream_chunks = stream_chunks or []
        self._raise_after = raise_after
        self.last_messages = None

    def embed(self, texts, *, model=None):
        return [[0.0] * self._embedding_dim for _ in texts]

    def chat(self, messages, *, model=None):
        raise NotImplementedError

    def stream_chat(self, messages, *, model=None):
        self.last_messages = messages
        for i, chunk in enumerate(self._stream_chunks):
            yield chunk
            if self._raise_after is not None and i == self._raise_after:
                raise RuntimeError("simulated provider failure mid-stream")


def make_chat_chunks(text_parts, *, tokens_in=10, tokens_out=20):
    chunks = [ChatChunk(text_delta=part) for part in text_parts]
    chunks.append(ChatChunk(usage=ChatUsage(tokens_in=tokens_in, tokens_out=tokens_out)))
    return chunks
