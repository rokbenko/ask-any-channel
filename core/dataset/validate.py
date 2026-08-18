"""Validates a dataset bundle's structural integrity: manifest completeness, consistent
row counts, embedding dimensions, and timestamp sanity. Used by `aac dataset validate` and
as the pre-flight check before `dataset load` decides whether bundled embeddings are usable."""

import re
from pathlib import Path
from urllib.parse import urlsplit

from core.chat.suggestions import sanitize_question
from core.constants import (
    DATASET_SCHEMA_VERSION,
    SUGGESTED_QUESTION_MAX_CHARS,
    SUGGESTED_QUESTIONS_MAX_COUNT,
)
from core.dataset.bundle import read_bundle

# yt-dlp's flat-playlist duration_s is known-imprecise (same reason published_at comes back
# None — flat extraction skips the full per-video metadata fetch), and real captions can
# legitimately run well past it (e.g. an outro card). This only needs to catch genuine
# corruption (wrong units, garbage data), not realistic metadata slop, so the tolerance is
# generous: 50% plus a flat 30s buffer.
_DURATION_SLACK_RATIO = 1.5
_DURATION_SLACK_BASE_S = 30.0

# Bundles are untrusted input (they can come from strangers via the registry model), and these
# ids/titles end up in citation URLs, markdown links, and widget labels in the chat UI. Pin the
# shapes YouTube actually uses so a crafted value can't smuggle markdown or URL syntax through:
# video ids are exactly 11 chars of [A-Za-z0-9_-]; channel ids are "UC" + 22 of the same.
_YT_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_YT_CHANNEL_ID_RE = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_MAX_TITLE_CHARS = 300
# thumbnail_url is rendered with st.image on the Channels page; only YouTube's CDNs, over TLS.
_THUMBNAIL_HOSTS = frozenset({"i.ytimg.com", "yt3.googleusercontent.com", "yt3.ggpht.com"})


def _thumbnail_url_error(url: str | None) -> str | None:
    if url is None:
        return None
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in _THUMBNAIL_HOSTS:
        return (
            f"manifest.channel.thumbnail_url {url!r} must be an https URL on one of "
            f"{sorted(_THUMBNAIL_HOSTS)}"
        )
    return None


def _suggested_questions_errors(raw: object) -> list[str]:
    """Rejects (rather than silently cleaning) so a tampered bundle is visible at `validate`;
    the loader still re-sanitizes as belt-and-braces."""
    if not isinstance(raw, list):
        return ["manifest.suggested_questions must be a list of strings"]
    errors: list[str] = []
    if len(raw) > SUGGESTED_QUESTIONS_MAX_COUNT:
        errors.append(
            f"manifest.suggested_questions has {len(raw)} entries (max "
            f"{SUGGESTED_QUESTIONS_MAX_COUNT})"
        )
    for i, q in enumerate(raw):
        if not isinstance(q, str):
            errors.append(f"manifest.suggested_questions[{i}] is not a string")
        elif len(q) > SUGGESTED_QUESTION_MAX_CHARS or "\n" in q or sanitize_question(q) != q:
            errors.append(
                f"manifest.suggested_questions[{i}] must be a single line of at most "
                f"{SUGGESTED_QUESTION_MAX_CHARS} plain characters (no markdown/HTML syntax)"
            )
    return errors


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

    if not _YT_CHANNEL_ID_RE.match(manifest.channel.yt_channel_id):
        errors.append(
            f"manifest.channel.yt_channel_id {manifest.channel.yt_channel_id!r} is not a "
            "YouTube channel id (expected 'UC' + 22 chars of [A-Za-z0-9_-])"
        )
    if manifest.channel.title and len(manifest.channel.title) > _MAX_TITLE_CHARS:
        errors.append(f"manifest.channel.title exceeds {_MAX_TITLE_CHARS} characters")
    if thumb_error := _thumbnail_url_error(manifest.channel.thumbnail_url):
        errors.append(thumb_error)
    errors.extend(_suggested_questions_errors(manifest.suggested_questions))

    bad_video_ids = sorted(
        {v.yt_video_id for v in bundle.videos if not _YT_VIDEO_ID_RE.match(v.yt_video_id)}
        | {c.yt_video_id for c in bundle.chunks if not _YT_VIDEO_ID_RE.match(c.yt_video_id)}
    )
    if bad_video_ids:
        errors.append(
            "Malformed yt_video_id values (expected 11 chars of [A-Za-z0-9_-]): "
            f"{bad_video_ids[:5]}"
        )
    long_titles = [
        v.yt_video_id for v in bundle.videos if v.title and len(v.title) > _MAX_TITLE_CHARS
    ]
    if long_titles:
        errors.append(f"Video titles exceed {_MAX_TITLE_CHARS} characters: {long_titles[:5]}")

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
