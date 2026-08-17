"""The dataset bundle: the canonical, portable interchange format for an ingested channel.
Everything downstream (Postgres load, validate, registry entries) reads what this writes.
Bundles are local-only artifacts — see .gitignore's `datasets/` entry; transcript content
is never committed to the repo."""

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from core.constants import DATASETS_DIR
from core.dataset.manifest import Manifest, read_manifest, write_manifest

VIDEOS_FILENAME = "videos.jsonl"
CHUNKS_FILENAME = "chunks.parquet"

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_-]+")
_TAB_SUFFIX_RE = re.compile(r"/(videos|streams|shorts|playlists|community|featured)$")


def embeddings_filename(model: str) -> str:
    return f"embeddings-{model.replace('/', '-')}.parquet"


def default_bundle_dir(channel_input: str) -> Path:
    """Derives a stable datasets/{slug} path directly from the raw CLI input string, with
    no network call — so a repeated `dataset build` can check bundle_exists() and no-op
    before doing any work at all."""
    raw = channel_input.strip().rstrip("/")
    raw = re.sub(r"^https?://(www\.)?youtube\.com/", "", raw)
    raw = _TAB_SUFFIX_RE.sub("", raw)
    raw = raw.lstrip("@")
    slug = _SLUG_RE.sub("-", raw).strip("-") or "channel"
    return Path(DATASETS_DIR) / slug


@dataclass
class VideoRecord:
    yt_video_id: str
    title: str | None
    duration_s: int | None
    view_count: int | None
    published_at: str | None  # ISO 8601, or None
    status: str


@dataclass
class ChunkRecord:
    yt_video_id: str
    idx: int
    text: str
    t_start_s: float
    t_end_s: float
    token_count: int


@dataclass
class Bundle:
    manifest: Manifest
    videos: list[VideoRecord]
    chunks: list[ChunkRecord]
    embeddings: dict[tuple[str, int], list[float]] | None  # (yt_video_id, idx) -> vector


def bundle_exists(out_dir: Path) -> bool:
    """True if a complete bundle (manifest + videos + chunks) is already present at out_dir."""
    out_dir = Path(out_dir)
    return (
        (out_dir / "manifest.json").exists()
        and (out_dir / VIDEOS_FILENAME).exists()
        and (out_dir / CHUNKS_FILENAME).exists()
    )


def write_bundle(
    out_dir: Path,
    manifest: Manifest,
    videos: list[VideoRecord],
    chunks: list[ChunkRecord],
    embeddings: dict[tuple[str, int], list[float]] | None,
) -> Path:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with (out_dir / VIDEOS_FILENAME).open("w", encoding="utf-8") as f:
        for v in videos:
            f.write(json.dumps(asdict(v)) + "\n")

    chunks_table = pa.table(
        {
            "yt_video_id": [c.yt_video_id for c in chunks],
            "idx": [c.idx for c in chunks],
            "text": [c.text for c in chunks],
            "t_start_s": [c.t_start_s for c in chunks],
            "t_end_s": [c.t_end_s for c in chunks],
            "token_count": [c.token_count for c in chunks],
        }
    )
    pq.write_table(chunks_table, out_dir / CHUNKS_FILENAME)

    if embeddings and manifest.embedding is not None:
        keys = list(embeddings.keys())
        embeddings_table = pa.table(
            {
                "yt_video_id": [k[0] for k in keys],
                "idx": [k[1] for k in keys],
                "embedding": pa.array([embeddings[k] for k in keys], type=pa.list_(pa.float32())),
            }
        )
        pq.write_table(embeddings_table, out_dir / embeddings_filename(manifest.embedding.model))

    write_manifest(out_dir, manifest)
    return out_dir


def read_bundle(path: Path) -> Bundle:
    path = Path(path)
    manifest = read_manifest(path)

    videos: list[VideoRecord] = []
    videos_path = path / VIDEOS_FILENAME
    if videos_path.exists():
        for line in videos_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                videos.append(VideoRecord(**json.loads(line)))

    chunks: list[ChunkRecord] = []
    chunks_path = path / CHUNKS_FILENAME
    if chunks_path.exists():
        chunks = [ChunkRecord(**row) for row in pq.read_table(chunks_path).to_pylist()]

    embeddings: dict[tuple[str, int], list[float]] | None = None
    if manifest.embedding is not None:
        emb_path = path / embeddings_filename(manifest.embedding.model)
        if emb_path.exists():
            embeddings = {
                (row["yt_video_id"], row["idx"]): row["embedding"]
                for row in pq.read_table(emb_path).to_pylist()
            }

    return Bundle(manifest=manifest, videos=videos, chunks=chunks, embeddings=embeddings)
