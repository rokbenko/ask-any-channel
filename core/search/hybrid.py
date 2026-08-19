"""Reciprocal Rank Fusion and lexical-query preparation for hybrid retrieval. Pure functions —
no store/provider access — so the fusion math is unit-testable without Postgres."""

from uuid import UUID

from core.constants import MAX_QUESTION_CHARS, RRF_K


def rrf_fuse(ranked_lists: list[list[UUID]], *, k: int = RRF_K) -> list[tuple[UUID, float]]:
    """Reciprocal Rank Fusion (Cormack et al. 2009): each id's score is the sum of
    1/(k + rank) across every list it appears in (rank is 0-based position). Ids absent from a
    list simply don't contribute from it. Ties break by first appearance across the lists, so
    the result is deterministic regardless of dict iteration order."""
    scores: dict[UUID, float] = {}
    first_seen: dict[UUID, int] = {}
    seen_count = 0
    for ranked_list in ranked_lists:
        for rank, item_id in enumerate(ranked_list):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
            if item_id not in first_seen:
                first_seen[item_id] = seen_count
                seen_count += 1
    return sorted(scores.items(), key=lambda pair: (-pair[1], first_seen[pair[0]]))


def prepare_lexical_query(text: str) -> str | None:
    """Collapse whitespace and cap length for websearch_to_tsquery. Returns None when the text
    has no alphanumeric content at all (pure punctuation/whitespace) — the lexical arm is
    skipped rather than sent a query that can't match anything. Quotes and other special
    characters are passed through untouched: websearch_to_tsquery parses them safely (it's
    designed for raw user search-box input), so no manual escaping is needed or attempted."""
    collapsed = " ".join(text.split())
    if not collapsed or not any(ch.isalnum() for ch in collapsed):
        return None
    return collapsed[:MAX_QUESTION_CHARS]
