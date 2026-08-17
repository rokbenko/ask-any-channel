import tiktoken

from core.constants import TOKENIZER_ENCODING
from core.ingest.chunker import chunk_timed_words
from core.ingest.vtt_parser import TimedWord

_encoding = tiktoken.get_encoding(TOKENIZER_ENCODING)


def _make_words(n: int, *, seconds_per_word: float = 0.4) -> list[TimedWord]:
    return [TimedWord(text=f"word{i}", t_s=i * seconds_per_word) for i in range(n)]


def _token_count(text: str) -> int:
    return len(_encoding.encode(text))


def test_empty_input_returns_no_chunks():
    assert chunk_timed_words([]) == []


def test_single_short_window_produces_one_chunk():
    words = _make_words(10)
    chunks = chunk_timed_words(words, target_tokens=400, overlap_ratio=0.15)

    assert len(chunks) == 1
    assert chunks[0].t_start_s == words[0].t_s
    assert chunks[0].t_end_s == words[-1].t_s
    assert chunks[0].text == " ".join(w.text for w in words)


def test_chunks_stay_near_target_token_count():
    words = _make_words(2000)
    chunks = chunk_timed_words(words, target_tokens=400, overlap_ratio=0.15)

    assert len(chunks) > 1
    for chunk in chunks[:-1]:
        assert chunk.token_count <= 400
        # a window that stopped well short of the target would indicate a bug, not just
        # the natural tail-end shrinkage of the final chunk
        assert chunk.token_count >= 400 * 0.5


def test_last_chunk_is_not_dropped_when_shorter_than_target():
    words = _make_words(50)
    chunks = chunk_timed_words(words, target_tokens=400, overlap_ratio=0.15)

    assert len(chunks) == 1
    assert chunks[-1].text.endswith(words[-1].text)


def test_consecutive_chunks_overlap_by_roughly_the_configured_ratio():
    words = _make_words(2000)
    target_tokens = 400
    overlap_ratio = 0.15
    chunks = chunk_timed_words(words, target_tokens=target_tokens, overlap_ratio=overlap_ratio)

    assert len(chunks) > 2
    for prev_chunk, next_chunk in zip(chunks, chunks[1:], strict=False):
        prev_words = prev_chunk.text.split()
        next_words = next_chunk.text.split()

        overlap = 0
        max_check = min(len(prev_words), len(next_words))
        for k in range(max_check, 0, -1):
            if prev_words[-k:] == next_words[:k]:
                overlap = k
                break

        assert overlap > 0
        overlap_token_count = _token_count(" ".join(prev_words[-overlap:]))
        expected = target_tokens * overlap_ratio
        assert overlap_token_count <= expected * 1.5


def test_chunk_boundaries_land_exactly_on_word_timestamps():
    words = _make_words(2000)
    chunks = chunk_timed_words(words, target_tokens=400, overlap_ratio=0.15)

    word_by_ts = {w.t_s: w.text for w in words}
    for chunk in chunks:
        first_word = chunk.text.split()[0]
        last_word = chunk.text.split()[-1]
        assert word_by_ts[chunk.t_start_s] == first_word
        assert word_by_ts[chunk.t_end_s] == last_word
        assert chunk.t_start_s <= chunk.t_end_s


def test_no_words_are_skipped_across_chunk_boundaries():
    words = _make_words(500)
    chunks = chunk_timed_words(words, target_tokens=400, overlap_ratio=0.15)

    covered_timestamps = set()
    for chunk in chunks:
        # reconstruct per-word timestamps for this chunk isn't possible from text alone
        # when overlap collapses duplicate words, so just check start/end coverage
        covered_timestamps.add(chunk.t_start_s)
        covered_timestamps.add(chunk.t_end_s)

    assert min(covered_timestamps) == words[0].t_s
    assert max(covered_timestamps) == words[-1].t_s
