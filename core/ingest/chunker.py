"""Greedy token-window chunking over a timed-word stream. Chunk boundaries are word
boundaries, so t_start_s/t_end_s always land exactly on a spoken word."""

from dataclasses import dataclass

import tiktoken

from core.constants import CHUNK_OVERLAP_RATIO, CHUNK_TARGET_TOKENS, TOKENIZER_ENCODING
from core.ingest.vtt_parser import TimedWord


@dataclass
class ChunkDraft:
    idx: int
    text: str
    t_start_s: float
    t_end_s: float
    token_count: int


def chunk_timed_words(
    words: list[TimedWord],
    *,
    target_tokens: int = CHUNK_TARGET_TOKENS,
    overlap_ratio: float = CHUNK_OVERLAP_RATIO,
    encoding_name: str = TOKENIZER_ENCODING,
) -> list[ChunkDraft]:
    if not words:
        return []

    encoding = tiktoken.get_encoding(encoding_name)
    word_tokens = [len(encoding.encode(w.text)) for w in words]
    overlap_tokens = int(target_tokens * overlap_ratio)

    chunks: list[ChunkDraft] = []
    start = 0
    n = len(words)

    while start < n:
        end = start
        tokens_so_far = 0
        while end < n and (tokens_so_far + word_tokens[end] <= target_tokens or end == start):
            tokens_so_far += word_tokens[end]
            end += 1

        window = words[start:end]
        chunks.append(
            ChunkDraft(
                idx=len(chunks),
                text=" ".join(w.text for w in window),
                t_start_s=window[0].t_s,
                t_end_s=window[-1].t_s,
                token_count=tokens_so_far,
            )
        )

        if end >= n:
            break

        # Step the next window back by overlap_tokens worth of words from `end`.
        back = end
        overlap_so_far = 0
        while back > start and overlap_so_far + word_tokens[back - 1] <= overlap_tokens:
            back -= 1
            overlap_so_far += word_tokens[back]

        start = back if back > start else end

    return chunks
