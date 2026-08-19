"""Corpus-derived style profile generation: samples a channel's own transcripts and asks the
configured chat model to describe HOW the creator talks, in a fixed, editable section layout.
Best-effort by design — every entry point here is meant to be safe to call from an ingest hook
that must not fail an otherwise-successful ingest."""

from dataclasses import replace
from datetime import UTC, datetime

import tiktoken

from core.config import Settings
from core.constants import (
    PERSONA_STALE_GROWTH_RATIO,
    STYLE_PROFILE_MAX_CHARS,
    STYLE_SAMPLE_CHUNKS_PER_VIDEO,
    STYLE_SAMPLE_MAX_TOKENS,
    STYLE_SAMPLE_RANDOM_CHUNKS,
    STYLE_SAMPLE_TOP_VIDEOS,
    TOKENIZER_ENCODING,
)
from core.credentials import CredentialsProvider
from core.models import Channel
from core.persona.model import Persona, get_persona, set_persona
from core.providers.base import ChatMessage, LLMProvider
from core.providers.factory import build_chat_provider_if_configured
from core.store.base import VectorStore

_SYSTEM_PROMPT = """\
You study transcript excerpts from one YouTube creator's own videos and describe HOW they \
talk — not what they talk about. Output editable markdown with EXACTLY these section \
headings, in this order, each with 1-3 sentences:

## Tone & energy
## Sentence rhythm
## Characteristic expressions & catchphrases
## Analogy habits
## How they address the audience
## Profanity level
## Favorite frameworks & how they name them

No preamble, no closing remarks, no other headings."""


def _cap_by_tokens(texts: list[str], max_tokens: int) -> list[str]:
    encoding = tiktoken.get_encoding(TOKENIZER_ENCODING)
    kept: list[str] = []
    used = 0
    for text in texts:
        n = len(encoding.encode(text))
        if used + n > max_tokens:
            break
        kept.append(text)
        used += n
    return kept


def parse_style_profile(raw: str) -> str:
    text = raw.strip()
    if text.startswith("```"):
        first_newline = text.find("\n")
        text = text[first_newline + 1 :] if first_newline != -1 else ""
        text = text.rstrip()
        if text.endswith("```"):
            text = text[:-3]
    return text.strip()[:STYLE_PROFILE_MAX_CHARS].strip()


def build_style_profile(
    store: VectorStore, chat_provider: LLMProvider, model: str, channel: Channel
) -> str | None:
    """Returns None when the channel has no embedded chunks yet — the caller decides whether
    that's worth retrying later (ensure_style_profile) or is a hard stop (aac persona build)."""
    samples = store.list_style_sample_chunk_texts(
        channel.id,
        top_videos=STYLE_SAMPLE_TOP_VIDEOS,
        chunks_per_video=STYLE_SAMPLE_CHUNKS_PER_VIDEO,
        random_chunks=STYLE_SAMPLE_RANDOM_CHUNKS,
    )
    if not samples:
        return None

    sampled = _cap_by_tokens(samples, STYLE_SAMPLE_MAX_TOKENS)
    excerpts = "\n\n---\n\n".join(sampled)
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT),
        ChatMessage(
            role="user",
            content=f"Creator: {channel.title or channel.handle}\n\nExcerpts:\n{excerpts}",
        ),
    ]
    response = chat_provider.chat(messages, model=model)
    return parse_style_profile(response.content)


def ensure_style_profile(
    store: VectorStore,
    credentials: CredentialsProvider,
    settings: Settings,
    channel: Channel,
    *,
    force: bool = False,
) -> str | None:
    """Skips gracefully (returns None, no write) when no chat key is configured or the channel
    has no embedded chunks yet. Skips (returns the existing profile unchanged) when one already
    exists and force=False — a creator's voice rarely changes between videos, so this isn't
    regenerated on every ingest. Provider/API errors are NOT swallowed here — callers that need
    best-effort behavior (the ingest hook) wrap this themselves, the same way
    core.chat.suggestions._try_generate_suggested_questions does."""
    persona = get_persona(channel)
    if persona.style_profile and not force:
        return persona.style_profile

    provider_and_model = build_chat_provider_if_configured(settings, credentials)
    if provider_and_model is None:
        return None
    chat_provider, model = provider_and_model

    style_profile = build_style_profile(store, chat_provider, model, channel)
    if style_profile is None:
        return None

    updated = replace(
        persona,
        style_profile=style_profile,
        profile_generated_at=datetime.now(UTC).isoformat(),
        profile_chunk_count=store.count_channel_chunks(channel.id),
    )
    set_persona(store, channel.id, updated)
    return style_profile


def is_profile_stale(persona: Persona, current_chunk_count: int) -> bool:
    if not persona.style_profile or not persona.profile_chunk_count:
        return False  # nothing to compare against — absent, not "stale"
    growth_ratio = (current_chunk_count - persona.profile_chunk_count) / persona.profile_chunk_count
    return growth_ratio >= PERSONA_STALE_GROWTH_RATIO
