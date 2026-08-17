"""Dataset bundle manifest — schema-versioned metadata describing how a bundle was built.

The manifest is what makes a bundle self-describing and reproducible: chunking params,
embedding model/dims (or None for --skip-embeddings bundles), and enough channel/tool
provenance that `dataset load`/`validate`/`registry entry` never need to guess.
"""

import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

MANIFEST_FILENAME = "manifest.json"


@dataclass
class ChannelMeta:
    yt_channel_id: str
    handle: str | None
    title: str | None
    thumbnail_url: str | None


@dataclass
class ChunkingParams:
    target_tokens: int
    overlap_ratio: float
    encoding: str


@dataclass
class EmbeddingMeta:
    model: str
    dims: int


@dataclass
class Manifest:
    schema_version: int
    channel: ChannelMeta
    snapshot_date: str  # ISO 8601
    chunking: ChunkingParams
    embedding: EmbeddingMeta | None
    tool_version: str
    contributor: str
    video_count: int
    chunk_count: int
    limit: int | None
    sort: str

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Manifest":
        return cls(
            schema_version=data["schema_version"],
            channel=ChannelMeta(**data["channel"]),
            snapshot_date=data["snapshot_date"],
            chunking=ChunkingParams(**data["chunking"]),
            embedding=EmbeddingMeta(**data["embedding"]) if data.get("embedding") else None,
            tool_version=data["tool_version"],
            contributor=data["contributor"],
            video_count=data["video_count"],
            chunk_count=data["chunk_count"],
            limit=data.get("limit"),
            sort=data.get("sort", "recent"),
        )


def write_manifest(out_dir: Path, manifest: Manifest) -> Path:
    path = Path(out_dir) / MANIFEST_FILENAME
    path.write_text(json.dumps(manifest.to_dict(), indent=2), encoding="utf-8")
    return path


def read_manifest(out_dir: Path) -> Manifest:
    path = Path(out_dir) / MANIFEST_FILENAME
    data = json.loads(path.read_text(encoding="utf-8"))
    return Manifest.from_dict(data)


def get_contributor() -> str:
    """Best-effort contributor identity from git config; never blocks a build on failure."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return "anonymous"
    name = result.stdout.strip()
    return name if result.returncode == 0 and name else "anonymous"
