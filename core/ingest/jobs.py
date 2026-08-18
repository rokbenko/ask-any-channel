"""Queue-facing job lifecycle: enqueue (with per-channel dedupe + option validation), retry,
cancel. Distinct from core/ingest/runner.py's synchronous CLI-facing entry points — this is
what the worker daemon's queue and the UI's Add/Retry/Cancel buttons call into.

The one-active-job-per-channel invariant is enforced by the store (partial unique indexes,
migration 0005); `ensure_no_active_job` is a pre-check for a friendlier message, not the
guarantee."""

from uuid import UUID

from core.constants import MAX_INGEST_LIMIT, VALID_SORTS
from core.ingest.channel_source import resolve_channel_input
from core.models import IngestJob
from core.search.search import ChannelNotFoundError
from core.store.base import ActiveJobExistsError, VectorStore

__all__ = [
    "ActiveJobExistsError",
    "InvalidJobOptionsError",
    "JobNotCancellableError",
    "JobNotRetryableError",
    "cancel_job",
    "enqueue_ingest_job",
    "enqueue_update_job",
    "ensure_no_active_job",
    "retry_job",
    "validate_job_options",
]

_ACTIVE_STATUSES = ("queued", "running")


class InvalidJobOptionsError(RuntimeError):
    pass


class JobNotRetryableError(RuntimeError):
    pass


class JobNotCancellableError(RuntimeError):
    pass


def validate_job_options(*, limit: int | None, sort: str) -> None:
    """Server-side, so a future HTTP API or a UI without max_value can't bypass it — the CLI's
    Typer checks and the UI's number_input bounds are conveniences, not the guard."""
    if limit is not None and not (1 <= limit <= MAX_INGEST_LIMIT):
        raise InvalidJobOptionsError(f"limit must be between 1 and {MAX_INGEST_LIMIT}, got {limit}")
    if sort not in VALID_SORTS:
        raise InvalidJobOptionsError(f"sort must be one of {VALID_SORTS}, got {sort!r}")


def ensure_no_active_job(store: VectorStore, channel_id: UUID) -> None:
    latest = store.get_latest_job_for_channel(channel_id)
    if latest is not None and latest.status in _ACTIVE_STATUSES:
        raise ActiveJobExistsError(
            f"Channel already has a job {latest.status} — wait for it to finish, or cancel it."
        )


def enqueue_ingest_job(
    store: VectorStore, channel_input: str, *, limit: int | None, sort: str
) -> IngestJob:
    """No network call here — channel resolution happens once the worker claims the job, so a
    bogus-but-well-formed handle surfaces as a failed job row (visible + retryable in the UI)
    rather than an exception at enqueue time. Malformed input (non-YouTube URL, garbage) IS
    rejected here via the pure resolve_channel_input() check. The dedupe pre-check is DB-only
    (get_channel_by_handle_or_id); the store's unique indexes catch what it misses."""
    channel_input = channel_input.strip()
    resolve_channel_input(channel_input)  # raises ChannelInputError; result unused here
    validate_job_options(limit=limit, sort=sort)

    existing = store.get_channel_by_handle_or_id(channel_input)
    channel_id = existing.id if existing else None
    if channel_id is not None:
        ensure_no_active_job(store, channel_id)

    return store.create_job(
        channel_id=channel_id,
        payload={"channel_input": channel_input, "limit": limit, "sort": sort, "kind": "ingest"},
    )


def enqueue_update_job(
    store: VectorStore, channel_id: UUID, *, limit: int | None, sort: str
) -> IngestJob:
    validate_job_options(limit=limit, sort=sort)

    channel = store.get_channel(channel_id)
    if channel is None:
        raise ChannelNotFoundError(f"No channel found for id {channel_id}")

    ensure_no_active_job(store, channel_id)

    channel_input = channel.handle or channel.yt_channel_id
    return store.create_job(
        channel_id=channel_id,
        payload={"channel_input": channel_input, "limit": limit, "sort": sort, "kind": "update"},
    )


def retry_job(store: VectorStore, job_id: UUID) -> IngestJob:
    job = store.get_job(job_id)
    if job.channel_id is not None:
        ensure_no_active_job(store, job.channel_id)  # a newer job may have started since
    retried = store.retry_job(job_id)
    if retried is None:
        raise JobNotRetryableError(f"Job {job_id} is not in a failed state")
    return retried


def cancel_job(store: VectorStore, job_id: UUID) -> IngestJob:
    job = store.cancel_job(job_id)
    if job is None:
        raise JobNotCancellableError(f"Job {job_id} is not queued")
    return job
