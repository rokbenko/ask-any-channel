"""Parses YouTube VTT captions into an accurate, deduped, timestamped word stream.

YouTube's auto-generated captions use a "rolling window" style: each cue re-emits words
from the previous cue as scrolling context before appending new ones, and inline
<HH:MM:SS.mmm><c>word</c> tags give per-word timestamps within a cue. Manual captions are
plain, non-overlapping cues with no inline tags. Both are handled by the same pipeline:
parse_vtt -> dedupe_rolling_cues -> cues_to_clean_text.
"""

import re
from dataclasses import dataclass

_TS = r"(?:\d+:)?\d{2}:\d{2}\.\d{3}"
_TIMING_LINE_RE = re.compile(rf"({_TS})\s*-->\s*({_TS})")
_INLINE_TS_RE = re.compile(rf"<({_TS})>")
_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


@dataclass
class Cue:
    start_s: float
    end_s: float
    text: str


@dataclass
class TimedWord:
    text: str
    t_s: float


def _parse_timestamp(ts: str) -> float:
    parts = ts.split(":")
    if len(parts) == 3:
        h, m, s = parts
    else:
        h = "0"
        m, s = parts
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_vtt(raw_vtt: str) -> list[Cue]:
    """Parses a VTT document into cues, tolerant of the WEBVTT header, NOTE/STYLE blocks,
    optional numeric cue identifiers, and YouTube's align/position cue settings."""
    text = raw_vtt.replace("\r\n", "\n").replace("\r", "\n")
    blocks = re.split(r"\n\s*\n", text.strip())
    cues: list[Cue] = []

    for block in blocks:
        lines = [line for line in block.split("\n") if line.strip() != ""]
        if not lines:
            continue

        timing_idx = next((i for i, line in enumerate(lines) if "-->" in line), None)
        if timing_idx is None:
            continue

        m = _TIMING_LINE_RE.match(lines[timing_idx])
        if not m:
            continue

        cue_text = "\n".join(lines[timing_idx + 1 :]).strip()
        if not cue_text:
            continue

        cues.append(
            Cue(
                start_s=_parse_timestamp(m.group(1)),
                end_s=_parse_timestamp(m.group(2)),
                text=cue_text,
            )
        )

    return cues


def _words_with_timestamps(cue_text: str, cue_start: float) -> list[TimedWord]:
    """Splits a cue's raw text on inline <HH:MM:SS.mmm> tags and strips markup tags
    (<c>, </c>, etc.), producing one TimedWord per whitespace-separated token. Segments
    before the first inline tag (or all of it, for untagged cues) get the cue's start time."""
    parts = _INLINE_TS_RE.split(cue_text)

    words: list[TimedWord] = []
    ts = cue_start
    clean = _TAG_RE.sub("", parts[0])
    words.extend(TimedWord(text=w, t_s=ts) for w in clean.split())

    for i in range(1, len(parts), 2):
        ts = _parse_timestamp(parts[i])
        seg_text = parts[i + 1] if i + 1 < len(parts) else ""
        clean = _TAG_RE.sub("", seg_text)
        words.extend(TimedWord(text=w, t_s=ts) for w in clean.split())

    return words


def dedupe_rolling_cues(cues: list[Cue]) -> list[Cue]:
    """Collapses YouTube's rolling-window auto-caption cues into non-overlapping, deduped
    cues — one per newly-introduced word, at the earliest timestamp that word appeared at.

    Each cue's word list is matched against the longest suffix of ALL words emitted so far
    (not just the immediately preceding cue) that equals a prefix of the current cue's
    words: YouTube's rolling window scrolls, so a later cue may repeat only the tail of
    what's been shown, not the full cumulative sentence, and comparing against just the
    previous cue would miss that overlap. Cues that don't overlap at all with prior output
    (manual captions, or a new sentence) pass through unchanged at the word level.
    """
    deduped: list[Cue] = []
    seen_words: list[str] = []

    for cue in cues:
        words = _words_with_timestamps(cue.text, cue.start_s)
        word_texts = [w.text for w in words]

        overlap = 0
        max_overlap = min(len(seen_words), len(word_texts))
        for k in range(max_overlap, 0, -1):
            if seen_words[-k:] == word_texts[:k]:
                overlap = k
                break

        new_words = words[overlap:]
        for i, w in enumerate(new_words):
            next_t_s = new_words[i + 1].t_s if i + 1 < len(new_words) else max(w.t_s, cue.end_s)
            deduped.append(Cue(start_s=w.t_s, end_s=max(next_t_s, w.t_s), text=w.text))

        seen_words.extend(w.text for w in new_words)

    return deduped


def cues_to_clean_text(cues: list[Cue]) -> list[TimedWord]:
    """Explodes deduped cues into an ordered TimedWord stream. Multi-word cues (plain
    manual captions with no inline timing) get their words spread evenly across the cue's
    [start_s, end_s] span; single-word cues (the common post-dedupe case) keep their exact
    timestamp."""
    words: list[TimedWord] = []

    for cue in cues:
        cue_words = cue.text.split()
        if not cue_words:
            continue
        if len(cue_words) == 1:
            words.append(TimedWord(text=cue_words[0], t_s=cue.start_s))
            continue

        span = max(cue.end_s - cue.start_s, 0.0)
        step = span / len(cue_words) if span > 0 else 0.0
        words.extend(TimedWord(text=w, t_s=cue.start_s + step * i) for i, w in enumerate(cue_words))

    return words
