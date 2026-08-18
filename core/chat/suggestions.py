"""Suggested starter questions for a channel's chat empty state, generated from a sample of
its transcript chunks via one small non-streaming chat call — the first caller of
LLMProvider.chat() in this codebase (core.chat.answer only ever uses stream_chat()).

Every question that reaches storage passes through `sanitize_question()`, whether it came
from the model (transcripts are untrusted text) or from a bundle's manifest (bundles are
untrusted, registry model): the strings render as Streamlit button labels, which support
Markdown links and images."""

import re

from core.config import Settings
from core.constants import (
    SUGGESTED_QUESTION_MAX_CHARS,
    SUGGESTED_QUESTIONS_COUNT,
    SUGGESTED_QUESTIONS_MAX_COUNT,
)
from core.credentials import CredentialsProvider
from core.models import Channel
from core.providers.base import ChatMessage, LLMProvider
from core.providers.factory import build_chat_provider_if_configured
from core.store.base import VectorStore

BRANDING_KEY = "suggested_questions"

_SYSTEM_PROMPT = (
    "You write short, specific starter questions a viewer could ask a chatbot about a YouTube "
    "channel, based on transcript excerpts. Output exactly {n} questions, one per line, no "
    "numbering, no bullets, no markdown, no preamble or closing remarks."
)
# Markdown syntax that turns a plain label into a link/image/HTML/emphasis in Streamlit widgets.
_MARKDOWN_SYNTAX_RE = re.compile(r"[\[\]()<>*_`!#|~\\]")
_LEADING_BULLET_RE = re.compile(r"^\s*(?:[-•*]|\d+[.)])\s*")


def sanitize_question(raw: object) -> str | None:
    """One clean, single-line, markdown-neutral question, or None if nothing usable remains."""
    if not isinstance(raw, str):
        return None
    text = _LEADING_BULLET_RE.sub("", raw.splitlines()[0] if raw else "")
    text = _MARKDOWN_SYNTAX_RE.sub("", text)
    text = " ".join(text.split())[:SUGGESTED_QUESTION_MAX_CHARS].strip()
    return text or None


def sanitize_questions(raw: object) -> list[str]:
    if not isinstance(raw, list):
        return []
    cleaned = [q for q in (sanitize_question(item) for item in raw) if q]
    return cleaned[:SUGGESTED_QUESTIONS_MAX_COUNT]


def generate_suggested_questions(
    chat_provider: LLMProvider,
    model: str,
    channel_title: str | None,
    sample_texts: list[str],
) -> list[str]:
    if not sample_texts:
        return []

    excerpts = "\n\n".join(t[:800] for t in sample_texts)
    messages = [
        ChatMessage(role="system", content=_SYSTEM_PROMPT.format(n=SUGGESTED_QUESTIONS_COUNT)),
        ChatMessage(
            role="user",
            content=f"Channel: {channel_title or 'this channel'}\n\nExcerpts:\n{excerpts}",
        ),
    ]
    response = chat_provider.chat(messages, model=model)
    return sanitize_questions(response.content.splitlines())[:SUGGESTED_QUESTIONS_COUNT]


def ensure_suggested_questions(
    store: VectorStore,
    settings: Settings,
    credentials: CredentialsProvider,
    channel: Channel,
) -> list[str]:
    """Lazy fallback for channels whose build had no chat key at the time: returns the stored
    questions if the key is present in branding (an empty list means "tried, the model gave
    nothing usable" — don't bill again on every render), else generates and persists them from
    chunks already loaded in Postgres. Never raises for a missing key — no chips, not a broken
    chat page. Only persists after an actual generation attempt: a channel with no embedded
    chunks yet returns [] without marking itself tried, so it's retried once content lands."""
    if BRANDING_KEY in channel.branding:
        return sanitize_questions(channel.branding[BRANDING_KEY])

    configured = build_chat_provider_if_configured(settings, credentials)
    if configured is None:
        return []
    chat_provider, model = configured

    sample_texts = store.list_sample_chunk_texts(channel.id)
    if not sample_texts:
        return []

    questions = generate_suggested_questions(chat_provider, model, channel.title, sample_texts)
    store.set_channel_branding(channel.id, {BRANDING_KEY: questions})
    return questions
