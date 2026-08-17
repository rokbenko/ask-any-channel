"""Lists a channel's videos via yt-dlp's flat playlist extraction (no downloads)."""

import re
from dataclasses import dataclass
from datetime import UTC, datetime

import yt_dlp

from core.constants import YTDLP_BASE_OPTS

_CHANNEL_ID_RE = re.compile(r"^UC[\w-]{22}$")
_URL_RE = re.compile(r"^https?://")
_TAB_SUFFIXES = ("/videos", "/streams", "/shorts", "/playlists", "/community", "/featured")


def resolve_channel_input(raw: str) -> str:
    """Normalizes @handle / bare channel id / full URL into a channel *videos tab* URL.

    A bare channel URL (e.g. https://www.youtube.com/@SomeChannel) lists yt-dlp's flat
    playlist as the channel's TABS (Videos, Live, Shorts, ...), not actual videos —
    targeting the /videos tab directly is what makes flat extraction return real entries.
    """
    raw = raw.strip()
    if _URL_RE.match(raw):
        url = raw.rstrip("/")
    elif _CHANNEL_ID_RE.match(raw):
        url = f"https://www.youtube.com/channel/{raw}"
    else:
        handle = raw if raw.startswith("@") else f"@{raw}"
        url = f"https://www.youtube.com/{handle}"

    if not url.endswith(_TAB_SUFFIXES):
        url = f"{url}/videos"
    return url


@dataclass
class VideoListing:
    yt_video_id: str
    title: str | None
    duration_s: int | None
    view_count: int | None
    published_at: datetime | None


@dataclass
class ChannelListing:
    yt_channel_id: str
    handle: str | None
    title: str | None
    thumbnail_url: str | None
    videos: list[VideoListing]


def _best_thumbnail(entry: dict) -> str | None:
    thumbnails = entry.get("thumbnails") or []
    if not thumbnails:
        return entry.get("thumbnail")
    return thumbnails[-1].get("url")


def _parse_upload_date(entry: dict) -> datetime | None:
    upload_date = entry.get("upload_date") or entry.get("release_date")
    if not upload_date:
        return None
    try:
        return datetime.strptime(upload_date, "%Y%m%d").replace(tzinfo=UTC)
    except ValueError:
        return None


def list_channel_videos(
    channel_url: str, *, limit: int | None = None, sort: str = "recent"
) -> ChannelListing:
    ydl_opts = {
        **YTDLP_BASE_OPTS,
        "extract_flat": True,
        "skip_download": True,
        "quiet": True,
        "no_warnings": True,
        "playlistend": limit,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(channel_url, download=False)

    entries = [e for e in (info.get("entries") or []) if e]
    # Channels are often nested as a "Videos" tab playlist under the top-level entry.
    if len(entries) == 1 and entries[0].get("entries") is not None:
        info = entries[0]
        entries = [e for e in (info.get("entries") or []) if e]

    videos = [
        VideoListing(
            yt_video_id=e["id"],
            title=e.get("title"),
            duration_s=int(e["duration"]) if e.get("duration") else None,
            view_count=e.get("view_count"),
            published_at=_parse_upload_date(e),
        )
        for e in entries
    ]

    if sort == "views":
        videos.sort(key=lambda v: v.view_count or 0, reverse=True)

    if limit is not None:
        videos = videos[:limit]

    return ChannelListing(
        yt_channel_id=info.get("channel_id") or info.get("id"),
        handle=info.get("uploader_id") or info.get("channel") or None,
        title=info.get("channel") or info.get("title"),
        thumbnail_url=_best_thumbnail(info),
        videos=videos,
    )
