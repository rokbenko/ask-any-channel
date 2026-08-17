"""Pure-function tests for the input-normalization helpers that the CLI's idempotency and
caption-selection behavior depend on. No network, no database."""

from pathlib import Path

import pytest

from core.constants import DATASETS_DIR
from core.dataset.bundle import default_bundle_dir
from core.ingest.captions import _pick_best_candidate
from core.ingest.channel_source import resolve_channel_input


@pytest.mark.parametrize(
    "raw",
    [
        "@SomeChannel",
        "SomeChannel",
        "https://www.youtube.com/@SomeChannel",
        "https://youtube.com/@SomeChannel/",
        "https://www.youtube.com/@SomeChannel/videos",
        "https://www.youtube.com/@SomeChannel/shorts",
    ],
)
def test_default_bundle_dir_is_stable_across_input_forms(raw):
    assert default_bundle_dir(raw) == Path(DATASETS_DIR) / "SomeChannel"


def test_default_bundle_dir_never_escapes_datasets_dir():
    # Path separators and dots must be neutralised, not interpreted.
    out = default_bundle_dir("../../etc/passwd")
    assert out.parent == Path(DATASETS_DIR)
    assert ".." not in out.parts
    assert "/" not in out.name and "\\" not in out.name


def test_default_bundle_dir_falls_back_when_input_has_no_usable_chars():
    assert default_bundle_dir("@") == Path(DATASETS_DIR) / "channel"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("@SomeChannel", "https://www.youtube.com/@SomeChannel/videos"),
        ("SomeChannel", "https://www.youtube.com/@SomeChannel/videos"),
        (
            "UCBR8-60-B28hp2BmDPdntcQ",
            "https://www.youtube.com/channel/UCBR8-60-B28hp2BmDPdntcQ/videos",
        ),
        ("https://www.youtube.com/@SomeChannel", "https://www.youtube.com/@SomeChannel/videos"),
        ("https://www.youtube.com/@SomeChannel/", "https://www.youtube.com/@SomeChannel/videos"),
        # An explicit tab is respected, not double-suffixed.
        (
            "https://www.youtube.com/@SomeChannel/videos",
            "https://www.youtube.com/@SomeChannel/videos",
        ),
        (
            "https://www.youtube.com/@SomeChannel/streams",
            "https://www.youtube.com/@SomeChannel/streams",
        ),
    ],
)
def test_resolve_channel_input_targets_a_videos_tab(raw, expected):
    assert resolve_channel_input(raw) == expected


def test_pick_best_candidate_prefers_manual_english_over_other_variants(tmp_path):
    vid = "abcdefghijk"
    en_orig = tmp_path / f"{vid}.en-orig.vtt"
    en = tmp_path / f"{vid}.en.vtt"
    for p in (en_orig, en):
        p.write_text("WEBVTT\n", encoding="utf-8")

    # Order given deliberately does NOT match preference order.
    chosen = _pick_best_candidate([en_orig, en], vid)
    assert chosen == en


def test_pick_best_candidate_falls_back_to_first_when_no_known_lang(tmp_path):
    vid = "abcdefghijk"
    weird = tmp_path / f"{vid}.zz.vtt"
    weird.write_text("WEBVTT\n", encoding="utf-8")
    assert _pick_best_candidate([weird], vid) == weird


def test_pick_best_candidate_handles_empty():
    assert _pick_best_candidate([], "abcdefghijk") is None
