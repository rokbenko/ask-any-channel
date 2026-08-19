"""Tiny helpers shared by the UI components. Rendering only."""

import logging
import re

import streamlit as st

from core.constants import APP_NAME
from core.doctor import CheckResult, run_checks

_MD_SPECIALS_RE = re.compile(r"([\\`*_{}\[\]()#+\-.!|<>~])")
_ENV_CHECK_TTL_S = 60


def fail(message: str) -> None:
    """Show the error and halt this run WITHOUT a trailing st.rerun() — a rerun would redraw
    from the DB and wipe the message before anyone reads it (see DECISIONS.md, Phase 2)."""
    st.error(message)
    st.stop()


@st.cache_resource(ttl=_ENV_CHECK_TTL_S, show_spinner=False)
def _ui_check_failures() -> list[CheckResult]:
    # Cached across reruns and sessions: the checks open a DB connection, and Streamlit reruns
    # the whole page on every widget interaction — once a minute is plenty for config drift.
    return [r for r in run_checks("ui") if not r.ok]


def require_healthy_environment() -> None:
    """Call at the top of every page (Home and Channels — each page is its own script, so a
    guard on one doesn't cover the other). Halts with one actionable line per failing check;
    the same subset the compose healthcheck runs (`aac doctor --role ui`)."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    failures = _ui_check_failures()
    if failures:
        lines = "\n".join(f"- **{r.name}**: {r.detail}" for r in failures)
        fail(
            f"{APP_NAME} can't start:\n\n{lines}\n\n"
            "Fix the above and reload. Run `aac doctor` (or "
            "`docker compose run --rm worker aac doctor`) for the full report."
        )


def escape_markdown(text: str) -> str:
    """For text that ends up in a Markdown-rendering widget label but must read literally
    (button labels support links and images)."""
    return _MD_SPECIALS_RE.sub(r"\\\1", text)
