"""Builds metadata-only entries for the community `registry/channels.json` — never
includes transcript content, only enough for someone else to reproduce the bundle
themselves via `aac dataset build`."""

from datetime import UTC, datetime

from core.dataset.manifest import Manifest


def build_registry_entry(manifest: Manifest) -> dict:
    return {
        "handle": manifest.channel.handle,
        "yt_channel_id": manifest.channel.yt_channel_id,
        "title": manifest.channel.title,
        "suggested_config": {
            "limit": manifest.limit,
            "sort": manifest.sort,
            "chunking": {
                "target_tokens": manifest.chunking.target_tokens,
                "overlap_ratio": manifest.chunking.overlap_ratio,
            },
        },
        "video_count": manifest.video_count,
        "chunk_count": manifest.chunk_count,
        "last_verified": datetime.now(UTC).date().isoformat(),
        "contributor": manifest.contributor,
    }
