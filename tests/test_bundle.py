import pytest

from core.dataset.bundle import (
    ChunkRecord,
    VideoRecord,
    bundle_exists,
    read_bundle,
    write_bundle,
)
from core.dataset.manifest import ChannelMeta, ChunkingParams, EmbeddingMeta, Manifest


def _make_manifest(*, with_embedding: bool = True) -> Manifest:
    return Manifest(
        schema_version=1,
        channel=ChannelMeta(
            yt_channel_id="UC_test",
            handle="@test",
            title="Test Channel",
            thumbnail_url=None,
        ),
        snapshot_date="2026-08-16T00:00:00+00:00",
        chunking=ChunkingParams(target_tokens=400, overlap_ratio=0.15, encoding="cl100k_base"),
        embedding=EmbeddingMeta(model="text-embedding-3-small", dims=4) if with_embedding else None,
        tool_version="0.1.0",
        contributor="tester",
        video_count=1,
        chunk_count=2,
        limit=None,
        sort="recent",
    )


def _make_videos() -> list[VideoRecord]:
    return [
        VideoRecord(
            yt_video_id="vid001",
            title="Video One",
            duration_s=120,
            view_count=1000,
            published_at="2026-01-01T00:00:00+00:00",
            status="embedded",
        )
    ]


def _make_chunks() -> list[ChunkRecord]:
    return [
        ChunkRecord(
            yt_video_id="vid001",
            idx=0,
            text="hello world",
            t_start_s=0.0,
            t_end_s=2.0,
            token_count=2,
        ),
        ChunkRecord(
            yt_video_id="vid001",
            idx=1,
            text="goodbye world",
            t_start_s=2.0,
            t_end_s=4.0,
            token_count=2,
        ),
    ]


def test_bundle_exists_false_for_missing_dir(tmp_path):
    assert bundle_exists(tmp_path / "nope") is False


def test_write_then_read_bundle_round_trips_with_embeddings(tmp_path):
    out_dir = tmp_path / "test-channel"
    manifest = _make_manifest(with_embedding=True)
    videos = _make_videos()
    chunks = _make_chunks()
    embeddings = {
        ("vid001", 0): [0.1, 0.2, 0.3, 0.4],
        ("vid001", 1): [0.5, 0.6, 0.7, 0.8],
    }

    write_bundle(out_dir, manifest, videos, chunks, embeddings)

    assert bundle_exists(out_dir) is True

    bundle = read_bundle(out_dir)
    assert bundle.manifest.channel.yt_channel_id == "UC_test"
    assert bundle.manifest.embedding.model == "text-embedding-3-small"
    assert bundle.videos == videos
    assert bundle.chunks == chunks
    assert bundle.embeddings is not None
    # float32 round-trip loses precision vs. the original float64 literals, so approx-compare
    assert bundle.embeddings[("vid001", 0)] == pytest.approx([0.1, 0.2, 0.3, 0.4], rel=1e-6)
    assert bundle.embeddings[("vid001", 1)] == pytest.approx([0.5, 0.6, 0.7, 0.8], rel=1e-6)


def test_write_then_read_bundle_without_embeddings(tmp_path):
    out_dir = tmp_path / "skip-embed-channel"
    manifest = _make_manifest(with_embedding=False)
    videos = _make_videos()
    chunks = _make_chunks()

    write_bundle(out_dir, manifest, videos, chunks, embeddings=None)

    bundle = read_bundle(out_dir)
    assert bundle.manifest.embedding is None
    assert bundle.embeddings is None
    assert bundle.chunks == chunks
    assert not (out_dir / "embeddings-text-embedding-3-small.parquet").exists()
