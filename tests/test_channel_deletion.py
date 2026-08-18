from datetime import UTC, datetime
from uuid import uuid4

from core.models import Channel, Video
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


def _make_video(channel_id) -> Video:
    return Video(
        id=uuid4(),
        channel_id=channel_id,
        yt_video_id="abcdefghijk",
        title="A video",
        published_at=None,
        duration_s=120,
        view_count=10,
        status="embedded",
        error=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


def test_delete_channel_removes_videos_chats_and_messages():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    store.videos[uuid4()] = _make_video(channel.id)
    chat = store.create_chat(channel_id=channel.id)
    store.add_message(chat_id=chat.id, role="user", content="hi")
    store.add_message(chat_id=chat.id, role="assistant", content="hello")

    store.delete_channel(channel.id)

    assert store.get_channel(channel.id) is None
    assert all(v.channel_id != channel.id for v in store.videos.values())
    assert chat.id not in store.chats
    assert store.list_messages(chat.id) == []


def test_delete_channel_preserves_usage_events_but_nulls_their_foreign_keys():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    chat = store.create_chat(channel_id=channel.id)
    event = store.record_usage_event(
        channel_id=channel.id,
        chat_id=chat.id,
        model="gpt-4.1-mini",
        tokens_in=100,
        tokens_out=50,
        est_cost_usd=0.001,
    )

    store.delete_channel(channel.id)

    assert len(store.usage_events) == 1
    surviving = store.usage_events[0]
    assert surviving.id == event.id
    assert surviving.channel_id is None
    assert surviving.chat_id is None
    assert surviving.tokens_in == 100  # the record itself is untouched, only the FKs are nulled


def test_delete_channel_does_not_touch_usage_events_from_other_channels():
    channel = _make_channel()
    other_channel_id = uuid4()
    store = FakeVectorStore(channel=channel)
    other_event = store.record_usage_event(
        channel_id=other_channel_id,
        chat_id=None,
        model="gpt-4.1-mini",
        tokens_in=1,
        tokens_out=1,
        est_cost_usd=0.0,
    )

    store.delete_channel(channel.id)

    assert store.usage_events == [other_event]


def test_delete_channel_removes_jobs_for_that_channel():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    job = store.create_job(channel_id=channel.id, payload={"channel_input": "@some"})

    store.delete_channel(channel.id)

    assert job.id not in store.jobs
    assert store.get_latest_job_for_channel(channel.id) is None
