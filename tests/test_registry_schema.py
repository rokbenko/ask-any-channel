"""registry/channels.json must always validate against registry/schema.json — the same schema
scripts/validate_registry.py runs in CI (.github/workflows/registry.yml)."""

import json
from pathlib import Path

import jsonschema
import pytest

from core.dataset.manifest import ChannelMeta, ChunkingParams, EmbeddingMeta, Manifest
from core.dataset.registry import build_registry_entry
from scripts.validate_registry import validate

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA = json.loads((REPO_ROOT / "registry" / "schema.json").read_text(encoding="utf-8"))


def _manifest(*, handle: str | None = "@Example", limit: int | None = 50) -> Manifest:
    return Manifest(
        schema_version=1,
        channel=ChannelMeta(
            yt_channel_id="UC" + "x" * 22, handle=handle, title="Example", thumbnail_url=None
        ),
        snapshot_date="2026-08-18T00:00:00+00:00",
        chunking=ChunkingParams(target_tokens=400, overlap_ratio=0.15, encoding="cl100k_base"),
        embedding=EmbeddingMeta(model="text-embedding-3-small", dims=1536),
        tool_version="0.1.0",
        contributor="tester",
        video_count=10,
        chunk_count=120,
        limit=limit,
        sort="recent",
    )


def test_real_registry_file_is_valid() -> None:
    validate(REPO_ROOT / "registry" / "channels.json")


def test_broken_entry_is_rejected() -> None:
    with pytest.raises(jsonschema.ValidationError):
        validate(FIXTURES / "registry_broken.json")


def test_real_build_registry_entry_output_matches_schema() -> None:
    # Runs the actual emitter, so a new field added to build_registry_entry() without a schema
    # bump (additionalProperties: false) fails here — not as a CI surprise on a stranger's PR.
    entry = build_registry_entry(_manifest())

    jsonschema.validate(instance=[entry], schema=SCHEMA)


def test_entry_with_no_handle_and_no_limit_still_validates() -> None:
    # Both are legitimately null: a UC…-only channel, and a build with no --limit.
    entry = build_registry_entry(_manifest(handle=None, limit=None))

    jsonschema.validate(instance=[entry], schema=SCHEMA)


def test_schema_rejects_an_unknown_top_level_field() -> None:
    entry = build_registry_entry(_manifest())
    entry["embedding_model"] = "text-embedding-3-small"

    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(instance=[entry], schema=SCHEMA)
