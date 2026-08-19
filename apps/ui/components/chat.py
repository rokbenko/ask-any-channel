"""Message history, chat input, and the streaming turn handler. Rendering + core calls
only — no SQL, no vendor SDK imports, no prompt strings (those live in core.chat.prompt)."""

import logging
from uuid import UUID

import streamlit as st

from apps.ui import state
from apps.ui.components._common import escape_markdown, fail
from core.chat.answer import answer
from core.chat.citations import Citation
from core.chat.errors import ChatNotFoundError, EmbeddingModelMismatchError, QuestionTooLongError
from core.chat.suggestions import ensure_suggested_questions
from core.config import get_settings
from core.constants import MAX_QUESTION_CHARS
from core.credentials import CredentialError, CredentialsProvider
from core.providers.base import ProviderError
from core.providers.factory import build_chat_provider
from core.providers.openai_provider import OpenAIProvider
from core.search.search import ChannelNotFoundError
from core.store.base import VectorStore

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
)


def _mmss(t_start_s: float) -> str:
    total = int(t_start_s)
    return f"{total // 60}:{total % 60:02d}"


def _render_citation(*, n: int, title: str | None, url: str, t_start_s: float) -> None:
    with st.expander(f"[{n}] {title or 'Untitled video'} @ {_mmss(t_start_s)}"):
        st.markdown(f"[Open on YouTube]({url})")
        st.video(url, start_time=int(t_start_s))


def _render_citations(citations: list[Citation]) -> None:
    for c in citations:
        _render_citation(n=c.n, title=c.title, url=c.url, t_start_s=c.t_start_s)


def _render_stored_citations(payload: list[dict]) -> None:
    # `messages.citations` is a persisted format now; a malformed/older entry must not take
    # the whole page down, so read defensively and skip anything unusable.
    for c in payload:
        if not isinstance(c, dict) or "url" not in c:
            continue
        _render_citation(
            n=c.get("n", 0), title=c.get("title"), url=c["url"], t_start_s=c.get("t_start_s", 0)
        )


def render(store: VectorStore, channel_id: UUID, chat_id: UUID | None) -> None:
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

    suggested_prompt = None
    if chat_id is None:
        channel = store.get_channel(channel_id)
        questions = []
        if channel is not None:
            try:
                with st.spinner("Preparing starter questions…"):
                    questions = ensure_suggested_questions(store, settings, credentials, channel)
            except Exception:
                # Must never break the chat page — a fresh chat with no chips is a fine fallback.
                logger.warning(
                    "suggested-question generation failed channel_id=%s", channel_id, exc_info=True
                )
        if questions:
            st.caption("Try asking:")
            columns = st.columns(len(questions))
            for i, (col, question) in enumerate(zip(columns, questions, strict=True)):
                # Sanitized in core already; escaping is belt-and-braces — the label is Markdown.
                if col.button(escape_markdown(question), key=f"suggested-{i}"):
                    suggested_prompt = question

    typed_prompt = st.chat_input("Ask about this channel's videos...", max_chars=MAX_QUESTION_CHARS)
    prompt = suggested_prompt or typed_prompt
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
            chat_id = store.create_chat(channel_id=channel_id).id
            state.set_chat_id(chat_id)

        try:
            result = answer(
                store,
                embedding_provider,
                chat_provider,
                channel_id=channel_id,
                chat_id=chat_id,
                user_text=prompt,
                chat_model=chat_model,
                retrieval_mode=settings.retrieval_mode,
            )
            st.write_stream(result.text_stream)
            _render_citations(result.citations)
        except _USER_FACING_ERRORS as exc:
            logger.warning("chat turn failed chat_id=%s: %s", chat_id, exc)
            fail(str(exc))
        except Exception:
            logger.exception("chat turn crashed chat_id=%s channel_id=%s", chat_id, channel_id)
            fail("Something went wrong answering that — the server log has the traceback.")

    st.rerun()  # success only: re-render from DB so history and the ephemeral echo can't diverge
