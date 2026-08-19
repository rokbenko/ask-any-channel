"""Retrieval-grounded chat orchestration. answer() embeds the question, retrieves top-k
channel-scoped chunks, builds a citing prompt, streams the completion, and persists both
turns plus a usage event. Store/provider construction is the caller's job (the Streamlit
page, mirroring core/ingest/pipeline.py's dependency-injection convention) — this module
only ever receives them."""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID

from core.chat.citations import Citation, parse_citations
from core.chat.errors import ChatNotFoundError, EmbeddingModelMismatchError, QuestionTooLongError
from core.chat.pricing import estimate_cost_usd
from core.chat.prompt import build_messages
from core.constants import (
    DEFAULT_RETRIEVAL_MODE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    MAX_QUESTION_CHARS,
)
from core.providers.base import LLMProvider
from core.search.search import ChannelNotFoundError
from core.store.base import VectorStore

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 8
DEFAULT_HISTORY_WINDOW = 6


@dataclass
class AnswerUsage:
    model: str
    tokens_in: int | None
    tokens_out: int | None
    est_cost_usd: float | None


@dataclass
class AnswerResult:
    """text_stream is a plain Iterator[str] — pass directly to st.write_stream(). citations,
    usage, and assistant_message_id are populated as a side effect of exhausting text_stream
    (persistence happens inline in the generator) — read them only AFTER the stream is fully
    consumed."""

    text_stream: Iterator[str]
    citations: list[Citation] = field(default_factory=list)
    usage: AnswerUsage | None = None
    assistant_message_id: UUID | None = None


def answer(
    store: VectorStore,
    embedding_provider: LLMProvider,
    chat_provider: LLMProvider,
    *,
    channel_id: UUID,
    chat_id: UUID,
    user_text: str,
    chat_model: str,
    top_k: int = DEFAULT_TOP_K,
    history_window: int = DEFAULT_HISTORY_WINDOW,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
) -> AnswerResult:
    if len(user_text) > MAX_QUESTION_CHARS:
        raise QuestionTooLongError(
            f"Question is {len(user_text)} characters; the limit is {MAX_QUESTION_CHARS}."
        )

    channel = store.get_channel(channel_id)
    if channel is None:
        raise ChannelNotFoundError(f"No channel found for id {channel_id}")

    # Tenant boundary: a chat_id from the caller (URL param, session state, future API) is
    # never trusted to belong to channel_id — verify before reading or writing its messages.
    chat = store.get_chat(chat_id)
    if chat is None or chat.channel_id != channel_id:
        raise ChatNotFoundError(f"No chat {chat_id} in channel {channel_id}")

    query_embedding = embedding_provider.embed([user_text])[0]
    if len(query_embedding) != EMBEDDING_DIM:
        raise EmbeddingModelMismatchError(
            f"The configured embedding model produced a {len(query_embedding)}-dimension "
            f"vector, but this channel's chunks are stored as {EMBEDDING_DIM}-dimension "
            f"vectors (EMBEDDING_MODEL={EMBEDDING_MODEL!r}). Re-run `aac dataset load` for "
            "this channel with the configured embedding model, or fix EMBEDDING_MODEL."
        )

    context = store.search(
        channel_ids=[channel_id],
        query_embedding=query_embedding,
        top_k=top_k,
        query_text=user_text,
        mode=retrieval_mode,
    )

    full_history = store.list_messages(chat_id)  # chronological ascending
    windowed_history = full_history[-history_window:] if history_window else []
    messages = build_messages(
        channel_title=channel.title,
        history=windowed_history,
        context=context,
        user_text=user_text,
    )

    # Persisted synchronously — the user's question is recorded even if the caller never
    # consumes text_stream, or the stream fails partway through.
    store.add_message(chat_id=chat_id, role="user", content=user_text)

    def _stream() -> Iterator[str]:
        started = time.perf_counter()
        parts: list[str] = []
        tokens_in: int | None = None
        tokens_out: int | None = None
        for chunk in chat_provider.stream_chat(messages, model=chat_model):
            if chunk.text_delta:
                parts.append(chunk.text_delta)
                yield chunk.text_delta
            if chunk.usage is not None:
                tokens_in, tokens_out = chunk.usage.tokens_in, chunk.usage.tokens_out

        full_text = "".join(parts)
        citations = parse_citations(full_text, context)
        est_cost = estimate_cost_usd(chat_model, tokens_in, tokens_out)

        citations_payload = [
            {
                "n": c.n,
                "video_id": str(c.video_id),
                "yt_video_id": c.yt_video_id,
                "title": c.title,
                "url": c.url,
                "t_start_s": c.t_start_s,
                "quote": c.quote,
            }
            for c in citations
        ]
        message = store.add_message(
            chat_id=chat_id, role="assistant", content=full_text, citations=citations_payload
        )
        store.record_usage_event(
            channel_id=channel_id,
            chat_id=chat_id,
            model=chat_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=est_cost,
        )

        result.citations = citations
        result.usage = AnswerUsage(
            model=chat_model, tokens_in=tokens_in, tokens_out=tokens_out, est_cost_usd=est_cost
        )
        result.assistant_message_id = message.id
        # The one line a self-hoster needs to triage "it was slow / it cost a lot / it cited
        # nothing" from the server log, without any per-request tracing infrastructure.
        logger.info(
            "chat turn ok chat_id=%s channel_id=%s model=%s tokens_in=%s tokens_out=%s "
            "citations=%d context_chunks=%d latency_ms=%d",
            chat_id,
            channel_id,
            chat_model,
            tokens_in,
            tokens_out,
            len(citations),
            len(context),
            int((time.perf_counter() - started) * 1000),
        )

    # The generator body doesn't run until first next(), so `result` is bound by the time the
    # closure reads it — no placeholder-then-reassign needed.
    result = AnswerResult(text_stream=_stream())
    return result
