"""Add-channel form, "pending adds" (jobs whose channel isn't resolved yet), and per-channel
management cards (progress, retry/cancel, chat/update/delete). Rendering + core calls only —
no SQL, no vendor SDK imports."""

import logging

import streamlit as st

from apps.ui import state
from apps.ui.components._common import fail
from core.constants import MAX_INGEST_LIMIT, WORKER_STALL_WARNING_S
from core.ingest.channel_source import ChannelInputError
from core.ingest.jobs import (
    ActiveJobExistsError,
    InvalidJobOptionsError,
    JobNotCancellableError,
    JobNotRetryableError,
    cancel_job,
    enqueue_ingest_job,
    enqueue_update_job,
    retry_job,
)
from core.ingest.runner import run_delete_channel
from core.models import IngestJob
from core.search.search import ChannelNotFoundError
from core.store.base import ChannelSummary, VectorStore

logger = logging.getLogger(__name__)

_ADD_CHANNEL_DEFAULT_LIMIT = 300
_UPDATE_DEFAULT_LIMIT = 300
_ACTIVE_STATUSES = ("queued", "running")
_FLASH_KEY = "channels_flash"

_USER_FACING_ERRORS = (
    ActiveJobExistsError,
    ChannelInputError,
    ChannelNotFoundError,
    InvalidJobOptionsError,
    JobNotCancellableError,
    JobNotRetryableError,
)


def _flash(message: str) -> None:
    """Survives the st.rerun() that follows a successful action (a plain st.success would be
    wiped by the redraw before anyone reads it — the same trap as the Phase 2 error bug)."""
    st.session_state[_FLASH_KEY] = message


def _render_flash() -> None:
    message = st.session_state.pop(_FLASH_KEY, None)
    if message:
        st.success(message)


def render_add_channel_form(store: VectorStore) -> None:
    st.subheader("Add a channel")
    with st.form("add_channel_form", clear_on_submit=True):
        channel_input = st.text_input("Channel URL, @handle, or channel id")
        col1, col2 = st.columns(2)
        limit = col1.number_input(
            "Max videos",
            min_value=1,
            max_value=MAX_INGEST_LIMIT,
            value=_ADD_CHANNEL_DEFAULT_LIMIT,
            step=10,
        )
        sort = col2.selectbox("Sort", options=["recent", "views"])
        submitted = st.form_submit_button("Add channel")

    if not submitted:
        return
    if not channel_input.strip():
        st.error("Enter a channel URL, @handle, or channel id.")
        return

    try:
        job = enqueue_ingest_job(store, channel_input.strip(), limit=int(limit), sort=sort)
    except _USER_FACING_ERRORS as exc:
        logger.warning("enqueue ingest job failed for %r: %s", channel_input, exc)
        st.error(str(exc))
        return
    except Exception:
        logger.exception("enqueue ingest job crashed for %r", channel_input)
        st.error("Something went wrong queuing that channel — the server log has the traceback.")
        return

    _flash(
        f"Queued {job.payload.get('channel_input')!r} — the worker will pick it up shortly. "
        "Ingestion needs OPENAI_API_KEY configured to complete."
    )
    st.rerun()


def _render_job_progress(job: IngestJob) -> None:
    progress = job.progress or {}
    done, total = progress.get("done", 0), progress.get("total", 0)
    stage = progress.get("stage", job.status)
    attempts = progress.get("attempts", 0)
    suffix = f" · attempt {attempts + 1}" if attempts else ""
    st.caption(f"{job.status} — {stage} ({done}/{total}){suffix}")
    if total:
        st.progress(min(done / total, 1.0))


def _render_active_job_section(store: VectorStore, job: IngestJob) -> None:
    @st.fragment(run_every=2)
    def _progress_fragment() -> None:
        current = store.get_job(job.id)
        if current.status not in _ACTIVE_STATUSES:
            # The outer card chose this section on a snapshot; only a full-app rerun lets it
            # flip to the finished/failed card. Without this the fragment polls forever.
            st.rerun(scope="app")
        _render_job_progress(current)
        if current.status == "queued" and st.button("Cancel", key=f"cancel-{current.id}"):
            try:
                cancel_job(store, current.id)
            except _USER_FACING_ERRORS as exc:
                st.error(str(exc))
            else:
                st.rerun(scope="app")

    _progress_fragment()


def _render_failed_job_section(store: VectorStore, job: IngestJob) -> None:
    st.error("Ingestion failed:")
    st.code(job.error or "(no error message recorded)", language=None)
    if st.button("Retry", key=f"retry-{job.id}"):
        try:
            retry_job(store, job.id)
        except _USER_FACING_ERRORS as exc:
            fail(str(exc))
        st.rerun()


def _render_pending_adds(store: VectorStore, jobs: list[IngestJob]) -> None:
    """Jobs enqueued for a channel that hasn't been resolved yet (no channel row → no card).
    A bogus handle lives here forever as a failed job with its error and a Retry button."""
    if not jobs:
        return
    st.subheader("Pending adds")
    for job in jobs:
        with st.container(border=True):
            st.markdown(f"**{job.payload.get('channel_input', '?')}**")
            if job.status in _ACTIVE_STATUSES:
                _render_active_job_section(store, job)
            else:
                _render_failed_job_section(store, job)


def render_channel_card(
    store: VectorStore, channel_summary: ChannelSummary, latest_job: IngestJob | None
) -> None:
    channel = channel_summary.channel
    with st.container(border=True):
        cols = st.columns([1, 3])
        with cols[0]:
            if channel.thumbnail_url:
                st.image(channel.thumbnail_url, width=96)
        with cols[1]:
            st.markdown(f"**{channel.title or channel.handle or channel.yt_channel_id}**")
            st.caption(
                f"{channel_summary.embedded_video_count}/{channel_summary.video_count} videos "
                f"ready · {channel_summary.chunk_count} chunks"
                + (
                    f" · updated {channel_summary.last_updated_at:%Y-%m-%d %H:%M}"
                    if channel_summary.last_updated_at
                    else ""
                )
            )

        if latest_job is not None and latest_job.status in _ACTIVE_STATUSES:
            _render_active_job_section(store, latest_job)
            return
        if latest_job is not None and latest_job.status == "failed":
            _render_failed_job_section(store, latest_job)
            return

        action_cols = st.columns(3)
        if action_cols[0].button("Chat", key=f"chat-{channel.id}"):
            state.ensure_channel(channel.id)
            st.switch_page("Home.py")

        with action_cols[1].popover("Check for new videos"):
            update_limit = st.number_input(
                "Max new videos",
                min_value=1,
                max_value=MAX_INGEST_LIMIT,
                value=_UPDATE_DEFAULT_LIMIT,
                step=10,
                key=f"update-limit-{channel.id}",
            )
            if st.button("Check now", key=f"update-{channel.id}"):
                try:
                    enqueue_update_job(store, channel.id, limit=int(update_limit), sort="recent")
                except _USER_FACING_ERRORS as exc:
                    st.error(str(exc))
                else:
                    _flash("Checking for new videos — the worker will pick it up shortly.")
                    st.rerun()

        with action_cols[2].popover("Delete"):
            confirm_target = channel.handle or channel.title or channel.yt_channel_id
            st.caption(f"Type '{confirm_target}' to confirm — this deletes all local data.")
            typed = st.text_input("Confirm", key=f"delete-confirm-{channel.id}")
            if st.button(
                "Delete permanently",
                key=f"delete-{channel.id}",
                disabled=typed != confirm_target,
                type="primary",
            ):
                try:
                    run_delete_channel(channel.id)
                except Exception:
                    logger.exception("delete channel failed for %s", channel.id)
                    st.error("Delete failed — the server log has the traceback.")
                else:
                    if state.get_channel_id() == channel.id:
                        state.clear()  # don't leave chat pointing at a deleted channel
                    _flash(f"Deleted {confirm_target}.")
                    st.rerun()


def render(store: VectorStore, channels: list[ChannelSummary]) -> None:
    _render_flash()
    render_add_channel_form(store)
    st.divider()

    if store.count_stale_queued_jobs(WORKER_STALL_WARNING_S):
        st.warning(
            "A queued job hasn't been picked up for a while — is a worker running? Start one "
            "with `docker compose up -d worker` or `uv run aac worker`."
        )

    _render_pending_adds(store, store.list_unattached_jobs())

    st.subheader("Your channels")
    if not channels:
        st.info("No channels yet — add one above.")
        return
    latest_jobs = store.list_latest_jobs_by_channel()
    for channel_summary in channels:
        render_channel_card(store, channel_summary, latest_jobs.get(channel_summary.channel.id))
