"""Retrieval-grounded, multi-channel chat orchestration.

answer() runs a persisted chat turn: retrieves top-k chunks PER selected source, builds a
grouped/voiced prompt, streams the completion, and persists both turns plus a usage event.
ask() is the stateless one-shot variant (Part D's /ask endpoint) — identical retrieval/prompt/
streaming, but no chat row and no messages are written, only a usage event (chat_id=None).
Both share _prepare_turn() for everything up to "the provider-ready message list".

Store/provider construction is the caller's job (DI, mirroring core/ingest/pipeline.py) — this
module only ever receives them."""

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from uuid import UUID

from core.chat.citations import Citation, citation_to_payload, parse_citations
from core.chat.errors import ChatNotFoundError, EmbeddingModelMismatchError, QuestionTooLongError
from core.chat.pricing import estimate_cost_usd
from core.chat.prompt import ContextGroup, build_messages, flatten_context
from core.chat.scope import ChatScope, coerce_voice
from core.constants import (
    DEFAULT_RETRIEVAL_MODE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    MAX_QUESTION_CHARS,
    MIN_BLOCKS_PER_SOURCE,
    PROBE_MIN_SCORE,
    PROBE_TOP_K,
)
from core.models import Channel, Message
from core.persona import disclosure_string, get_persona, render_persona_section
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
    usage, assistant_message_id, and suggested_source_channels are populated as a side effect
    of exhausting text_stream (persistence happens inline in the generator) — read them only
    AFTER the stream is fully consumed. scope/voice_channel/disclosure are known up front."""

    text_stream: Iterator[str]
    scope: ChatScope
    voice_channel: Channel | None
    disclosure: str | None
    citations: list[Citation] = field(default_factory=list)
    usage: AnswerUsage | None = None
    assistant_message_id: UUID | None = None
    suggested_source_channels: list[Channel] = field(default_factory=list)


def _display_name(channel: Channel) -> str:
    return channel.title or channel.handle or channel.yt_channel_id


def _resolve_sources(store: VectorStore, source_channel_ids: list[UUID]) -> list[Channel]:
    channels = store.get_channels(list(source_channel_ids))
    found_ids = {c.id for c in channels}
    missing = [cid for cid in source_channel_ids if cid not in found_ids]
    if missing:
        raise ChannelNotFoundError(f"No channel found for id(s): {missing}")
    return channels


def _retrieve_groups(
    store: VectorStore,
    query_embedding: list[float],
    user_text: str,
    *,
    sources: list[Channel],
    top_k: int,
    retrieval_mode: str,
) -> list[ContextGroup]:
    # Each selected source gets its own quota (floored at MIN_BLOCKS_PER_SOURCE) rather than
    # one shared top_k search across all of them — a single large channel would otherwise
    # crowd a small one out of the prompt entirely, and every selected creator needs SOME
    # material present to be attributed or to disagree.
    per_source_k = max(MIN_BLOCKS_PER_SOURCE, top_k // len(sources)) if sources else top_k
    groups = []
    for channel in sources:
        results = store.search(
            channel_ids=[channel.id],
            query_embedding=query_embedding,
            top_k=per_source_k,
            query_text=user_text,
            mode=retrieval_mode,
        )
        groups.append(ContextGroup(channel=channel, results=results))
    return groups


def _probe_unselected_channels(
    store: VectorStore, query_embedding: list[float], *, sources: list[Channel]
) -> list[Channel]:
    """ "Try adding X" detection: one extra vector-mode search over ingested channels NOT in
    the current scope, reusing the question embedding (zero extra API cost). Returns channels
    whose best-matching chunk clears PROBE_MIN_SCORE, in score order, deduped."""
    selected_ids = {c.id for c in sources}
    unselected = {
        cs.channel.id: cs.channel
        for cs in store.list_channels()
        if cs.channel.id not in selected_ids
    }
    if not unselected:
        return []

    hits = store.search(
        channel_ids=list(unselected),
        query_embedding=query_embedding,
        top_k=PROBE_TOP_K,
        mode="dense",
    )
    candidates: list[Channel] = []
    seen: set[UUID] = set()
    for hit in hits:
        if hit.score < PROBE_MIN_SCORE or hit.channel_id in seen:
            continue
        seen.add(hit.channel_id)
        candidates.append(unselected[hit.channel_id])
    return candidates


def _prepare_turn(
    store: VectorStore,
    embedding_provider: LLMProvider,
    *,
    source_channel_ids: list[UUID],
    voice_channel_id: UUID | None,
    user_text: str,
    history: list[Message],
    top_k: int,
    retrieval_mode: str,
) -> tuple[list, list[ContextGroup], Channel | None, list[Channel]]:
    """Shared by answer() and ask(): resolves sources/voice, embeds the question, retrieves
    per source, probes unselected channels, and builds the provider-ready message list.
    Returns (messages, groups, voice_channel, candidates)."""
    if len(user_text) > MAX_QUESTION_CHARS:
        raise QuestionTooLongError(
            f"Question is {len(user_text)} characters; the limit is {MAX_QUESTION_CHARS}."
        )

    sources = _resolve_sources(store, source_channel_ids)
    # A since-disabled persona (or a voice channel dropped from sources) on an already-open
    # chat degrades to Neutral here rather than raising — strict validation happens once, at
    # scope-build time (core.chat.scope.build_scope), not on every turn of an existing chat.
    resolved_voice_id, _ = coerce_voice(sources, voice_channel_id)
    voice_channel = next((c for c in sources if c.id == resolved_voice_id), None)
    if voice_channel is not None:
        # Voice's material renders first in both the prompt's context and the model's reading
        # order — the rest of the scope keeps its original (user-selected) relative order.
        sources = [voice_channel, *(c for c in sources if c.id != voice_channel.id)]

    query_embedding = embedding_provider.embed([user_text])[0]
    if len(query_embedding) != EMBEDDING_DIM:
        raise EmbeddingModelMismatchError(
            f"The configured embedding model produced a {len(query_embedding)}-dimension "
            f"vector, but chunks are stored as {EMBEDDING_DIM}-dimension vectors "
            f"(EMBEDDING_MODEL={EMBEDDING_MODEL!r}). Re-run `aac dataset load` for the "
            "selected channels with the configured embedding model, or fix EMBEDDING_MODEL."
        )

    groups = _retrieve_groups(
        store,
        query_embedding,
        user_text,
        sources=sources,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
    )
    candidates = _probe_unselected_channels(store, query_embedding, sources=sources)

    persona_section = None
    if voice_channel is not None:
        persona_section = render_persona_section(
            get_persona(voice_channel), _display_name(voice_channel)
        )

    messages = build_messages(
        groups=groups,
        voice=voice_channel,
        persona_section=persona_section,
        candidates=candidates,
        history=history,
        user_text=user_text,
    )
    return messages, groups, voice_channel, candidates


def _log_turn(
    *,
    chat_id: UUID | None,
    voice_channel: Channel | None,
    source_channel_ids: list[UUID],
    chat_model: str,
    tokens_in: int | None,
    tokens_out: int | None,
    citations: list[Citation],
    context_len: int,
    candidates: list[Channel],
    started: float,
) -> None:
    # The one line a self-hoster needs to triage "it was slow / it cost a lot / it cited
    # nothing / it should have suggested another channel" from the server log.
    logger.info(
        "chat turn ok chat_id=%s voice=%s sources=%d model=%s tokens_in=%s tokens_out=%s "
        "citations=%d context_chunks=%d suggested=%d latency_ms=%d",
        chat_id,
        voice_channel.id if voice_channel else None,
        len(source_channel_ids),
        chat_model,
        tokens_in,
        tokens_out,
        len(citations),
        context_len,
        len(candidates),
        int((time.perf_counter() - started) * 1000),
    )


def answer(
    store: VectorStore,
    embedding_provider: LLMProvider,
    chat_provider: LLMProvider,
    *,
    chat_id: UUID,
    user_text: str,
    chat_model: str,
    top_k: int = DEFAULT_TOP_K,
    history_window: int = DEFAULT_HISTORY_WINDOW,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
) -> AnswerResult:
    chat = store.get_chat(chat_id)
    if chat is None:
        raise ChatNotFoundError(f"No chat {chat_id}")

    full_history = store.list_messages(chat_id)  # chronological ascending
    windowed_history = full_history[-history_window:] if history_window else []

    messages, groups, voice_channel, candidates = _prepare_turn(
        store,
        embedding_provider,
        source_channel_ids=chat.source_channel_ids,
        voice_channel_id=chat.voice_channel_id,
        user_text=user_text,
        history=windowed_history,
        top_k=top_k,
        retrieval_mode=retrieval_mode,
    )
    context = flatten_context(groups)

    # Persisted synchronously — the user's question is recorded even if the caller never
    # consumes text_stream, or the stream fails partway through.
    store.add_message(chat_id=chat_id, role="user", content=user_text)

    scope = ChatScope(
        source_channel_ids=tuple(chat.source_channel_ids),
        voice_channel_id=voice_channel.id if voice_channel else None,
    )
    disclosure = disclosure_string(_display_name(voice_channel)) if voice_channel else None

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

        message = store.add_message(
            chat_id=chat_id,
            role="assistant",
            content=full_text,
            citations=[citation_to_payload(c) for c in citations],
        )
        usage_channel_id = (
            voice_channel.id
            if voice_channel
            else (chat.source_channel_ids[0] if chat.source_channel_ids else None)
        )
        store.record_usage_event(
            channel_id=usage_channel_id,
            chat_id=chat_id,
            model=chat_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=est_cost,
            source_channel_ids=chat.source_channel_ids,
        )

        result.citations = citations
        result.usage = AnswerUsage(
            model=chat_model, tokens_in=tokens_in, tokens_out=tokens_out, est_cost_usd=est_cost
        )
        result.assistant_message_id = message.id
        result.suggested_source_channels = candidates if not citations else []
        _log_turn(
            chat_id=chat_id,
            voice_channel=voice_channel,
            source_channel_ids=chat.source_channel_ids,
            chat_model=chat_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            citations=citations,
            context_len=len(context),
            candidates=candidates,
            started=started,
        )

    # The generator body doesn't run until first next(), so `result` is bound by the time the
    # closure reads it — no placeholder-then-reassign needed.
    result = AnswerResult(
        text_stream=_stream(), scope=scope, voice_channel=voice_channel, disclosure=disclosure
    )
    return result


def ask(
    store: VectorStore,
    embedding_provider: LLMProvider,
    chat_provider: LLMProvider,
    *,
    scope: ChatScope,
    user_text: str,
    chat_model: str,
    top_k: int = DEFAULT_TOP_K,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
) -> AnswerResult:
    """Stateless one-shot: no chat row, no messages persisted — only a usage_event
    (chat_id=None). No conversation history to window, since there's no chat to read it from."""
    messages, groups, voice_channel, candidates = _prepare_turn(
        store,
        embedding_provider,
        source_channel_ids=list(scope.source_channel_ids),
        voice_channel_id=scope.voice_channel_id,
        user_text=user_text,
        history=[],
        top_k=top_k,
        retrieval_mode=retrieval_mode,
    )
    context = flatten_context(groups)
    disclosure = disclosure_string(_display_name(voice_channel)) if voice_channel else None

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

        usage_channel_id = (
            voice_channel.id
            if voice_channel
            else (scope.source_channel_ids[0] if scope.source_channel_ids else None)
        )
        store.record_usage_event(
            channel_id=usage_channel_id,
            chat_id=None,
            model=chat_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            est_cost_usd=est_cost,
            source_channel_ids=list(scope.source_channel_ids),
        )

        result.citations = citations
        result.usage = AnswerUsage(
            model=chat_model, tokens_in=tokens_in, tokens_out=tokens_out, est_cost_usd=est_cost
        )
        result.suggested_source_channels = candidates if not citations else []
        _log_turn(
            chat_id=None,
            voice_channel=voice_channel,
            source_channel_ids=list(scope.source_channel_ids),
            chat_model=chat_model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            citations=citations,
            context_len=len(context),
            candidates=candidates,
            started=started,
        )

    result = AnswerResult(
        text_stream=_stream(), scope=scope, voice_channel=voice_channel, disclosure=disclosure
    )
    return result
