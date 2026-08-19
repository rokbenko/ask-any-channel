"""Channel management: add a channel, watch it ingest live, chat/update/delete existing ones.
Rendering + core calls only, per CLAUDE.md's "zero logic in Streamlit files" rule."""

import streamlit as st

from apps.ui.components import channels
from apps.ui.components._common import require_healthy_environment
from core.constants import APP_NAME
from core.store.pgvector_store import PgVectorStore

st.set_page_config(page_title=f"{APP_NAME} — Channels", page_icon="📺")
st.title("Channels")

require_healthy_environment()

store = PgVectorStore()

try:
    channel_summaries = store.list_channels()
except Exception as exc:
    st.error(str(exc))
    st.stop()

channels.render(store, channel_summaries)
