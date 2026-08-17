"""Parses [n] citation markers out of the assembled assistant response and maps them to the
retrieved context chunks that produced them."""

import logging
import re
from dataclasses import dataclass
from uuid import UUID

from core.search.search import build_timestamped_url
from core.store.base import SearchResult

logger = logging.getLogger(__name__)

_CITATION_RE = re.compile(r"\[(\d+)\]")
_QUOTE_SNIPPET_MAX_CHARS = 200


@dataclass
class Citation:
    n: int
    video_id: UUID
    yt_video_id: str
    title: str | None
    url: str
    t_start_s: float
    quote: str


def _snippet(text: str, max_len: int = _QUOTE_SNIPPET_MAX_CHARS) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1].rstrip() + "…"


def parse_citations(text: str, context: list[SearchResult]) -> list[Citation]:
    found: dict[int, Citation] = {}
    for match in _CITATION_RE.finditer(text):
        n = int(match.group(1))
        if n in found:
            continue
        if n < 1 or n > len(context):
            logger.warning(
                "chat: hallucinated citation [%d] — only %d context blocks were sent; dropping",
                n,
                len(context),
            )
            continue
        chunk = context[n - 1]
        # t_start_s comes from a Postgres NUMERIC column, which psycopg returns as Decimal —
        # not JSON-serializable, and this value ends up in the messages.citations jsonb
        # payload. Cast to float here, once, rather than at every downstream call site.
        t_start_s = float(chunk.t_start_s)
        found[n] = Citation(
            n=n,
            video_id=chunk.video_id,
            yt_video_id=chunk.yt_video_id,
            title=chunk.video_title,
            url=build_timestamped_url(chunk.yt_video_id, t_start_s),
            t_start_s=t_start_s,
            quote=_snippet(chunk.text),
        )
    return [found[n] for n in sorted(found)]
