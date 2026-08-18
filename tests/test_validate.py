import json

import pyarrow as pa
import pyarrow.parquet as pq

from core.dataset.bundle import ChunkRecord, VideoRecord, write_bundle
from core.dataset.manifest import ChannelMeta, ChunkingParams, EmbeddingMeta, Manifest
from core.dataset.validate import validate_bundle

# Real-shaped ids: validate_bundle rejects anything that isn't 11 / "UC"+22 url-safe chars.
_CHANNEL_ID = "UC" + "x" * 22
_VIDEO_ID = "abcdefghijk"


def _valid_manifest() -> Manifest:
    return Manifest(
        schema_version=1,
        channel=ChannelMeta(
            yt_channel_id=_CHANNEL_ID, handle="@test", title="Test Channel", thumbnail_url=None
        ),
        snapshot_date="2026-08-16T00:00:00+00:00",
        chunking=ChunkingParams(target_tokens=400, overlap_ratio=0.15, encoding="cl100k_base"),
        embedding=EmbeddingMeta(model="text-embedding-3-small", dims=4),
        tool_version="0.1.0",
        contributor="tester",
        video_count=1,
        chunk_count=2,
        limit=None,
        sort="recent",
    )


def _valid_videos() -> list[VideoRecord]:
    return [
        VideoRecord(
            yt_video_id=_VIDEO_ID,
            title="Video One",
            duration_s=10,
            view_count=1000,
            published_at=None,
            status="embedded",
        )
    ]


def _valid_chunks() -> list[ChunkRecord]:
    return [
        ChunkRecord(
            yt_video_id=_VIDEO_ID, idx=0, text="hello", t_start_s=0.0, t_end_s=2.0, token_count=1
        ),
        ChunkRecord(
            yt_video_id=_VIDEO_ID, idx=1, text="world", t_start_s=2.0, t_end_s=4.0, token_count=1
        ),
    ]


def _valid_embeddings() -> dict[tuple[str, int], list[float]]:
    return {
        (_VIDEO_ID, 0): [0.1, 0.2, 0.3, 0.4],
        (_VIDEO_ID, 1): [0.5, 0.6, 0.7, 0.8],
    }


def _build_valid_bundle(out_dir):
    write_bundle(out_dir, _valid_manifest(), _valid_videos(), _valid_chunks(), _valid_embeddings())
    return out_dir


def test_valid_bundle_has_no_errors(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "valid")
    assert validate_bundle(out_dir) == []


def test_missing_bundle_directory_is_a_single_clear_error(tmp_path):
    errors = validate_bundle(tmp_path / "does-not-exist")
    assert len(errors) == 1
    assert "Could not read bundle" in errors[0]


def test_duplicate_chunk_idx_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "dup-idx")

    corrupted = pa.table(
        {
            "yt_video_id": [_VIDEO_ID, _VIDEO_ID],
            "idx": [0, 0],  # duplicate idx
            "text": ["hello", "hello again"],
            "t_start_s": [0.0, 1.0],
            "t_end_s": [2.0, 3.0],
            "token_count": [1, 2],
        }
    )
    pq.write_table(corrupted, out_dir / "chunks.parquet")

    errors = validate_bundle(out_dir)
    assert any("Duplicate" in e for e in errors)


def test_manifest_chunk_count_mismatch_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "count-mismatch")

    manifest_path = out_dir / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["chunk_count"] = 999
    manifest_path.write_text(json.dumps(data), encoding="utf-8")

    errors = validate_bundle(out_dir)
    assert any("chunk_count" in e for e in errors)


def test_embedding_dimension_mismatch_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "dim-mismatch")

    wrong_dims = pa.table(
        {
            "yt_video_id": [_VIDEO_ID, _VIDEO_ID],
            "idx": [0, 1],
            "embedding": pa.array([[0.1, 0.2], [0.3, 0.4]], type=pa.list_(pa.float32())),
        }
    )
    pq.write_table(wrong_dims, out_dir / "embeddings-text-embedding-3-small.parquet")

    errors = validate_bundle(out_dir)
    assert any("dim" in e for e in errors)


def test_malformed_video_id_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "bad-id")

    # A crafted id that would break out of a markdown link if it reached the UI unchecked.
    injected = "x) [Sign in](https://evil.example"
    corrupted = pa.table(
        {
            "yt_video_id": [injected, injected],
            "idx": [0, 1],
            "text": ["hello", "world"],
            "t_start_s": [0.0, 2.0],
            "t_end_s": [2.0, 4.0],
            "token_count": [1, 1],
        }
    )
    pq.write_table(corrupted, out_dir / "chunks.parquet")

    errors = validate_bundle(out_dir)
    assert any("Malformed yt_video_id" in e for e in errors)


def test_chunk_timestamp_beyond_video_duration_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "bad-timestamp")

    corrupted = pa.table(
        {
            "yt_video_id": [_VIDEO_ID, _VIDEO_ID],
            "idx": [0, 1],
            "text": ["hello", "world"],
            "t_start_s": [0.0, 2.0],
            "t_end_s": [2.0, 500.0],  # video duration_s is 10
            "token_count": [1, 1],
        }
    )
    pq.write_table(corrupted, out_dir / "chunks.parquet")

    errors = validate_bundle(out_dir)
    assert any("exceeds video" in e for e in errors)


def _rewrite_manifest(out_dir, mutate) -> None:
    manifest_path = out_dir / "manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    mutate(data)
    manifest_path.write_text(json.dumps(data), encoding="utf-8")


def test_plain_suggested_questions_are_accepted(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "good-questions")
    _rewrite_manifest(
        out_dir, lambda d: d.update(suggested_questions=["What is this about?", "Who's it for?"])
    )

    assert validate_bundle(out_dir) == []


def test_bundle_without_suggested_questions_field_is_still_valid(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "old-manifest")
    _rewrite_manifest(out_dir, lambda d: d.pop("suggested_questions"))

    assert validate_bundle(out_dir) == []


def test_markdown_link_in_suggested_question_is_flagged(tmp_path):
    """Questions render as Streamlit button labels, which support Markdown links and images —
    a crafted bundle must not be able to plant a phishing chip on the owner's chat page."""
    out_dir = _build_valid_bundle(tmp_path / "phish-question")
    _rewrite_manifest(
        out_dir, lambda d: d.update(suggested_questions=["[Sign in](https://evil.example)"])
    )

    errors = validate_bundle(out_dir)
    assert any("suggested_questions[0]" in e for e in errors)


def test_too_many_or_too_long_suggested_questions_are_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "long-questions")
    _rewrite_manifest(out_dir, lambda d: d.update(suggested_questions=["x" * 500] + ["ok?"] * 20))

    errors = validate_bundle(out_dir)
    assert any("entries" in e for e in errors)
    assert any("suggested_questions[0]" in e for e in errors)


def test_non_list_suggested_questions_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "bad-type-questions")
    _rewrite_manifest(out_dir, lambda d: d.update(suggested_questions="not a list"))

    errors = validate_bundle(out_dir)
    assert any("must be a list" in e for e in errors)


def test_thumbnail_url_off_youtube_cdn_is_flagged(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "bad-thumb")
    _rewrite_manifest(
        out_dir, lambda d: d["channel"].update(thumbnail_url="http://attacker.example/t.png")
    )

    errors = validate_bundle(out_dir)
    assert any("thumbnail_url" in e for e in errors)


def test_youtube_cdn_thumbnail_url_is_accepted(tmp_path):
    out_dir = _build_valid_bundle(tmp_path / "good-thumb")
    _rewrite_manifest(
        out_dir,
        lambda d: d["channel"].update(thumbnail_url="https://yt3.googleusercontent.com/abc=s0"),
    )

    assert validate_bundle(out_dir) == []
