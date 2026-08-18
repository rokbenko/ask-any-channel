from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from core.ingest import pipeline
from core.ingest.pipeline import filter_new_videos, run_update_job
from core.models import Channel, Video
from tests.fakes import FakeVectorStore


def _make_video(yt_video_id: str, *, channel_id=None) -> Video:
    return Video(
        id=uuid4(),
        channel_id=channel_id or uuid4(),
        yt_video_id=yt_video_id,
        title=f"Video {yt_video_id}",
        published_at=None,
        duration_s=120,
        view_count=100,
        status="pending",
        error=None,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


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


def test_filter_new_videos_keeps_only_videos_not_yet_processed():
    processed = {"aaaaaaaaaaa", "bbbbbbbbbbb"}
    videos = [_make_video("aaaaaaaaaaa"), _make_video("ccccccccccc"), _make_video("ddddddddddd")]

    new_videos = filter_new_videos(processed, videos)

    assert [v.yt_video_id for v in new_videos] == ["ccccccccccc", "ddddddddddd"]


def test_filter_new_videos_returns_everything_when_nothing_is_processed():
    videos = [_make_video("aaaaaaaaaaa"), _make_video("bbbbbbbbbbb")]

    assert filter_new_videos(set(), videos) == videos


def test_filter_new_videos_returns_nothing_when_all_videos_are_processed():
    videos = [_make_video("aaaaaaaaaaa"), _make_video("bbbbbbbbbbb")]

    assert filter_new_videos({v.yt_video_id for v in videos}, videos) == []


# --- run_update_job orchestration (network + embedding stages stubbed) --------------------


def _stub_pipeline(monkeypatch, store, channel, listing_ids, *, built):
    """Replace the network/embedding stages so run_update_job can be driven with the fake:
    stage_list_and_upsert upserts metadata rows exactly like the real one does (that upsert-
    before-processing ordering is what the retry-safety test is about)."""

    def fake_list_and_upsert(store_, channel_input, *, limit, sort):
        videos = [
            store_.upsert_video(
                channel_id=channel.id,
                yt_video_id=vid,
                title=vid,
                published_at=None,
                duration_s=10,
                view_count=1,
            )
            for vid in listing_ids
        ]
        return channel, videos

    def fake_build_dataset(store_, provider, job, *, channel, videos, out_dir, **kwargs):
        built.extend(v.yt_video_id for v in videos)
        for v in videos:
            store_.set_video_status(v.id, "embedded")
        store_.update_job(
            job.id, status="done", progress={"done": len(videos), "total": len(videos)}
        )
        return out_dir

    monkeypatch.setattr(pipeline, "stage_list_and_upsert", fake_list_and_upsert)
    monkeypatch.setattr(pipeline, "build_dataset", fake_build_dataset)
    monkeypatch.setattr(pipeline, "OpenAIProvider", lambda credentials: object())
    monkeypatch.setattr(pipeline, "read_bundle", lambda out_dir: object())
    monkeypatch.setattr(pipeline, "load_bundle_into_store", lambda *a, **k: channel)


def test_run_update_job_processes_only_videos_not_yet_processed(monkeypatch, tmp_path):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    already = store.upsert_video(
        channel_id=channel.id,
        yt_video_id="aaaaaaaaaaa",
        title="a",
        published_at=None,
        duration_s=10,
        view_count=1,
    )
    store.set_video_status(already.id, "embedded")
    job = store.create_job(
        channel_id=channel.id, payload={"channel_input": "@some", "kind": "update"}
    )
    built: list[str] = []
    _stub_pipeline(monkeypatch, store, channel, ["aaaaaaaaaaa", "bbbbbbbbbbb"], built=built)

    result = run_update_job(store, credentials=None, job=job, out_dir=tmp_path / "u")

    assert built == ["bbbbbbbbbbb"]
    assert result.status == "done"


def test_run_update_job_is_retry_safe_after_metadata_rows_already_exist(monkeypatch, tmp_path):
    """A first attempt that died after stage_list_and_upsert leaves `pending` metadata rows.
    The retry must still process them — a "row exists" diff would see nothing new."""
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    # simulate the first attempt's upsert having happened, but no processing
    store.upsert_video(
        channel_id=channel.id,
        yt_video_id="bbbbbbbbbbb",
        title="b",
        published_at=None,
        duration_s=10,
        view_count=1,
    )
    job = store.create_job(
        channel_id=channel.id, payload={"channel_input": "@some", "kind": "update"}
    )
    built: list[str] = []
    _stub_pipeline(monkeypatch, store, channel, ["bbbbbbbbbbb"], built=built)

    run_update_job(store, credentials=None, job=job, out_dir=tmp_path / "u")

    assert built == ["bbbbbbbbbbb"]


def test_run_update_job_finishes_done_with_no_new_videos(monkeypatch, tmp_path):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    v = store.upsert_video(
        channel_id=channel.id,
        yt_video_id="aaaaaaaaaaa",
        title="a",
        published_at=None,
        duration_s=10,
        view_count=1,
    )
    store.set_video_status(v.id, "no_captions")
    job = store.create_job(
        channel_id=channel.id, payload={"channel_input": "@some", "kind": "update"}
    )
    built: list[str] = []
    _stub_pipeline(monkeypatch, store, channel, ["aaaaaaaaaaa"], built=built)

    result = run_update_job(store, credentials=None, job=job, out_dir=Path(tmp_path) / "u")

    assert built == []
    assert result.status == "done"
    assert result.progress["stage"] == "no-new-videos"


@pytest.mark.parametrize("terminal_status", ["embedded", "no_captions"])
def test_processed_video_ids_only_count_terminal_content_states(terminal_status):
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    done = store.upsert_video(
        channel_id=channel.id,
        yt_video_id="aaaaaaaaaaa",
        title="a",
        published_at=None,
        duration_s=1,
        view_count=1,
    )
    store.set_video_status(done.id, terminal_status)
    mid = store.upsert_video(
        channel_id=channel.id,
        yt_video_id="bbbbbbbbbbb",
        title="b",
        published_at=None,
        duration_s=1,
        view_count=1,
    )
    store.set_video_status(mid.id, "chunked")  # a crash left it mid-flight

    assert store.list_processed_video_ids(channel.id) == {"aaaaaaaaaaa"}
