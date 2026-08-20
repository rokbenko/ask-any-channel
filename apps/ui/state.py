"""Session-state helpers — what keys exist and how they change, kept apart from rendering.

A chat now has an independent SOURCES set (any subset of ingested channels) and VOICE (Neutral
or one selected creator) — both editable on an already-open chat, not fixed at creation. The
active draft (before a chat exists) or the open chat's own scope is mirrored into the URL query
string, because Streamlit's session_state does not survive a browser refresh: a reload is a
brand-new session. `?chat=<id>` wins when a chat is open (the chat row is authoritative for its
own scope); otherwise `?sources=a,b&voice=<id|neutral>` mirrors the draft. A legacy `?channel=
<id>` link (from before multi-source chat) still resolves to a single-source draft."""

from uuid import UUID

import streamlit as st

from core.chat.scope import ChatScope, coerce_voice, default_voice, update_chat_scope
from core.models import Chat
from core.store.base import ChannelSummary, VectorStore

_SOURCES_KEY = "sources"
_VOICE_KEY = "voice"
_CHAT_ID_KEY = "chat_id"
_VOICE_FALLBACK_KEY = "voice_fallback_notice"
_PENDING_PROMPT_KEY = "pending_prompt"

_SOURCES_PARAM = "sources"
_VOICE_PARAM = "voice"
_CHAT_PARAM = "chat"
_CHANNEL_PARAM = "channel"  # legacy, pre-multi-source links


def get_sources() -> list[UUID]:
    return list(st.session_state.get(_SOURCES_KEY, []))


def get_voice() -> UUID | None:
    return st.session_state.get(_VOICE_KEY)


def get_chat_id() -> UUID | None:
    return st.session_state.get(_CHAT_ID_KEY)


def _mirror_draft_to_url() -> None:
    sources = get_sources()
    if sources:
        st.query_params[_SOURCES_PARAM] = ",".join(str(s) for s in sources)
    else:
        st.query_params.pop(_SOURCES_PARAM, None)
    voice = get_voice()
    st.query_params[_VOICE_PARAM] = str(voice) if voice else "neutral"


def set_chat_id(chat_id: UUID | None) -> None:
    st.session_state[_CHAT_ID_KEY] = chat_id
    if chat_id is None:
        st.query_params.pop(_CHAT_PARAM, None)
        _mirror_draft_to_url()
    else:
        st.query_params[_CHAT_PARAM] = str(chat_id)
        st.query_params.pop(_SOURCES_PARAM, None)
        st.query_params.pop(_VOICE_PARAM, None)


def start_scope(source_ids: list[UUID], voice_id: UUID | None) -> None:
    """Sets the DRAFT scope (before a chat exists — the sidebar widgets, or the Channels
    page's "Chat" button) and starts a fresh chat."""
    st.session_state[_SOURCES_KEY] = list(source_ids)
    st.session_state[_VOICE_KEY] = voice_id
    set_chat_id(None)


def apply_scope_edit(store: VectorStore) -> None:
    """Call after the Sources/Voice widgets change (their on_change handlers). If a chat is
    open, the edit is persisted to THAT chat — sources/voice are editable on an open chat, not
    fixed at creation. Otherwise it just updates the draft + URL for the next chat."""
    chat_id = get_chat_id()
    if chat_id is not None:
        scope = ChatScope(source_channel_ids=tuple(get_sources()), voice_channel_id=get_voice())
        update_chat_scope(store, chat_id, scope)
    else:
        _mirror_draft_to_url()


def load_chat(chat: Chat) -> None:
    """Loads an existing chat's own scope into the widgets and makes it the active chat."""
    st.session_state[_SOURCES_KEY] = list(chat.source_channel_ids)
    st.session_state[_VOICE_KEY] = chat.voice_channel_id
    st.session_state[_CHAT_ID_KEY] = chat.id
    st.query_params[_CHAT_PARAM] = str(chat.id)
    st.query_params.pop(_SOURCES_PARAM, None)
    st.query_params.pop(_VOICE_PARAM, None)


def clear() -> None:
    """Forget the active scope/chat (e.g. its only channel was just deleted)."""
    for key in (_SOURCES_KEY, _VOICE_KEY, _CHAT_ID_KEY, _VOICE_FALLBACK_KEY, _PENDING_PROMPT_KEY):
        st.session_state.pop(key, None)
    for param in (_CHAT_PARAM, _SOURCES_PARAM, _VOICE_PARAM, _CHANNEL_PARAM):
        st.query_params.pop(param, None)


def set_voice_fallback_notice(message: str) -> None:
    st.session_state[_VOICE_FALLBACK_KEY] = message


def pop_voice_fallback_notice() -> str | None:
    return st.session_state.pop(_VOICE_FALLBACK_KEY, None)


def set_pending_prompt(text: str) -> None:
    st.session_state[_PENDING_PROMPT_KEY] = text


def pop_pending_prompt() -> str | None:
    return st.session_state.pop(_PENDING_PROMPT_KEY, None)


def _try_uuid(raw: str | None) -> UUID | None:
    if not raw:
        return None
    try:
        return UUID(raw)
    except ValueError:
        return None


def restore_from_url(store: VectorStore, channels: list[ChannelSummary]) -> None:
    """On a fresh session (refresh, shared link), seed sources/voice/chat from the URL. A
    ?chat= param wins — the chat row is authoritative for its own scope, and an unknown chat id
    is dropped rather than trusted. No usable params at all defaults to every ingested channel
    selected, voice = default_voice(all) (Neutral unless exactly one channel — today's
    single-channel feel)."""
    # NOT `if get_sources() or ...`: an explicit, deliberate empty selection (the user
    # deselected every source) is falsy but must NOT be treated as "not yet initialized" — a
    # real bug caught live (AppTest) where clearing Sources silently snapped back to "all
    # channels" on the very next rerun. Presence in session_state is what "already seeded"
    # actually means.
    if _SOURCES_KEY in st.session_state or get_chat_id() is not None:
        return

    chat_id = _try_uuid(st.query_params.get(_CHAT_PARAM))
    if chat_id is not None:
        chat = store.get_chat(chat_id)
        if chat is not None:
            load_chat(chat)
            return
        st.query_params.pop(_CHAT_PARAM, None)

    all_channels = [cs.channel for cs in channels]
    by_id = {c.id: c for c in all_channels}

    sources_param = st.query_params.get(_SOURCES_PARAM)
    if sources_param:
        ids = [uid for raw in sources_param.split(",") if (uid := _try_uuid(raw)) in by_id]
        if ids:
            source_channels = [by_id[uid] for uid in ids]
            voice_param = st.query_params.get(_VOICE_PARAM)
            voice_id = None if voice_param in (None, "neutral") else _try_uuid(voice_param)
            resolved_voice, _ = coerce_voice(source_channels, voice_id)
            start_scope(ids, resolved_voice)
            return

    legacy_channel_id = _try_uuid(st.query_params.get(_CHANNEL_PARAM))
    if legacy_channel_id in by_id:
        channel = by_id[legacy_channel_id]
        start_scope([channel.id], default_voice([channel]))
        return

    start_scope([c.id for c in all_channels], default_voice(all_channels))
