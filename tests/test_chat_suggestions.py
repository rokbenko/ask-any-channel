from datetime import UTC, datetime
from uuid import uuid4

from core.chat.suggestions import (
    BRANDING_KEY,
    ensure_suggested_questions,
    generate_suggested_questions,
    sanitize_question,
    sanitize_questions,
)
from core.config import Settings
from core.constants import SUGGESTED_QUESTION_MAX_CHARS, SUGGESTED_QUESTIONS_COUNT
from core.credentials import CredentialsProvider
from core.models import Channel
from tests.fakes import FakeLLMProvider, FakeVectorStore


def _make_channel(branding=None) -> Channel:
    return Channel(
        id=uuid4(),
        yt_channel_id="UC" + "x" * 22,
        handle="@some",
        title="Some Channel",
        thumbnail_url=None,
        branding=branding or {},
        created_at=datetime.now(UTC),
    )


def _settings(*, openai_key: str | None) -> Settings:
    return Settings(
        instance_mode="selfhost",
        database_url="postgresql://x",
        openai_api_key=openai_key,
        openai_base_url=None,
        anthropic_api_key=None,
        anthropic_base_url=None,
        chat_provider="openai",
        chat_model=None,
        raw_captions_dir="data/raw",
    )


# --- sanitizer ------------------------------------------------------------------------


def test_sanitize_question_strips_markdown_link_syntax():
    assert (
        sanitize_question("[Sign in](https://evil.example) now?")
        == "Sign inhttps://evil.example now?"
    )


def test_sanitize_question_strips_bullets_numbering_and_extra_whitespace():
    assert sanitize_question("  3)   What   is  this? ") == "What is this?"
    assert sanitize_question("- What is this?") == "What is this?"


def test_sanitize_question_keeps_only_the_first_line_and_caps_length():
    long = "a" * (SUGGESTED_QUESTION_MAX_CHARS + 50)
    assert len(sanitize_question(long)) == SUGGESTED_QUESTION_MAX_CHARS
    assert sanitize_question("first line?\nsecond line") == "first line?"


def test_sanitize_question_returns_none_for_non_strings_and_empties():
    assert sanitize_question(42) is None
    assert sanitize_question("") is None
    assert sanitize_question("***") is None


def test_sanitize_questions_drops_junk_and_caps_count():
    raw = ["ok?", 7, "", "![img](x)"] + [f"q{i}?" for i in range(20)]

    cleaned = sanitize_questions(raw)

    assert cleaned[0] == "ok?"
    assert "imgx" in cleaned  # markdown syntax stripped, text kept
    assert len(cleaned) <= 10


# --- generation ------------------------------------------------------------------------


def test_generate_suggested_questions_parses_one_per_line_and_caps_count():
    provider = FakeLLMProvider(
        embedding_dim=4, chat_reply="- What is X?\n2. Why Y?\n\n* How Z?\nA?\nB?\nC?\nD?"
    )

    questions = generate_suggested_questions(provider, "m", "Chan", ["some transcript text"])

    assert questions[:3] == ["What is X?", "Why Y?", "How Z?"]
    assert len(questions) == SUGGESTED_QUESTIONS_COUNT
    assert provider.chat_calls == 1
    assert "some transcript text" in provider.last_messages[-1].content


def test_generate_suggested_questions_makes_no_call_without_sample_texts():
    provider = FakeLLMProvider(embedding_dim=4, chat_reply="What?")

    assert generate_suggested_questions(provider, "m", "Chan", []) == []
    assert provider.chat_calls == 0


# --- lazy ensure -------------------------------------------------------------------


def test_ensure_returns_stored_questions_without_touching_a_provider():
    channel = _make_channel(branding={BRANDING_KEY: ["Stored?"]})
    store = FakeVectorStore(channel=channel)
    settings = _settings(openai_key=None)  # would fail if a provider were built

    assert ensure_suggested_questions(store, settings, CredentialsProvider(settings), channel) == [
        "Stored?"
    ]


def test_ensure_treats_stored_empty_list_as_already_attempted():
    """An empty reply is persisted as [] so every later render doesn't bill again."""
    channel = _make_channel(branding={BRANDING_KEY: []})
    store = FakeVectorStore(channel=channel)
    settings = _settings(openai_key=None)

    assert ensure_suggested_questions(store, settings, CredentialsProvider(settings), channel) == []


def test_ensure_returns_empty_without_persisting_when_no_chat_key():
    channel = _make_channel()
    store = FakeVectorStore(channel=channel)
    settings = _settings(openai_key=None)

    result = ensure_suggested_questions(store, settings, CredentialsProvider(settings), channel)

    assert result == []
    assert BRANDING_KEY not in store.get_channel(channel.id).branding  # retried once key exists
