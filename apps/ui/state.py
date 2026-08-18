"""Session-state helpers — what keys exist and how they change, kept apart from rendering.

The active channel/chat are mirrored into the URL query string (`?channel=...&chat=...`) because
Streamlit's session_state does not survive a browser refresh: a reload is a brand-new session.
Restoring from the URL is what makes "refresh keeps the conversation" true, and it makes chats
linkable for free."""

from uuid import UUID

import streamlit as st

from core.store.base import VectorStore

_CHANNEL_ID_KEY = "channel_id"
_CHAT_ID_KEY = "chat_id"
_CHANNEL_PARAM = "channel"
_CHAT_PARAM = "chat"


def get_channel_id() -> UUID | None:
    return st.session_state.get(_CHANNEL_ID_KEY)


def get_chat_id() -> UUID | None:
    return st.session_state.get(_CHAT_ID_KEY)


def ensure_channel(new_channel_id: UUID) -> None:
    """A chat belongs to exactly one channel — switching channels always starts a fresh chat."""
    if st.session_state.get(_CHANNEL_ID_KEY) != new_channel_id:
        st.session_state[_CHANNEL_ID_KEY] = new_channel_id
        set_chat_id(None)
    # Re-mirror even when unchanged: st.switch_page() (Channels → Chat) clears query params, and
    # without the param a refresh right after would fall back to the first channel.
    if st.query_params.get(_CHANNEL_PARAM) != str(new_channel_id):
        st.query_params[_CHANNEL_PARAM] = str(new_channel_id)


def clear() -> None:
    """Forget the active channel/chat (e.g. the channel was just deleted)."""
    st.session_state.pop(_CHANNEL_ID_KEY, None)
    st.session_state.pop(_CHAT_ID_KEY, None)
    st.query_params.pop(_CHANNEL_PARAM, None)
    st.query_params.pop(_CHAT_PARAM, None)


def set_chat_id(chat_id: UUID | None) -> None:
    st.session_state[_CHAT_ID_KEY] = chat_id
    if chat_id is None:
        st.query_params.pop(_CHAT_PARAM, None)
    else:
        st.query_params[_CHAT_PARAM] = str(chat_id)


def _param_uuid(name: str) -> UUID | None:
    raw = st.query_params.get(name)
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def restore_from_url(store: VectorStore) -> None:
    """On a fresh session (refresh, shared link), seed channel/chat from the URL. A chat param
    wins over a channel param — the chat's own channel is authoritative, and a chat id that
    doesn't exist is silently dropped rather than trusted."""
    if get_channel_id() is not None:
        return

    chat_id = _param_uuid(_CHAT_PARAM)
    if chat_id is not None:
        chat = store.get_chat(chat_id)
        if chat is not None:
            st.session_state[_CHANNEL_ID_KEY] = chat.channel_id
            st.session_state[_CHAT_ID_KEY] = chat.id
            return
        st.query_params.pop(_CHAT_PARAM, None)

    channel_id = _param_uuid(_CHANNEL_PARAM)
    if channel_id is not None:
        st.session_state[_CHANNEL_ID_KEY] = channel_id
