"""Channel picker, "New chat" button, and this channel's previous-chats list. Rendering +
core calls only."""

import streamlit as st

from apps.ui import state
from core.store.base import ChannelSummary, VectorStore


def _channel_label(cs: ChannelSummary) -> str:
    return cs.channel.title or cs.channel.handle or cs.channel.yt_channel_id


def render(store: VectorStore, channels: list[ChannelSummary]) -> None:
    with st.sidebar:
        # `key` gives the widget a stable identity and `index` re-seeds it from session/URL
        # state on a fresh session. The label deliberately excludes the live video counts:
        # without a key, Streamlit derives the widget's identity from its formatted options, so
        # a count changing mid-ingest would silently reset the selection (and the open chat).
        current_channel_id = state.get_channel_id()
        current_index = next(
            (i for i, cs in enumerate(channels) if cs.channel.id == current_channel_id), 0
        )
        selected = st.selectbox(
            "Channel",
            options=channels,
            index=current_index,
            key="channel",
            format_func=_channel_label,
        )
        state.ensure_channel(selected.channel.id)
        st.caption(f"{selected.embedded_video_count}/{selected.video_count} videos ready to chat")

        if st.button("New chat"):
            state.set_chat_id(None)
            st.rerun()

        st.caption("Previous chats")
        try:
            chats = store.list_chats(channel_id=selected.channel.id)
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
                state.set_chat_id(chat_summary.id)
                st.rerun()
