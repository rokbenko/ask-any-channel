from datetime import UTC, datetime
from uuid import uuid4

import pytest

from core.constants import MAX_INGEST_LIMIT
from core.ingest.channel_source import ChannelInputError
from core.ingest.jobs import (
    ActiveJobExistsError,
    InvalidJobOptionsError,
    JobNotCancellableError,
    JobNotRetryableError,
    cancel_job,
    enqueue_ingest_job,
    enqueue_update_job,
    ensure_no_active_job,
    retry_job,
    validate_job_options,
)
from core.models import Channel, IngestJob
from core.search.search import ChannelNotFoundError
from tests.fakes import FakeVectorStore


def _make_channel(*, handle="@some") -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle,
        title="Some Channel",
        thumbnail_url=None,
        branding={},
        created_at=datetime.now(UTC),
    )


def _make_job(*, channel_id, status) -> IngestJob:
    now = datetime.now(UTC)
    return IngestJob(
        id=uuid4(),
        channel_id=channel_id,
        payload={"channel_input": "@some", "limit": None, "sort": "recent", "kind": "ingest"},
        status=status,
        progress={},
        error="boom" if status == "failed" else None,
        created_at=now,
        started_at=now if status == "running" else None,
        finished_at=None,
        heartbeat_at=now,
    )


# --- enqueue_ingest_job dedupe ---------------------------------------------------------


def test_enqueue_ingest_job_creates_queued_job_for_brand_new_channel():
    store = FakeVectorStore()

    job = enqueue_ingest_job(store, "@brand-new", limit=20, sort="recent")

    assert job.status == "queued"
    assert job.channel_id is None
    assert job.payload == {
        "channel_input": "@brand-new",
        "limit": 20,
        "sort": "recent",
        "kind": "ingest",
    }


@pytest.mark.parametrize("active_status", ["queued", "running"])
def test_enqueue_ingest_job_rejects_when_channel_has_active_job(active_status):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    store.seed_job(_make_job(channel_id=channel.id, status=active_status))

    with pytest.raises(ActiveJobExistsError):
        enqueue_ingest_job(store, channel.handle, limit=None, sort="recent")


@pytest.mark.parametrize("finished_status", ["done", "failed", "cancelled"])
def test_enqueue_ingest_job_allows_when_latest_job_is_finished(finished_status):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    store.seed_job(_make_job(channel_id=channel.id, status=finished_status))

    job = enqueue_ingest_job(store, channel.handle, limit=None, sort="recent")

    assert job.status == "queued"
    assert job.channel_id == channel.id


def test_ensure_no_active_job_is_a_noop_when_channel_has_no_prior_job():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)

    ensure_no_active_job(store, channel.id)  # must not raise


def test_enqueue_ingest_job_dedupes_unresolved_channels_on_the_raw_input():
    """Two Adds for a handle that has no channel row yet must not both queue (the DB enforces
    this with a partial unique index on payload->>'channel_input'; the fake mirrors it)."""
    store = FakeVectorStore()
    enqueue_ingest_job(store, "@brand-new", limit=20, sort="recent")

    with pytest.raises(ActiveJobExistsError):
        enqueue_ingest_job(store, "@brand-new", limit=20, sort="recent")


def test_enqueue_ingest_job_rejects_non_youtube_urls_before_creating_a_job():
    store = FakeVectorStore()

    with pytest.raises(ChannelInputError):
        enqueue_ingest_job(store, "http://169.254.169.254/latest/meta-data", limit=5, sort="recent")

    assert store.jobs == {}


@pytest.mark.parametrize(
    ("limit", "sort"),
    [(0, "recent"), (MAX_INGEST_LIMIT + 1, "recent"), (10, "popular"), (-5, "views")],
)
def test_validate_job_options_rejects_out_of_range_limit_or_unknown_sort(limit, sort):
    with pytest.raises(InvalidJobOptionsError):
        validate_job_options(limit=limit, sort=sort)


def test_validate_job_options_accepts_no_limit_and_known_sorts():
    validate_job_options(limit=None, sort="recent")
    validate_job_options(limit=MAX_INGEST_LIMIT, sort="views")


def test_create_job_with_running_status_is_never_claimable():
    """Inline CLI ingests create their row already running so an always-on worker can't claim
    and run the same job a second time."""
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)

    job = store.create_job(
        channel_id=channel.id, payload={"channel_input": "@some"}, status="running"
    )

    assert job.status == "running"
    assert job.started_at is not None
    assert store.claim_next_queued_job() is None


# --- enqueue_update_job -----------------------------------------------------------------


def test_enqueue_update_job_requires_an_existing_channel():
    store = FakeVectorStore()

    with pytest.raises(ChannelNotFoundError):
        enqueue_update_job(store, uuid4(), limit=20, sort="recent")


def test_enqueue_update_job_rejects_when_channel_has_active_job():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    store.seed_job(_make_job(channel_id=channel.id, status="running"))

    with pytest.raises(ActiveJobExistsError):
        enqueue_update_job(store, channel.id, limit=20, sort="recent")


def test_enqueue_update_job_derives_channel_input_from_handle():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)

    job = enqueue_update_job(store, channel.id, limit=20, sort="recent")

    assert job.channel_id == channel.id
    assert job.payload["channel_input"] == channel.handle
    assert job.payload["kind"] == "update"


# --- retry_job ---------------------------------------------------------------------------


def test_retry_job_requeues_a_failed_job_and_clears_its_error():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    failed_job = _make_job(channel_id=channel.id, status="failed")
    store.seed_job(failed_job)

    retried = retry_job(store, failed_job.id)

    assert retried.status == "queued"
    assert retried.error is None
    assert retried.started_at is None


@pytest.mark.parametrize("status", ["done", "cancelled"])
def test_retry_job_rejects_a_finished_job_that_did_not_fail(status):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_job(channel_id=channel.id, status=status)
    store.seed_job(job)

    with pytest.raises(JobNotRetryableError):
        retry_job(store, job.id)


@pytest.mark.parametrize("status", ["queued", "running"])
def test_retry_job_rejects_a_job_that_is_still_active(status):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_job(channel_id=channel.id, status=status)
    store.seed_job(job)

    with pytest.raises(ActiveJobExistsError):
        retry_job(store, job.id)


def test_retry_job_rejects_when_a_newer_job_for_the_channel_is_active():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    old_failed = _make_job(channel_id=channel.id, status="failed")
    store.seed_job(old_failed)
    store.seed_job(_make_job(channel_id=channel.id, status="running"))

    with pytest.raises(ActiveJobExistsError):
        retry_job(store, old_failed.id)


# --- cancel_job --------------------------------------------------------------------------


def test_cancel_job_cancels_a_queued_job():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_job(channel_id=channel.id, status="queued")
    store.seed_job(job)

    cancelled = cancel_job(store, job.id)

    assert cancelled.status == "cancelled"
    assert cancelled.finished_at is not None


@pytest.mark.parametrize("status", ["running", "done", "failed", "cancelled"])
def test_cancel_job_rejects_a_job_that_is_not_queued(status):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_job(channel_id=channel.id, status=status)
    store.seed_job(job)

    with pytest.raises(JobNotCancellableError):
        cancel_job(store, job.id)
