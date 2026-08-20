"""Message history, chat input, and the streaming turn handler. Rendering + core calls
only — no SQL, no vendor SDK imports, no prompt strings (those live in core.chat.prompt)."""

import logging

import streamlit as st

from apps.ui import state
from apps.ui.components._common import escape_markdown, fail
from core.chat.answer import answer
from core.chat.citations import Citation
from core.chat.errors import (
    ChatNotFoundError,
    EmbeddingModelMismatchError,
    EmptyScopeError,
    InvalidVoiceError,
    QuestionTooLongError,
)
from core.chat.scope import build_scope, create_chat
from core.chat.suggestions import blend_suggested_questions, ensure_suggested_questions
from core.config import get_settings
from core.constants import MAX_QUESTION_CHARS
from core.credentials import CredentialError, CredentialsProvider
from core.models import Channel
from core.persona import disclosure_string
from core.providers.base import ProviderError
from core.providers.factory import build_chat_provider
from core.providers.openai_provider import OpenAIProvider
from core.search.search import ChannelNotFoundError
from core.store.base import ChannelSummary, VectorStore

logger = logging.getLogger(__name__)

# Errors whose message is already written for the person at the keyboard. Anything else is a
# bug: the user gets a generic line, the operator gets the traceback in the server log.
_USER_FACING_ERRORS = (
    CredentialError,
    ProviderError,
    ChannelNotFoundError,
    ChatNotFoundError,
    EmbeddingModelMismatchError,
    QuestionTooLongError,
    EmptyScopeError,
    InvalidVoiceError,
)


def _name(channel: Channel) -> str:
    return channel.title or channel.handle or channel.yt_channel_id


def _mmss(t_start_s: float) -> str:
    total = int(t_start_s)
    return f"{total // 60}:{total % 60:02d}"


def _render_citation(
    *, n: int, title: str | None, url: str, t_start_s: float, channel_title: str | None
) -> None:
    prefix = f"{channel_title} — " if channel_title else ""
    with st.expander(f"[{n}] {prefix}{title or 'Untitled video'} @ {_mmss(t_start_s)}"):
        st.markdown(f"[Open on YouTube]({url})")
        st.video(url, start_time=int(t_start_s))


def _render_citations(citations: list[Citation]) -> None:
    # Only label citations with their creator when more than one appears — a single-source
    # chat keeps today's plain "[n] title @ m:ss" look.
    multi = len({c.channel_title for c in citations if c.channel_title}) > 1
    for c in citations:
        _render_citation(
            n=c.n,
            title=c.title,
            url=c.url,
            t_start_s=c.t_start_s,
            channel_title=c.channel_title if multi else None,
        )


def _render_stored_citations(payload: list[dict]) -> None:
    # `messages.citations` is a persisted format now; a malformed/older entry must not take
    # the whole page down, so read defensively and skip anything unusable.
    valid = [c for c in payload if isinstance(c, dict) and "url" in c]
    multi = len({c.get("channel_title") for c in valid if c.get("channel_title")}) > 1
    for c in valid:
        _render_citation(
            n=c.get("n", 0),
            title=c.get("title"),
            url=c["url"],
            t_start_s=c.get("t_start_s", 0),
            channel_title=c.get("channel_title") if multi else None,
        )


def render(store: VectorStore, channels: list[ChannelSummary]) -> None:
    channels_by_id = {cs.channel.id: cs.channel for cs in channels}
    chat_id = state.get_chat_id()
    source_ids = state.get_sources()
    voice_id = state.get_voice()

    messages = []
    if chat_id is not None:
        try:
            messages = store.list_messages(chat_id)
        except Exception as exc:
            logger.exception("loading messages failed chat_id=%s", chat_id)
            st.error(str(exc))

    for m in messages:
        with st.chat_message(m.role):
            st.markdown(m.content)
            if m.role == "assistant" and m.citations:
                _render_stored_citations(m.citations)

    settings = get_settings()
    credentials = CredentialsProvider(settings)

    active_voice = channels_by_id.get(voice_id) if voice_id else None
    if active_voice is not None and chat_id is not None:
        st.caption(disclosure_string(_name(active_voice)))

    suggested_prompt = None
    if chat_id is None and source_ids:
        selected_channels = [channels_by_id[cid] for cid in source_ids if cid in channels_by_id]
        questions: list[str] = []
        try:
            with st.spinner("Preparing starter questions…"):
                per_channel = [
                    ensure_suggested_questions(store, settings, credentials, c)
                    for c in selected_channels
                ]
            questions = blend_suggested_questions(per_channel)
        except Exception:
            # Must never break the chat page — a fresh chat with no chips is a fine fallback.
            logger.warning(
                "suggested-question generation failed sources=%s", source_ids, exc_info=True
            )
        if questions:
            st.caption("Try asking:")
            columns = st.columns(len(questions))
            for i, (col, question) in enumerate(zip(columns, questions, strict=True)):
                # Sanitized in core already; escaping is belt-and-braces — the label is Markdown.
                if col.button(escape_markdown(question), key=f"suggested-{i}"):
                    suggested_prompt = question

    pending_prompt = state.pop_pending_prompt()
    typed_prompt = st.chat_input(
        "Ask about the selected channels' videos...",
        max_chars=MAX_QUESTION_CHARS,
        disabled=not source_ids,
    )
    prompt = pending_prompt or suggested_prompt or typed_prompt
    if not prompt:
        return

    with st.chat_message("user"):
        st.markdown(prompt)  # ephemeral echo this run only — answer() persists the real row

    with st.chat_message("assistant"):
        # Providers first, chat row second: a missing key must not leave an empty chat behind.
        try:
            embedding_provider = OpenAIProvider(credentials)
        except CredentialError as exc:
            fail(f"Chat needs an OpenAI key to embed your question: {exc}")

        try:
            chat_provider, chat_model = build_chat_provider(settings, credentials)
        except CredentialError as exc:
            fail(f"Chat needs a {settings.chat_provider} key to answer: {exc}")

        if chat_id is None:
            selected_channels = [channels_by_id[cid] for cid in source_ids if cid in channels_by_id]
            try:
                scope = build_scope(selected_channels, voice_id)
            except (EmptyScopeError, InvalidVoiceError) as exc:
                fail(str(exc))
            chat_id = create_chat(store, scope).id
            state.set_chat_id(chat_id)

        try:
            result = answer(
                store,
                embedding_provider,
                chat_provider,
                chat_id=chat_id,
                user_text=prompt,
                chat_model=chat_model,
                retrieval_mode=settings.retrieval_mode,
            )
            if result.disclosure:
                st.caption(result.disclosure)
            st.write_stream(result.text_stream)
            _render_citations(result.citations)
            if result.suggested_source_channels:
                st.info("The selected sources don't seem to cover this.")
                for c in result.suggested_source_channels:
                    if st.button(f"Add {_name(c)} to Sources and re-ask", key=f"add-source-{c.id}"):
                        state.start_scope([*source_ids, c.id], voice_id)
                        state.set_pending_prompt(prompt)
                        st.rerun()
        except _USER_FACING_ERRORS as exc:
            logger.warning("chat turn failed chat_id=%s: %s", chat_id, exc)
            fail(str(exc))
        except Exception:
            logger.exception("chat turn crashed chat_id=%s", chat_id)
            fail("Something went wrong answering that — the server log has the traceback.")

    st.rerun()  # success only: re-render from DB so history and the ephemeral echo can't diverge
