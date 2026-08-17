"""Fetches and caches YouTube captions as VTT, preferring manual English subs over
auto-generated ones. Idempotent: an existing cache file is reused without hitting the
network again."""

import shutil
import time
from pathlib import Path

import yt_dlp

from core.constants import YTDLP_BASE_OPTS

RETRY_BASE_DELAY_S = 2
MAX_RETRIES = 3

_SUB_LANGS = ["en", "en-US", "en-orig"]


class JsRuntimeMissingError(RuntimeError):
    pass


def ensure_js_runtime() -> None:
    """yt-dlp needs a JS runtime for full YouTube extraction. Without one it does NOT error
    — it silently returns empty caption lists, so every video would be recorded as
    `no_captions` and a build would "succeed" with zero chunks. Fail fast instead."""
    if shutil.which("node") is None:
        raise JsRuntimeMissingError(
            "Node.js is required (yt-dlp uses it to extract YouTube captions) but `node` "
            "was not found on PATH. Install Node.js, or run ingestion via the Docker worker "
            "which bundles it."
        )


def fetch_captions(yt_video_id: str, *, cache_dir: Path) -> Path | None:
    """Returns the cached VTT path, or None if the video genuinely has no captions in any
    of the requested languages. Raises on repeated transient failures (network, rate
    limiting) after exhausting retries — callers should treat that as a hard failure, not
    a `no_captions` video."""
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{yt_video_id}.vtt"

    if cache_path.exists():
        return cache_path

    video_url = f"https://www.youtube.com/watch?v={yt_video_id}"
    ydl_opts = {
        **YTDLP_BASE_OPTS,
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": _SUB_LANGS,
        "subtitlesformat": "vtt",
        "outtmpl": str(cache_dir / "%(id)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
    }

    last_error: Exception | None = None
    for attempt in range(MAX_RETRIES + 1):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([video_url])
            last_error = None
            break
        except yt_dlp.utils.DownloadError as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                time.sleep(RETRY_BASE_DELAY_S * (2**attempt))

    if last_error is not None:
        raise last_error

    candidates = list(cache_dir.glob(f"{yt_video_id}*.vtt"))
    chosen = _pick_best_candidate(candidates, yt_video_id)
    if chosen is None:
        return None

    for other in candidates:
        if other != chosen:
            other.unlink(missing_ok=True)

    if chosen != cache_path:
        chosen.replace(cache_path)

    return cache_path


def _pick_best_candidate(candidates: list[Path], yt_video_id: str) -> Path | None:
    """yt-dlp can write more than one language variant for a single video (e.g. both `en`
    and `en-orig`). Prefer them in _SUB_LANGS order rather than picking arbitrarily —
    otherwise which transcript gets used depends on filesystem glob ordering."""
    by_lang = {
        c.stem.removeprefix(f"{yt_video_id}."): c for c in candidates if c.stem != yt_video_id
    }
    for lang in _SUB_LANGS:
        if lang in by_lang:
            return by_lang[lang]
    return candidates[0] if candidates else None
