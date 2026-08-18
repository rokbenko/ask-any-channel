import threading
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.constants import MAX_JOB_ATTEMPTS
from core.models import Channel, IngestJob
from core.worker import daemon
from core.worker.daemon import poll_and_run
from tests.fakes import FakeVectorStore


def _make_channel() -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle="@some",
        title="Some Channel",
        thumbnail_url=None,
        branding={},
        created_at=datetime.now(UTC),
    )


def _make_running_job(*, channel_id, heartbeat_age_s: float, attempts: int = 0) -> IngestJob:
    now = datetime.now(UTC)
    return IngestJob(
        id=uuid4(),
        channel_id=channel_id,
        payload={"channel_input": "@some", "kind": "ingest"},
        status="running",
        progress={"stage": "building", "done": 3, "total": 10, "attempts": attempts},
        error=None,
        created_at=now,
        started_at=now,
        finished_at=None,
        heartbeat_at=now - timedelta(seconds=heartbeat_age_s),
    )


def _run_one_iteration(store, monkeypatch, *, run_impl):
    """Drive poll_and_run for exactly one claimed job: the stub run_* sets the stop event."""
    stop = threading.Event()

    def _run(store_, credentials, job, *, out_dir):
        try:
            run_impl(store_, job)
        finally:
            stop.set()

    monkeypatch.setattr(daemon, "run_ingest_job", _run)
    monkeypatch.setattr(daemon, "run_update_job", _run)
    poll_and_run(store, credentials=None, poll_interval_s=0.01, stop_event=stop)


def test_daemon_marks_a_job_failed_when_its_run_raises_and_keeps_going(monkeypatch):
    store = FakeVectorStore()
    job = store.create_job(channel_id=None, payload={"channel_input": "@nope", "kind": "ingest"})

    def _explode(store_, job_):
        raise RuntimeError("HTTP Error 404: Not Found")

    _run_one_iteration(store, monkeypatch, run_impl=_explode)

    final = store.get_job(job.id)
    assert final.status == "failed"
    assert "404" in final.error


def test_daemon_dispatches_update_jobs_to_run_update_job(monkeypatch):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = store.create_job(
        channel_id=channel.id, payload={"channel_input": "@some", "kind": "update"}
    )
    seen: list[str] = []
    stop = threading.Event()

    def _ingest(store_, credentials, job_, *, out_dir):
        seen.append("ingest")
        stop.set()

    def _update(store_, credentials, job_, *, out_dir):
        seen.append(f"update:{out_dir.as_posix()}")
        store_.update_job(job_.id, status="done")
        stop.set()

    monkeypatch.setattr(daemon, "run_ingest_job", _ingest)
    monkeypatch.setattr(daemon, "run_update_job", _update)
    poll_and_run(store, credentials=None, poll_interval_s=0.01, stop_event=stop)

    assert seen == [f"update:datasets/some/_updates/{job.id}"]


def test_daemon_exits_promptly_when_stop_is_set_and_queue_is_empty():
    store = FakeVectorStore()
    stop = threading.Event()
    stop.set()

    poll_and_run(store, credentials=None, poll_interval_s=60, stop_event=stop)  # must return


def test_stale_running_job_is_requeued_with_attempts_incremented():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_running_job(channel_id=channel.id, heartbeat_age_s=9999)
    store.seed_job(job)

    requeued = store.reclaim_stale_jobs(600, max_attempts=MAX_JOB_ATTEMPTS)

    assert requeued == [job.id]
    after = store.get_job(job.id)
    assert after.status == "queued"
    assert after.progress["attempts"] == 1
    assert after.progress["stage"] == "reclaimed"


def test_fresh_running_job_is_left_alone_by_reclaim():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_running_job(channel_id=channel.id, heartbeat_age_s=5)
    store.seed_job(job)

    assert store.reclaim_stale_jobs(600, max_attempts=MAX_JOB_ATTEMPTS) == []
    assert store.get_job(job.id).status == "running"


def test_poison_job_is_failed_not_requeued_after_max_attempts():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = _make_running_job(
        channel_id=channel.id, heartbeat_age_s=9999, attempts=MAX_JOB_ATTEMPTS - 1
    )
    store.seed_job(job)

    requeued = store.reclaim_stale_jobs(600, max_attempts=MAX_JOB_ATTEMPTS)

    assert requeued == []
    after = store.get_job(job.id)
    assert after.status == "failed"
    assert "died" in after.error
