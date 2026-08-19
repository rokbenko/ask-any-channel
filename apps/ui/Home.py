"""Entry point: `streamlit run apps/ui/Home.py`. Wires the sidebar and chat components —
rendering + core calls only, per CLAUDE.md's "zero logic in Streamlit files" rule."""

import streamlit as st

from apps.ui import state
from apps.ui.components import chat, empty_state, sidebar
from apps.ui.components._common import require_healthy_environment
from core.constants import APP_NAME
from core.store.pgvector_store import PgVectorStore

st.set_page_config(page_title=APP_NAME, page_icon="💬")
st.title(APP_NAME)

require_healthy_environment()

store = PgVectorStore()

try:
    channels = store.list_channels()
    state.restore_from_url(store)  # no-op unless this is a fresh session (refresh / shared link)
except Exception as exc:
    st.error(str(exc))
    st.stop()

if not channels:
    empty_state.render()
    st.stop()

sidebar.render(store, channels)

channel_id = state.get_channel_id()
chat_id = state.get_chat_id()
chat.render(store, channel_id, chat_id)
