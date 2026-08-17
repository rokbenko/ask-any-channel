import json
import logging
from decimal import Decimal
from uuid import uuid4

from core.chat.citations import parse_citations
from core.store.base import SearchResult


def _make_context(n: int) -> list[SearchResult]:
    return [
        SearchResult(
            chunk_id=uuid4(),
            video_id=uuid4(),
            yt_video_id=f"vid{i}",
            video_title=f"Video {i}",
            text=f"some transcript text for chunk {i} " * 10,
            t_start_s=float(i * 30),
            t_end_s=float(i * 30 + 10),
            score=0.9,
        )
        for i in range(1, n + 1)
    ]


def test_parses_single_citation():
    context = _make_context(3)
    citations = parse_citations("The answer is here [2].", context)

    assert [c.n for c in citations] == [2]
    assert citations[0].yt_video_id == "vid2"


def test_parses_adjacent_multi_citations():
    context = _make_context(3)
    citations = parse_citations("Supported by [1][3].", context)

    assert [c.n for c in citations] == [1, 3]


def test_dedupes_repeated_citation_of_same_number():
    context = _make_context(3)
    citations = parse_citations("First [1], and again [1].", context)

    assert [c.n for c in citations] == [1]


def test_drops_out_of_range_citation_and_logs_warning(caplog):
    context = _make_context(2)
    with caplog.at_level(logging.WARNING):
        citations = parse_citations("See [9] for more.", context)

    assert citations == []
    assert "hallucinated citation" in caplog.text


def test_returns_empty_list_when_no_citations_present():
    context = _make_context(3)
    citations = parse_citations("This channel doesn't cover that topic.", context)

    assert citations == []


def test_citation_includes_timestamped_url_and_quote_snippet():
    context = _make_context(1)
    citations = parse_citations("[1]", context)

    citation = citations[0]
    assert citation.url == "https://www.youtube.com/watch?v=vid1&t=30s"
    assert citation.quote.startswith("some transcript text")


def test_citations_are_sorted_by_number_not_first_appearance():
    context = _make_context(3)
    citations = parse_citations("Mentions [3] before [1].", context)

    assert [c.n for c in citations] == [1, 3]


def test_decimal_timestamps_from_postgres_numeric_become_json_safe_floats():
    # Regression for the live bug: chunks.t_start_s is NUMERIC, psycopg hands back Decimal,
    # and the citation payload is persisted as jsonb — json.dumps(Decimal) raises.
    context = _make_context(1)
    context[0].t_start_s = Decimal("381.336")
    citations = parse_citations("[1]", context)

    assert isinstance(citations[0].t_start_s, float)
    assert citations[0].url.endswith("&t=381s")
    json.dumps({"t_start_s": citations[0].t_start_s})  # must not raise
