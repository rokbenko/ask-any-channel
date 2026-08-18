"""Tiny helpers shared by the UI components. Rendering only."""

import re

import streamlit as st

_MD_SPECIALS_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")


def fail(message: str) -> None:
    """Show the error and halt this run WITHOUT a trailing st.rerun() — a rerun would redraw
    from the DB and wipe the message before anyone reads it (see DECISIONS.md, Phase 2)."""
    st.error(message)
    st.stop()


def escape_markdown(text: str) -> str:
    """For text that ends up in a Markdown-rendering widget label but must read literally
    (button labels support links and images)."""
    return _MD_SPECIALS_RE.sub(r"\\\1", text)
