"""Validates a dataset bundle's structural integrity: manifest completeness, consistent
row counts, embedding dimensions, and timestamp sanity. Used by `aac dataset validate` and
as the pre-flight check before `dataset load` decides whether bundled embeddings are usable."""

from pathlib import Path

from core.constants import DATASET_SCHEMA_VERSION
from core.dataset.bundle import read_bundle

# yt-dlp's flat-playlist duration_s is known-imprecise (same reason published_at comes back
# None — flat extraction skips the full per-video metadata fetch), and real captions can
# legitimately run well past it (e.g. an outro card). This only needs to catch genuine
# corruption (wrong units, garbage data), not realistic metadata slop, so the tolerance is
# generous: 50% plus a flat 30s buffer.
_DURATION_SLACK_RATIO = 1.5
_DURATION_SLACK_BASE_S = 30.0


def validate_bundle(path: Path) -> list[str]:
    """Returns a list of human-readable errors; an empty list means the bundle is valid."""
    path = Path(path)

    try:
        bundle = read_bundle(path)
    except Exception as exc:
        return [f"Could not read bundle at {path}: {exc}"]

    errors: list[str] = []
    manifest = bundle.manifest

    if manifest.schema_version != DATASET_SCHEMA_VERSION:
        errors.append(
            f"Unrecognized schema_version {manifest.schema_version} "
            f"(this tool understands version {DATASET_SCHEMA_VERSION})"
        )

    if manifest.video_count != len(bundle.videos):
        errors.append(
            f"manifest.video_count ({manifest.video_count}) != actual videos.jsonl rows "
            f"({len(bundle.videos)})"
        )

    if manifest.chunk_count != len(bundle.chunks):
        errors.append(
            f"manifest.chunk_count ({manifest.chunk_count}) != actual chunks.parquet rows "
            f"({len(bundle.chunks)})"
        )

    seen_ids: set[tuple[str, int]] = set()
    duplicates: set[tuple[str, int]] = set()
    for c in bundle.chunks:
        key = (c.yt_video_id, c.idx)
        if key in seen_ids:
            duplicates.add(key)
        seen_ids.add(key)
    if duplicates:
        errors.append(f"Duplicate (yt_video_id, idx) pairs in chunks.parquet: {sorted(duplicates)}")

    durations = {v.yt_video_id: v.duration_s for v in bundle.videos}
    for c in bundle.chunks:
        if c.t_start_s > c.t_end_s:
            errors.append(f"Chunk {c.yt_video_id}#{c.idx}: t_start_s > t_end_s")
        duration = durations.get(c.yt_video_id)
        if duration is not None:
            tolerance = duration * _DURATION_SLACK_RATIO + _DURATION_SLACK_BASE_S
            if c.t_end_s > tolerance:
                errors.append(
                    f"Chunk {c.yt_video_id}#{c.idx}: t_end_s ({c.t_end_s}) exceeds video "
                    f"duration ({duration}s)"
                )

    if manifest.embedding is not None:
        if bundle.embeddings is None:
            errors.append(
                f"manifest declares embedding model {manifest.embedding.model!r} but no "
                "matching embeddings parquet file was found"
            )
        else:
            embedding_keys = set(bundle.embeddings.keys())
            if embedding_keys != seen_ids:
                errors.append(
                    "Embeddings do not exactly match chunks: "
                    f"{len(embedding_keys - seen_ids)} extra, "
                    f"{len(seen_ids - embedding_keys)} missing"
                )
            for key, vec in bundle.embeddings.items():
                if len(vec) != manifest.embedding.dims:
                    errors.append(
                        f"Embedding for {key[0]}#{key[1]} has dim {len(vec)}, "
                        f"expected {manifest.embedding.dims}"
                    )
                    break

    return errors
