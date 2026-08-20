from datetime import UTC, datetime, timedelta
from uuid import uuid4

from core.ingest.jobs import ActiveJobExistsError
from core.models import Channel
from core.worker import scheduler
from core.worker.scheduler import is_due, jitter_for, run_auto_ingest_tick
from tests.fakes import FakeVectorStore

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_channel(*, auto_update=True, last_checked_at=None, handle="@some") -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle=handle,
        title="Some Channel",
        thumbnail_url=None,
        branding={},
        created_at=NOW,
        auto_update=auto_update,
        last_checked_at=last_checked_at,
    )


# --- jitter_for / is_due -----------------------------------------------------------------


def test_jitter_for_is_deterministic_and_stable_across_calls():
    channel_id = uuid4()
    assert jitter_for(channel_id, 900) == jitter_for(channel_id, 900)


def test_jitter_for_zero_window_is_always_zero():
    assert jitter_for(uuid4(), 0) == 0.0


def test_jitter_for_stays_within_the_window():
    for _ in range(20):
        assert 0 <= jitter_for(uuid4(), 900) < 900


def test_is_due_when_never_checked():
    assert is_due(last_checked_at=None, now=NOW, interval_s=3600, jitter_s=0, channel_id=uuid4())


def test_is_due_false_within_the_interval():
    channel_id = uuid4()
    last_checked = NOW - timedelta(seconds=100)
    assert not is_due(
        last_checked_at=last_checked, now=NOW, interval_s=3600, jitter_s=0, channel_id=channel_id
    )


def test_is_due_true_past_the_interval_with_no_jitter():
    channel_id = uuid4()
    last_checked = NOW - timedelta(seconds=3601)
    assert is_due(
        last_checked_at=last_checked, now=NOW, interval_s=3600, jitter_s=0, channel_id=channel_id
    )


def test_is_due_respects_jitter_boundary():
    channel_id = uuid4()
    jitter = jitter_for(channel_id, 900)
    just_before = NOW - timedelta(seconds=3600 + jitter - 1)
    just_after = NOW - timedelta(seconds=3600 + jitter + 1)
    assert not is_due(
        last_checked_at=just_before, now=NOW, interval_s=3600, jitter_s=900, channel_id=channel_id
    )
    assert is_due(
        last_checked_at=just_after, now=NOW, interval_s=3600, jitter_s=900, channel_id=channel_id
    )


# --- run_auto_ingest_tick ------------------------------------------------------------------


def test_tick_does_nothing_when_interval_is_zero_globally():
    channel = _make_channel(last_checked_at=None)
    store = FakeVectorStore(channel=channel)

    enqueued = run_auto_ingest_tick(store, now=NOW, interval_hours=0)

    assert enqueued == []
    assert store.jobs == {}


def test_tick_does_nothing_for_a_channel_with_auto_update_off():
    channel = _make_channel(auto_update=False)
    store = FakeVectorStore(channel=channel)

    enqueued = run_auto_ingest_tick(store, now=NOW, interval_hours=1)

    assert enqueued == []
    assert store.jobs == {}


def test_tick_enqueues_exactly_one_update_job_for_a_due_channel():
    channel = _make_channel(last_checked_at=None)
    store = FakeVectorStore(channel=channel)

    enqueued = run_auto_ingest_tick(store, now=NOW, interval_hours=1)

    assert enqueued == [channel.id]
    jobs = list(store.jobs.values())
    assert len(jobs) == 1
    assert jobs[0].payload["kind"] == "update"
    assert store.get_channel(channel.id).last_checked_at == NOW


def test_tick_skips_a_channel_not_yet_due():
    channel = _make_channel(last_checked_at=NOW - timedelta(minutes=1))
    store = FakeVectorStore(channel=channel)

    enqueued = run_auto_ingest_tick(store, now=NOW, interval_hours=1)

    assert enqueued == []
    assert store.jobs == {}


def test_tick_marks_checked_but_does_not_double_enqueue_when_a_job_is_already_active():
    channel = _make_channel(last_checked_at=None)
    store = FakeVectorStore(channel=channel)
    store.create_job(channel_id=channel.id, payload={"channel_input": "@some", "kind": "update"})

    enqueued = run_auto_ingest_tick(store, now=NOW, interval_hours=1)

    assert enqueued == []  # the existing active job blocked a second enqueue
    assert store.get_channel(channel.id).last_checked_at == NOW  # still marked checked
    assert len(store.jobs) == 1  # no duplicate


def test_tick_next_call_within_interval_does_not_reenqueue_restart_scenario():
    """A fresh scheduler object (a plain function call has no persistent state of its own) is
    the daemon-restart scenario — is_due must derive purely from what's stored, not in-memory
    state, so restarting the worker doesn't re-trigger channels checked moments ago."""
    channel = _make_channel(last_checked_at=None)
    store = FakeVectorStore(channel=channel)

    first = run_auto_ingest_tick(store, now=NOW, interval_hours=1)
    assert first == [channel.id]

    second = run_auto_ingest_tick(store, now=NOW + timedelta(seconds=5), interval_hours=1)
    assert second == []


def test_tick_raises_active_job_exists_error_is_handled_not_propagated(monkeypatch):
    channel = _make_channel(last_checked_at=None)
    store = FakeVectorStore(channel=channel)

    def _explode(*a, **k):
        raise ActiveJobExistsError("boom")

    monkeypatch.setattr(scheduler, "enqueue_update_job", _explode)

    enqueued = run_auto_ingest_tick(store, now=NOW, interval_hours=1)

    assert enqueued == []
    assert store.get_channel(channel.id).last_checked_at == NOW
