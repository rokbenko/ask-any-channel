"""Sources multiselect, Voice selector, "New chat" button, and the full chats list (chats
aren't scoped to one channel anymore — every chat shows its own scope/voice badge).
Rendering + core calls only."""

import streamlit as st

from apps.ui import state
from core.chat.scope import coerce_voice
from core.models import Channel
from core.persona import disclosure_string, get_persona
from core.store.base import ChannelSummary, VectorStore


def _label(channel: Channel) -> str:
    return channel.title or channel.handle or channel.yt_channel_id


def _label_for_id(channel_id, channels_by_id: dict) -> str:
    channel = channels_by_id.get(channel_id)
    return _label(channel) if channel is not None else "(unknown)"


def _on_sources_changed(store: VectorStore, channels_by_id: dict) -> None:
    selected_channels = [
        channels_by_id[cid] for cid in st.session_state["sources"] if cid in channels_by_id
    ]
    current_voice = st.session_state.get("voice")
    resolved_voice, changed = coerce_voice(selected_channels, current_voice)
    if changed:
        dropped = channels_by_id.get(current_voice)
        if dropped is not None:
            state.set_voice_fallback_notice(
                f"Voice reset to Neutral — {_label(dropped)} is no longer a source."
            )
    st.session_state["voice"] = resolved_voice
    state.apply_scope_edit(store)


def _on_voice_changed(store: VectorStore) -> None:
    state.apply_scope_edit(store)


def render(store: VectorStore, channels: list[ChannelSummary]) -> None:
    channels_by_id = {cs.channel.id: cs.channel for cs in channels}

    with st.sidebar:
        # Defensive clamp: a channel referenced by the current draft/chat may have been
        # deleted (in another tab, or by the worker's own UI) since it was last set — a stale
        # id in session_state would otherwise make the widgets below raise.
        valid_ids = set(channels_by_id)
        clamped_sources = [cid for cid in state.get_sources() if cid in valid_ids]
        if clamped_sources != state.get_sources():
            st.session_state["sources"] = clamped_sources
        if state.get_voice() not in (None, *clamped_sources):
            st.session_state["voice"] = None

        st.multiselect(
            "Sources",
            options=list(channels_by_id),
            format_func=lambda cid: _label_for_id(cid, channels_by_id),
            key="sources",
            on_change=_on_sources_changed,
            args=(store, channels_by_id),
        )

        selected_ids = state.get_sources()
        if not selected_ids:
            st.warning("Select at least one source to chat.")
        selected_channels = [channels_by_id[cid] for cid in selected_ids if cid in channels_by_id]
        voice_options = [None, *(c.id for c in selected_channels if get_persona(c).enabled)]

        st.selectbox(
            "Voice",
            options=voice_options,
            format_func=lambda cid: (
                "Neutral" if cid is None else _label_for_id(cid, channels_by_id)
            ),
            key="voice",
            on_change=_on_voice_changed,
            args=(store,),
        )

        notice = state.pop_voice_fallback_notice()
        if notice:
            st.info(notice)

        active_voice_id = state.get_voice()
        if active_voice_id is not None and active_voice_id in channels_by_id:
            st.caption(disclosure_string(_label(channels_by_id[active_voice_id])))
        else:
            st.caption("Neutral assistant — every creator is attributed in the third person.")

        if st.button("New chat"):
            state.set_chat_id(None)
            st.rerun()

        st.caption("Previous chats")
        try:
            chats = store.list_chats()
        except Exception as exc:
            st.error(str(exc))
            return

        active_chat_id = state.get_chat_id()
        for chat_summary in chats:
            is_active = chat_summary.id == active_chat_id
            label = chat_summary.title or "Untitled chat"
            if st.button(
                label, key=str(chat_summary.id), type="primary" if is_active else "secondary"
            ):
                chat = store.get_chat(chat_summary.id)
                if chat is not None:
                    state.load_chat(chat)
                    st.rerun()
            source_names = ", ".join(
                _label_for_id(cid, channels_by_id) for cid in chat_summary.source_channel_ids
            )
            voice_name = (
                "Neutral"
                if chat_summary.voice_channel_id is None
                else _label_for_id(chat_summary.voice_channel_id, channels_by_id)
            )
            st.caption(f"Sources: {source_names or '—'} · Voice: {voice_name}")
