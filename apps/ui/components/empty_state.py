"""Rendered when no channel has been ingested yet. Static text only — no core calls."""

import streamlit as st


def render() -> None:
    st.info("No channels ingested yet. Run these from the repo root, then refresh this page:")
    st.code(
        "uv run aac ingest @SomeChannel --limit 20\n"
        'uv run aac search "what does this channel say about X?" --channel @SomeChannel',
        language="bash",
    )
    st.caption("See README.md's Quickstart for the full setup.")
