"""Rendered when no channel has been ingested yet. Static text + a page link only — no core
calls."""

import streamlit as st


def render() -> None:
    st.info("No channels ingested yet.")
    st.page_link("pages/1_Channels.py", label="Add a channel", icon="📺")
    with st.expander("Prefer the CLI?"):
        st.code(
            "uv run aac ingest @SomeChannel --limit 20\n"
            'uv run aac search "what does this channel say about X?" --channel @SomeChannel',
            language="bash",
        )
        st.caption("See README.md's Quickstart for the full setup.")
