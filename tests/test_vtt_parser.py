from pathlib import Path

from core.ingest.vtt_parser import cues_to_clean_text, dedupe_rolling_cues, parse_vtt

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _dedupe_to_words(raw_vtt: str) -> list[str]:
    cues = parse_vtt(raw_vtt)
    deduped = dedupe_rolling_cues(cues)
    words = cues_to_clean_text(deduped)
    return [w.text for w in words]


def test_parse_vtt_empty_and_malformed_input_returns_no_cues():
    assert parse_vtt("") == []
    assert parse_vtt("WEBVTT\n\n") == []
    assert parse_vtt("this is not a vtt file at all") == []


def test_parse_vtt_manual_captions_exact_cues():
    cues = parse_vtt(_read("manual.vtt"))

    assert len(cues) == 3
    assert cues[0].start_s == 0.0
    assert cues[0].end_s == 2.5
    assert cues[0].text == "Hello everyone, welcome"

    assert cues[1].start_s == 2.5
    assert cues[1].end_s == 5.2
    assert cues[1].text == "back to the channel."

    assert cues[2].start_s == 5.2
    assert cues[2].end_s == 8.0
    assert cues[2].text == "Today we're going to talk\nabout something interesting."


def test_manual_captions_have_no_overlap_and_every_word_survives_dedupe():
    cues = parse_vtt(_read("manual.vtt"))
    deduped = dedupe_rolling_cues(cues)

    # no repeated words across manual cues, so dedupe is a pure word-level re-split
    assert len(deduped) == 15
    words = [c.text for c in deduped]
    assert words == [
        "Hello", "everyone,", "welcome",
        "back", "to", "the", "channel.",
        "Today", "we're", "going", "to", "talk", "about", "something", "interesting.",
    ]  # fmt: skip


def test_cues_to_clean_text_interpolates_timestamps_across_multiword_cues():
    cues = parse_vtt(_read("manual.vtt"))
    words = cues_to_clean_text(cues)

    assert [w.text for w in words] == [
        "Hello", "everyone,", "welcome",
        "back", "to", "the", "channel.",
        "Today", "we're", "going", "to", "talk", "about", "something", "interesting.",
    ]  # fmt: skip

    timestamps = [w.t_s for w in words]
    assert timestamps == sorted(timestamps)
    assert timestamps[0] == 0.0


def test_auto_generated_rolling_cues_dedupe_to_clean_transcript():
    words = _dedupe_to_words(_read("auto_generated.vtt"))

    assert words == [
        "Hello", "everyone", "welcome", "to",
        "the", "channel", "today", "we", "discuss",
        "something", "interesting", "and", "useful",
    ]  # fmt: skip


def test_leading_empty_cue_is_skipped_without_breaking_subsequent_parsing():
    """Real YouTube auto-captions often open with a genuinely empty cue
    (e.g. 00:00:00.000 --> 00:00:00.080 with no text) before the first spoken word."""
    cues = parse_vtt(_read("auto_generated.vtt"))
    assert cues[0].start_s == 0.080
    assert "Hello" in cues[0].text


def test_auto_generated_rolling_cues_preserve_exact_inline_timestamps():
    cues = parse_vtt(_read("auto_generated.vtt"))
    deduped = dedupe_rolling_cues(cues)

    by_word = {c.text: c.start_s for c in deduped}
    assert by_word["Hello"] == 0.080
    assert by_word["everyone"] == 0.560
    assert by_word["welcome"] == 1.520
    assert by_word["to"] == 2.070
    assert by_word["the"] == 2.760
    assert by_word["channel"] == 3.040
    assert by_word["something"] == 5.320
    assert by_word["interesting"] == 5.760
    assert by_word["useful"] == 7.040

    timestamps = [c.start_s for c in deduped]
    assert timestamps == sorted(timestamps)


def test_scrolling_rolling_window_still_dedupes_when_window_drops_older_prefix():
    """The 'flush' cues in this fixture repeat only the tail of what's been shown (the
    window scrolls), not the full cumulative sentence — comparing only against the
    immediately preceding cue would miss this overlap and reintroduce duplicates."""
    words = _dedupe_to_words(_read("auto_generated_duplicated_cues.vtt"))

    assert words == [
        "so", "today", "we", "are", "going", "to",
        "build", "something", "really", "cool",
        "using", "Python", "and", "pytest",
    ]  # fmt: skip


def test_scrolling_rolling_window_keeps_final_word_timestamp_at_cue_boundary():
    cues = parse_vtt(_read("auto_generated_duplicated_cues.vtt"))
    deduped = dedupe_rolling_cues(cues)

    by_word = {c.text: c for c in deduped}
    assert by_word["to"].end_s == 3.0
    assert by_word["cool"].end_s == 6.5
    assert by_word["pytest"].end_s == 9.8
